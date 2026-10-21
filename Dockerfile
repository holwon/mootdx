# 通达信数据服务镜像：多阶段构建（builder 出 wheel，final 只装 wheel）。
#
# 用法：docker run -d -p 127.0.0.1:7701:7701 ghcr.io/holwon/mootdx:latest
# 说明见 docs/cli/serve.md。
#
# 基座选 Alpine 而非 Debian slim：slim 光基座就 177 MB，Alpine 只有 83 MB（省 94 MB）。
# 代价是 musl 上的科学计算 wheel 稍大（pandas+numpy 146 MB vs 142 MB，多 4 MB），
# 两相抵消后整机仍小 76 MB（实测 445 MB → 368 MB）。项目全部依赖都有 musllinux
# wheel，构建全程不编译，因此不需要在镜像里装 gcc。

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-alpine AS builder
WORKDIR /build
# 只为出 wheel：编译期需要 README/LICENSE，运行期不需要源码树与构建工具。
COPY pyproject.toml README.md LICENSE ./
COPY mootdx ./mootdx
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:${PYTHON_VERSION}-alpine

# TZ + tzdata 是**防御性修正**，与本仓的 `ticks` 方法无依赖关系（它走无闸的历史接口，
# 日期归属由 worker 用固定 +08:00 偏移算出，不查 tz 数据库）。
# 留着是因为 Alpine（musl）默认不带时区数据，`TZ=Asia/Shanghai` 会静默退回 UTC，于是：
#   * 任何依赖本地时间的代码都按 UTC 判时钟——例如 tdxpy 当日分笔的 time_frame() 闸会
#     在**上海 17:30–19:30 / 21:00–23:00** 误判为交易时段，而在真正盘中却判否；
#   * 日志时间戳与上海交易日历差 8 小时，排查时容易把时区差当成数据错。
# 它可独立回退：删掉 ENV 里的 TZ 与这一行 apk add，不影响任何协议方法。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    MOOTDX_HOST=0.0.0.0 \
    MOOTDX_PORT=7701

RUN apk add --no-cache tzdata

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels \
    # pip / setuptools / wheel 只在构建期有用，运行期不需要：直接删目录约省 8 MB。
    # 用 rm 而非 `pip uninstall`（后者要 import 自己，且失败会被 `|| true` 吞掉）。
    && rm -rf /usr/local/lib/python3.12/site-packages/pip \
              /usr/local/lib/python3.12/site-packages/pip-* \
              /usr/local/lib/python3.12/site-packages/setuptools \
              /usr/local/lib/python3.12/site-packages/setuptools-* \
              /usr/local/lib/python3.12/site-packages/_distutils_hack \
              /usr/local/lib/python3.12/site-packages/distutils-precedence.pth \
              /usr/local/lib/python3.12/site-packages/wheel \
              /usr/local/lib/python3.12/site-packages/wheel-*

# 缓存目录（复权数据 xdxr、服务器测速结果）落在 $HOME/.mootdx。
# 以非 root 运行：先建用户并把目录交给它，否则挂载卷后无权写入，会表现为
# 「缓存永远不生效」——比不用卷更隐蔽。
# Alpine 用 busybox 的 adduser，参数与 Debian 的 useradd 不同。
RUN addgroup -g 10001 mootdx \
    && adduser -u 10001 -G mootdx -h /home/mootdx -D -s /sbin/nologin mootdx \
    && mkdir -p /home/mootdx/.mootdx \
    && chown -R mootdx:mootdx /home/mootdx

USER mootdx

VOLUME ["/home/mootdx/.mootdx"]
EXPOSE 7701

# 健康检查只探本进程的监听端口并读一帧 hello：**不**触碰通达信服务器。
# 否则上游抖动会让容器被判不健康而被重启——那不是本服务能修的问题。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json,os,socket,sys; \
h=os.environ.get('MOOTDX_HOST','0.0.0.0'); \
p=int(os.environ.get('MOOTDX_PORT','7701')); \
s=socket.create_connection(('127.0.0.1' if h in ('0.0.0.0','::') else h, p), timeout=3); \
frame=json.loads(s.makefile('rb').readline().decode('utf-8')); \
s.close(); \
sys.exit(0 if frame.get('hello') is True else 1)"

ENTRYPOINT ["mootdx", "serve"]
CMD ["--host", "0.0.0.0", "--port", "7701"]
