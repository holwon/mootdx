# 04: Docker 端到端人工验收

**What to build:** 从「用户拿到一台干净机器」出发，完整走一遍：拉镜像 → `docker run` → 客户端连上 → 真取到通达信日 K。逐条记录在本票 Comments。

本票的存在理由是：**镜像能构建 ≠ 镜像能用**。03 只保证 `docker build` 与流水线通过；网络策略、卷权限、架构匹配、容器内监听地址这些只有真跑一次才暴露。

**Blocked by:** 03（先有可拉的镜像）

**Status:** ready-for-agent

## 验收清单（逐条执行并记录）

1. **冷启动**：`docker run --rm -p 127.0.0.1:7701:7701 ghcr.io/<owner>/mootdx:latest` → 日志出现监听地址；随后收到 `hello`（含 `protocol=1`）与 `ready`（含通达信服务器 IP）。
2. **真取数**：经 TCP 发 `daily_kline`（`sz002029`, count=60, adjust=qfq）→ `ok:true`，60 根，升序，价 > 0，量 > 0。
3. **缓存生效**：同一容器内连发两次同一请求，第二次明显更快；记录两次耗时。
4. **重启恢复**：`docker stop` 再 `docker start`（或 `--restart unless-stopped` 下模拟进程崩溃），服务自行恢复可连。
5. **上游不可达**：在阻断出站 7709 的环境（或临时改指向不存在的服务器）下起容器 → 回 `ready:false` + 可读错误文本，容器**不退出**、healthcheck 仍 healthy。
6. **端口占用**：宿主 7701 已被占用时启动 → 报错清晰，不留半启动状态。
7. **arm64**（如手边有 Apple Silicon）：同一 tag 能拉能跑，行为与 amd64 一致。

## Acceptance criteria

- [ ] 上述 7 条逐条执行，结果与证据（日志片段 / 计时 / 错误文本）记录于 Comments
- [ ] 任一不通过项已开对应修复票或直接修在本票并说明
- [ ] 验收完成后，本 effort 的 spec 中「Testing Decisions · 镜像」一节所述的手测承诺已兑现
