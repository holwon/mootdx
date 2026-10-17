"""mootdx 数据服务：行式 JSON 协议 v1（TCP 承载）。

``python -m mootdx serve`` 起一个常驻 TCP 服务。任何客户端（本机或另一台机器）连到
``host:port`` 后，以「一行一个完整 JSON 对象、UTF-8 编码」的方式收发消息：

* 连上即收到一行 ``hello``（协议版本 + python / mootdx 版本）；
* 随后尝试连接一台可用行情服务器：成功输出 ``ready``（server / warmup_seconds），
  失败输出 ``{"ready": false, "error": ...}``——**连接与进程都继续存活**；
* 请求形如 ``{"id": N, "method": "daily_kline", "params": {...}}``，应答回同一 ``id``：
  ``{"id": N, "ok": true, "bars": [...]}`` 或 ``{"id": N, "ok": false, "error": "..."}``；
* 连接关闭即会话结束（该会话的行情客户端随之释放）。

分层：``serve(reader, writer, factory)`` 只认「一条读流 + 一条写流」，与承载无关；
本模块的传输层负责监听、接受，并把每个 socket 的两端包成 reader / writer 后调它。
因此换承载（TCP / Unix socket / 内存流）不必触碰协议实现。

纪律：日志一律走 stderr（经 ``mootdx.logger``），不占用协议流；每个连接一个线程、
一个独立行情客户端（底层客户端持有一个 socket，共享实例上的并发请求会交错）；
连接内部请求严格串行（单飞行、写入等回读）。

``method`` 命名空间为后续数据面预留，当前仅实现 ``daily_kline``。
"""

from __future__ import annotations

import io
import json
import logging
import os
import platform
import socketserver
import threading
import time
from collections.abc import Callable
from typing import Any, BinaryIO, TextIO

from mootdx import __version__
from mootdx.logger import logger

#: 协议版本；帧格式变更时递增，调用方按此判坏。
PROTOCOL = 1

#: 服务的缺省监听地址与端口（容器内必须监听 0.0.0.0，否则端口映射不可达）。
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 7701

#: 应答中保留的行情列。``volume`` / ``adj`` / ``preclose`` 是复权诊断列，不外发。
BAR_FIELDS = ("datetime", "open", "high", "low", "close", "vol", "amount")

#: ``daily_kline`` 的缺省根数与频率（9 = 日K）。
DEFAULT_COUNT = 60
DAILY_FREQUENCY = 9
ALLOWED_ADJUSTS = ("qfq", "hfq")


def send(writer: BinaryIO, message: dict[str, Any]) -> None:
    """写一行协议消息到写流并立即刷出（直接写二进制 buffer，绕开文本层缓冲歧义）。"""

    payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    writer.write(payload)
    writer.flush()


