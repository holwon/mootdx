"""协议单元测试（协议 v1）：以假行情客户端驱动 ``worker.serve``。

覆盖：握手帧形状、请求-应答映射、字段裁剪、错误回收、两会话并发隔离、EOF 退出。
这些用例只认「读流 + 写流」，与承载无关（内存流驱动，不开 socket）。
TCP 传输层见 ``tests/test_serve.py``；真网络路径见 ``tests/test_worker_live.py``
（network 标记，默认套件排除）。
"""

from __future__ import annotations

import io
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

from mootdx import worker


def sample_frame() -> pd.DataFrame:
    """两根日K，附复权诊断列（volume/adj/preclose 不得出现在应答里）。"""

    return pd.DataFrame(
        {
            "open": [9.35, 9.6],
            "close": [9.6, 9.21],
            "high": [9.64, 9.7],
            "low": [9.33, 9.01],
            "vol": [589974, 612345],
            "amount": [550339648.0, 561234567.0],
            "year": [2026, 2026],
            "month": [10, 10],
            "day": [15, 16],
            "hour": [15, 15],
            "minute": [0, 0],
            "datetime": ["2026-10-15 15:00", "2026-10-16 15:00"],
            "volume": [609000.25, 631000.75],
            "adj": [0.98907, 1.0],
            "preclose": [None, 9.35],
        }
    )


def sample_ticks() -> pd.DataFrame:
    """两笔分笔，附历史接口之外的诊断列（``num`` / ``amount`` 不得出现在应答里）。"""

    return pd.DataFrame(
        {
            "time": ["14:59", "15:00"],
            "price": [1701.0, 1702.5],
            "vol": [12, 3],
            "buyorsell": [0, 2],
            "num": [5, 1],
            "amount": [2041200.0, 510750.0],
        }
    )


