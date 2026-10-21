"""TCP 传输层测试：监听、接受、每连接一个会话。

被测对象是「绑定 + 接受连接 + 把 socket 两端包成 reader/writer 交给 ``worker.serve``」
的那层薄封装。协议层本身由 ``tests/test_worker.py`` 覆盖，这里只关心承载带来的
新行为：握手帧按序到达、多连接互不干扰、单连接故障不影响监听循环、绑定失败的形状。

端口一律用 ``0`` 由系统分配（从监听对象回读），因此用例可并行、不依赖本机空闲端口。
连接与服务的脚手架在 ``tests/tcp_support.py``，与真网络用例共用。
"""

from __future__ import annotations

import socket
import threading
import time

import pandas as pd
import pytest

from mootdx import worker
from tests.tcp_support import RunningService

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


def sample_ticks() -> pd.DataFrame:
    """两笔分笔，附额外列（应答里只能是四列）。"""

    return pd.DataFrame(
        {
            "time": ["14:59", "15:00"],
            "price": [9.61, 9.6],
            "vol": [12, 3],
            "buyorsell": [0, 2],
            "num": [5, 1],
        }
    )


class FakeQuotes:
    """最小行情客户端替身：记录调用、可断开标记。"""

    def __init__(self, server=("127.0.0.1", 7709)) -> None:
        self.server = server
        self.calls: list[dict] = []
        self.closed = False

    def bars(self, **kwargs):
        self.calls.append(kwargs)
        return sample_frame()

    def transactions(self, **kwargs):
        self.calls.append(kwargs)
        return sample_ticks()

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


@pytest.fixture()
def factory():
    return RecordingFactory()


@pytest.fixture()
def service(factory):
    running = RunningService(factory, timeout=READY_TIMEOUT)
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
    hello, ready = client.handshake()

    assert hello["hello"] is True
    assert hello["protocol"] == 1
    assert hello["python"].startswith("3.")
    assert hello["mootdx"] == worker.__version__

    assert ready["ready"] is True
    assert ready["server"] == ["127.0.0.1", 7709]
    assert ready["warmup_seconds"] >= 0

    client.send({"id": 7, "method": "daily_kline", "params": {"symbol": "002029", "count": 1}})
    response = client.read_message(what="daily_kline 应答")

    assert response["id"] == 7
    assert response["ok"] is True
    assert len(response["bars"]) == 1
    assert set(response["bars"][0]) == {"datetime", "open", "high", "low", "close", "vol", "amount"}


def test_ticks_over_the_same_connection_after_daily_kline(client):
    """新方法与 ``daily_kline`` 走同一条连接、回同一 ``id``，不另开通道。"""

    client.handshake()

    client.send({"id": 1, "method": "daily_kline", "params": {"symbol": "002029", "count": 1}})
    assert client.read_message(what="daily_kline 应答")["ok"] is True

    client.send({"id": 2, "method": "ticks", "params": {"symbol": "002029", "date": "2026-10-20"}})
    response = client.read_message(what="ticks 应答")

    assert response["id"] == 2
    assert response["ok"] is True
    assert response["ticks"] == [
        {"time": "14:59", "price": 9.61, "vol": 12, "buyorsell": 0},
        {"time": "15:00", "price": 9.6, "vol": 3, "buyorsell": 2},
    ]


def test_two_clients_are_isolated_and_each_gets_its_own_quotes_client(service, factory):
    first, second = service.connect(), service.connect()
    try:
        first.handshake()
        second.handshake()

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
        active.handshake()
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
        hello, ready = peer.handshake()
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
    client.handshake()

    client.send_line("not-json")
    error = client.read_message(what="畸形行应答")

    assert error["id"] is None
    assert error["ok"] is False

    client.send({"id": 9, "method": "daily_kline", "params": {"symbol": "002029"}})
    assert client.read_message()["id"] == 9

    # 服务仍在监听：新连接照常握手
    other = service.connect()
    try:
        hello, ready = other.handshake()
        assert hello["protocol"] == 1
        assert ready["ready"] is True
    finally:
        other.close()


def test_ready_false_keeps_listening_and_answers_not_ready():
    running = RunningService(failing_factory, timeout=READY_TIMEOUT)
    try:
        first = running.connect()
        try:
            hello, ready = first.handshake()
            assert hello["hello"] is True
            assert ready["ready"] is False
            assert "无法连接任何可用的标准行情服务器" in ready["error"]

            first.send({"id": 1, "method": "daily_kline", "params": {"symbol": "002029"}})
            response = first.read_message(what="not ready 应答")
            assert response["id"] == 1
            assert response["ok"] is False
            assert "not ready" in response["error"]
        finally:
            first.close()

        # 进程没有退出：下一次连接仍能握手（网络恢复后重连即可）
        second = running.connect()
        try:
            assert second.handshake()[1]["ready"] is False
        finally:
            second.close()
    finally:
        running.stop()


def test_create_server_binds_system_assigned_port():
    server = worker.create_server("127.0.0.1", 0, factory=FakeQuotes)
    try:
        host, port = server.server_address[:2]
        assert host == "127.0.0.1"
        assert isinstance(port, int)
        assert port > 0
    finally:
        server.server_close()


def test_invalid_utf8_line_ends_that_session_only(client, service):
    client.handshake()

    client.socket.sendall(b"\xff\xfe\n")

    # 该会话按既有语义收尾（非法字节 = 协议违规），服务本身不受影响
    assert client.reader.readline() == b""

    other = service.connect()
    try:
        assert other.handshake()[0]["hello"] is True
    finally:
        other.close()


def test_session_releases_its_quotes_client_on_disconnect(service, factory):
    peer = service.connect()
    peer.handshake()
    peer.close()

    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline and not (factory.created and factory.created[0].closed):
        time.sleep(0.02)

    assert factory.created
    assert factory.created[0].closed is True
