# mootdx_plus

`mootdx_plus` 是一个面向 Python 3.12+ 的通达信数据工具包，提供通达信本地 VIPDOC 文件读取、行情接口、财务数据下载、板块解析、复权处理和命令行工具。

发行包名是 `mootdx_plus`，为了兼容已有项目，Python 导入路径仍然是 `mootdx`：

```python
from mootdx.reader import Reader
from mootdx.quotes import Quotes
```

## 这次升级做了什么

本版本不是简单改名，而是基于旧版 `mootdx` 做了兼容性和可靠性重构：

- Python 最低版本提升到 3.12，依赖改为现代版本范围，并补充标准 `pyproject.toml` 构建配置。
- 发行名称改为 `mootdx_plus`，保留 `mootdx` 导入路径和原有公开方法名，降低迁移成本。
- 本地读取器同时支持通达信安装根目录和直接传入 `vipdoc` 目录，完善目录、代码和市场校验。
- 市场识别覆盖沪市、深市、北交所和 920 新号段，支持 `600000`、`SH600000`、`SH.600000` 等写法。
- 日线、分钟线、5 分钟线和扩展市场数据解析适配新版通达信文件，增加真实 VIPDOC 集成测试。
- `to_data()` 统一处理列表、字典、DataFrame、NumPy 数组和空响应，修复 pandas/NumPy 布尔值歧义。
- 行情服务器探测改为并发、快速失败，避免失效节点和协议异常导致客户端启动长时间阻塞。
- 行情分页、日期范围、空数据、非法代码和非法参数增加明确校验；带市场前缀的代码会在调用服务器前规范化。
- 修复 `get_k_data()` 空范围、反向日期范围和服务器返回空数据时的崩溃问题。
- 配置文件读写采用默认值合并和原子替换，损坏的用户配置不会被静默覆盖。
- 文件缓存采用临时文件替换，避免进程中断留下半个 pickle 文件。
- 财务数据、除权除息、复权、节假日和命令行模块更新到 Python 3.12/pandas 2.x 的写法。
- 默认测试隔离网络和宿主机集成测试，补充边界单元测试、静态检查和可重复的构建验证。

## 安装

```bash
python -m pip install -U mootdx_plus
```

包含节假日接口所需的 JavaScript 引擎：

```bash
python -m pip install -U "mootdx_plus[all]"
```

升级：

```bash
python -m pip install -U mootdx_plus tdxpy
```

## 读取通达信本地数据

Windows 通常使用 `C:\new_tdx` 或 `C:\new_tdx\vipdoc`。WSL 可以直接使用挂载路径，例如 `/mnt/d/new_tdx_mock`。

```python
from mootdx.reader import Reader

reader = Reader.factory("std", tdxdir=r"D:\new_tdx")

# 日线
daily = reader.daily("600036")

# 1 分钟线；suffix="5" 或 fzline() 读取 5 分钟线
minute = reader.minute("SH.600036")
five_minute = reader.fzline("600036")

print(daily.tail())
```

直接传入 `vipdoc` 目录同样有效：

```python
reader = Reader.factory("std", tdxdir=r"D:\new_tdx\vipdoc")
```

扩展市场文件：

```python
reader = Reader.factory("ext", tdxdir=r"D:\new_tdx")
data = reader.daily("4#CF7D0LAO")
```

## 读取在线行情

```python
from mootdx.quotes import Quotes

client = Quotes.factory("std", timeout=5)
try:
    bars = client.bars("SH.600000", frequency=9, offset=20)
    print(bars.tail())
finally:
    client.close()
```

也可以使用上下文管理器：

```python
with Quotes.factory("std", timeout=5) as client:
    quotes = client.quotes(["600000", "000001"])
```

常用频率编号：`0` 5 分钟、`1` 15 分钟、`2` 30 分钟、`3` 1 小时、`9` 日线、`5` 周线、`6` 月线。

扩展市场接口仍保留原方法名，但部分远程扩展行情服务已经停止或改变协议，建议优先使用本地文件读取。

## 财务数据和板块

```python
from mootdx.affair import Affair

# 查看远程财务文件清单
files = Affair.files()

# 下载并解析单个财务文件
Affair.fetch(downdir="tmp", filename="gpcw19960630.zip")
data = Affair.parse(downdir="tmp", filename="gpcw19960630.zip")
```

```python
from mootdx.reader import Reader

reader = Reader.factory("std", tdxdir=r"D:\new_tdx")
block = reader.block("block_gn.dat")
```

## 命令行

安装后可以使用原有 `mootdx` 命令：

