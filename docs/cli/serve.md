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

### 容器时区

镜像里装了 `tzdata` 并设了 `TZ=Asia/Shanghai`。这是**防御性**修正，**不是**任何协议
方法的前提——`ticks` 走的是没有交易时段闸的历史接口，`date` 的缺省值也由服务端用
固定 `+08:00` 偏移算出，两者都不查时区数据库。留着它的理由是：Alpine（musl）自带
的时区数据不全，不设 `TZ` 时本地时间会静默退回 UTC，于是日志时间戳与上海交易日历差
8 小时，排查时容易把时区差当成数据错。

这条修正**可以独立回退**：删掉 Dockerfile 里的 `TZ` 与 `apk add tzdata` 不影响任何
方法的行为。

镜像同时提供 `linux/amd64` 与 `linux/arm64`，Apple Silicon 与 x86 服务器都能直接拉。

### 镜像什么时候发布

| 触发 | 行为 |
|---|---|
| **任意分支** `push` | 构建 `linux/amd64`、**不推送**（用于尽早发现 Dockerfile 腐化） |
| **Pull Request** | 同上，只构建不推送 |
| **push tag `v*`** | 构建 `linux/amd64` + `linux/arm64` 并推送到 ghcr（版本号 + `latest`） |

**只在功能分支上 push 是不会产生镜像的**——需要打 tag 才会发布：

```shell
git tag v0.12.0
git push origin v0.12.0
```

发布后在 `ghcr.io/holwon/mootdx` 可见 `0.12.0` 与 `latest` 两个 tag；确认多架构清单：

```shell
docker buildx imagetools inspect ghcr.io/holwon/mootdx:latest
```

## 用 Docker Compose 跑

仓库根目录带 `docker-compose.yml`。适合「要和自己的服务一起跑」或「想把端口、
卷、重启策略写进文件而不是记在命令行里」。

```shell
docker compose up -d          # 用已发布镜像
docker compose up -d --build  # 从本仓源码构建后起（改了代码时用）
docker compose logs -f        # 看日志
docker compose down           # 停（缓存卷保留）
```

**改端口**：不用改 compose 文件，在仓库根放一个 `.env`：

```ini
MOOTDX_PORT=17711
```

宿主映射端口、容器内监听端口、健康检查探的端口**三者会一起跟着变**——这一点是刻意的：
镜像的健康检查读环境变量 `MOOTDX_PORT`，如果只改宿主映射而容器内仍监听 7701，
健康检查就会指向一个没人监听的端口，容器会被误判为 unhealthy。

**缓存**：`mootdx-cache` 是固定名字的命名卷（`name: mootdx-cache`），所以
`docker compose down` 再 `up` 之后缓存还在；`docker compose down -v` 才会删。
多个 compose 项目共用同一份缓存也是靠这个名字。

### 同一个 Compose 里挂一个使用方

把自己的服务和 mootdx 写在同一个 compose 文件里时，**使用方连服务名即可**——
比走宿主端口更省事，也不受宿主端口映射影响：

```yaml
services:
  mootdx:
    image: ghcr.io/holwon/mootdx:latest
    ports: ["127.0.0.1:7701:7701"]
    volumes: ["mootdx-cache:/home/mootdx/.mootdx"]

  app:
    image: your-app:latest
    depends_on:
      mootdx:
        condition: service_healthy   # 等 hello 帧能读到再启动
    environment:
      TDX_ENDPOINT: mootdx:7701      # 服务名就是主机名
```

`depends_on ... service_healthy` 只等「服务本身在听并能给出 `hello`」，**不等通达信
服务器可达**——健康检查刻意不依赖上游（见下）。所以使用方仍要容忍
`ready:false` 或连接失败，按自己的策略重连。

## 给别的程序挂载

服务是「一个 TCP 端点 + 行式 JSON」，接入方式取决于使用方跑在哪里。三种典型形态：

### ① 同一台机器上的其他进程

用 `127.0.0.1:7701`，走宿主端口映射。这是最简单也最安全的一种——端口只绑回环，
局域网里的其他机器连不上。

