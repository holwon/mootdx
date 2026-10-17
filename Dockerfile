# 通达信数据服务镜像：多阶段构建（builder 出 wheel，final 只装 wheel）。
#
# 用法：docker run -d -p 127.0.0.1:7701:7701 ghcr.io/holwon/mootdx:latest
# 说明见 docs/cli/serve.md。

ARG PYTHON_VERSION=3.12-slim

FROM python:${PYTHON_VERSION} AS builder
WORKDIR /build
# 只为出 wheel：编译期需要 README/LICENSE，运行期不需要源码树与构建工具。
COPY pyproject.toml README.md LICENSE ./
COPY mootdx ./mootdx
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:${PYTHON_VERSION}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MOOTDX_HOST=0.0.0.0 \
    MOOTDX_PORT=7701

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# 缓存目录（复权数据 xdxr、服务器测速结果）落在 $HOME/.mootdx。
# 以非 root 运行：先建用户并把目录交给它，否则挂载卷后无权写入，会表现为
# 「缓存永远不生效」——比不用卷更隐蔽。
RUN useradd --create-home --uid 10001 mootdx \
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
