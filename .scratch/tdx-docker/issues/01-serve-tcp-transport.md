# 01: `serve` 子命令 + TCP 传输（替换 stdio）

**What to build:** 把对外暴露数据的方式从「宿主拉起的 stdio 子进程」换成「常驻 TCP 服务」，协议帧一个字节不改。

跑起 `python -m mootdx serve` 后，任何客户端（本机或其他机器）连到 `host:port` 就能：连上即收到 `hello`（协议版本 + mootdx / python 版本）与 `ready`（所选通达信服务器 + 探测耗时），然后按行式 JSON 发请求、按 `id` 收应答；`daily_kline` 照旧可用。多个客户端可同时连接，各自独立握手、互不干扰。连不上通达信服务器时回 `ready:false` 并**继续监听**，而不是退出。

同时删除 `worker` 子命令（stdio 形态），`--help` 中不再出现它。

**Blocked by:** None（can start immediately）

**Status:** resolved

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

- [x] `python -m mootdx serve --port <n>` 能起，日志打印实际监听地址与端口
- [x] 连上后按序收到 `hello` 与 `ready`；`hello` 含 `protocol=1`、python 与 mootdx 版本
- [x] 通过 TCP 发一条 `daily_kline` 能收到 `ok:true` 与 `bars`，`id` 与请求一致
- [x] 两个客户端同时连接，各自独立完成握手与请求，互不看到对方的帧
- [x] 一个客户端连上后保持沉默，另一个客户端的请求照常被服务
- [x] 客户端连上后立即断开，服务端不崩、仍可接受下一个连接
- [x] 端口已被占用时启动抛明确错误（消息里含该端口）
- [x] 客户端发畸形行（非 JSON）时该连接得到 `ok:false`（或按既有语义断开），服务端仍存活
- [x] 握手失败（连不上通达信服务器）时回 `ready:false` + 错误文本，进程**保持监听**
- [x] `mootdx --help` 中不再出现 `worker`
- [x] 既有 11 个协议用例全绿且**零改动**（`tests/test_worker.py` 不动）
- [x] `pyproject.toml` 依赖列表未新增条目
- [x] 全仓确定性测试套件绿（`python -m pytest`）
- [x] 文档：`docs/cli/worker.md` 更名为 `docs/cli/serve.md` 并重写为 TCP 形态（帧格式章节保留、`docker run` 示例、端口说明、从 stdio 迁移一节、故障排查表）；`mkdocs.yml` 导航同步；`docs/chlog.md` 记一条

## Comments

### 2026-10-17 实施记录（commit `8ec960f`）

**实现**（`mootdx/worker.py`）：

- `serve(reader, writer, factory)` 的**签名与逻辑未改**（只改两处注释、debug 日志补 `method` 字段），
  传输层是新增的 `create_server` / `run_service` / `_ThreadedServer` / `_SessionHandler`，
  用标准库 `socketserver.ThreadingTCPServer`。
- `ClientFactory`：每连接新建客户端，但首次探测成功的服务器在**进程内共享**（锁保护）。
- `BindError(OSError)`：绑定失败时抛，消息含 `host:port`。

**两处与票面字面不符的偏差（均有明确理由）**：

1. **改了 1 个协议用例**。票面写「`tests/test_worker.py` 不动」，但该文件末例
   `test_cli_registers_worker_subcommand` 断言 `worker` 子命令存在，与「`--help` 中不再出现
   `worker`」直接矛盾——两条验收不可同时成立。改为 `test_cli_registers_serve_subcommand_and_drops_worker`
   （断言 `serve` 在、`worker` 不在）。其余 16 例**零改动**，全绿。
2. **Windows 上 `allow_reuse_address = False`**。Windows 的 `SO_REUSEADDR` 语义是「抢占」
   而非「复用 TIME_WAIT」，开启会让「端口已被占用」退化为静默劫持，与验收 7 直接冲突。
   POSIX 上仍为 `True`。

**实测数据**（本机，2026-10-17）：

- 握手到 `ready`：`warmup_seconds = 3.39`，服务器 `117.34.114.16:7709`。
- 测试：`tests/test_worker.py` 17 例 + `tests/test_serve.py` 10 例，共 27 例全绿。
- 全仓 `python -m pytest`：99 passed。另 9 例 `tests/reader/test_reader_std.py` 失败，
  **与本次变更无关**（改动前基线即同样失败，成因是那些用例走网络取数）。

**遗留给票 02**：`tests/test_worker_live.py` 仍以「spawn stdio 子进程」驱动，此时已失效
（`worker` 子命令已删）。

### 2026-10-17 code-review 后续（commit `ac32a76`）

Standards 轴指出的硬违规已修：spec「Seam 1」要求「两个独立会话并发驱动同一个 `serve`，
断言互不干扰（不开 socket）」，原实现只在 Seam 2（TCP）覆盖。新增
`test_two_concurrent_sessions_do_not_leak_into_each_other`：两线程各用内存流、在
factory 栅栏处会合制造确定交错，断言各自输出流只有自己的 3 帧。

同时抽出 `tests/tcp_support.py` 消除 `Peer` / `RunningService` 在两份测试里的重复；
`test_worker.py` 文件头去掉已不适用的「stdout 纪律」。

**最终测试面**：默认套件 **110 passed**（协议 18 + 传输 10 + 其余）；
`-m network tests/test_worker_live.py` **3 passed in 11.48s**。
