# Edge TTS + Xiaomi MiMo HTTP Server __VERSION__

本次 Release 提供 Windows x64 ZIP、Linux amd64 Python 3.14 纯净运行包、GHCR 双架构镜像和 Linux amd64 Docker 离线镜像。服务使用 FastAPI + Uvicorn，`POST /v1/tts` 可通过 `model` 选择 Edge TTS 或 Xiaomi MiMo V2.5，并支持完整 MP3/WAV。MiMo 包含预置音色、音色设计和音色克隆；不是 OpenAI API，也不使用 HTTP 流式传输。

## 先按设备选择下载文件

- **Windows 本地运行**：`edge-tts-windows-x64.zip`。解压后将 `config.example.yaml` 复制为 `config.yaml`，修改 Key，再双击 `edge-tts-windows-x64.exe`。无需 Python、Docker 或额外依赖。
- **Linux 服务器，已有 Python 3.14**：`edge-tts-linux-amd64-python314.tar.gz`。这是纯净运行包，依赖位于 `libs/`；不适用于 Windows。
- **Linux 服务器 / 1Panel / Docker 离线部署**：`edge-tts-linux-amd64-docker-offline.tar.gz`。这是 Docker 离线镜像；不适用于 Windows，不能直接解压运行。
- **核对下载完整性**：`SHA256SUMS.txt`。
- 在线镜像：`__IMAGE__:__VERSION__` 与 `__IMAGE__:latest`，支持 `linux/amd64`、`linux/arm64`。

下载全部资产后校验：

```bash
sha256sum -c SHA256SUMS.txt
```

只校验 Linux tar：

```bash
grep 'edge-tts-linux-amd64-docker-offline.tar.gz' SHA256SUMS.txt | sha256sum -c -
```

## Windows 使用

1. 下载并解压 `edge-tts-windows-x64.zip`。
2. 将 `config.example.yaml` 复制为 `config.yaml`，修改 Key。
3. 双击 `edge-tts-windows-x64.exe`，默认监听 `http://127.0.0.1:5050`。
4. 如果开启 `docs_enabled: true`，访问 `http://127.0.0.1:5050/docs`。

ZIP 内只有 EXE 和配置示例，不需要 Python、Docker 或其他启动脚本。语音合成时仍需访问微软 TTS 上游。

## Linux Python 纯净运行包

该包适用于使用 glibc 的 Linux amd64 主机，服务器必须已安装 Python 3.14。不支持 Alpine/musl，也不包含 Python 解释器。运行时无需安装依赖，压缩包内不包含 Markdown 或 shell 启动脚本。

```bash
tar -xzf edge-tts-linux-amd64-python314.tar.gz
cd edge-tts-linux-amd64-python314
cp config.example.yaml config.yaml
# 编辑 config.yaml，替换 api_key
python3.14 run.py
```

首次直接运行且没有 `config.yaml` 时，服务也会在运行包目录创建带随机 API Key 的配置。生产环境应先检查并保存 Key，再通过反向代理开放服务。

## config.yaml

Docker/服务器应使用：

```yaml
api_key: "替换成足够长的随机密钥"
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
```

`api_key` 是客户端 `X-API-Key` 请求头使用的密钥。不要提交真实配置；修改后需重启服务。

从 Source code 准备配置：

```bash
cp config.example.yaml config.yaml
sudo chown 10001:10001 config.yaml
chmod 600 config.yaml
```

## 在线 Docker Compose

```bash
docker pull __IMAGE__:__VERSION__
echo 'EDGE_TTS_IMAGE_TAG=__VERSION__' > .env
docker compose -f compose.yaml up -d
docker compose -f compose.yaml ps
curl http://127.0.0.1:5050/health
```

`compose.yaml` 已启用非 root、只读文件系统、`tmpfs /tmp`、`cap_drop: ALL`、`no-new-privileges`、`init`、优雅停止、`pull_policy: missing`，以及 DNS `223.5.5.5`、`119.29.29.29`。查看日志：

