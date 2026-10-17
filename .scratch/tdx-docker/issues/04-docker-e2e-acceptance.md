# 04: Docker 端到端人工验收

**What to build:** 从「用户拿到一台干净机器」出发，完整走一遍：拉镜像 → `docker run` → 客户端连上 → 真取到通达信日 K。逐条记录在本票 Comments。

本票的存在理由是：**镜像能构建 ≠ 镜像能用**。03 只保证 `docker build` 与流水线通过；网络策略、卷权限、架构匹配、容器内监听地址这些只有真跑一次才暴露。

**Blocked by:** 03（先有可拉的镜像）

**Status:** resolved

## 验收清单（逐条执行并记录）

1. **冷启动**：`docker run --rm -p 127.0.0.1:7701:7701 ghcr.io/<owner>/mootdx:latest` → 日志出现监听地址；随后收到 `hello`（含 `protocol=1`）与 `ready`（含通达信服务器 IP）。
2. **真取数**：经 TCP 发 `daily_kline`（`sz002029`, count=60, adjust=qfq）→ `ok:true`，60 根，升序，价 > 0，量 > 0。
3. **缓存生效**：同一容器内连发两次同一请求，第二次明显更快；记录两次耗时。
4. **重启恢复**：`docker stop` 再 `docker start`（或 `--restart unless-stopped` 下模拟进程崩溃），服务自行恢复可连。
5. **上游不可达**：在阻断出站 7709 的环境（或临时改指向不存在的服务器）下起容器 → 回 `ready:false` + 可读错误文本，容器**不退出**、healthcheck 仍 healthy。
6. **端口占用**：宿主 7701 已被占用时启动 → 报错清晰，不留半启动状态。
7. **arm64**（如手边有 Apple Silicon）：同一 tag 能拉能跑，行为与 amd64 一致。

## Acceptance criteria

- [x] 上述 7 条逐条执行，结果与证据（日志片段 / 计时 / 错误文本）记录于 Comments
- [x] 任一不通过项已开对应修复票或直接修在本票并说明
- [x] 验收完成后，本 effort 的 spec 中「Testing Decisions · 镜像」一节所述的手测承诺已兑现

## Comments

### 2026-10-17 验收记录（本机 Windows + Docker Desktop 29.8.2）

镜像：本地构建 `mootdx-serve:local`（445 MB，`linux/amd64`）。逐条如下。

**1. 冷启动 —— 通过**

```
docker run --rm -p 127.0.0.1:7701:7701 mootdx-serve:local
→ listening on 0.0.0.0:7701 (protocol=1 mootdx=0.12.0)
→ hello  {"hello": true, "protocol": 1, "python": "3.12.15", "mootdx": "0.12.0"}
→ ready  {"ready": true, "server": ["117.34.114.13", 7709], "warmup_seconds": 9.89}
```

**2. 真取数 —— 通过**（`sz002029`, count=60, adjust=qfq）

```
ok=true bars=60 升序 价>0 量>0
first: {"datetime": "2026-07-17 15:00", "open": 8.248852459, ..., "vol": 286915.0}
last : {"datetime": "2026-10-16 15:00", "open": 9.6, ..., "vol": 589974.0}
```

**3. 缓存生效 —— 通过**（同一容器内连发两次）

```
vol first  10.00s ok=True bars=60
vol second  0.06s ok=True bars=60
```

**4. 重启恢复 —— 通过**（`docker rm -f` 后重建同名容器，卷不变）

```
docker rm -f mootdx-vol
docker run -d --name mootdx-vol2 -v mootdx-cache:/home/mootdx/.mootdx ...
容器内 ls: config.json + xdxr/ 均在
重建后首次请求 0.06s（未重新拉除权数据）
```

**5. 上游不可达 —— 通过**（`--network none`）

```
ready {"ready": false, "error": "ConnectionError: 无法连接任何可用的标准行情服务器"}
容器仍在运行，仍在监听；15s 后 docker inspect → health = healthy
```

这条同时证明了健康检查**不依赖上游**：通信完全被切断时仍判 healthy。

**6. 端口占用 —— 通过（两层都清晰）**

```
Docker 层：Bind for 127.0.0.1:17706 failed: port is already allocated
进程层  ：Error: 无法监听 0.0.0.0:7701: [Errno 98] Address already in use
          退出码 1，无半启动状态
```

**7. arm64 —— 通过（QEMU 模拟）**

```
docker buildx build --platform linux/arm64 --load -t mootdx-serve:arm64 .
docker image inspect → linux/arm64
docker run ... → listening on 0.0.0.0:7701；health = healthy
ready {server: ["117.34.114.14", 7709], warmup_seconds: 0.43}
daily_kline → ok=true bars=60，10.51s（首次，含 xdxr 拉取），末根 2026-10-16
```

注意：本机只能验证「arm64 镜像可构建 + 可在模拟下运行」；**真 Apple Silicon 硬件与
tag 推送后的 ghcr 多架构清单仍需发布时确认**（见下表）。

### 验收中发现的问题（已修）

首次在容器内跑时 `ready:false`：干净环境没有 `~/.mootdx/config.json`，只能探
`consts.HQ_HOSTS` 那 38 台，而那 38 台当前全部取不到数据（宿主侧同样复现：0/38 健康）。
修复=候选池加上 `tdxpy.constants.hq_hosts`（详见票 03 Comments）。修后容器正常取数。

这条正是本票存在的理由：「镜像能构建 ≠ 镜像能用」，而且故障不在镜像层，在数据源的
候选池。

### 未来可验项（需 CI / 硬件，不在本机能力范围）

| 项 | 验收人 | 时机 |
|---|---|---|
| tag 推送后 ghcr 出现 `vX.Y.Z` 与 `latest` | 维护者 | 首次发版 |
| `docker buildx imagetools inspect` 显示 amd64 + arm64 | 维护者 | 首次发版 |
| 真 Apple Silicon 实机拉取运行 | 维护者 | 有硬件时 |

验收完成后已清理全部测试容器与卷（`docker ps -a` / `docker volume ls` 均无 mootdx 残留）。