```python
import json, socket

sock = socket.create_connection(("127.0.0.1", 7701), timeout=30)
stream = sock.makefile("rwb")
```

### ② 同一 Compose / 同一 Docker 网络里的其他容器

用**服务名 + 容器端口**（`mootdx:7701`），不需要宿主映射，也不用写 IP。
前提是使用方容器加入了同一个 Docker 网络（同一 compose 文件里的服务默认就是）。

```shell
# 临时验证：借用 mootdx 所在的网络起一个 python 容器
docker run --rm --network <compose项目名>_default python:3.12-slim \
  python -c "import socket; socket.create_connection(('mootdx', 7701), timeout=30)"
```

**不要**把使用方 `--network host` 又指望 `mootdx:7701` 能解析：容器名解析只在
用户自定义网络上生效，`host` 网络里没有 Docker 的 DNS。

### ③ 局域网 / 另一台机器

把端口映射从 `127.0.0.1:7701:7701` 改成 `7701:7701`（或 `0.0.0.0:7701:7701`），
使用方连「那台机器的 IP:7701」。**先想清楚这件事**：服务不做鉴权也不做 TLS，
把它暴露出去等于把「谁能连上就能取行情」开放给整张网。若必须跨机，建议只在
受信内网、并用防火墙限制来源 IP：

```shell
docker run -d --name mootdx -p 7701:7701 -v mootdx-cache:/home/mootdx/.mootdx \
  --restart unless-stopped ghcr.io/holwon/mootdx:latest
```

### 使用方要自己实现的四件事

服务端**只做**「收一行、回一行」，其余都在使用方：

| 职责 | 说明 |
|---|---|
| **超时** | 服务端不主动断开空闲连接，也不会替你中断慢请求。读写都要设超时 |
| **重连** | 连接断开就重连。容器由 `--restart` 负责活过来，客户端只管重新连 |
| **`ready` 判读** | `ready:false` 时连接仍可用但请求会全部 `ok:false`；此时应重连（或降级到别的数据源），而不是重试同一条请求 |
| **`protocol` 校验** | `hello.protocol != 1` 就认为对端不兼容，显式失败，不要静默降级 |

### 各语言的接法

服务只要求「能开 TCP socket、能按 `\n` 切行」，没有 SDK。几个常见形态：

```python
# Python
import json, socket
sock = socket.create_connection(("127.0.0.1", 7701), timeout=30)
stream = sock.makefile("rwb")
hello, ready = json.loads(stream.readline()), json.loads(stream.readline())
```

```javascript
// Node.js
import net from "node:net";
const sock = net.createConnection({ host: "127.0.0.1", port: 7701 });
let buffer = "";
sock.on("data", (chunk) => {
  buffer += chunk.toString("utf8");
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const frame = JSON.parse(buffer.slice(0, index));
    buffer = buffer.slice(index + 1);
    // frame.hello / frame.ready / frame.id / frame.ok
  }
});
```

```csharp
// C# —— StreamReader/Writer 直接按行读写，UTF-8 无 BOM
using var client = new TcpClient();
await client.ConnectAsync("127.0.0.1", 7701);
using var stream = client.GetStream();
using var reader = new StreamReader(stream, new UTF8Encoding(false));
using var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
var hello = JsonDocument.Parse(await reader.ReadLineAsync()!);
```

**三个共同的坑**：

1. **必须按 `\n` 切行**，不能假设「一次 read = 一条消息」——TCP 是字节流，一次
   `read` 可能拿到半行或两行半。上面 Node 例子里那个 `buffer` 就是干这个的。
2. **UTF-8 无 BOM**。带 BOM 会让第一行解析失败（`\ufeff{` 不是合法 JSON 开头）。
3. **一条连接内串行**：发一条、收一条，不要在同一连接上并发发请求。需要并发就开
   多条连接——服务端本就按连接隔离，多开是受支持的用法。

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

失败与未知方法都得到同一形态的错误帧，会话继续可用：

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

### `ticks`

取某一日的**全部分笔**（成交明细）。走历史分笔接口，**不受交易时段限制**——收盘后
复盘随时可取，这是它与「当日分笔」接口的关键差别。

