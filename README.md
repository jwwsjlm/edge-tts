# Edge TTS + Xiaomi MiMo 7.5.3

本项目提供 Microsoft Edge TTS 与 Xiaomi MiMo V2.5 的统一非流式 HTTP 服务。`POST /v1/tts` 通过 `model` 自由选择 `edge-tts` 或 `mimo-v2-tts`，支持完整 MP3/WAV 返回；MiMo 还支持预置音色、音色设计和音色克隆。原 Edge 调用省略 `model` 时保持兼容。

> Windows Release 无需安装 Python 或联网安装依赖；实际合成仍需连接微软 Edge TTS 上游。聊天、工单和仓库中不要公开真实 API Key。

## 快速入口

- [完整 API 文档](docs/api.md)：音色筛选、MP3、字幕 Bundle、字段、限制、错误码和多语言示例
- [Docker 部署](docs/docker.md)：在线双架构镜像、Compose、离线 amd64 镜像
- [1Panel 部署](docs/1panel.md)：只维护 Docker-only 部署
- [Windows 构建与使用](docs/windows.md)：Python 3.14、PyInstaller、双击运行

## Release 文件说明

每个正式 Release 只发布下面四个下载文件：

| 文件 | 适用环境 | 用法 |
| --- | --- | --- |
| `edge-tts-windows-x64.zip` | Windows x64 | 解压、复制配置、双击 EXE |
| `edge-tts-linux-amd64-python314.tar.gz` | Linux amd64 + Python 3.14 | 解压后运行 `python3.14 run.py` |
| `edge-tts-linux-amd64-docker-offline.tar.gz` | Linux amd64 Docker / 1Panel | 使用 `docker load` 导入 |
| `SHA256SUMS.txt` | 所有下载文件 | 校验文件完整性 |

Linux arm64 不提供离线下载包，直接拉取 GHCR 多架构镜像。Release 不单独发布裸 EXE、重复 Windows 压缩包、脚本快捷方式或源码备份。

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

也可以只用项目根目录的单脚本启动，不需要 Docker：

```bash
python run.py --config config.yaml
```

这个脚本启动统一服务；原有 Edge TTS 请求保持兼容，MiMo 通过同一个 `/v1/tts` 接口的 `model` 字段选择。模型切换和本地文件输出示例见 `examples/multi_model_tts.py`。

### Windows 双击运行

从 GitHub Release 下载 `edge-tts-windows-x64.zip`，解压后将 `config.example.yaml` 复制为 `config.yaml`，修改 `api_key`，再双击 `edge-tts-windows-x64.exe`。程序会读取 EXE 同目录的 `config.yaml`；没有配置文件时会自动创建配置和随机 Key。无需 Python、Docker 或额外依赖。

### Linux Docker

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml，替换 api_key
echo "EDGE_TTS_IMAGE_TAG=7.5.3" > .env
docker compose -f compose.yaml up -d
curl http://127.0.0.1:5050/health
```

镜像支持 `linux/amd64`、`linux/arm64`。Compose 已启用非 root、只读文件系统、能力清空、自定义 DNS 和优雅停止。

### 离线部署

amd64 服务器从 Release 下载 `edge-tts-linux-amd64-docker-offline.tar.gz` 与 `SHA256SUMS.txt`，校验后 `docker load`。详见 [Docker](docs/docker.md) 与 [1Panel](docs/1panel.md) 文档。

## 多模型 API 快速调用

```bash
curl http://127.0.0.1:5050/v1/models -H "X-API-Key: YOUR_KEY"
```

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
  -d '{"model":"edge-tts","text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","response_format":"mp3","rate":"+0%","volume":"+0%","pitch":"+0Hz"}' \
  --output speech.mp3
```

MiMo 预置音色（先在 `config.yaml` 配置 `mimo_api_key`）：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" \
  -d '{"model":"mimo-v2-tts","mimo_mode":"preset","text":"你好，世界","voice":"冰糖","response_format":"wav"}' \
  --output speech.wav
```

完整 Python 脚本位于 `examples/multi_model_tts.py`，可通过 `--model`、`--mimo-mode`、`--voice-description` 或 `--reference-audio` 使用所有模式。

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

Swagger 只描述本项目的 `/v1/models`、`/v1/voices`、`/v1/tts`、`/v1/tts/bundle`，不提供 OpenAI 兼容接口。Authorize 使用 `X-API-Key`，不会持久保存授权。

## config.yaml

[config.example.yaml](config.example.yaml) 是完整示例：

```yaml
api_key: "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"
host: "0.0.0.0"
port: 5050
max_text_length: 5000
max_request_bytes: 15728640
max_concurrent_requests: 4
request_timeout_seconds: 120
max_audio_bytes: 67108864
docs_enabled: false
voices_cache_ttl_seconds: 3600
proxy: null
upstream_connect_timeout_seconds: 10
upstream_receive_timeout_seconds: 60
mimo_api_key: null
mimo_base_url: "https://api.xiaomimimo.com/v1"
mimo_request_timeout_seconds: 120
max_reference_audio_bytes: 10485760
mimo_recommended_max_text_length: 600
```

`mimo_api_key: null` 时 Edge TTS 完全可用，MiMo 请求返回 `503 provider_not_configured`。MiMo 密钥仅用于访问上游，不等同于客户端使用的 `api_key`。
MiMo 的 3000 字符是上游硬限制；`mimo_recommended_max_text_length` 默认 600，仅用于生成稳定性提示，不会自动拒绝请求。

## 模型能力

| model | 模式 | 音色 | 输出 | 字幕 |
| --- | --- | --- | --- | --- |
| `edge-tts` | `preset` | Microsoft 完整音色名 | MP3/WAV | `/v1/tts/bundle` 支持 SRT |
| `mimo-v2-tts` | `preset` | `mimo_default`、`冰糖`、`茉莉`、`苏打`、`白桦`、`Mia`、`Chloe`、`Milo`、`Dean` | MP3/WAV | 不支持 |
| `mimo-v2-tts` | `design` | `voice_description` 描述 | MP3/WAV | 不支持 |
| `mimo-v2-tts` | `clone` | WAV/MP3 Base64 参考音频 | MP3/WAV | 不支持 |

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
