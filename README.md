# Edge TTS 7.3.4

本项目提供 Microsoft Edge 在线语音合成的 Python 库、命令行工具，以及带 API Key 鉴权的 HTTP 服务。HTTP 服务基于 FastAPI + Uvicorn，适合本地程序调用，也可通过 Docker 部署到 Linux/1Panel。

> 语音合成依赖微软在线服务。Windows Release 无需安装 Python、无需联网安装依赖，但生成语音时仍需能够访问上游 TTS 服务。

## 快速入口

- [完整 API 调用文档](docs/api.md)：字段、限制、错误码及 curl、Python、JavaScript、PowerShell 示例
- [Docker 部署](docs/docker.md)：在线镜像、Compose、离线 amd64 镜像与安全加固
- [1Panel 部署](docs/1panel.md)：仅维护 Docker 部署方式
- [Windows 本地构建](docs/windows.md)：Python 3.14、PyInstaller 和产物验证

## HTTP 服务快速开始

### 源码启动

需要 Python 3.10 或更高版本，推荐 Python 3.14：

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cp config.example.yaml config.yaml
python -m edge_tts_server --config config.yaml
```

Windows PowerShell 复制配置可使用：

```powershell
Copy-Item .\config.example.yaml .\config.yaml
```

启动前务必把 `config.yaml` 中的 `api_key` 换成长随机字符串。健康检查地址是 `http://127.0.0.1:5050/health`。

### Windows 双击运行

从 GitHub Release 下载 `edge-tts-server-windows-x64.zip`，解压后双击 `edge-tts-server.exe`。首次启动会在 EXE 同目录创建 `config.yaml` 和随机 Key；目标电脑无需安装 Python。压缩包内含中文说明和 PowerShell 调用示例。

### Linux Docker

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml，替换 api_key
echo "EDGE_TTS_IMAGE_TAG=7.3.4" > .env
docker compose -f compose.yaml up -d
curl http://127.0.0.1:5050/health
```

镜像支持 `linux/amd64` 和 `linux/arm64`。Compose 已配置只读文件系统、非 root、能力清空、自定义 DNS 和优雅停止。详见 [Docker 文档](docs/docker.md)。

### 离线部署

amd64 服务器可从 Release 下载：

- `edge-tts-server-linux-amd64.tar.gz`
- `SHA256SUMS.txt`

校验后使用 `docker load` 导入，无需服务器访问 GHCR。完整流程见 [Docker 文档](docs/docker.md) 和 [1Panel 文档](docs/1panel.md)。

## 接口快速调用

`POST /v1/tts` 必须带 `X-API-Key`，成功响应是 MP3：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural"}' \
  --output speech.mp3
```

每个响应都包含服务端生成的 `X-Request-ID`。默认限制、全部错误码以及更多语言示例见 [API 文档](docs/api.md)。

## Swagger

Swagger 默认关闭。将 `config.yaml` 中的 `docs_enabled` 改为 `true` 并重启后可访问：

- `http://127.0.0.1:5050/docs`
- `http://127.0.0.1:5050/openapi.json`

Swagger 的 Authorize 按钮使用 `X-API-Key`；浏览器不会持久保存授权。公网环境不需要时请保持关闭。

## config.yaml

仓库根目录的 [config.example.yaml](config.example.yaml) 是完整示例：

```yaml
api_key: "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"
host: "0.0.0.0"
port: 5050
max_text_length: 5000
max_request_bytes: 65536
max_concurrent_requests: 4
request_timeout_seconds: 120
max_audio_bytes: 20971520
docs_enabled: false
```

旧版仅含 `api_key`、`host`、`port` 的配置仍可使用，其余字段自动采用上述默认值。本机直接运行建议把 `host` 改为 `127.0.0.1`；Docker 容器内必须使用 `0.0.0.0`。

## Python 库与命令行

从 PyPI 安装：

```bash
pip install edge-tts
```

生成音频和字幕：

```bash
edge-tts --text "Hello, world!" --write-media hello.mp3 --write-subtitles hello.srt
```

立即播放：

```bash
edge-playback --text "Hello, world!"
```

除 Windows 外，`edge-playback` 需要安装 [mpv](https://mpv.io/)。列出可用声音：

```bash
edge-tts --list-voices
```

调整语速、音量和音调：

```bash
edge-tts --rate=-20% --volume=+0% --pitch=+5Hz \
  --text "Hello, world!" --write-media hello.mp3
```

Python 调用示例位于 [examples](examples)。自定义 SSML 已不再支持，因为上游服务只接受 Microsoft Edge 能生成的 SSML；可用的 prosody 调节已通过 `rate`、`volume` 和 `pitch` 提供。
