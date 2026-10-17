# 02: 真网络用例迁移到 TCP

**What to build:** 让 `network` 标记的那组用例跟着换承载——从「spawn 一个 stdio 子进程并喂管道」改成「连一个 TCP 服务并走 socket」，断言内容一字不改。

这组用例是「本机真的能连通达信服务器取到数据」的证据，工具 01 落地后它不能因为承载变了就失去覆盖。

**Blocked by:** 01（先有 `serve` 子命令）

**Status:** resolved

## 关键约束

- **断言内容不变**：hello→ready 两帧、60 根日 K 与字段、UTF-8 往返无损、未知方法回 `ok:false` 且进程存活、字段裁剪（诊断列不外发）。
- 运行方式写进文件头 docstring（沿用现行惯例），并说明它需要真实外网通达信服务器。
- 默认套件仍排除这组用例（`-m 'not network and not integration'` 不动），CI 不依赖外网。
- 端口用系统分配的可用端口（不要写死 7701，避免与开发机上已在跑的服务撞车）。
- 若无法在本进程内起服务（例如想验证独立进程形态），允许改为「连一个已在跑的实例」，但需在 docstring 写明前置条件。

## Acceptance criteria

- [x] `python -m pytest -m network tests/test_worker_live.py` 在本机全绿
- [x] 断言内容与迁移前逐条对应（握手两帧、日 K 根数与字段、UTF-8、未知方法、stdout 无杂行不再适用则删去并在 docstring 说明）
- [x] 未设任何环境变量时，默认套件仍全绿且不触网
- [x] 用例结束后不留监听中的端口与残留进程

## Comments

### 2026-10-17 实施记录（commit `d65235e`）

**改法**：用例在进程内起一个真 `serve`（`worker.create_server("127.0.0.1", 0)` + 后台
线程跑 `serve_forever`），走 `socket.create_connection` 取数；不再 spawn 子进程、不再
喂 stdin/stdout。端口用系统分配的，不写死 7701。

**断言对应关系**：

| 迁移前 | 迁移后 |
|---|---|
| 握手两行（hello / ready） | 两条：`test_handshake_and_daily_kline_over_real_network` 的前半 |
| 日K 60 根、7 字段、升序、值域 | 同一用例后半，逐条未改 |
| 管道往返 + 未知方法 + UTF-8 | `test_socket_roundtrip_survives_unknown_method_and_utf8` |
| 关 stdin 后干净退出 | 改为 `test_connection_close_keeps_service_listening`（关连接后仍可新连） |
| stdout 无杂行 | 删除（协议流与日志已不同 fd）；文件头 docstring 已说明 |

**实测**：`-m network tests/test_worker_live.py` → **3 passed in 14.61s**；服务器
`117.34.114.16:7709`。用例结束后服务已 `shutdown()` + `server_close()`，无残留监听。

**默认套件**：`python -m pytest` → 99 passed（与票 01 一致）；`network` 组仍被
`addopts` 排除，未触网。
