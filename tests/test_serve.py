"""TCP 传输层测试：监听、接受、每连接一个会话。

被测对象是「绑定 + 接受连接 + 把 socket 两端包成 reader/writer 交给 ``worker.serve``」
的那层薄封装。协议层本身由 ``tests/test_worker.py`` 覆盖，这里只关心承载带来的
新行为：握手帧按序到达、多连接互不干扰、单连接故障不影响监听循环、绑定失败的形状。

端口一律用 ``0`` 由系统分配（从监听对象回读），因此用例可并行、不依赖本机空闲端口。
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pandas as pd
import pytest

from mootdx import worker

READY_TIMEOUT = 10.0


def sample_frame() -> pd.DataFrame:
    """一根日K，附复权诊断列（应答里不得出现 ``volume`` / ``adj`` / ``preclose``）。"""

    return pd.DataFrame(
        {
            "open": [9.35],
            "close": [9.6],
            "high": [9.64],
            "low": [9.33],
            "vol": [589974],
            "amount": [550339648.0],
            "datetime": ["2026-10-15 15:00"],
            "volume": [609000.25],
            "adj": [0.98907],
            "preclose": [None],
        }
    )


class FakeQuotes:
    """最小行情客户端替身；每个实例记录自己收到的调用，便于断言「每连接一个客户端」。"""

    def __init__(self, server=("127.0.0.1", 7709)) -> None:
        self.server = server
        self.calls: list[dict] = []
        self.closed = False

    def bars(self, **kwargs):
        self.calls.append(kwargs)
        return sample_frame()

    def close(self) -> None:
        self.closed = True


class RecordingFactory:
    """工厂替身：记录每次新建的客户端，用于断言「每连接一个独立客户端」。"""

    def __init__(self) -> None:
        self.created: list[FakeQuotes] = []
        self._lock = threading.Lock()

    def __call__(self) -> FakeQuotes:
        client = FakeQuotes()
        with self._lock:
            self.created.append(client)
        return client


def failing_factory():
    raise ConnectionError("无法连接任何可用的标准行情服务器")


class Peer:
    """把 socket 包成「发一行收一行」的客户端，并强制每一行都是合法协议帧。"""

    def __init__(self, address: tuple[str, int]) -> None:
        self.socket = socket.create_connection(address, timeout=READY_TIMEOUT)
        self.reader = self.socket.makefile("rb")

    def read_message(self, what: str = "message") -> dict:
        line = self.reader.readline()
        assert line, f"等待 {what} 时连接被关闭"
        return json.loads(line.decode("utf-8", errors="strict"))

    def send_line(self, text: str) -> None:
        self.socket.sendall((text + "\n").encode("utf-8"))

    def send(self, message: dict) -> None:
        self.send_line(json.dumps(message, ensure_ascii=False))

    def close(self) -> None:
        self.reader.close()
        self.socket.close()


def handshake(peer: Peer) -> tuple[dict, dict]:
    return peer.read_message("hello"), peer.read_message("ready")


class RunningService:
    """在后台线程里跑一个监听在 127.0.0.1 随机端口上的服务。"""

    def __init__(self, factory) -> None:
        self.server = worker.create_server("127.0.0.1", 0, factory=factory)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return host, port

    def connect(self) -> Peer:
        return Peer(self.address)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=READY_TIMEOUT)


@pytest.fixture()
def factory():
    return RecordingFactory()


@pytest.fixture()
def service(factory):
    running = RunningService(factory)
    try:
        yield running
    finally:
        running.stop()


@pytest.fixture()
def client(service):
    peer = service.connect()
    try:
        yield peer
    finally:
        peer.close()


def test_client_receives_hello_then_ready_and_can_request(client):
    hello, ready = handshake(client)

    assert hello["hello"] is True
    assert hello["protocol"] == 1
    assert hello["python"].startswith("3.")
    assert hello["mootdx"] == worker.__version__

    assert ready["ready"] is True
    assert ready["server"] == ["127.0.0.1", 7709]
    assert ready["warmup_seconds"] >= 0

    client.send({"id": 7, "method": "daily_kline", "params": {"symbol": "002029", "count": 1}})
    response = client.read_message("daily_kline 应答")

    assert response["id"] == 7
    assert response["ok"] is True
    assert len(response["bars"]) == 1
    assert set(response["bars"][0]) == {"datetime", "open", "high", "low", "close", "vol", "amount"}


def test_two_clients_are_isolated_and_each_gets_its_own_quotes_client(service, factory):
    first, second = service.connect(), service.connect()
    try:
        handshake(first)
        handshake(second)

        first.send({"id": 1, "method": "daily_kline", "params": {"symbol": "002029"}})
        second.send({"id": 2, "method": "daily_kline", "params": {"symbol": "600519"}})

        # 每个连接各自收到自己的应答：id 不会串台，也不会读到对方的帧
        assert first.read_message()["id"] == 1
        assert second.read_message()["id"] == 2

        # 每连接一个独立客户端是强制项而非优化：底层客户端持有一个 socket
        assert len(factory.created) == 2
        assert factory.created[0] is not factory.created[1]
    finally:
        first.close()
        second.close()


def test_silent_client_does_not_block_another_client(service):
    silent = service.connect()  # 连上后一个字节都不发
    active = service.connect()
    try:
        handshake(active)
        active.send({"id": 5, "method": "daily_kline", "params": {"symbol": "002029"}})
        assert active.read_message()["id"] == 5
    finally:
        silent.close()
        active.close()


def test_client_disconnect_immediately_after_connect_keeps_service_alive(service):
    orphan = service.connect()
    orphan.close()  # 连上就断，一个请求都不发

    peer = service.connect()
    try:
        hello, ready = handshake(peer)
        assert hello["hello"] is True
        assert ready["ready"] is True
    finally:
        peer.close()


def test_port_already_in_use_raises_bind_error_naming_the_port():
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    try:
        with pytest.raises(worker.BindError) as excinfo:
            worker.create_server("127.0.0.1", port, factory=FakeQuotes)
        assert str(port) in str(excinfo.value)
    finally:
        occupied.close()


def test_malformed_line_answers_error_and_service_survives(client, service):
    handshake(client)

    client.send_line("not-json")
    error = client.read_message("畸形行应答")

    assert error["id"] is None
    assert error["ok"] is False

    client.send({"id": 9, "method": "daily_kline", "params": {"symbol": "002029"}})
    assert client.read_message()["id"] == 9

    # 服务仍在监听：新连接照常握手
    other = service.connect()
    try:
        hello, ready = handshake(other)
        assert hello["protocol"] == 1
        assert ready["ready"] is True
    finally:
        other.close()


def test_ready_false_keeps_listening_and_answers_not_ready():
    running = RunningService(failing_factory)
    try:
        first = running.connect()
        try:
            hello, ready = handshake(first)
            assert hello["hello"] is True
            assert ready["ready"] is False
            assert "无法连接任何可用的标准行情服务器" in ready["error"]

            first.send({"id": 1, "method": "daily_kline", "params": {"symbol": "002029"}})
            response = first.read_message("not ready 应答")
            assert response["id"] == 1
            assert response["ok"] is False
            assert "not ready" in response["error"]
        finally:
            first.close()

        # 进程没有退出：下一次连接仍能握手（网络恢复后重连即可）
        second = running.connect()
        try:
            assert handshake(second)[1]["ready"] is False
        finally:
            second.close()
    finally:
        running.stop()


def test_create_server_binds_system_assigned_port():
    server = worker.create_server("127.0.0.1", 0, factory=FakeQuotes)
    try:
        host, port = server.server_address[:2]
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0
    finally:
        server.server_close()


def test_invalid_utf8_line_ends_that_session_only(client, service):
    handshake(client)

    client.socket.sendall(b"\xff\xfe\n")

    # 该会话按既有语义收尾（非法字节 = 协议违规），服务本身不受影响
    assert client.reader.readline() == b""

    other = service.connect()
    try:
        assert handshake(other)[0]["hello"] is True
    finally:
        other.close()


def test_session_releases_its_quotes_client_on_disconnect(service, factory):
    peer = service.connect()
    handshake(peer)
    peer.close()

    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline and not (factory.created and factory.created[0].closed):
        time.sleep(0.02)

    assert factory.created
    assert factory.created[0].closed is True
