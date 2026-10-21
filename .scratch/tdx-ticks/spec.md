# 数据服务新增 `ticks` 方法（通达信当日分笔）

Status: ready-for-agent

> **来源**：本 spec 由 `/to-spec` 于 2026-10-21 从仓库现状与一手实测合成。上游消费者是 `holwon/astock-mcp` 的通达信源（其决策见 `docs/adr/0023`，本仓不重复）。协议形状沿用既有的 [`docs/cli/serve.md`](../docs/cli/serve.md) v1（行式 JSON）；关联既有决策 [`ADR-0020`](../../docs/adr/0020-tdx-over-docker.md)（本仓不设 ADR，跨仓决策在消费方）与本仓 [`tdx-docker/spec.md`](../tdx-docker/spec.md)（`serve` 承载的来历）。
>
> **一手实测**：`astock-mcp/docs/research/0010-tdx-ticks-calibration.md`（消费方仓库）。本 spec 引用的每个数字都出自那里。

## Problem Statement

`mootdx serve` 目前只实现了一个协议方法 `daily_kline`。消费方（`astock-mcp`）的 `get_ticks` 工具由三枚 HTTP 上游服务，三者共享同一个风险面——都可能被出口 IP 级风控、被上游改版、被网络策略阻断。三枚全挂时工具只能报 `SOURCE_UNAVAILABLE`。

通达信 TCP 协议源（上百台行情服务器、不经任何 Web 风控面）已经在为日 K 提供链尾兜底，但它**没有暴露分笔能力**。消费方无法从本仓取到分笔，因此无法把通达信接成第四枚源。

另外有一个必须绕开的坑：`tdxpy` 的当日分笔接口 `get_transaction_data` 在发请求前有一道 `time_frame()` 客户端闸，只在 `09:30 < t < 11:30` 与 `13:00 < t < 15:00` 之间放行。闸外直接 `return None`，经 `to_data` 变成**空表**——不抛异常、不报错。而分笔的主要使用时刻恰恰是收盘后复盘。

## Solution

`mootdx serve` 新增协议方法 `ticks`，与 `daily_kline` 平行：

- **走历史接口** `StdQuotes.transactions(symbol, start, offset, date)`，**不走** `transaction()`。历史接口没有 `time_frame()` 闸，传 `date=今天` 即可取回当日数据。
- 参数：`symbol`（必填）、`date`（可省，省略时按 Asia/Shanghai 墙钟推定当日）、`start`（可省，默认 0）、`count`（可省，默认 2000）。
- 应答：`{"ok": true, "ticks": [{time, price, vol, buyorsell}]}`。
- **单位与清洗都不在 worker 做**：字段名与单位沿用上游原名（`vol` = 手、`price` = 元、`time` = `HH:MM`），剔除规则与单位归一在消费方的适配器里。这与 `daily_kline` 的分工一致——worker 只做「取数 → 裁列 → 记录化」。

## User Stories

### 协议形状

1. 作为消费方，我想用 `{"method":"ticks","params":{"symbol":"600519","date":"20261020"}}` 取回该日的全部分笔，以便不依赖任何 HTTP 上游。
2. 作为消费方，我想 `date` 可以省略，以便收盘后不必自己算归属日。
3. 作为消费方，我想 `date` 同时接受 `yyyyMMdd` 与 `yyyy-MM-dd` 两种形态（**由 worker 归一化：剥掉短横线后再传**），以便不必为格式单独适配。
4. 作为消费方，我想 `start` 与 `count` 可以省略（默认 0 / 2000），以便最常见的「取一页看看」不需要填参。
5. 作为消费方，我想应答里只有 `time` / `price` / `vol` / `buyorsell` 四列，以便不必过滤诊断列。
6. 作为消费方，我想空结果返回 `{"ok": true, "ticks": []}` 而不是 `ok:false`，以便区分「没有数据」与「取数失败」。
7. 作为消费方，我想未知方法仍回 `{"ok": false, "error": "unknown method: 'x'"}`，以便旧版镜像被新版本调用时错误可读。

### 参数校验（与 `daily_kline` 同风格）

8. 作为维护者，我想 `symbol` 为空或全空白时抛 `ValueError`，以便空请求不会打到上游。
9. 作为维护者，我想 `start` 为负时抛 `ValueError`，以便分页语义不被非法值破坏。
10. 作为维护者，我想 `count ≤ 0` 时抛 `ValueError`，以便与 `daily_kline` 的守卫一致。
11. 作为维护者，我想 `date` 形态非法时抛 `ValueError` 且错误文本说明它接受的两种形态，以便调用方一次改对。
12. 作为维护者，我想所有 `ValueError` 经 `serve()` 的既有 catch 变成 `ok:false`，以便我不新增错误通道。

