# 01: `serve` 子命令 + TCP 传输（替换 stdio）

**What to build:** 把对外暴露数据的方式从「宿主拉起的 stdio 子进程」换成「常驻 TCP 服务」，协议帧一个字节不改。

跑起 `python -m mootdx serve` 后，任何客户端（本机或其他机器）连到 `host:port` 就能：连上即收到 `hello`（协议版本 + mootdx / python 版本）与 `ready`（所选通达信服务器 + 探测耗时），然后按行式 JSON 发请求、按 `id` 收应答；`daily_kline` 照旧可用。多个客户端可同时连接，各自独立握手、互不干扰。连不上通达信服务器时回 `ready:false` 并**继续监听**，而不是退出。

同时删除 `worker` 子命令（stdio 形态），`--help` 中不再出现它。

**Blocked by:** None（can start immediately）

**Status:** ready-for-agent

## 关键约束

- **协议核心一行不改**：`serve(reader, writer, factory)` 的签名与实现保留，传输层只做「把 socket 两端包成 reader/writer，然后调它」。这是本票的硬标准——评审时任何触碰该函数内部的改动都要说明为什么承载层做不到。
- **模块名保持 `worker.py`，只改命令名**（用户 2026-10-17 裁决）。模块名是内部实现细节，命令名才是对外契约；改名会破坏「11 个协议用例零改动」。
- **每连接一个独立行情客户端**（`factory()` 每次都新建）。这不是优化而是强制：底层客户端持有一个 socket，共享实例上的并发请求会交错破坏协议流。连接关闭时 `close()` 它。
- **服务器探测结果进程内共享**。探测一台可用服务器实测约 3.4 秒，若每连接各自探测，多客户端场景成倍变慢。
- **零新增运行时依赖**：监听与并发用标准库。日志沿用既有 logger（stderr）。
- **不主动断开空闲连接**；宿主侧超时是宿主的责任。
- 默认 `--host 0.0.0.0 --port 7701`，两者都可配。
- 协议版本号**仍为 1**。

## Acceptance criteria

- [ ] `python -m mootdx serve --port <n>` 能起，日志打印实际监听地址与端口
- [ ] 连上后按序收到 `hello` 与 `ready`；`hello` 含 `protocol=1`、python 与 mootdx 版本
- [ ] 通过 TCP 发一条 `daily_kline` 能收到 `ok:true` 与 `bars`，`id` 与请求一致
- [ ] 两个客户端同时连接，各自独立完成握手与请求，互不看到对方的帧
- [ ] 一个客户端连上后保持沉默，另一个客户端的请求照常被服务
- [ ] 客户端连上后立即断开，服务端不崩、仍可接受下一个连接
- [ ] 端口已被占用时启动抛明确错误（消息里含该端口）
- [ ] 客户端发畸形行（非 JSON）时该连接得到 `ok:false`（或按既有语义断开），服务端仍存活
- [ ] 握手失败（连不上通达信服务器）时回 `ready:false` + 错误文本，进程**保持监听**
- [ ] `mootdx --help` 中不再出现 `worker`
- [ ] 既有 11 个协议用例全绿且**零改动**（`tests/test_worker.py` 不动）
- [ ] `pyproject.toml` 依赖列表未新增条目
- [ ] 全仓确定性测试套件绿（`python -m pytest`）
- [ ] 文档：`docs/cli/worker.md` 更名为 `docs/cli/serve.md` 并重写为 TCP 形态（帧格式章节保留、`docker run` 示例、端口说明、从 stdio 迁移一节、故障排查表）；`mkdocs.yml` 导航同步；`docs/chlog.md` 记一条