def _as_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def daily_kline(client: Any, params: dict[str, Any]) -> dict[str, Any]:
    """执行一次 ``daily_kline`` 请求：取数 → 裁剪列 → 记录化。

    :param client: 行情客户端（``Quotes.factory`` 产物或测试替身）
    :param params: ``{"symbol": str, "count": int, "start": int, "adjust": "qfq"|"hfq"}
        —— ``count`` / ``start`` 缺省为 60 / 0；``adjust`` 不给则取未复权原始价。
    :return: ``{"ok": True, "bars": [...]}``；空数据返回空列表。
    """

    symbol = str(params.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("daily_kline 缺少 symbol 参数")

    count = _as_int(params.get("count", DEFAULT_COUNT), "count")
    start = _as_int(params.get("start", 0), "start")
    if count <= 0:
        raise ValueError("count 必须大于 0")
    if start < 0:
        raise ValueError("start 必须大于等于 0")

    adjust = params.get("adjust")
    kwargs: dict[str, Any] = {}
    if adjust is not None:
        adjust = str(adjust).lower()
        if adjust not in ALLOWED_ADJUSTS:
            raise ValueError(f"adjust 仅支持 {'/'.join(ALLOWED_ADJUSTS)}，收到: {adjust!r}")
        kwargs["adjust"] = adjust

    frame = client.bars(symbol=symbol, frequency=DAILY_FREQUENCY, start=start, offset=count, **kwargs)
    if frame is None or frame.empty:
        return {"ok": True, "bars": []}

    missing = [field for field in BAR_FIELDS if field not in frame.columns]
    if missing:
        raise ValueError(f"行情缺少字段: {', '.join(missing)}")

    bars = json.loads(frame[list(BAR_FIELDS)].to_json(orient="records", date_format="iso"))
    return {"ok": True, "bars": bars}


def handle(client: Any, request: dict[str, Any]) -> dict[str, Any]:
    """分发一条请求；未知方法回 ``ok:false`` 而不落异常（进程存活）。"""

    method = request.get("method")
    params = request.get("params") or {}

    if method == "daily_kline":
        return daily_kline(client, params)

    return {"ok": False, "error": f"unknown method: {method!r}"}


def serve(reader: TextIO, writer: BinaryIO, factory: Callable[[], Any]) -> int:
    """协议主循环：握手 → 串行收发 → 读流 EOF 结束会话。

    这是**承载无关**的一层：它只认一条读流与一条写流。生产上由 TCP 传输层把每个
    连接的两端包成 reader / writer 后调用它，因此换承载不必触碰这里。

    :param reader: 请求输入流（生产为包装成 UTF-8 的 socket 读端）
    :param writer: 协议输出流（生产为 socket 写端）
    :param factory: 行情客户端工厂；抛异常即表现为 ``ready:false``（会话保持可读）
    :return: 退出码——正常路径（含 EOF）为 0，读流出现非法 UTF-8 时为 2
    """

    logger.info("hello: protocol=%s python=%s mootdx=%s", PROTOCOL, platform.python_version(), __version__)
    send(
        writer,
        {
            "hello": True,
            "protocol": PROTOCOL,
            "python": platform.python_version(),
            "mootdx": __version__,
        },
    )

    client = None
    ready_error = None
    failure_code = 0
    warmup_started = time.perf_counter()
    try:
        client = factory()
    except Exception as exc:  # noqa: BLE001 — 连不上服务器如实上报，不终止进程
        ready_error = f"{type(exc).__name__}: {exc}"
        logger.error("ready failed: %s", ready_error)
        send(writer, {"ready": False, "error": ready_error})
    else:
        server = list(client.server) if getattr(client, "server", None) else None
        warmup_seconds = round(time.perf_counter() - warmup_started, 2)
        # 日志先于协议帧：stdout 上观察到 ready 时，stderr 必已有对应行
        logger.info("ready: server=%s warmup=%.2fs", server, warmup_seconds)
        send(writer, {"ready": True, "server": server, "warmup_seconds": warmup_seconds})

    while True:
        try:
            line = reader.readline()
        except UnicodeDecodeError as exc:
            # 非法 UTF-8 = 对端协议违规；本会话以非零码结束（传输层负责收尾，服务不中断）
            logger.error("读流不是有效 UTF-8，结束会话: %s", exc)
            failure_code = 2
            break
        if not line:  # 读流 EOF（连接关闭）→ 会话结束
            logger.info("client closed the connection")
            break
        line = line.strip()
        if not line:
            continue

        request_id = None
        started = time.perf_counter()
        try:
            request = json.loads(line)
            request_id = request.get("id") if isinstance(request, dict) else None
            if client is None:
                raise RuntimeError(f"worker is not ready: {ready_error}")
            result = handle(client, request)
            send(writer, {"id": request_id, **result})
            logger.debug(
                "request id=%s method=%s ok=%s in %.3fs",
                request_id,
                request.get("method") if isinstance(request, dict) else None,
                result.get("ok"),
                time.perf_counter() - started,
            )
        except Exception as exc:  # noqa: BLE001 — 错误如实回报，进程存活
            send(writer, {"id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            logger.debug("request id=%s failed in %.3fs: %s", request_id, time.perf_counter() - started, exc)

    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001 — 退出路径尽力而为
            logger.debug("close failed", exc_info=True)
    return failure_code


def _setup_logging() -> None:
    """日志一律走 stderr（日志不占用协议流）。"""

    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


class BindError(OSError):
    """监听地址/端口绑定失败（端口被占用、无权限、地址不可用）。"""


class ClientFactory:
    """行情客户端工厂：每连接新建一个客户端，但**进程内共享探测结果**。

    底层客户端持有一个 socket，共享同一实例会让不同连接的请求在同一个 socket 上
    交错、破坏协议流，因此每个连接必须各自 ``factory()``。但探测一台可用服务器
    实测约 3.4 秒，若每个连接都走完整探测，多客户端场景会成倍变慢——故首次探测
    成功的服务器在本进程内记住，后续连接直接指定它；该服务器不可用时回落完整探测。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._preferred: tuple[Any, ...] | None = None

    @property
    def preferred(self) -> tuple[Any, ...] | None:
        """最近一次探测成功的服务器（仅供观测与测试）。"""

        with self._lock:
            return self._preferred

    def _remember(self, value: tuple[Any, ...] | None) -> None:
        with self._lock:
            self._preferred = value

    def __call__(self) -> Any:
        from mootdx.quotes import Quotes

        with self._lock:
            preferred = self._preferred

        if preferred:
            try:
                return Quotes.factory(market="std", timeout=10, server=preferred)
            except Exception:  # noqa: BLE001 — 缓存的服务器可能已下线，回落完整探测
                logger.info("cached server %s unusable, re-probing", preferred)
                self._remember(None)

        client = Quotes.factory(market="std", timeout=10)
        server = getattr(client, "server", None)
        if server:
            self._remember(tuple(server))
        return client


class _SessionHandler(socketserver.StreamRequestHandler):
    """一个连接 = 一个会话：把 socket 两端包成 reader / writer，然后交给 ``serve``。"""

    server: _ThreadedServer  # 由 socketserver 注入

    def handle(self) -> None:
        peer = self.client_address[:2]
        logger.info("client connected: %s:%s", *peer)
        reader = io.TextIOWrapper(self.rfile, encoding="utf-8", errors="strict")
        try:
            code = serve(reader, self.wfile, self.server.factory)
        except (ConnectionError, OSError) as exc:  # noqa: BLE001 — 单连接故障不得拖垮服务
            logger.warning("client %s:%s dropped: %s", *peer, exc)
        else:
            if code:
                logger.warning("client %s:%s session ended with code %s", *peer, code)
        finally:
            # 交还底层缓冲给 socketserver 收尾，避免 TextIOWrapper 的析构把 rfile 关掉
            reader.detach()
            logger.info("client disconnected: %s:%s", *peer)


class _ThreadedServer(socketserver.ThreadingTCPServer):
    """每连接一线程；单连接的异常不外溢到监听循环。"""

    daemon_threads = True
    #: POSIX 上允许越过 TIME_WAIT 立刻重启；Windows 上 ``SO_REUSEADDR`` 会**抢占**
    #: 已被别的进程监听的端口，让「端口被占用」变成静默劫持，故那边一律关掉。
    allow_reuse_address = os.name != "nt"

    def __init__(self, address: tuple[str, int], factory: Callable[[], Any]) -> None:
        self.factory = factory
        super().__init__(address, _SessionHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        logger.warning("connection %s error", client_address, exc_info=True)


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, factory: Callable[[], Any] | None = None) -> Any:
    """绑定并返回一个已就绪的监听对象（不开始接受连接，便于测试与实际端口回读）。

    :param host: 监听地址；容器内必须为 ``0.0.0.0`` 才能被端口映射触达
    :param port: 监听端口；``0`` 表示由系统分配（实际端口见 ``server_address[1]``）
    :param factory: 行情客户端工厂；缺省为 :class:`ClientFactory`
    :raise BindError: 绑定失败（端口已被占用、地址不可用、无权限）
    """

    try:
        return _ThreadedServer((host, int(port)), factory or ClientFactory())
    except OSError as exc:
        raise BindError(f"无法监听 {host}:{port}: {exc}") from exc


def run_service(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, factory: Callable[[], Any] | None = None) -> int:
    """``python -m mootdx serve`` 的实现：绑定、打印监听地址、阻塞服务直到被中断。

    :raise BindError: 绑定失败（端口已被占用、地址不可用、无权限）
    """

    server = create_server(host, port, factory)

    bound_host, bound_port = server.server_address[:2]
    logger.info("listening on %s:%s (protocol=%s mootdx=%s)", bound_host, bound_port, PROTOCOL, __version__)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # noqa: PERF203 — Ctrl-C 属正常收尾
        logger.info("interrupted, shutting down")
    finally:
        server.server_close()
    return 0


def main(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
    """``serve`` 子命令的入口。"""

    _setup_logging()
    return run_service(host, port)