class FakeQuotes:
    """最小行情客户端替身：记录调用、返回预置 DataFrame。"""

    def __init__(
        self,
        frame: pd.DataFrame | None = None,
        server=("127.0.0.1", 7709),
        error=None,
        tick_frame: pd.DataFrame | None = None,
    ) -> None:
        self.frame = sample_frame() if frame is None else frame
        self.tick_frame = sample_ticks() if tick_frame is None else tick_frame
        self.server = server
        self.error = error
        self.calls: list[dict] = []
        self.closed = False

    def bars(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.frame

    def transactions(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.tick_frame

    def close(self) -> None:
        self.closed = True


def run_serve(lines: list[str], factory):
    """喂入请求行，返回（协议消息列表, 退出码）。任何非 JSON 输出行都会让测试失败。"""

    reader = io.StringIO("".join(f"{line}\n" for line in lines))
    writer = io.BytesIO()
    code = worker.serve(reader, writer, factory=factory)
    text = writer.getvalue().decode("utf-8")
    messages = [json.loads(line) for line in text.splitlines()]
    return messages, code


def request(message_id: int, method: str, params: dict | None = None) -> str:
    return json.dumps({"id": message_id, "method": method, "params": params or {}})


def test_hello_frame_declares_protocol_and_versions():
    messages, code = run_serve([], lambda: FakeQuotes())

    assert code == 0
    hello = messages[0]
    assert hello["hello"] is True
    assert hello["protocol"] == 1
    assert hello["python"].startswith("3.")
    assert hello["mootdx"] == worker.__version__


def test_ready_frame_reports_server_and_warmup():
    messages, _ = run_serve([], lambda: FakeQuotes(server=("117.34.114.16", 7709)))

    ready = messages[1]
    assert ready["ready"] is True
    assert ready["server"] == ["117.34.114.16", 7709]
    assert isinstance(ready["warmup_seconds"], float)
    assert ready["warmup_seconds"] >= 0


def test_ready_frame_without_server_is_null():
    messages, _ = run_serve([], lambda: FakeQuotes(server=None))

    assert messages[1]["ready"] is True
    assert messages[1]["server"] is None


def test_failed_ready_reports_error_and_requests_answer_not_ready():
    def failing_factory():
        raise ConnectionError("无法连接任何可用的标准行情服务器")

    messages, code = run_serve([request(1, "daily_kline", {"symbol": "002029"})], failing_factory)

    assert code == 0
    assert messages[1]["ready"] is False
    assert "无法连接任何可用的标准行情服务器" in messages[1]["error"]
    assert messages[2]["id"] == 1
    assert messages[2]["ok"] is False
    assert "not ready" in messages[2]["error"]


def test_daily_kline_maps_request_and_strips_diagnostic_columns():
    client = FakeQuotes()

    messages, _ = run_serve(
        [request(7, "daily_kline", {"symbol": "002029", "count": 2, "start": 0, "adjust": "qfq"})],
        lambda: client,
    )

    response = messages[2]
    assert response["id"] == 7
    assert response["ok"] is True
    assert response["bars"] == [
        {
            "datetime": "2026-10-15 15:00",
            "open": 9.35,
            "high": 9.64,
            "low": 9.33,
            "close": 9.6,
            "vol": 589974,
            "amount": 550339648.0,
        },
        {
            "datetime": "2026-10-16 15:00",
            "open": 9.6,
            "high": 9.7,
            "low": 9.01,
            "close": 9.21,
            "vol": 612345,
            "amount": 561234567.0,
        },
    ]
    assert client.calls == [{"symbol": "002029", "frequency": 9, "start": 0, "offset": 2, "adjust": "qfq"}]


def test_daily_kline_defaults_count_start_and_leaves_adjust_unset():
    client = FakeQuotes()

    messages, _ = run_serve([request(1, "daily_kline", {"symbol": "SZ002029"})], lambda: client)

    assert messages[2]["ok"] is True
    assert client.calls == [{"symbol": "SZ002029", "frequency": 9, "start": 0, "offset": 60}]


def test_daily_kline_requires_symbol():
    messages, _ = run_serve([request(1, "daily_kline", {})], lambda: FakeQuotes())

    assert messages[2]["ok"] is False
    assert "symbol" in messages[2]["error"]


def test_daily_kline_rejects_unknown_adjust():
    messages, _ = run_serve([request(1, "daily_kline", {"symbol": "002029", "adjust": "abc"})], lambda: FakeQuotes())

    assert messages[2]["ok"] is False
    assert "adjust" in messages[2]["error"]


def test_empty_frame_answers_no_bars():
    messages, _ = run_serve(
        [request(1, "daily_kline", {"symbol": "002029"})],
        lambda: FakeQuotes(frame=pd.DataFrame()),
    )

    assert messages[2] == {"id": 1, "ok": True, "bars": []}


def test_frame_missing_fields_is_an_error():
    client = FakeQuotes(frame=sample_frame().drop(columns=["amount"]))

    messages, _ = run_serve([request(1, "daily_kline", {"symbol": "002029"})], lambda: client)

    assert messages[2]["ok"] is False
    assert "缺少字段" in messages[2]["error"]
    assert "amount" in messages[2]["error"]


def test_client_exception_becomes_failed_response():
    client = FakeQuotes(error=RuntimeError("服务器连接已断开"))

    messages, code = run_serve([request(1, "daily_kline", {"symbol": "002029"})], lambda: client)

    assert code == 0
    assert messages[2]["ok"] is False
    assert "服务器连接已断开" in messages[2]["error"]


def test_unknown_method_keeps_session_alive():
    messages, code = run_serve(
        [request(1, "no_such_method"), request(2, "daily_kline", {"symbol": "002029"})],
        lambda: FakeQuotes(),
    )

    assert code == 0
    assert messages[2] == {"id": 1, "ok": False, "error": "unknown method: 'no_such_method'"}
    assert messages[3]["id"] == 2
    assert messages[3]["ok"] is True


def test_malformed_json_answers_error_and_blank_lines_are_ignored():
    messages, code = run_serve(
        ["not-json", "", request(2, "daily_kline", {"symbol": "002029"})],
        lambda: FakeQuotes(),
    )

    assert code == 0
    assert len(messages) == 4  # hello、ready、一次错误应答、一次成功应答
    assert messages[2]["id"] is None
    assert messages[2]["ok"] is False
    assert messages[3]["id"] == 2


def test_stdin_eof_exits_zero_and_closes_client():
    client = FakeQuotes()

    messages, code = run_serve([], lambda: client)

    assert code == 0
    assert len(messages) == 2
    assert client.closed is True


class InvalidUtf8Reader:
    """模拟读到非法 UTF-8 字节的严格解码读流。"""

    def readline(self):
        raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")


def test_invalid_utf8_on_stdin_exits_with_code_2():
    writer = io.BytesIO()
    reader = InvalidUtf8Reader()

    code = worker.serve(reader, writer, factory=lambda: FakeQuotes())

    messages = [json.loads(line) for line in writer.getvalue().decode("utf-8").splitlines()]
    assert code == 2
    assert [message.get("hello") for message in messages[:1]] == [True]  # 握手已发生，失败在请求阶段


def test_two_concurrent_sessions_do_not_leak_into_each_other():
    """两条独立会话并发驱动同一个 ``serve``：各自的输出流里只出现自己的帧。

    隔离性是协议层自身的性质——承载层（每连接一线程）只是把它并行化，不该成为
    隔离的前提。两条会话用不同标的，断言谁也没读到对方的请求结果。

    交错是确定的：两个 factory 在栅栏处会合后才各自返回，因此两边必然同时处于
    ``serve`` 内部（握手已发、请求未处理），后续不再有任何时序假设。
    """

    meeting = threading.Barrier(2, timeout=10)

    def run(symbol: str, close: float):
        def factory():
            meeting.wait()
            return FakeQuotes(frame=sample_frame().assign(close=[close, close]))

        reader = io.StringIO(request(1, "daily_kline", {"symbol": symbol}) + "\n")
        writer = io.BytesIO()
        code = worker.serve(reader, writer, factory=factory)
        return code, [json.loads(line) for line in writer.getvalue().decode("utf-8").splitlines()]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, "AAA", 1.0)
        second = pool.submit(run, "BBB", 2.0)
        results = {"AAA": first.result(timeout=20), "BBB": second.result(timeout=20)}

    for symbol, expected_close in (("AAA", 1.0), ("BBB", 2.0)):
        code, messages = results[symbol]
        assert code == 0
        assert messages[0]["hello"] is True
        assert messages[1]["ready"] is True
        assert messages[2]["id"] == 1
        assert {bar["close"] for bar in messages[2]["bars"]} == {expected_close}
        assert len(messages) == 3, f"{symbol} 会话多出了不属于它的帧"


def test_logs_stay_off_the_protocol_stream(caplog):
    with caplog.at_level(logging.INFO, logger="mootdx"):
        messages, _ = run_serve([], lambda: FakeQuotes(server=("117.34.114.16", 7709)))

    assert len(messages) == 2
    assert any("117.34.114.16" in record.getMessage() for record in caplog.records)


# --- ticks：历史分笔方法 ---------------------------------------------------------


def test_shanghai_today_uses_wall_clock_of_shanghai_not_utc():
    """UTC 的 17:00 已是上海次日 01:00——归属日必须按上海墙钟算。"""

    utc_evening = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)

    assert worker.shanghai_today(utc_evening) == "20261021"