```bash
mootdx --help
mootdx --version
mootdx reader --help
mootdx quotes --help
```

## 用 Docker 跑数据服务

不想碰 Python 环境时，直接用官方镜像起一个常驻数据服务，然后让任何程序连
`host:port` 取数：

```bash
docker run -d --name mootdx \
  -p 127.0.0.1:7701:7701 \
  --restart unless-stopped \
  ghcr.io/holwon/mootdx:latest
```

* **端口**：容器监听 `7701`；只映射到 `127.0.0.1` 是刻意的（服务不做鉴权，没必要广播到局域网）。
  要对外提供时改成 `-p 7701:7701`。
* **缓存**：复权数据与测速结果缓存在容器内 `/home/mootdx/.mootdx`。加
  `-v mootdx-cache:/home/mootdx/.mootdx` 可在重建容器后复用（热路径约 0.06s，冷路径约 10s）。
* **网络**：容器需要**出站 TCP 7709** 才能连通达信行情服务器；不需要任何入站放行（除映射端口）。
* 镜像同时提供 `linux/amd64` 与 `linux/arm64`。

取数示例（行式 JSON 协议，每行一个对象）：

```python
import json, socket

with socket.create_connection(("127.0.0.1", 7701), timeout=30) as sock:
    stream = sock.makefile("rwb")
    print(json.loads(stream.readline()))   # hello
    print(json.loads(stream.readline()))   # ready
    stream.write(b'{"id": 1, "method": "daily_kline", "params": {"symbol": "002029", "count": 60}}\n')
    stream.flush()
    print(json.loads(stream.readline())["bars"][-1])
```

### 用 Compose

仓库根目录带 `docker-compose.yml`，适合和自己的服务一起跑：

```bash
docker compose up -d          # 用已发布镜像
docker compose up -d --build  # 改了本仓代码时
```

改端口不用动 compose 文件，在仓库根建一个 `.env` 写 `MOOTDX_PORT=17711` 即可
（宿主映射、容器内监听、健康检查会一起跟着变）。

### 让别的程序用上它

服务只是一个 TCP 端点，接入方式取决于使用方在哪：

| 使用方位置 | 连什么 | 备注 |
|---|---|---|
| 同机进程 | `127.0.0.1:7701` | 走宿主端口映射；端口只绑回环 |
| 同一 Compose 里的服务 | `mootdx:7701` | 服务名即主机名，不用宿主映射，也不用写 IP |
| 同一 Docker 网络的其他容器 | `mootdx:7701` | 需在同一用户自定义网络上 |
| 局域网 / 另一台机器 | `<宿主 IP>:7701` | 需把映射改成 `-p 7701:7701`；服务无鉴权，慎用 |

使用方要自己实现**超时、重连、`ready` 判读与 `protocol` 校验**——服务端只做「收一行、回一行」，
不主动断空闲连接，也不替你重试。

协议帧、方法参数、从 `mootdx worker`（已删除的 stdio 形态）的迁移、各语言接法与排查表见
[docs/cli/serve.md](docs/cli/serve.md)。也可以不用容器，直接 `mootdx serve --port 7701`。

## 测试和本地验证

在项目目录执行默认离线测试：

```bash
pytest -q
```

使用真实通达信目录运行 VIPDOC 集成测试：

```bash
MOOTDX_TDXDIR=/mnt/d/new_tdx_mock pytest -q -m integration tests/integration/test_vipdoc.py
```

Windows PowerShell：

```powershell
$env:MOOTDX_TDXDIR = "D:\new_tdx_mock"
pytest -q -m integration tests\integration\test_vipdoc.py
```

构建发行包：

```bash
python -m build
```

## 注意事项

- 本项目只读取通达信本地文件或调用公开行情服务，不包含交易下单功能。
- 在线行情依赖通达信服务器和 `tdxpy` 协议实现；服务器可能临时不可用或升级协议。
- 复权数据需要可用的除权除息数据源；网络不可用时应传入已缓存的 `xdxr` 数据。
- 节假日接口属于可选功能，安装 `mootdx_plus[holiday]` 或 `mootdx_plus[all]` 后使用。
- 请遵守通达信数据服务条款和目标数据源的使用限制。本项目仅供学习、研究和个人数据处理使用。

## 兼容迁移

旧项目通常无需修改导入代码，只需替换安装命令：

```bash
python -m pip uninstall mootdx
python -m pip install -U mootdx_plus
```

```python
# 仍然有效
from mootdx.reader import Reader
from mootdx.quotes import Quotes
```

## 开源协议

MIT License。项目主页：<https://github.com/mootdx/mootdx>
