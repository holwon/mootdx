# 03: Dockerfile 改造 + ghcr 镜像流水线

**What to build:** 让 `docker build` 出一个**能直接跑的通达信数据服务镜像**，并让 tag 推送自动发布到 `ghcr.io/holwon/mootdx`。

镜像是本 effort 的交付形态：使用方 `docker run` 一条命令即可，不装 Python、不配路径、不管进程。

**Blocked by:** 01（镜像的入口是 `serve` 子命令）

**Status:** ready-for-agent

## 关键约束

- **多阶段构建**：builder 阶段出 wheel，final 阶段只装 wheel——镜像里不带编译工具链与源码树。
- **入口 = `serve`**，默认参数在镜像里给定（`--host 0.0.0.0 --port 7701`）。容器内**必须**监听 `0.0.0.0`，否则 Docker 端口映射不可达。
- **`VOLUME /root/.mootdx`**：缓存（复权数据、服务器测速结果）在容器重建、不删容器时保留。
- **`EXPOSE 7701`** 仅作文档性声明。
- **健康检查必须不依赖上游**：对监听端口做一次 TCP 连接并读 `hello` 帧即可。**不得**用「能否连上通达信服务器」做探活——那会让上游抖动导致容器被重启。
- **非 root 运行**：若 base 镜像与卷权限允许则启用；若与 `VOLUME` 权限冲突，允许退回 root 并在 Dockerfile 里注释原因。
- **流水线**：push tag `v*` → 构建并推送 `ghcr.io/<owner>/mootdx:<version>` 与 `:latest`；PR 与 push master → 只构建、不推送。平台 `linux/amd64` + `linux/arm64`（buildx）。认证用 `GITHUB_TOKEN`（`packages: write`），不引入额外密钥。
- 现有测试工作流保持不变，新增的镜像工作流与之并列。
- 不要迁移到 Alpine / distroless：`pandas` 在 musl 上代价高，slim base 即可。

## Acceptance criteria

- [ ] `docker build .` 成功，镜像小于一个可接受的基准（记录实际体积于 Comments）
- [ ] `docker run -p 127.0.0.1:7701:7701 <image>` 起容器后，本机连 `127.0.0.1:7701` 能收到 `hello` + `ready` 并取到日 K
- [ ] 不挂卷时能工作；挂命名卷后重建容器缓存保留（对比第二次请求耗时可见）
- [ ] `docker inspect` 的 healthcheck 显示 healthy，且**上游不可达时仍为 healthy**（验证探活不依赖上游）
- [ ] PR 上工作流只构建不推送；tag 推送后 ghcr 上出现对应版本与 `latest`，两种架构都可用（`docker buildx imagetools inspect` 可见）
- [ ] README 增加「用 Docker 跑」入口段，给出 `docker run` 一行命令与端口/卷说明
- [ ] `docs/quick.md` 或 `docs/setup.md` 指到 Docker 用法（安装章节不再只讲 pip）