| 参数 | 必填 | 说明 |
|---|---|---|
| `symbol` | 是 | 股票代码，如 `600519`（也接受 `SH600519` / `SH.600519` 写法） |
| `date` | 否 | 归属日，接受 `20261020` 或 `2026-10-20`；省略时按**上海墙钟**取当天 |
| `start` | 否 | 偏移，默认 0。**自最近一笔往回数**（见下） |
| `count` | 否 | 条数，默认 2000。服务器单次上限也就是 2000，更大的值被静默截断 |

应答 `ticks` 为**每笔一条**，字段固定四个：

| 字段 | 单位 / 口径 |
|---|---|
| `time` | `HH:MM`（3 秒快照的时刻，不含秒） |
| `price` | 元 |
| `vol` | **手**；需要股数请乘 100 |
| `buyorsell` | 方向码，原样透传上游。实测：`8` = 集合竞价虚拟撮合、`5` = 盘后固定价格交易、`2` = 09:25 的真实竞价成交 |

请求与应答样例（可直接抄）：

```json
{"id": 8, "method": "ticks", "params": {"symbol": "600519", "date": "20261020", "start": 0, "count": 2000}}
```

```json
{"id": 8, "ok": true, "ticks": [{"time": "14:59", "price": 1701.0, "vol": 12, "buyorsell": 0}, {"time": "15:00", "price": 1702.5, "vol": 3, "buyorsell": 2}]}
```

#### 两个必须知道的坑

**① `start` 是自最近一笔往回数的偏移，不是自最老。** `start=0` 给出的是**收盘段**，
不是开盘段。要拼出正序的全天序列，必须逐页取回后**整体反转**，而不是把各页按顺序追加。

**② 高位的 `start` 会回绕成第 0 页，且不报任何错。** 实测 `start ≥ 32768` 时服务器
直接返回第 0 页的 2000 行**完整复制**。因此分页循环的**唯一安全终止条件是「遇空即停」**：
不能按固定页数翻，否则会拿到一段看起来有数据、实际是同一段重复 N 遍的畸形序列。

#### 本方法不做的事

`ticks` **不做清洗、不去重、不换算单位**：

* 集合竞价虚拟撮合（`vol == 0`）与盘后固定价格交易（`time > 15:00`）**原样返回**；
* `vol` 是**手**，不换算成股；
* 方向码不映射成 `Buy` / `Sell` / `Neutral`。

剔除规则与单位归一由使用方在自己的适配层完成——服务端与使用方各清洗一遍必然分叉。

> 空结果（非交易日、停牌、日期越界）返回 `{"ok": true, "ticks": []}`，不是 `ok:false`。
> 这样才能把「没有数据」与「取数失败」分开。

`method` 命名空间为后续数据面（quote / 分钟 K / 财务 / 除权）预留。

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
| `ticks` 拿到的行数比预期少 | 单次上限 2000 行；分页请用 `start` 逐页取、**遇空即停**（见上方两个坑） |
| 中文乱码 | 客户端未按 UTF-8 编解码；服务端读到非法 UTF-8 字节会结束该会话（不影响其他连接） |
| `docker inspect` 显示 unhealthy | 健康检查只探本服务的监听端口与 `hello` 帧，与上游无关；unhealthy 说明服务本身没在听 |
| Compose 里改了 `MOOTDX_PORT`，但容器仍显示 unhealthy | 只改了宿主映射却没改容器内监听端口。本仓的 `docker-compose.yml` 已让三者联动；自己另写 compose 时要一起改 `command`、`environment.MOOTDX_PORT` 与端口映射 |
| 另一个容器连 `mootdx:7701` 报「名称解析失败」 | 两者不在同一 Docker 网络，或使用方用了 `--network host`（该网络里没有 Docker DNS） |
| 另一个容器连 `127.0.0.1:7701` 失败 | 容器里的 `127.0.0.1` 是它自己，不是宿主。同网络用服务名，否则用宿主 IP |

查看日志：`docker logs -f mootdx`（compose 下 `docker compose logs -f`）。能看到监听地址、
每个连接的来源与断开、所选服务器 IP 与探测耗时。
