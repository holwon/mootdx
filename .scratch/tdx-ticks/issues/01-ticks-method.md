# 01: 数据服务新增 `ticks` 方法

**What to build:**

`mootdx serve` 的协议新增一个 `ticks` 方法，与 `daily_kline` 平行。给定 `symbol` 与可省略的 `date`，它走**历史接口** `StdQuotes.transactions(...)` 取回该日的分笔，按 `start` / `count` 透传分页语义。

走历史接口而不走当日接口，是因为当日接口 `get_transaction_data` 有一道 `time_frame()` 客户端闸：收盘后直接返回空表，不抛异常、不报错。而分笔的主要使用时刻恰恰是收盘后复盘。

**worker 只做「取数 → 裁列 → 记录化」，不做任何清洗或单位换算**——剔除规则与单位归一都在消费方的适配器里。两个地方各清洗一遍必然分叉。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 新增 `TICK_FIELDS = ("time", "price", "vol", "buyorsell")` 常量，与 `BAR_FIELDS` 并列
- [ ] 新增 `ticks(client, params)` 函数，结构照 `daily_kline`：参数校验 → 调 `client.transactions(...)` → 空表返回空列表 → 缺列抛 `ValueError` → 裁列 → `json.loads`
- [ ] 走 `StdQuotes.transactions(symbol, start, offset, date)`，**不调 `transaction()`**
- [ ] `handle()` 加一条分发；未知方法仍落到最后一行返回 `ok:false`
- [ ] 入参：`symbol`（必填，裸六位码）、`date`（可省，接受 `yyyyMMdd` 与 `yyyy-MM-dd`，省略时按 Asia/Shanghai 墙钟取当天）、`start`（可省，默认 0）、`count`（可省，默认 2000）
- [ ] **`date` 的两种形态必须在 worker 内归一化**：底层 `StdQuotes.transactions` 内部执行 `int(date)`，传 `"2026-10-20"` 会直接抛 `ValueError`。故先剥掉短横线（`yyyy-MM-dd` → `yyyyMMdd`）再传，不得只做透传
- [ ] 出参：`{"ok": true, "ticks": [{time, price, vol, buyorsell}]}`；**不带 `amount`、不带 `num`**
- [ ] 参数校验照 `daily_kline` 风格：空 symbol / 负 start / `count ≤ 0` / 非法 date 形态各抛 `ValueError`，经既有 catch 变 `ok:false`
- [ ] 空结果返回 `{"ok": true, "ticks": []}` 而非 `ok:false`
- [ ] **`PROTOCOL` 保持 1**（新增方法不 bump 协议版本）
- [ ] 不改 `tdxpy` 一行（包含 `quotes.py`）
- [ ] 单测用既有 `tests/test_worker.py` 的 `FakeQuotes` 扩一个 `transactions()` 方法，覆盖：参数校验四例、**`yyyy-MM-dd` 归一化**、裁列（含喂额外诊断列）、空结果、缺列、未知方法
