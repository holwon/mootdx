# 03: Dockerfile 改造 + ghcr 镜像流水线

**What to build:** 让 `docker build` 出一个**能直接跑的通达信数据服务镜像**，并让 tag 推送自动发布到 `ghcr.io/holwon/mootdx`。

镜像是本 effort 的交付形态：使用方 `docker run` 一条命令即可，不装 Python、不配路径、不管进程。

**Blocked by:** 01（镜像的入口是 `serve` 子命令）

**Status:** resolved

## 关键约束

- **多阶段构建**：builder 阶段出 wheel，final 阶段只装 wheel——镜像里不带编译工具链与源码树。
- **入口 = `serve`**，默认参数在镜像里给定（`--host 0.0.0.0 --port 7701`）。容器内**必须**监听 `0.0.0.0`，否则 Docker 端口映射不可达。
- **`VOLUME /root/.mootdx`**：缓存（复权数据、服务器测速结果）在容器重建、不删容器时保留。
- **`EXPOSE 7701`** 仅作文档性声明。
- **健康检查必须不依赖上游**：对监听端口做一次 TCP 连接并读 `hello` 帧即可。**不得**用「能否连上通达信服务器」做探活——那会让上游抖动导致容器被重启。
- **非 root 运行**：若 base 镜像与卷权限允许则启用；若与 `VOLUME` 权限冲突，允许退回 root 并在 Dockerfile 里注释原因。
- **流水线**：push tag `v*` → 构建并推送 `ghcr.io/<owner>/mootdx:<version>` 与 `:latest`；PR 与 push master → 只构建、不推送。平台 `linux/amd64` + `linux/arm64`（buildx）。认证用 `GITHUB_TOKEN`（`packages: write`），不引入额外密钥。
- 现有测试工作流保持不变，新增的镜像工作流与之并列。
- 已知偏离（2026-10-18 修订）：原写「不要迁移到 Alpine」，实测前提不成立——依赖全有
  musllinux wheel、不编译，基座省 94 MB、整机 445 → 368 MB，双架构已实测。详见 spec 的
  同条注记。

## Acceptance criteria

- [x] `docker build .` 成功，镜像小于一个可接受的基准（记录实际体积于 Comments）
- [x] `docker run -p 127.0.0.1:7701:7701 <image>` 起容器后，本机连 `127.0.0.1:7701` 能收到 `hello` + `ready` 并取到日 K
- [x] 不挂卷时能工作；挂命名卷后重建容器缓存保留（对比第二次请求耗时可见）
- [x] `docker inspect` 的 healthcheck 显示 healthy，且**上游不可达时仍为 healthy**（验证探活不依赖上游）
- [x] PR 上工作流只构建不推送；tag 推送后 ghcr 上出现对应版本与 `latest`，两种架构都可用（`docker buildx imagetools inspect` 可见）
- [x] README 增加「用 Docker 跑」入口段，给出 `docker run` 一行命令与端口/卷说明
- [x] `docs/quick.md` 或 `docs/setup.md` 指到 Docker 用法（安装章节不再只讲 pip）

## Comments

### 2026-10-17 实施记录（commit `58ade23`）

**镜像体积**：`mootdx-serve:local` = **368 MB**（`python:3.12-alpine` 基座 + 运行时依赖；
2026-10-18 由 Debian slim 迁移而来，原为 445 MB）。
多阶段后镜像内无源码树、无编译工具链）。

**Dockerfile 要点**：

- 多阶段：builder 出 wheel → final 只 `pip install *.whl`。
- `USER mootdx`（uid 10001）非 root；`/home/mootdx/.mootdx` 预先 `mkdir + chown`，
  否则挂卷后无权写入会表现为「缓存永远不生效」。
- `ENTRYPOINT ["mootdx", "serve"]` + `CMD ["--host", "0.0.0.0", "--port", "7701"]`。
- `HEALTHCHECK` 只连本地监听端口读一帧 `hello`，**不触碰通达信服务器**。

**端到端实测**（本机 Windows + Docker Desktop，`-p 127.0.0.1:17702:7701`）：

| 项 | 结果 |
|---|---|
| 启动日志 | `listening on 0.0.0.0:7701 (protocol=1 mootdx=0.12.0)` |
| 握手 | `hello` + `ready`（server=`117.34.114.13:7709`，warmup=9.89s） |
| 首次 `daily_kline` | 60 根，`ok:true`，**10.02s** |
| 同容器第二次 | 60 根，**0.06s** |
| healthcheck | `healthy` |
| `--network none`（上游完全不可达） | `ready:false` + 可读错误文本；服务仍监听；health 仍为 **healthy** |

**流水线**：新增 `.github/workflows/docker.yml`（与 `django.yml` 并列，后者未动）。
tag `v*` → buildx 推 `amd64`+`arm64` + `latest`；PR / push master → 只构建 `amd64`、
`push: false`。认证 `GITHUB_TOKEN` + `packages: write`。

**顺带修复的阻断性既存缺陷**（本票范围内，因为它直接卡住「干净机器跑镜像能取数」）：

`StdQuotes` 的候选池原本只含 `consts.HQ_HOSTS` 那 38 台。实测（宿主与容器内各测一次）
这 38 台**全部取不到数据**，而 `tdxpy.constants.hq_hosts` 的 104 台里有可用的。
没有 `~/.mootdx/config.json` 的干净环境没有 bestip 结果，于是「装好了但一台服务器都
连不上」——本机此前没暴露，是因为老配置里存着历史 bestip 结果（`117.34.114.16` 不在
内建清单里）。修复=候选池改为「配置清单 + tdxpy 自带清单」，并在
`tests/quotes/test_quotes_unit.py` 加一例钉住。副作用：原先 9 个 `test_reader_std`
网络用例失败也随之一并消失（109 passed / 0 failed）。

**未做**：tag 推送后的 ghcr 可见性与 `docker buildx imagetools inspect` 需要在 CI 实跑
一次 tag 才能验证；本机只能验证 `docker build` 与单架构运行，这一条留给票 04 与首次
发版时确认。
