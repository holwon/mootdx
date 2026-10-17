"""数据服务真网络实测（``network`` 标记）：真 TCP 服务、真连通达信服务器。

前置条件：**本机可出站连通达信行情服务器（TCP 7709）**。用例在进程内起一个监听在
``127.0.0.1`` 随机端口的 ``serve``（系统分配端口，不写死 7701），因此不会与开发机上
已在跑的容器/进程撞端口；用例结束即关服务，不留监听与残留进程。

默认套件与 CI 通过 ``addopts = -m 'not network and not integration'`` 排除；
本机验收运行方式::

    .venv\\Scripts\\python.exe -m pytest -m network tests/test_worker_live.py -q

逐条覆盖：握手两帧、日K 60 根与字段、按 id 收发、未知方法存活、UTF-8 往返。

与迁移前（stdio 形态）的差异：旧用例还断言「关 stdin 后子进程干净退出」与「stdout
无杂行」——TCP 形态下没有 stdin，进程也不因单个连接结束而退出（它继续监听下一个
连接），故这两条改为「关连接后服务仍可接受新连接」；日志不再需要「不进协议流」的
断言，因为日志与协议流已经彻底分离（不同 fd）。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from mootdx import worker
from tests.tcp_support import RunningService

pytestmark = pytest.mark.network
HANDSHAKE_TIMEOUT = 60.0
REQUEST_TIMEOUT = 60.0


@pytest.fixture(scope="module")
def service():
    # 用生产工厂 ClientFactory（每连接一个新客户端 + 进程内共享探测结果），
    # 不传替身——这组用例的职责就是验证真实路径。
    # 模块级共享：一个服务实例覆盖整组用例，首次探测只付一次（约 10s），
    # 而不是每个用例各付一次。连接仍各用例独立，隔离性不受影响。
    running = RunningService(worker.ClientFactory(), timeout=REQUEST_TIMEOUT)
    try:
        yield running
    finally:
        running.stop()


@pytest.fixture()
def peer(service):
    client = service.connect()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def ready_peer(peer):
    hello, ready = peer.handshake()

    assert hello["hello"] is True
    assert hello["protocol"] == 1
    assert hello["python"].startswith("3.")
    assert hello["mootdx"]

    assert ready["ready"] is True, ready
    assert isinstance(ready["server"], list), ready
    assert isinstance(ready["warmup_seconds"], (int, float)), ready
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


def test_socket_roundtrip_survives_unknown_method_and_utf8(ready_peer):
    peer = ready_peer

    # 未知方法：ok:false，id 对应；中文方法名证明 UTF-8 双侧无损
    peer.send({"id": 7, "method": "没有这个方法", "params": {}})
    unknown = peer.read_message(what="未知方法应答")
    assert unknown["id"] == 7
    assert unknown["ok"] is False
    assert "没有这个方法" in unknown["error"]

    # 会话存活：紧接着的合法请求照常应答（发一行收一行）
    peer.send({"id": 8, "method": "daily_kline", "params": {"symbol": "002029", "count": 3, "adjust": "qfq"}})
    followup = peer.read_message(what="后续请求应答")
    assert followup["id"] == 8
    assert followup["ok"] is True
    assert len(followup["bars"]) <= 3


def test_connection_close_keeps_service_listening(service):
    """关连接 = 会话结束；服务不退出，下一条连接照常可用。"""

    first = service.connect()
    try:
        assert first.handshake()[0]["protocol"] == 1
    finally:
        first.close()

    second = service.connect()
    try:
        hello, ready = second.handshake()
        assert hello["hello"] is True
        assert ready["ready"] is True
    finally:
        second.close()