def test_shanghai_today_before_local_midnight_stays_on_same_day():
    utc_afternoon = datetime(2026, 10, 20, 15, 59, tzinfo=timezone.utc)  # 上海 23:59

    assert worker.shanghai_today(utc_afternoon) == "20261020"


def test_shanghai_today_accepts_naive_datetime_as_utc():
    naive = datetime(2026, 10, 20, 17, 0)

    assert worker.shanghai_today(naive) == "20261021"


def test_ticks_maps_request_and_strips_extra_columns():
    """应答只含四列且顺序固定；诊断列（``num`` / ``amount``）不外发。"""

    client = FakeQuotes()

    messages, _ = run_serve([request(3, "ticks", {"symbol": "600519", "date": "20261020"})], lambda: client)

    response = messages[2]
    assert response["id"] == 3
    assert response["ok"] is True
    assert response["ticks"] == [
        {"time": "14:59", "price": 1701.0, "vol": 12, "buyorsell": 0},
        {"time": "15:00", "price": 1702.5, "vol": 3, "buyorsell": 2},
    ]
    assert [list(tick) for tick in response["ticks"]] == [
        ["time", "price", "vol", "buyorsell"],
        ["time", "price", "vol", "buyorsell"],
    ]
    assert client.calls == [{"symbol": "600519", "start": 0, "offset": 2000, "date": "20261020"}]


def test_ticks_defaults_start_and_count():
    client = FakeQuotes()

    messages, _ = run_serve([request(1, "ticks", {"symbol": "600519", "date": "20261020"})], lambda: client)

    assert messages[2]["ok"] is True
    assert client.calls == [{"symbol": "600519", "start": 0, "offset": 2000, "date": "20261020"}]


def test_ticks_passes_start_and_count_through():
    client = FakeQuotes()

    run_serve(
        [request(1, "ticks", {"symbol": "600519", "date": "20261020", "start": 2000, "count": 800})],
        lambda: client,
    )

    assert client.calls == [{"symbol": "600519", "start": 2000, "offset": 800, "date": "20261020"}]


