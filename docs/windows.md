# Windows x64 本地构建与运行

GitHub Release 只提供一个 Windows ZIP，内含单文件 EXE 和配置示例。本页用于需要从源码复现产物的维护者。正式发布固定使用 Python 3.14 x64 和当前仓库的 `edge-tts-server.spec`。

## 构建要求

- 64 位 Windows 10/11 或 Windows Server
- Python 3.14 x64
- PowerShell 5.1 或 7+
- 首次安装依赖时可访问 PyPI

目标电脑无需安装 Python，也无需联网下载运行库；内置 FFmpeg 支持 Edge/MiMo 的 MP3/WAV 转换。语音合成本身仍需连接所选上游。

## 创建虚拟环境

在仓库根目录运行：

```powershell
py -3.14 -m venv .venv
# 已确认当前 python 是 3.14 时也可执行：python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
pip install PyInstaller
```

如果执行策略阻止激活，可只在当前 PowerShell 进程放开：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## 执行 PyInstaller 构建

推荐使用仓库脚本，它会清理受控产物目录、运行 PyInstaller、启动 EXE 检查 `/health`，最后创建只含 EXE 和配置示例的 ZIP：

```powershell
.\build_windows_release.ps1 -Python .\.venv\Scripts\python.exe
```

只在排查打包问题时跳过启动冒烟：

```powershell
.\build_windows_release.ps1 -Python .\.venv\Scripts\python.exe -SkipSmokeTest
```

规范文件会显式收集 FastAPI、Pydantic、Uvicorn、imageio-ffmpeg 和服务包的动态模块。不要用系统中未安装完整运行依赖的 Python 调用 PyInstaller。

MiMo 使用前在 `config.yaml` 设置 `mimo_api_key`。客户端继续使用独立的 `api_key` 和 `X-API-Key`，通过 `/v1/models` 查看能力并在 `/v1/tts` 的 `model` 中选择 `edge-tts` 或 `mimo-v2-tts`。

## 产物位置

- 单文件 EXE：`dist/edge-tts-server.exe`
- Release 文件：`releases/windows/edge-tts-windows-x64.zip`

ZIP 内只包含两个文件：

```text
edge-tts-windows-x64.exe
config.example.yaml
```

使用时只需要：

1. 下载并解压 `edge-tts-windows-x64.zip`。
2. 将 `config.example.yaml` 复制为 `config.yaml` 并修改 `api_key`。
3. 双击 `edge-tts-windows-x64.exe`。

没有 `config.yaml` 时，程序会自动创建一份随机 API Key 配置。目标电脑无需安装 Python、Docker 或其他依赖。

## 手工健康检查

把 ZIP 中的 `config.example.yaml` 复制为 `config.yaml`，将 `host` 改为 `127.0.0.1` 并替换 Key，然后双击 `edge-tts-windows-x64.exe`。另开 PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:5050/health
```

应返回 `status = ok`。然后使用文档中的 curl、Python 或 PowerShell 示例调用 API。确认生成音频后关闭服务窗口或按 `Ctrl+C`。构建脚本会自动结束本次冒烟测试启动的 EXE，避免文件被锁定。

## 音色、字幕与代理

服务运行后可带 `X-API-Key` 调用 `/v1/voices?locale=zh-CN` 查询音色；用返回的 `name` 调用 `/v1/tts/bundle`，下载的 ZIP 固定包含 `speech.mp3` 和 `speech.srt`，`boundary` 可选 `WordBoundary` 或 `SentenceBoundary`。

Windows 独立包同样支持在 `config.yaml` 设置全局 `proxy`、`upstream_connect_timeout_seconds` 和 `upstream_receive_timeout_seconds`。代理只用于服务访问微软上游，不是请求字段；修改配置后重启 EXE，且不要公开含凭据的代理 URL。

## 发布环境

`.github/workflows/release.yml` 在 `windows-2022` 上使用 Python 3.14 x64 重建并冒烟测试。只有 `vX.Y.Z` Tag 与 `src/edge_tts/version.py` 完全一致，且测试、格式、类型、lint 全部通过时，Windows ZIP 才会进入 GitHub Release；最终 SHA-256 位于同一 Release 的 `SHA256SUMS.txt`。
