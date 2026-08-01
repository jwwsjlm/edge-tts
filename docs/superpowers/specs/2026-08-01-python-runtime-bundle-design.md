# Linux amd64 Python 3.14 纯净运行包设计

## 目标

每次稳定 Tag 发布时，除 Windows ZIP、Docker 双架构镜像和 Linux amd64 离线 Docker 镜像外，再发布一个无需执行 `pip install` 的 Linux amd64 Python 3.14 运行包。运行包只包含服务运行所需文件，所有项目库和第三方依赖都放在 `libs/`，不包含 Markdown、测试、示例、GitHub 配置或构建缓存。

本轮版本更新为 7.3.2，并在完整验证后发布 `v7.3.2`。已经成功发布的 7.3.1 保持不变。

## 发布资产与目录结构

资产名称固定为：

```text
edge-tts-server-python314-linux-amd64.tar.gz
```

解压后只有一个顶层目录：

```text
edge-tts-server-python314-linux-amd64/
├── libs/
├── config.example.yaml
├── run.py
└── LICENSE
```

约束：

- `libs/` 包含 `edge_tts`、`edge_tts_server` 和全部运行时第三方依赖。
- 不提供 `start.sh`，用户使用 `python3.14 run.py` 启动。
- 不包含任何 `.md`、测试、示例、`.git`、`.github`、Docker 文件、构建脚本、`__pycache__` 或 `.pyc`。
- `LICENSE` 保留许可证信息，不属于 Markdown。
- `config.example.yaml` 使用现有安全默认配置。

## 启动器

发布包中的 `run.py` 负责：

1. 通过 `Path(__file__).resolve().parent` 找到发布包根目录。
2. 将同目录的 `libs/` 插入 `sys.path` 首位。
3. 从 `edge_tts_server.cli` 导入 `main`。
4. 没有命令行参数时，等价执行 `main(["--config", "<包目录>/config.yaml"])`。
5. 用户传入参数时原样交给服务 CLI，因此仍支持 `python3.14 run.py --config /path/to/config.yaml`。

首次运行且 `config.yaml` 不存在时，现有配置加载器会在包目录创建带随机 API Key 的配置。用户也可以先复制并编辑 `config.example.yaml`。

## 构建方式

仓库增加专用 Python bundle 打包脚本和运行器模板。GitHub Action 的 `python-bundle` Job 在 `ubuntu-latest` 上使用 `python:3.14-slim` 并明确指定 `linux/amd64`：

1. 创建干净的 staging 目录。
2. 使用构建容器将当前项目及其依赖安装到 staging 的 `libs/`。
3. 移除不属于服务运行包的 `edge_playback` 包和不需要的缓存、字节码。
4. 复制 `run.py`、根目录 `config.example.yaml` 和 `LICENSE`。
5. 审计目录，发现 `.md`、`__pycache__`、`.pyc` 或计划外顶层文件时立即失败。
6. 生成 gzip tar，保持单一顶层目录。

构建依赖继续以项目安装元数据为唯一来源，不新增手工维护且可能漂移的第二份 requirements 文件。

## 自动验证

测试先行覆盖：

- bundle 模板、构建脚本和 Release 工作流文件存在。
- `run.py` 将 `libs/` 放在导入路径首位，并默认使用自身目录的 `config.yaml`。
- 构建脚本固定 Linux amd64 和 Python 3.14，清理 Markdown、缓存和字节码。
- Release 工作流的 Release Job 必须依赖 `python-bundle` Job。
- 新资产进入 GitHub Release 和 `SHA256SUMS.txt`。
- 代码版本和 OpenAPI 版本为 7.3.2。
- Release Notes 包含解压、配置和 `python3.14 run.py` 调用方法。

Action 在打包后使用一个全新的 `python:3.14-slim` 容器进行无网络验证：

- 挂载或复制已生成的发布目录。
- 使用 `--network none`，确保运行时不能联网安装依赖。
- 启动 `python3.14 run.py`。
- 在容器内部通过回环地址请求 `/health` 并要求返回 200。
- 确认运行过程中没有调用 `pip`。

本地验收运行全量 pytest、Black、isort、Linux/Windows mypy、Pylint、Compose 配置解析和 bundle 目录审计。Docker daemon 不可用时，本地容器验证可跳过，但 Tag Release Action 的无网络 bundle 冒烟是发布前置门禁，失败则不创建 Release。

## Release 工作流

- 新增 `python-bundle` Job，依赖 `validate`。
- `release` Job 同时依赖 `validate`、`windows`、`docker` 和 `python-bundle`。
- `python-bundle` 上传 `edge-tts-server-python314-linux-amd64.tar.gz`。
- `release` 下载该资产并将它加入 `SHA256SUMS.txt`。
- GitHub Release 上传三个可下载包和一个校验文件：Windows ZIP、离线 Docker tar、纯净 Python tar、`SHA256SUMS.txt`。
- Release Notes 说明运行包要求 glibc Linux amd64 和系统 Python 3.14，不支持 Alpine/musl。

## 兼容性与非目标

- 运行包目标为 glibc Linux amd64，适用于常见 Ubuntu、Debian、CentOS 和 1Panel 主机。
- 运行包不包含 Python 解释器，服务器必须提供 Python 3.14。
- 不支持 Alpine/musl、Linux arm64 或 Windows Python 目录包；Windows 继续使用独立 EXE。
- 不改变 HTTP API、配置字段、鉴权、资源限制或 Docker/Compose 契约。
- 不引入自动更新、流式响应、数据库或额外服务管理脚本。

## 发布执行修订

首次执行使用 `v7.3.2`，bundle 构建成功，但冒烟步骤把 tar 解压到构建阶段遗留的同名 root-owned staging 目录，因文件已存在而失败；Release Job 被依赖门禁正确跳过。失败 Tag 保留用于审计，不覆盖、不移动。工作流增加“解压前删除 staging 目录”的回归测试和最小修复，正式完整发布版本顺延为 `v7.3.3`。