```bash
docker compose -f compose.yaml logs -f --tail=200
```

## 在线 docker run

```bash
docker run -d \
  --name edge-tts --restart unless-stopped \
  --init --read-only --tmpfs /tmp:size=64m,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges \
  --dns 223.5.5.5 --dns 119.29.29.29 \
  -p 5050:5050 \
  --mount type=bind,source="$(pwd)/config.yaml",target=/config/config.yaml,readonly \
  __IMAGE__:__VERSION__
```

## 离线 Docker / 1Panel

将 `edge-tts-linux-amd64-docker-offline.tar.gz`、`SHA256SUMS.txt`、`compose.yaml` 和 `config.yaml` 上传到服务器。校验后导入：

```bash
grep 'edge-tts-linux-amd64-docker-offline.tar.gz' SHA256SUMS.txt | sha256sum -c -
gzip -dc edge-tts-linux-amd64-docker-offline.tar.gz | docker load
docker image inspect __IMAGE__:__VERSION__ >/dev/null
echo 'EDGE_TTS_IMAGE_TAG=__VERSION__' > .env
docker compose -f compose.yaml up -d
```

离线 tar 仅支持 amd64。1Panel 中请使用容器/Compose 部署，不使用 Python 运行环境；反向代理填 `http://127.0.0.1:5050`，并启用 HTTPS。

## API 快速调用

健康检查无需 Key：

```bash
curl http://127.0.0.1:5050/health
```

调用 `POST /v1/tts` 并保存 MP3：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: 替换成config中的密钥" \
  -d '{"model":"edge-tts","text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","rate":"+0%"}' \
  --output speech.mp3
```

MiMo 配置 `mimo_api_key` 后可调用：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" -H "X-API-Key: 替换成config中的密钥" \
  -d '{"model":"mimo-v2-tts","mimo_mode":"preset","voice":"冰糖","text":"你好，世界","response_format":"wav"}' \
  --output speech.wav
```

成功响应按 `response_format` 返回 `audio/mpeg` 或 `audio/wav`，所有响应含 `X-Request-ID`。并发已满返回 `429 too_many_requests` 和 `Retry-After: 1`；请求、文本、音频超限返回对应 `413`；上游超时返回 `504 upstream_timeout`。

如需 Swagger，将 `docs_enabled` 设为 `true` 并重启，然后访问 `/docs` 或 `/openapi.json`。Swagger 使用公共 CDN，API Key 不持久化。

查询中文音色并下载 MP3 + SRT：

```bash
curl "http://127.0.0.1:5050/v1/voices?locale=zh-CN" \
  -H "X-API-Key: 替换成config中的密钥"
curl -X POST http://127.0.0.1:5050/v1/tts/bundle \
  -H "Content-Type: application/json" -H "X-API-Key: 替换成config中的密钥" \
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","boundary":"SentenceBoundary"}' \
  --output speech-bundle.zip
unzip -l speech-bundle.zip
```

ZIP 固定只含 `speech.mp3`、`speech.srt`，两者来自同一次上游合成，不使用 HTTP 流式传输。`proxy`、`upstream_connect_timeout_seconds`、`upstream_receive_timeout_seconds` 只能在服务端 `config.yaml` 设置，音色查询和合成共用代理；凭据不会写入安全访问日志。

## 升级、回滚与安全

生产环境固定 `__IMAGE__:__VERSION__`。升级前保存 `.env`，升级后同时验证 `/health` 和 `/v1/tts`；失败时恢复旧 `EDGE_TTS_IMAGE_TAG` 并再次 `docker compose -f compose.yaml up -d`。

公网必须通过反向代理或负载均衡启用 HTTPS，并禁止公网直连 `5050`。API Key 不能加密传输，可在代理层增加按 IP 的频率限制。

## Changes
