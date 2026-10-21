# 02: `ticks` 方法文档与容器时区修正

**What to build:**

两件事，都影响「别人能不能正确用上这个方法」：

1. `docs/cli/serve.md` 的「当前支持的方法」下出现 `### ticks` 小节——参数表、完整请求/应答样例、以及**两个必须写进去的坑**。
2. Dockerfile 补 `tzdata` + `TZ=Asia/Shanghai`——防御性修正，不是本功能的前提。

**两个坑必须写清楚**（否则每个新调用方都会踩一次）：

- `start` 是**自最近一笔往回数**的偏移，不是自最老。`start=0` 给的是收盘段。
- `start` 超过某个值（实测 `≥ 32768`）时服务器**回绕成第 0 页并返回 2000 行完整复制，不报任何错**。所以分页循环的唯一安全终止条件是「遇空即停」，不能用固定页数。

**Blocked by:** 01

**Status:** resolved

- [x] `docs/cli/serve.md` 新增 `### ticks` 小节，与 `### daily_kline` 平行
- [x] 小节含参数表：`symbol` / `date` / `start` / `count`，逐个给缺省值与语义
- [x] 小节含完整请求与应答 JSON 样例（各一条，可直接抄）
- [x] 小节明确写出「`start` 自最近一笔往回数」
- [x] 小节明确写出「高位 `start` 会回绕成第 0 页且不报错；终止条件只能用遇空即停」
- [x] 小节明确写出「本方法不做清洗、不去重、不换算单位」
- [x] 「`method` 命名空间为后续数据面预留」那句里去掉已实现的 `ticks`（保留未实现项）
- [x] Dockerfile final 阶段补 `apk add --no-cache tzdata` 与 `ENV TZ=Asia/Shanghai`
- [x] 文档里说明这条容器时区修正是**防御性**的（`ticks` 走无闸接口，不依赖它），可独立回退
- [x] 不改健康检查（它只探本进程端口，不触碰通达信服务器）

## Comments

### 2026-10-21 实施记录（commit `e01eae2`，评审修正 `1dd96fa`）

**文档**：`docs/cli/serve.md` 新增 `### ticks` 小节（参数表 / 四字段表 / 请求+应答样例 / 坑位 / 免责声明），并把「`method` 命名空间为后续数据面预留」那句里的 `ticks` 去掉（改为 `quote / 分钟 K / 财务 / 除权`）。另加一行排查表条目、一个新节「容器时区」。

**坑从「两个」变「三个」**：施工时实测发现 ticket 与 spec 漏了一个必须写进文档的事实——**每页页内已按时间升序，页号越大时间越早**（实测第 0 页 `13:00 → 15:28`、第 1 页 `09:32 → 13:00`）。只说「自最近往回数」不足以推出正确的拼装方式，调用方很容易写成「拼接后整体反转」而拿到逐页降序的乱序序列。这条与 `astock-mcp/docs/research/0010-tdx-ticks-calibration.md` §3.7 一致。

**Dockerfile**：final 阶段 `ENV ... TZ=Asia/Shanghai` + `RUN apk add --no-cache tzdata`。注释写明它是防御性修正、回退方式（删 `TZ=` 与那行 `apk add`）、以及为什么留着它（musl 无时区数据时本地时间静默退回 UTC，`tdxpy` 的 `time_frame()` 闸会在上海 17:30–19:30 / 21:00–23:00 误判为交易时段）。

**健康检查**未动。`docker build` 未在本机验证（本机无 Docker 需求），Dockerfile 改动仅为 `ENV` + 单条 `apk add`。