def test_ticks_normalizes_dashed_date_before_calling_upstream():
    """``StdQuotes.transactions`` 内部 ``int(date)``，故短横线必须在 worker 内剥掉。"""

    client = FakeQuotes()

    messages, _ = run_serve([request(1, "ticks", {"symbol": "600519", "date": "2026-10-20"})], lambda: client)

    assert messages[2]["ok"] is True
    assert client.calls[0]["date"] == "20261020"


def test_ticks_defaults_date_to_shanghai_today(monkeypatch):
    monkeypatch.setattr(worker, "shanghai_today", lambda: "20261020")
    client = FakeQuotes()

    messages, _ = run_serve([request(1, "ticks", {"symbol": "600519"})], lambda: client)

    assert messages[2]["ok"] is True
    assert client.calls[0]["date"] == "20261020"


def test_ticks_requires_symbol():
    messages, _ = run_serve([request(1, "ticks", {"date": "20261020"})], lambda: FakeQuotes())

    assert messages[2]["ok"] is False
    assert "symbol" in messages[2]["error"]


def test_ticks_rejects_blank_symbol():
    messages, _ = run_serve([request(1, "ticks", {"symbol": "   "})], lambda: FakeQuotes())

    assert messages[2]["ok"] is False
    assert "symbol" in messages[2]["error"]


def test_ticks_rejects_negative_start():
    messages, _ = run_serve(
        [request(1, "ticks", {"symbol": "600519", "start": -1})],
        lambda: FakeQuotes(),
    )

    assert messages[2]["ok"] is False
    assert "start" in messages[2]["error"]


def test_ticks_rejects_non_positive_count():
    messages, _ = run_serve(
        [request(1, "ticks", {"symbol": "600519", "count": 0})],
        lambda: FakeQuotes(),
    )

    assert messages[2]["ok"] is False
    assert "count" in messages[2]["error"]


def test_ticks_rejects_malformed_date_and_names_both_accepted_forms():
    messages, _ = run_serve(
        [request(1, "ticks", {"symbol": "600519", "date": "2026/10/20"})],
        lambda: FakeQuotes(),
    )

    error = messages[2]["error"]
    assert messages[2]["ok"] is False
    assert "yyyyMMdd" in error
    assert "yyyy-MM-dd" in error


def test_ticks_rejects_non_existent_date():
    """``20260231`` 形态合法但日期不存在，不能透传给上游。"""

    client = FakeQuotes()

    messages, _ = run_serve([request(1, "ticks", {"symbol": "600519", "date": "20260231"})], lambda: client)

    assert messages[2]["ok"] is False
    assert "date" in messages[2]["error"]
    assert client.calls == []


def test_ticks_empty_frame_answers_no_ticks():
    messages, _ = run_serve(
        [request(1, "ticks", {"symbol": "600519", "date": "20261020"})],
        lambda: FakeQuotes(tick_frame=pd.DataFrame()),
    )

    assert messages[2] == {"id": 1, "ok": True, "ticks": []}


def test_ticks_missing_field_is_an_error():
    client = FakeQuotes(tick_frame=sample_ticks().drop(columns=["buyorsell"]))

    messages, _ = run_serve([request(1, "ticks", {"symbol": "600519", "date": "20261020"})], lambda: client)

    assert messages[2]["ok"] is False
    assert "缺少字段" in messages[2]["error"]
    assert "buyorsell" in messages[2]["error"]


def test_ticks_client_exception_becomes_failed_response():
    client = FakeQuotes(error=RuntimeError("服务器连接已断开"))

    messages, _ = run_serve([request(1, "ticks", {"symbol": "600519", "date": "20261020"})], lambda: client)

    assert messages[2]["ok"] is False
    assert "服务器连接已断开" in messages[2]["error"]


def test_ticks_and_daily_kline_share_one_session_and_keep_ids():
    client = FakeQuotes()

    messages, code = run_serve(
        [
            request(1, "ticks", {"symbol": "600519", "date": "20261020"}),
            request(2, "daily_kline", {"symbol": "002029"}),
        ],
        lambda: client,
    )

    assert code == 0
    assert messages[2]["id"] == 1
    assert "ticks" in messages[2]
    assert messages[3]["id"] == 2
    assert "bars" in messages[3]


def test_cli_registers_serve_subcommand_and_drops_worker():
    from click.testing import CliRunner

    from mootdx.__main__ import entry

    result = CliRunner().invoke(entry, ["--help"])

    assert result.exit_code == 0, result.output
    assert "serve" in result.output
    assert "worker" not in result.output
