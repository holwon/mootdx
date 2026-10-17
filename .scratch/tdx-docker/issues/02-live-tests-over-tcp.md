# 02: 真网络用例迁移到 TCP

**What to build:** 让 `network` 标记的那组用例跟着换承载——从「spawn 一个 stdio 子进程并喂管道」改成「连一个 TCP 服务并走 socket」，断言内容一字不改。

这组用例是「本机真的能连通达信服务器取到数据」的证据，工具 01 落地后它不能因为承载变了就失去覆盖。

**Blocked by:** 01（先有 `serve` 子命令）

**Status:** ready-for-agent

## 关键约束

- **断言内容不变**：hello→ready 两帧、60 根日 K 与字段、UTF-8 往返无损、未知方法回 `ok:false` 且进程存活、字段裁剪（诊断列不外发）。
- 运行方式写进文件头 docstring（沿用现行惯例），并说明它需要真实外网通达信服务器。
- 默认套件仍排除这组用例（`-m 'not network and not integration'` 不动），CI 不依赖外网。
- 端口用系统分配的可用端口（不要写死 7701，避免与开发机上已在跑的服务撞车）。
- 若无法在本进程内起服务（例如想验证独立进程形态），允许改为「连一个已在跑的实例」，但需在 docstring 写明前置条件。

## Acceptance criteria

- [ ] `python -m pytest -m network tests/test_worker_live.py` 在本机全绿
- [ ] 断言内容与迁移前逐条对应（握手两帧、日 K 根数与字段、UTF-8、未知方法、stdout 无杂行不再适用则删去并在 docstring 说明）
- [ ] 未设任何环境变量时，默认套件仍全绿且不触网
- [ ] 用例结束后不留监听中的端口与残留进程
