# Python 3.14 构建与发布升级设计

## 目标

将项目所有维护中的构建、质量检查、Docker 和 Windows 发布环境从 Python 3.12 或浮动 `3.x` 固定到 Python 3.14，并发布补丁版本 7.3.1。Python 库的最低兼容版本继续保持 3.10。

## 范围

### GitHub Actions

- Release 工作流的验证 Job 使用 Python 3.14。
- Windows x64 PyInstaller Job 使用 Python 3.14 x64。
- Code Quality 工作流从浮动 `3.x` 固定为 Python 3.14，避免 GitHub Runner 默认版本变化造成不可重复结果。
- Release Tag 继续只接受 `vX.Y.Z`，且必须等于代码版本。

### Docker

- Dockerfile 的 builder 和 runtime 均改为 `python:3.14-slim`。
- 保留多阶段构建、非 root UID/GID 10001、只读文件系统兼容性、健康检查和现有入口命令。
- Compose 文件不改变服务契约、端口、DNS 或安全限制。

### 版本与兼容性

- `src/edge_tts/version.py` 更新为 `7.3.1`。
- `setup.cfg` 的 `python_requires = >=3.10` 保持不变。
- FastAPI、Pydantic、Uvicorn 和其他依赖范围保持不变；安装测试将验证其 Python 3.14 可用性。

### 文档

- README、Windows 构建文档和当前发布说明中的推荐构建版本更新为 Python 3.14。
- 用户可执行命令更新为 `py -3.14` 或等价的 Python 3.14 命令。
- 历史 `docs/superpowers/plans` 与旧设计文档保留原值，作为当时决策记录，不参与当前文档一致性检查。

## 测试策略

测试先行增加静态发布契约，要求：

- Dockerfile 的两个阶段均使用 `python:3.14-slim`。
- Release 和 Code Quality Action 明确固定 Python 3.14。
- Windows 构建文档使用 Python 3.14。
- 代码版本为 7.3.1。
- 维护中的运行与部署文档不再出现 Python 3.12。

实现后运行：

- 全量 pytest。
- Black、isort、mypy 和 Pylint。
- Linux/Windows 双平台 mypy 回归检查。
- Docker Compose 配置解析。
- 本地 Python 3.14 PyInstaller 构建和 Windows EXE 健康检查。
- Docker daemon 可用时执行本地镜像构建；不可用时由 Release Action 的原生 amd64、离线导入和双架构发布门禁验证。

## 发布流程

1. 提交并推送 Python 3.14 升级到 `master`。
2. 确认 master 的 Code Quality Action 通过。
3. 创建并推送 `v7.3.1`。
4. 观察 Release Action 直到验证、Windows、Docker 和 Release 全部成功。
5. 确认 Release 包含 Windows ZIP、Linux amd64 离线 tar 和 `SHA256SUMS.txt`，GHCR 包含 `7.3.1` 与 `latest`。

## 非目标

- 不停止支持 Python 3.10–3.13。
- 不增加 Python 版本矩阵。
- 不改变 HTTP API、配置字段、鉴权、资源限制或错误码。
- 不覆盖或删除已经成功发布的 7.3.0。
