# 桥 worker

`mootdx worker` 是一个**常驻子进程**形态的命令：进程不做本地输出，而是以「行式 JSON」
在 stdin/stdout 上与调用它的宿主程序对话——宿主拉起它、握手、此后反复发请求收应答，
直到宿主关闭 stdin。

用途：让**非 Python 进程**（例如 .NET 服务）复用 mootdx 的取数与复权能力，而不必移植
通达信协议；宿主只需按下面的帧格式读写行。

```shell
python -m mootdx worker
```

## 帧格式（协议 v1）

每行一个完整 JSON 对象，UTF-8 编码。**stdout 只承载协议消息**，人类可读日志全部走 stderr。

启动后 worker 依次输出两行：

```json
{"hello": true, "protocol": 1, "python": "3.12.14", "mootdx": "0.12.0"}
{"ready": true, "server": ["117.34.114.16", 7709], "warmup_seconds": 3.4}
```

* `hello`：协议版本（`protocol`）与两侧版本；宿主应校验 `protocol == 1`，不符即判坏。
* `ready`：行情服务器连接结果。连不上时改为 `{"ready": false, "error": "..."}`
  ——**进程不退出**，此后的请求会得到 `ok:false`（便于宿主排查而非面对一个静默死掉的子进程）。

请求与应答各占一行，`id` 原样回传以便对应：

```json
{"id": 7, "method": "daily_kline", "params": {"symbol": "002029", "count": 60, "adjust": "qfq"}}
{"id": 7, "ok": true, "bars": [{"datetime": "2026-10-16 15:00", "open": 9.6, "high": 9.64, "low": 9.01, "close": 9.21, "vol": 589974, "amount": 550339648.0}]}
```

失败的请求与未知方法都得到同一形态的错误帧，进程继续服务：

```json
{"id": 7, "ok": false, "error": "unknown method: 'x'"}
```

关闭 stdin（EOF）即退出，退出码 `0`；宿主被强杀时不会留下孤儿进程。请求严格串行：
一次只处理一条，发一行收一行。

## 当前支持的方法

### `daily_kline`

| 参数 | 必填 | 说明 |
|---|---|---|
| `symbol` | 是 | 股票代码，如 `002029`（也接受 `SZ002029` / `SZ.002029` 写法） |
| `count` | 否 | 根数，默认 60 |
| `start` | 否 | 偏移，默认 0（0 = 从最新一根开始） |
| `adjust` | 否 | `qfq` / `hfq`；不给则为未复权原始价 |

应答 `bars` 为**升序**（旧 → 新），每根含 7 个字段：

| 字段 | 单位 / 口径 |
|---|---|
| `datetime` | 定格在 15:00（收盘时刻），取日期部分即可 |
| `open` / `high` / `low` / `close` | 元；`adjust` 给出时按对应口径复权 |
| `vol` | **手**（未复权）；需要股数请乘 100 |
| `amount` | 元 |

> 注意：此处 `vol` 是**手**且未复权，与 API 侧 `volume` 列（复权口径、含小数）语义不同，
> 不要混用。

## 宿主侧最小示例

```python
import json, subprocess, sys

worker = subprocess.Popen(
    [sys.executable, "-m", "mootdx", "worker"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)

def recv():
    return json.loads(worker.stdout.readline())

assert recv()["hello"] and recv()["ready"]

worker.stdin.write(b'{"id": 1, "method": "daily_kline", "params": {"symbol": "002029", "count": 60}}\n')
worker.stdin.flush()
print(recv()["bars"][-1])

worker.stdin.close()   # EOF → worker 干净退出
worker.wait()
```

Windows 宿主记得把三流的编码设为 UTF-8 无 BOM，并给子进程注入
`PYTHONUTF8=1` / `PYTHONUNBUFFERED=1`；worker 侧也已对读流强制 UTF-8，双保险。

## 排查

| 现象 | 原因与处理 |
|---|---|
| `hello` 后无 `ready` | 服务器探测中（本机实测约 3–5s）；若迟迟不来，检查出站能否连 TCP 7709 |
| `{"ready": false, ...}` | 连不上任何行情服务器：网络受限或服务器池整体不可达；日志（stderr）含具体报错 |
| 应答 `ok:false`，且此后请求都失败 | 就绪失败状态下的预期行为；需要恢复请由宿主重启进程 |
| 首次 `daily_kline` 慢（约 4s） | 首次复权需拉取除权除息数据并缓存 24h，之后为热路径（毫秒级） |
| 中文乱码 | 宿主侧三流未使用 UTF-8；见上节。worker 读到非法 UTF-8 字节会记日志并以退出码 2 结束（宿主按崩溃重建） |
