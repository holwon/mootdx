# 数据服务（`serve`）

`mootdx serve` 把通达信取数能力开放成一个**常驻 TCP 服务**：任何进程（本机或另一台
机器、任何语言）连上 `host:port`，按「行式 JSON」发请求、按 `id` 收应答。

用途：让**非 Python 进程**（例如 .NET 服务、Node 服务）复用 mootdx 的取数与复权能力，
而不必移植通达信协议、也不必在宿主侧管理一个 Python 子进程。

最省事的用法是官方 Docker 镜像（见下节）；也可以直接跑命令：

```shell
python -m mootdx serve --host 0.0.0.0 --port 7701
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `0.0.0.0` | 监听地址。容器内必须是 `0.0.0.0`，否则端口映射不可达 |
| `--port` | `7701` | 监听端口；被占用时启动即报错，不会静默失败 |

## 用 Docker 跑

```shell
# 只把端口暴露给本机回环，避免整个局域网都能连
docker run -d --name mootdx \
  -p 127.0.0.1:7701:7701 \
  --restart unless-stopped \
  ghcr.io/holwon/mootdx:latest
```

* **`-p 127.0.0.1:7701:7701` 带前缀是刻意的**：服务本身不做鉴权，数据虽是公开行情，
  但没有理由把 7701 广播到局域网。明知风险要对外提供时再改成 `-p 7701:7701`。
* **`--restart unless-stopped`**：机器重启或进程崩溃后服务自己回来，不必宿主侧重试。
* **缓存卷**：复权数据（`xdxr`）与服务器测速结果缓存在容器内 `/home/mootdx/.mootdx`
  （镜像以非 root 运行，家目录就是这里）。挂命名卷可在**重建容器**后复用，冷启动首次
  请求约 10s（含探测与除权数据拉取）、热路径约 0.06s：

```shell
docker run -d --name mootdx \
  -p 127.0.0.1:7701:7701 \
  -v mootdx-cache:/home/mootdx/.mootdx \
  --restart unless-stopped \
  ghcr.io/holwon/mootdx:latest
```

不挂卷也能工作，只是 `docker run --rm` 或删容器时缓存丢失。

**网络要求**：容器需要**出站 TCP 7709** 才能连通达信行情服务器；除映射端口外
**不需要任何入站放行**（容器不会主动连回宿主）。

镜像同时提供 `linux/amd64` 与 `linux/arm64`，Apple Silicon 与 x86 服务器都能直接拉。

## 帧格式（协议 v1）

每行一个完整 JSON 对象，UTF-8 编码。**日志全部走容器/进程的 stderr**，不占用协议流。

连接建立后服务端**主动**先发两行：

```json
{"hello": true, "protocol": 1, "python": "3.12.14", "mootdx": "0.12.0"}
{"ready": true, "server": ["117.34.114.16", 7709], "warmup_seconds": 3.4}
```

* `hello`：协议版本（`protocol`）与两侧版本；客户端应校验 `protocol == 1`，不符即判坏。
  它与 `ready` 分开，是为了让客户端能区分「TCP 通了」与「真的能取数」——后者要探测
  通达信服务器，约 3.4 秒。
* `ready`：行情服务器连接结果。连不上时改为 `{"ready": false, "error": "..."}`，
  **服务不退出、连接也不关闭**；该连接此后的请求会得到 `ok:false`（错误文本说明
  not ready）。网络恢复后**重连即可**，不需要重启容器。

请求与应答各占一行，`id` 原样回传以便对应：

```json
{"id": 7, "method": "daily_kline", "params": {"symbol": "002029", "count": 60, "adjust": "qfq"}}
{"id": 7, "ok": true, "bars": [{"datetime": "2026-10-16 15:00", "open": 9.6, "high": 9.64, "low": 9.01, "close": 9.21, "vol": 589974, "amount": 550339648.0}]}
```

失败的请求与未知方法都得到同一形态的错误帧，会话继续可用：

```json
{"id": 7, "ok": false, "error": "unknown method: 'x'"}
```

请求严格串行：一次只处理一条，发一行收一行。

### 会话与并发

* **一个连接 = 一个会话**。连接关闭即会话结束，服务端释放该会话的行情客户端。
* **多个客户端可同时连接**，各自独立握手、独立请求队列；一个客户端的慢速或挂起
  请求**不影响**另一个客户端。
* **服务端不主动断开空闲连接**。要超时请在客户端实现——服务端不猜你的语义。
* 服务端**不主动退出**：只有当进程被杀（或容器被停）时才会结束。

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

`method` 命名空间为后续数据面（quote / ticks / 分钟 K / 财务 / 除权）预留。

## 客户端最小示例

```python
import json, socket

with socket.create_connection(("127.0.0.1", 7701), timeout=30) as sock:
    stream = sock.makefile("rwb")

    def recv():
        return json.loads(stream.readline())

    assert recv()["hello"] and recv()["ready"]

    stream.write(b'{"id": 1, "method": "daily_kline", "params": {"symbol": "002029", "count": 60}}\n')
    stream.flush()
    print(recv()["bars"][-1])

    stream.close()   # 连接关闭 = 会话结束
```

## 从 `mootdx worker` 迁移

`worker`（stdio 形态）已在本次变更中**删除**，没有兼容开关。若是从旧形态迁过来：

| 旧（stdio） | 新（TCP） |
|---|---|
| 宿主 `spawn` `python -m mootdx worker` | 客户端连 `host:port`（通常是一个常驻容器） |
| 往 stdin 写一行，从 stdout 读一行 | 往 socket 写一行，从 socket 读一行 |
| 关闭 stdin 表示会话结束 | 关闭连接表示会话结束 |
| 宿主负责握手判坏、退避重建、杀进程树 | 交给 `docker run -d` + `--restart` |
| 宿主需注入 `PYTHONUTF8=1` 等环境 | 不需要；编码由协议固定为 UTF-8 |

**协议帧形状一字未改**，`protocol` 仍是 `1`：只改承载，不改帧。已有的解析代码不必动。

## 排查

| 现象 | 原因与处理 |
|---|---|
| 启动即报 `无法监听 0.0.0.0:7701` | 端口已被占用（消息里带端口号）；换端口或先释放 |
| 连上了但迟迟没有 `hello` | 连的不是本服务（检查映射端口），或中间有代理缓冲 |
| `hello` 后无 `ready` | 服务器探测中（实测约 3–5s）；若迟迟不来，检查**出站** TCP 7709 |
| `{"ready": false, ...}` | 连不上任何行情服务器：网络受限或服务器池整体不可达；日志含具体报错 |
| 应答 `ok:false`，且此后都失败 | 该会话处于未就绪状态；**重连**即可（不必重启容器） |
| 首次 `daily_kline` 慢（约 4s） | 首次复权需拉取除权除息数据并缓存 24h，之后为热路径（毫秒级） |
| 中文乱码 | 客户端未按 UTF-8 编解码；服务端读到非法 UTF-8 字节会结束该会话（不影响其他连接） |
| `docker inspect` 显示 unhealthy | 健康检查只探本服务的监听端口与 `hello` 帧，与上游无关；unhealthy 说明服务本身没在听 |

查看日志：`docker logs -f mootdx`。能看到监听地址、每个连接的来源与断开、所选服务器
IP 与探测耗时；`-v` 级别的请求耗时不在容器默认日志级别内。