### 协议演进

13. 作为维护者，我想新增方法**不 bump 协议版本**（`PROTOCOL` 保持 `1`），以便消费方的 `hello.protocol == 1` 校验不必随每个新方法改动。
14. 作为维护者，我想 `handle()` 的分发保持「一条 if、未知方法落到最后一行」，以便加方法与读代码都简单。
15. 作为维护者，我想 `ticks` 复用同一个 `handle(client, request)` 的签名与同一个 `client`（每连接一个行情客户端），以便连接隔离与单飞行语义不变。

### 数据完整性（本仓只负责"取全"，不负责清洗）

16. 作为消费方，我想 `ticks` 支持 `start` 偏移分页，以便取回超过单次上限（2000 条）的完整交易日。
17. 作为消费方，我想文档写明 `start` 是**自最近一笔往回数**的偏移（不是自最老），以便我不把分页方向搞反、拿到半个交易日还以为完整。
18. 作为消费方，我想文档写明 `start` 超过某个值时会**回绕成第 0 页且不报错**，以便我的分页循环只用「遇空即停」作终止条件。
19. 作为消费方，我想 worker **不做去重、不做剔除**，以便清洗规则只存在于消费方一处（两个地方各清洗一遍必然分叉）。

### 文档

20. 作为使用者，我想 `docs/cli/serve.md` 的「当前支持的方法」下出现 `### ticks` 小节，以便不读源码就知道怎么调。
21. 作为使用者，我想该小节列出参数表（含缺省值与两个坑：方向为「自最近往回数」、高位会回绕）。
22. 作为使用者，我想该小节给出完整的请求/应答样例，以便可以直接抄。
23. 作为使用者，我想「`method` 命名空间为后续数据面预留」那句里去掉已实现的 `ticks`，以便文档不把已实现项说成预留。

### 容器时区（防御性修正）

24. 作为使用者，我想 Dockerfile 补上 `tzdata` 与 `TZ=Asia/Shanghai`，以便同一容器里的 `transaction()` 闸与日志时间戳不再因 musl 退回 UTC 而错位。
25. 作为使用者，我想这条修正**不是** `ticks` 方法的前提（它走无闸接口），以便知道它可以独立回退。

### 健康检查工具

26. 作为维护者，我想保留 `mootdx/tools/probe_ticks.py` 作为长期健康检查（Σ分笔量 ≡ 当日日 K 量），以便上游变型时能一条命令定位。
27. 作为维护者，我想该工具的判据是**成交量**而非行数或成交额，以便不把正常数据误判为有问题。

> **已落地**：该工具已在先前的一手校准中实现（`--wide` 12 只全通过）。本批不重写，只验收它在新方法落地后仍可用、并决定是否改为经 `serve` 验证。

## Implementation Decisions

### `worker.py`

- 新增 `TICK_FIELDS = ("time", "price", "vol", "buyorsell")` 常量，与 `BAR_FIELDS` 并列。
- 新增 `ticks(client, params)` 函数，结构照 `daily_kline`：先校验参数（抛 `ValueError`），再调 `client.transactions(...)`，空表直接返回空列表，缺列抛 `ValueError`，否则 `json.loads(frame[list(TICK_FIELDS)].to_json(orient="records"))`。
- `handle()` 加一条 `if method == "ticks": return ticks(client, params)`。
- **`PROTOCOL` 保持 1**。

### `date` 的推定

省略 `date` 时按 Asia/Shanghai 墙钟取当天 `yyyyMMdd`。注意这是**归属日的推定**，不是交易日推定——非交易日调用会取到空列表（`ok:true` + 空数组），由消费方按 `NoData` 处理。本仓不引入交易日历。

**两种形态的归一化在 worker 内完成**：`StdQuotes.transactions` 内部执行 `int(date)`，传 `"2026-10-20"` 会直接抛 `ValueError`。所以 `ticks()` 必须先把短横线剥掉（`yyyy-MM-dd` → `yyyyMMdd`）再透传，不能只做原样中转。

### Dockerfile

- `RUN apk add --no-cache tzdata`（在 final 阶段）。
- `ENV TZ=Asia/Shanghai`。
- 不改健康检查（它只探本进程端口，不触碰通达信服务器）。

