"""桥协议单元测试（协议 v1）：以假行情客户端驱动 ``worker.serve``。

覆盖：握手帧形状、请求-应答映射、字段裁剪、错误回收、stdout 纪律、EOF 退出。
真网络路径见 ``tests/test_worker_live.py``（network 标记，默认套件排除）。
"""

from __future__ import annotations

import io
import json
import logging

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


class FakeQuotes:
    """最小行情客户端替身：记录调用、返回预置 DataFrame。"""

    def __init__(self, frame: pd.DataFrame | None = None, server=("127.0.0.1", 7709), error=None) -> None:
        self.frame = sample_frame() if frame is None else frame
        self.server = server
        self.error = error
        self.calls: list[dict] = []
        self.closed = False

    def bars(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.frame

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


def test_logs_stay_off_the_protocol_stream(caplog):
    with caplog.at_level(logging.INFO, logger="mootdx"):
        messages, _ = run_serve([], lambda: FakeQuotes(server=("117.34.114.16", 7709)))

    assert len(messages) == 2
    assert any("117.34.114.16" in record.getMessage() for record in caplog.records)


def test_cli_registers_serve_subcommand_and_drops_worker():
    from click.testing import CliRunner

    from mootdx.__main__ import entry

    result = CliRunner().invoke(entry, ["--help"])

    assert result.exit_code == 0, result.output
    assert "serve" in result.output
    assert "worker" not in result.output
