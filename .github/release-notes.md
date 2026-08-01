# Edge TTS HTTP Server __VERSION__

本次 Release 提供 Windows x64 独立包、GHCR 双架构镜像和 Linux amd64 离线镜像。服务使用 FastAPI + Uvicorn，保留 `POST /v1/tts`、`GET /health`、`X-API-Key` 与稳定错误 JSON。

## Release 资产

- `edge-tts-server-windows-x64.zip`：双击运行，目标电脑无需安装 Python或联网安装依赖。
- `edge-tts-server-linux-amd64.tar.gz`：可上传到离线 Docker/1Panel 服务器。
- `SHA256SUMS.txt`：上述两个资产的 SHA-256。
- 在线镜像：`__IMAGE__:__VERSION__` 与 `__IMAGE__:latest`，支持 `linux/amd64`、`linux/arm64`。

下载全部资产后校验：

```bash
sha256sum -c SHA256SUMS.txt
```

只校验 Linux tar：

```bash
grep 'edge-tts-server-linux-amd64.tar.gz' SHA256SUMS.txt | sha256sum -c -
```

## Windows 使用

1. 解压 `edge-tts-server-windows-x64.zip`。
2. 双击 `edge-tts-server.exe`。
3. 首次启动会在 EXE 同目录生成 `config.yaml` 和随机 API Key。
4. 在同目录运行 `call-example.ps1`，生成 `speech.mp3`。

压缩包内已包含 Python 解释器、运行库、配置示例、PowerShell 示例和中文说明。语音合成时仍需访问微软 TTS 上游。

## config.yaml

Docker/服务器应使用：

```yaml
api_key: "替换成足够长的随机密钥"
host: "0.0.0.0"
port: 5050
max_text_length: 5000
max_request_bytes: 65536
max_concurrent_requests: 4
request_timeout_seconds: 120
max_audio_bytes: 20971520
docs_enabled: false
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

将 `edge-tts-server-linux-amd64.tar.gz`、`SHA256SUMS.txt`、`compose.yaml` 和 `config.yaml` 上传到服务器。校验后导入：

```bash
grep 'edge-tts-server-linux-amd64.tar.gz' SHA256SUMS.txt | sha256sum -c -
gzip -dc edge-tts-server-linux-amd64.tar.gz | docker load
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
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","rate":"+0%"}' \
  --output speech.mp3
```

成功响应为 `audio/mpeg`，所有响应含 `X-Request-ID`。并发已满返回 `429 too_many_requests` 和 `Retry-After: 1`；请求、文本、音频超限返回对应 `413`；上游超时返回 `504 upstream_timeout`。

如需 Swagger，将 `docs_enabled` 设为 `true` 并重启，然后访问 `/docs` 或 `/openapi.json`。Swagger 使用公共 CDN，API Key 不持久化。

## 升级、回滚与安全

生产环境固定 `__IMAGE__:__VERSION__`。升级前保存 `.env`，升级后同时验证 `/health` 和 `/v1/tts`；失败时恢复旧 `EDGE_TTS_IMAGE_TAG` 并再次 `docker compose -f compose.yaml up -d`。

公网必须通过反向代理或负载均衡启用 HTTPS，并禁止公网直连 `5050`。API Key 不能加密传输，可在代理层增加按 IP 的频率限制。

## Changes
