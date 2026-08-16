# Edge TTS 7.4.1

本项目提供 Microsoft Edge 在线语音合成的 Python 库、命令行工具，以及带 `X-API-Key` 鉴权的非流式 HTTP 服务。服务基于 FastAPI + Uvicorn，可查询微软音色、生成完整 MP3，也可一次下载 MP3 与 SRT 字幕 ZIP；Linux 推荐 Docker/1Panel，Windows 可直接双击独立 EXE。

> Windows Release 无需安装 Python 或联网安装依赖；实际合成仍需连接微软 Edge TTS 上游。聊天、工单和仓库中不要公开真实 API Key。

## 快速入口

- [完整 API 文档](docs/api.md)：音色筛选、MP3、字幕 Bundle、字段、限制、错误码和多语言示例
- [Docker 部署](docs/docker.md)：在线双架构镜像、Compose、离线 amd64 镜像
- [1Panel 部署](docs/1panel.md)：只维护 Docker-only 部署
- [Windows 构建与使用](docs/windows.md)：Python 3.14、PyInstaller、双击运行

## 启动服务

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

Windows PowerShell 使用 `Copy-Item .\config.example.yaml .\config.yaml`。启动前把 `api_key` 换成长随机字符串；健康检查为 `http://127.0.0.1:5050/health`。

### Windows 双击运行

从 GitHub Release 下载 `edge-tts-server-windows-x64.zip`，解压后双击 `edge-tts-server.exe`。首次启动会在 EXE 同目录创建 `config.yaml` 和随机 Key，包内含配置示例、中文说明与 PowerShell 调用示例。

### Linux Docker

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml，替换 api_key
echo "EDGE_TTS_IMAGE_TAG=7.4.1" > .env
docker compose -f compose.yaml up -d
curl http://127.0.0.1:5050/health
```

镜像支持 `linux/amd64`、`linux/arm64`。Compose 已启用非 root、只读文件系统、能力清空、自定义 DNS 和优雅停止。

### 离线部署

amd64 服务器从 Release 下载 `edge-tts-server-linux-amd64.tar.gz` 与 `SHA256SUMS.txt`，校验后 `docker load`。详见 [Docker](docs/docker.md) 与 [1Panel](docs/1panel.md) 文档。

## 三个 API 快速调用

除 `/health` 外，以下接口都需要 `X-API-Key`，并返回 `X-Request-ID`。

先查询中文女声：

```bash
curl "http://127.0.0.1:5050/v1/voices?locale=zh-CN&gender=Female" \
  -H "X-API-Key: YOUR_KEY"
```

把返回的 `name` 用于完整 MP3 合成：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" \
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","rate":"+0%","volume":"+0%","pitch":"+0Hz"}' \
  --output speech.mp3
```

一次生成 MP3 和 SentenceBoundary 字幕：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts/bundle \
  -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" \
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","boundary":"SentenceBoundary"}' \
  --output speech-bundle.zip
```

ZIP 固定只含 `speech.mp3` 与 `speech.srt`。所有音频和 ZIP 都在服务端受限内存中完整生成后一次性返回，不使用 HTTP 流式传输或 Base64。

## Swagger

将 `docs_enabled` 改为 `true` 并重启，然后打开：

- `http://127.0.0.1:5050/docs`
- `http://127.0.0.1:5050/openapi.json`

Swagger 只描述本项目的 `/v1/voices`、`/v1/tts`、`/v1/tts/bundle`，不提供 OpenAI 兼容接口。Authorize 使用 `X-API-Key`，不会持久保存授权。

## config.yaml

[config.example.yaml](config.example.yaml) 是完整示例：

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
voices_cache_ttl_seconds: 3600
proxy: null
upstream_connect_timeout_seconds: 10
upstream_receive_timeout_seconds: 60
```

`proxy` 是服务端全局 HTTP/HTTPS 代理，只能在配置中设置，音色查询和合成都使用它；不要把含账号密码的代理地址写入客户端请求。`request_timeout_seconds` 限制整个请求，另外两个 upstream 超时分别限制连接和接收。旧版三字段配置仍兼容；本机建议 `127.0.0.1`，Docker 必须 `0.0.0.0`。

## 与原版能力对应

| rany2/edge-tts 原版能力 | HTTP 服务 |
| --- | --- |
| `--list-voices` / `list_voices()` | `GET /v1/voices`，支持 locale/language/gender 筛选与缓存 |
| voice、rate、volume、pitch | 两个合成接口的同名字段 |
| Word/Sentence Boundary 与 SRT | `POST /v1/tts/bundle` 的 `boundary` 和 `speech.srt` |
| proxy、连接/接收超时 | 仅服务端 `config.yaml` 全局配置 |
| MP3 音频 | `POST /v1/tts` 或 Bundle 中的 `speech.mp3` |

播放器、客户端文件读取、底层 connector 和实时 chunk stream 不作为服务端 HTTP 接口。

## Python 库与命令行

```bash
pip install edge-tts
edge-tts --list-voices
edge-tts --text "Hello, world!" --write-media hello.mp3 --write-subtitles hello.srt
edge-tts --rate=-20% --volume=+0% --pitch=+5Hz --text "Hello" --write-media hello.mp3
```

`edge-playback` 可立即播放；除 Windows 外需要 [mpv](https://mpv.io/)。Python 示例位于 [examples](examples)。自定义 SSML 已被上游限制，可调 prosody 已通过 `rate`、`volume`、`pitch` 提供。
