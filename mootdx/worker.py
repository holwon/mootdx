"""mootdx 桥 worker：行式 JSON 协议 v1（stdin/stdout）。

外部程序（如常驻宿主）经 ``python -m mootdx worker`` 拉起本模块，以「一行一个完整
JSON 对象、UTF-8 编码」的方式收发消息：

* 启动即输出一行 ``hello``（协议版本 + python / mootdx 版本）；
* 随后尝试连接一台可用行情服务器：成功输出 ``ready``（server / warmup_seconds），
  失败输出 ``{"ready": false, "error": ...}`` 并保持进程存活；
* 请求形如 ``{"id": N, "method": "daily_kline", "params": {...}}``，应答回同一 ``id``：
  ``{"id": N, "ok": true, "bars": [...]}`` 或 ``{"id": N, "ok": false, "error": "..."}``；
* stdin 关闭（EOF）即退出，退出码 0——父进程被强杀时不留孤儿进程的前提；
  stdin 出现非法 UTF-8 字节属宿主协议违规，记日志并以退出码 2 结束（宿主按崩溃处理）。

纪律：stdout 只承载协议消息（直接写 buffer，绕开文本层缓冲歧义）；日志一律走 stderr
（经 ``mootdx.logger``，由调用方接管 stderr 转发）；请求严格串行（单飞行、写入等回读）。

``method`` 命名空间为后续数据面预留，当前仅实现 ``daily_kline``。
"""

from __future__ import annotations

import io
import json
import logging
import platform
import sys
import time
from collections.abc import Callable
from typing import Any, BinaryIO, TextIO

from mootdx import __version__
from mootdx.logger import logger

#: 协议版本；帧格式变更时递增，调用方按此判坏。
PROTOCOL = 1

#: 应答中保留的行情列。``volume`` / ``adj`` / ``preclose`` 是复权诊断列，不外发。
BAR_FIELDS = ("datetime", "open", "high", "low", "close", "vol", "amount")

#: ``daily_kline`` 的缺省根数与频率（9 = 日K）。
DEFAULT_COUNT = 60
DAILY_FREQUENCY = 9
ALLOWED_ADJUSTS = ("qfq", "hfq")


def send(writer: BinaryIO, message: dict[str, Any]) -> None:
    """写一行协议消息到 stdout 并立即刷出（绕过文本层，直写 buffer）。"""

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
    """协议主循环：握手 → 串行收发 → EOF 退出。

    :param reader: 请求输入流（生产为包装成 UTF-8 的 stdin）
    :param writer: 协议输出流（生产为 stdout 的二进制 buffer）
    :param factory: 行情客户端工厂；抛异常即表现为 ``ready:false``（进程保持存活）
    :return: 进程退出码——正常路径（含 EOF）为 0，stdin 出现非法 UTF-8 时为 2
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
            # 非法 UTF-8 = 宿主侧协议违规；响亮失败（非零码），由宿主按崩溃处理并重建
            logger.error("stdin 不是有效 UTF-8，退出: %s", exc)
            failure_code = 2
            break
        if not line:  # stdin 关闭 → 退出
            logger.info("stdin closed, exiting")
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
            logger.debug("request id=%s ok=%s in %.3fs", request_id, result.get("ok"), time.perf_counter() - started)
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
    """日志一律走 stderr（stdout 只承载协议消息）。"""

    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


def _setup_streams() -> None:
    """读流强制 UTF-8，严格模式（Windows 默认 cp936 会乱码或解码失败）。

    严格（``errors="strict"``，同原型底稿）：宿主契约是 UTF-8 无 BOM 写行，出现非法
    字节属于协议违规，宁可响亮失败（日志 + 非零退出，宿主按崩溃重建）也不静默替换。
    """

    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="strict")


def main() -> int:
    """``python -m mootdx worker`` 的入口。"""

    _setup_logging()
    _setup_streams()

    def factory() -> Any:
        from mootdx.quotes import Quotes

        return Quotes.factory(market="std", timeout=10)

    return serve(sys.stdin, sys.stdout.buffer, factory)