### 不改的部分

- `serve()` 主循环、握手帧、`ClientFactory`、连接隔离、单飞行——全部不动。
- `daily_kline` 一行不动。
- `tdxpy` 一行不动（不摸其内部类）。
- `pyproject.toml` 的依赖不动。

## Testing Decisions

### 什么算好测试

只断言**协议层可观测的行为**：给定请求、给定假行情客户端的应答，`handle()` 返回什么。不断言内部循环、不测 pandas 的中间形态。

### 测试落点

| 层 | 用什么测 | 断言什么 | 既有先例 |
|---|---|---|---|
| 方法分发与参数校验 | `tests/test_worker.py` 的 `FakeQuotes` 扩一个 `transactions()` 方法 | 缺 symbol / 负 start / `count ≤ 0` / 非法 date 各抛 `ValueError`；未知方法仍是 `ok:false` | `daily_kline` 的既有用例 |
| 裁列 | 同上，喂一个含额外诊断列的 DataFrame | 应答只含四列，顺序固定 | `BAR_FIELDS` 的既有用例 |
| 空结果 | 同上，喂空 DataFrame | `{"ok": true, "ticks": []}` | `daily_kline` 空数据用例 |
| 缺列 | 同上，喂缺 `buyorsell` 的 DataFrame | `ValueError` → `ok:false` | `daily_kline` 缺列用例 |
| 端到端承载 | `tests/test_serve.py` 的既有 TCP 夹具 | 新方法走同一条连接、同一 `id` 回执 | 既有 `daily_kline` over TCP 用例 |

### 不测的部分

- 不清洗、不去重、不换算单位——这些不在本仓，测了会与消费方的适配器测试重复。
- 不测真实通达信服务器的返回值形态——那是 `probe_ticks.py` 的职责，且它需要网络。

## Out of Scope

- **清洗、去重、单位归一、方向码映射** —— 全部在消费方适配器。worker 只做取数。
- **分页循环** —— worker 只接受单次 `start`/`count`，翻页由调用方做。
- **交易日历 / 归属日判定** —— 省略 `date` 时只按墙钟取当天，不做交易日推定。
- **历史分笔工具面** —— `date` 参数存在且能用，但本仓不新增 CLI 子命令或其它入口。
- **`transaction()` 当日接口** —— 不采用、不包装、不修改 tdxpy。
- **bump 协议版本** —— 新增方法不改 `PROTOCOL`；只有帧格式变更才 bump。
- **其它数据面**（分钟 K、除权除息、财务、板块、F10）—— 各自单独成批。

## Further Notes

### 为什么 `start` 的坑必须写进文档

`start` 在协议里打包为 `uint16`。实测 `start ≥ 32768` 时服务器**回绕成第 0 页并返回 2000 行完整复制**，不报任何错。任何「固定页数」的循环（本仓 `sample/fq.py` 就是 21 页 × 800）会在高位重复取第 0 页，得到一个看起来有数据、实际是同一段重复 N 遍的畸形序列。消费方唯一的自保方式是「遇空即停」——这个事实必须写在方法文档里，否则每个新的调用方都会踩。

### 为什么走历史接口

当日接口 `get_transaction_data` 有客户端闸（`time_frame()`），闸外 `return None` → 空表。历史接口 `get_history_transaction_data` 没有闸，传 `date=今天` 即可。两者列集差一个 `num`（历史没有），差异已在消费方接受。

绕开闸的另一条路是在 `worker.py` 里直接实例化 `GetTransactionData` 解析器。**否决**：那是摸第三方库的非公开内部类，上游一改就断；而历史接口本就是公开 API 且行为正确。

### 发布顺序

本仓必须先打 `v*` tag 发布含 `ticks` 的新镜像，消费方才能用上。消费方在镜像落后时的表现是 `unknown method` → 该源 `Failed` → 自动降级到前三枚源，**不会报错**。

### 与消费方的边界

| 事项 | 归谁 |
|---|---|
| 通达信服务器选择、连接、单飞行 | 本仓（`serve`） |
| `start` 分页的单次语义 | 本仓（透传） |
| 翻页循环（遇空即停、页数守卫） | 消费方 |
| 拼装成时间单调序列 | 消费方 |
| 剔除 `vol=0` / `time>15:00` | 消费方 |
| 手 → 股、`price × vol × 100` | 消费方 |
| 方向码 → `Buy`/`Sell`/`Neutral` | 消费方 |
