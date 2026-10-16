"""桥协议真网络实测（``network`` 标记）：拉起真 worker 子进程、真连通达信服务器。

默认套件与 CI 通过 ``addopts = -m 'not network and not integration'`` 排除；
本机验收运行方式::

    .venv\\Scripts\\python.exe -m pytest -m network tests/test_worker_live.py -q

逐条覆盖票据 01 的验收：握手两行、日K 60 根与字段、管道往返、未知方法存活、
UTF-8 往返、stdout 零污染、关 stdin 干净退出。
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.network

HANDSHAKE_TIMEOUT = 60.0
REQUEST_TIMEOUT = 60.0


class WorkerPeer:
    """把 worker 子进程包成「发一行收一行」的对端，并对两条流做并发读取。"""

    def __init__(self) -> None:
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        self.process = subprocess.Popen(
            [sys.executable, "-m", "mootdx", "worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.stdout_lines: queue.Queue[bytes] = queue.Queue()
        self.stderr_lines: list[str] = []
        self._threads = [
            threading.Thread(target=self._pump, args=(self.process.stdout, self.stdout_lines.put), daemon=True),
            threading.Thread(target=self._drain_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _pump(self, stream, sink) -> None:
        for line in iter(stream.readline, b""):
            sink(line)
        sink(None)

    def _drain_stderr(self) -> None:
        for line in iter(self.process.stderr.readline, b""):
            self.stderr_lines.append(line.decode("utf-8", errors="replace").rstrip("\r\n"))

    def wait_for_stderr(self, needle: str, timeout: float = 10.0) -> bool:
        """有界等待 stderr 出现含 needle 的行（跨管道投递非瞬时）。"""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(needle in line for line in self.stderr_lines):
                return True
            time.sleep(0.05)
        return False

    def read_message(self, timeout: float = REQUEST_TIMEOUT, what: str = "message") -> dict:
        try:
            line = self.stdout_lines.get(timeout=timeout)
        except queue.Empty:
            pytest.fail(f"等待 {what} 超时（{timeout}s）\nstderr:\n" + "\n".join(self.stderr_lines))
        if line is None:
            pytest.fail(f"等待 {what} 时 stdout 关闭\nstderr:\n" + "\n".join(self.stderr_lines))
        # 硬性纪律：stdout 的每一行都必须是可解析的协议消息（UTF-8 无乱码）
        return json.loads(line.decode("utf-8", errors="strict"))

    def send(self, message: dict) -> None:
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        self.process.stdin.write(payload)
        self.process.stdin.flush()

    def close_stdin(self) -> None:
        self.process.stdin.close()

    def shutdown(self) -> None:
        if self.process.poll() is None:
            self.process.kill()


@pytest.fixture()
def peer():
    peer = WorkerPeer()
    try:
        yield peer
    finally:
        peer.shutdown()
        # 等子进程收尸，防止句柄泄漏警告
        with contextlib.suppress(subprocess.TimeoutExpired):
            peer.process.wait(timeout=10)


@pytest.fixture()
def ready_peer(peer):
    hello = peer.read_message(what="hello")
    assert hello["hello"] is True
    assert hello["protocol"] == 1
    assert hello["python"].startswith("3.")
    assert hello["mootdx"]

    ready = peer.read_message(timeout=HANDSHAKE_TIMEOUT, what="ready")
    assert ready["ready"] is True, ready
    assert isinstance(ready["server"], list), ready
    assert isinstance(ready["warmup_seconds"], (int, float)), ready

    # 日志可见所选服务器 IP、且只出现于 stderr（stdout 无协议外杂行由 read_message 强制）
    server_ip = ready["server"][0]
    assert peer.wait_for_stderr(server_ip), peer.stderr_lines[-5:]
    return peer


def test_handshake_and_daily_kline_over_real_network(ready_peer):
    peer = ready_peer
    peer.send({"id": 1, "method": "daily_kline", "params": {"symbol": "002029", "count": 60, "adjust": "qfq"}})
    response = peer.read_message(what="daily_kline 应答")

    assert response["id"] == 1
    assert response["ok"] is True, response
    bars = response["bars"]
    assert len(bars) == 60

    expected_fields = {"datetime", "open", "high", "low", "close", "vol", "amount"}
    assert set(bars[0]) == expected_fields, bars[0]

    # 升序（旧→新），且含最近交易日附近的一根
    stamps = [bar["datetime"] for bar in bars]
    assert stamps == sorted(stamps)
    last_date = date.fromisoformat(stamps[-1][:10])
    assert date.today() - timedelta(days=10) <= last_date <= date.today(), stamps[-1]

    # 值域：价 >0、high ≥ max(open, close)、low ≤ min(open, close)、量额为正
    for bar in bars:
        assert bar["open"] > 0 and bar["close"] > 0
        assert bar["high"] >= max(bar["open"], bar["close"])
        assert bar["low"] <= min(bar["open"], bar["close"])
        assert bar["vol"] > 0
        assert bar["amount"] > 0


def test_pipe_roundtrip_survives_unknown_method_and_utf8(ready_peer):
    peer = ready_peer

    # 未知方法：ok:false，id 对应；中文方法名证明 UTF-8 双侧无损
    peer.send({"id": 7, "method": "没有这个方法", "params": {}})
    unknown = peer.read_message(what="未知方法应答")
    assert unknown["id"] == 7
    assert unknown["ok"] is False
    assert "没有这个方法" in unknown["error"]

    # 进程存活：紧接着的合法请求照常应答（发一行收一行）
    peer.send({"id": 8, "method": "daily_kline", "params": {"symbol": "002029", "count": 3, "adjust": "qfq"}})
    followup = peer.read_message(what="后续请求应答")
    assert followup["id"] == 8
    assert followup["ok"] is True
    assert len(followup["bars"]) <= 3


def test_stdin_close_exits_cleanly(ready_peer):
    peer = ready_peer
    peer.close_stdin()

    assert peer.process.wait(timeout=15) == 0
    assert peer.stdout_lines.get(timeout=5) is None  # stdout 到 EOF 无残余行
