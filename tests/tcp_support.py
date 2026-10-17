"""TCP 用例共用的脚手架：起服务、连上去、按行收发协议帧。

不是测试文件（不收集用例），只被 ``tests/test_serve.py`` 与 ``tests/test_worker_live.py``
共用，免得两份几乎一样的 Peer / Service 各自演化。
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from typing import Any

DEFAULT_TIMEOUT = 60.0


class Peer:
    """把连接包成「发一行收一行」的对端，并强制每一行都是合法协议帧。"""

    def __init__(self, address: tuple[str, int], timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self.socket = socket.create_connection(address, timeout=timeout)
        self.reader = self.socket.makefile("rb")

    def read_message(self, timeout: float | None = None, what: str = "message") -> dict:
        self.socket.settimeout(self.timeout if timeout is None else timeout)
        line = self.reader.readline()
        if not line:
            raise ConnectionError(f"等待 {what} 时连接被关闭")
        # 硬性纪律：每一行都必须是可解析的协议消息（UTF-8 无损）
        return json.loads(line.decode("utf-8", errors="strict"))

    def handshake(self, timeout: float | None = None) -> tuple[dict, dict]:
        return self.read_message(timeout, "hello"), self.read_message(timeout, "ready")

    def send(self, message: dict) -> None:
        self.send_line(json.dumps(message, ensure_ascii=False))

    def send_line(self, text: str) -> None:
        self.socket.sendall((text + "\n").encode("utf-8"))

    def close(self) -> None:
        self.reader.close()
        self.socket.close()


class RunningService:
    """在后台线程里跑一个监听在 127.0.0.1 随机端口上的服务（端口用 0 由系统分配）。"""

    def __init__(
        self,
        factory: Callable[[], Any],
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        from mootdx import worker

        self.timeout = timeout
        self.server = worker.create_server(host, port, factory=factory)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return host, port

    def connect(self) -> Peer:
        return Peer(self.address, timeout=self.timeout)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=max(self.timeout, 15))
