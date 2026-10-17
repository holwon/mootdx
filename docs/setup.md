# 安装方式

## 用 Docker（不装 Python）

只要一个常驻数据服务时，装 Python 并不是必需的——直接跑官方镜像：

```shell
docker run -d --name mootdx \
  -p 127.0.0.1:7701:7701 \
  --restart unless-stopped \
  ghcr.io/holwon/mootdx:latest
```

容器监听 `7701`，任何程序连上后按行式 JSON 协议取数。端口映射的前缀 `127.0.0.1:` 是刻意的
（服务不做鉴权），缓存卷、网络要求与协议细节见 [数据服务](cli/serve.md)。
镜像同时提供 `linux/amd64` 与 `linux/arm64`。

## 普通安装

### 标准安装

> 新手建议使用 `pip install -U 'mootdx_plus[all]'` 安装

```shell
# 包含核心依赖安装
pip install -U 'mootdx_plus'

# 包含命令行依赖安装, 如果使用命令行工具可以使用这种方式安装
pip install -U 'mootdx_plus[cli]'

# 包含所有扩展依赖安装, 如果不清楚各种依赖关系就用这个命令
pip install -U 'mootdx_plus[all]'
```

## 升级版本

```shell
pip install -U mootdx_plus
```

## 源码安装[不建议, 速度较慢]

直接远程源码安装

```shell
pip install git+https://github.com/mootdx/mootdx.git
```

