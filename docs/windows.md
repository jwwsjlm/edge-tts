# Windows x64 本地构建与运行

GitHub Release 已提供可双击运行的 x64 ZIP。本页用于需要从源码复现产物的维护者。正式发布固定使用 Python 3.14 x64 和当前仓库的 `edge-tts-server.spec`。

## 构建要求

- 64 位 Windows 10/11 或 Windows Server
- Python 3.14 x64
- PowerShell 5.1 或 7+
- 首次安装依赖时可访问 PyPI

目标电脑无需安装 Python，也无需联网下载运行库；语音合成本身仍需连接微软 TTS 上游。

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

推荐使用仓库脚本，它会清理受控产物目录、运行 PyInstaller、启动 EXE 检查 `/health`，最后创建 ZIP：

```powershell
.\build_windows_release.ps1 -Python .\.venv\Scripts\python.exe
```

只在排查打包问题时跳过启动冒烟：

```powershell
.\build_windows_release.ps1 -Python .\.venv\Scripts\python.exe -SkipSmokeTest
```

规范文件会显式收集 FastAPI、Pydantic、Uvicorn 和服务包的动态模块。不要用系统中未安装完整运行依赖的 Python 调用 PyInstaller。

## 产物位置

- 单文件 EXE：`dist/edge-tts-server.exe`
- 可分发目录：`releases/windows/edge-tts-server-windows-x64/`
- 最终压缩包：`releases/windows/edge-tts-server-windows-x64.zip`

ZIP 包含：

- `edge-tts-server.exe`（Python 解释器和运行库已封装）
- `config.example.yaml`
- `call-example.ps1`
- `README.txt`

不要把本机生成的 `config.yaml` 或真实 API Key 放进 ZIP。

## 手工健康检查

把 `config.example.yaml` 复制为 `config.yaml`，将 `host` 改为 `127.0.0.1` 并替换 Key，然后双击 EXE。另开 PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:5050/health
```

应返回 `status = ok`。随后执行压缩包中的调用示例：

```powershell
powershell -ExecutionPolicy Bypass -File .\call-example.ps1 -Text "Windows 构建测试"
```

确认生成 `speech.mp3` 后关闭服务窗口或按 `Ctrl+C`。构建脚本会自动结束由本次产物启动的 PyInstaller 子进程，避免 EXE 被锁定。

## 发布环境

`.github/workflows/release.yml` 在 `windows-2022` 上使用 Python 3.14 x64 重建并冒烟测试。只有 `vX.Y.Z` Tag 与 `src/edge_tts/version.py` 完全一致，且测试、格式、类型、lint 全部通过时，Windows ZIP 才会进入 GitHub Release；最终 SHA-256 位于同一 Release 的 `SHA256SUMS.txt`。
