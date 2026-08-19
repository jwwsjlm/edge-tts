# Docker 部署 Edge TTS HTTP 服务

发布镜像：`ghcr.io/jwwsjlm/edge-tts:7.5.1`，支持 `linux/amd64` 和 `linux/arm64`。服务器建议固定版本，不要长期依赖 `latest`。离线 Release 资产仅提供 `linux/amd64`。

## 准备 config.yaml

```bash
cp config.example.yaml config.yaml
sudo chown 10001:10001 config.yaml
chmod 600 config.yaml
```

镜像内固定使用 UID/GID `10001`。Linux bind mount 会保留宿主机权限，因此需把配置所有者设为 `10001:10001`；否则非 root 容器可能无权读取 API Key。Windows Docker Desktop 不执行这条 `chown`。

Windows PowerShell：

```powershell
Copy-Item .\config.example.yaml .\config.yaml
```

完整配置：

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

Docker 内 `host` 必须为 `0.0.0.0`。客户端通过 `X-API-Key` 提交密钥；不要提交真实 `config.yaml`。修改配置后需重启容器。

## 在线 Compose 部署（推荐）

仓库提供：

- `compose.yaml`：拉取 GHCR 生产镜像；
- `compose.dev.yaml`：从当前源码构建，仅用于开发验证。

固定版本：

```bash
printf 'EDGE_TTS_IMAGE_TAG=7.5.1\n' > .env
docker compose -f compose.yaml pull
docker compose -f compose.yaml up -d
```

查看状态、健康和日志：

```bash
docker compose -f compose.yaml ps
curl http://127.0.0.1:5050/health
docker compose -f compose.yaml logs -f --tail=200
```

停止服务：

```bash
docker compose -f compose.yaml down
```

Compose 已启用以下安全运行约束：

- 非 root 用户和 `read_only: true`；
- `tmpfs /tmp`；
- `cap_drop: ALL`；
- `no-new-privileges`；
- `init: true`、30 秒优雅停止；
- `pull_policy: missing`；
- DNS `223.5.5.5`、`119.29.29.29`。

源码构建：

```bash
docker compose -f compose.dev.yaml up -d --build
```

两份 Compose 使用同一容器名和端口，切换前先停止当前服务。

## 在线 docker run

```bash
docker pull ghcr.io/jwwsjlm/edge-tts:7.5.1
docker run -d \
  --name edge-tts \
  --restart unless-stopped \
  --init --read-only --tmpfs /tmp:size=64m,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges \
  --dns 223.5.5.5 --dns 119.29.29.29 \
  -p 5050:5050 \
  --mount type=bind,source="$(pwd)/config.yaml",target=/config/config.yaml,readonly \
  ghcr.io/jwwsjlm/edge-tts:7.5.1
```

如果 GHCR Package 不是 Public，需要有 `read:packages` 权限的 Token：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin
```

## amd64 离线镜像

从同一 GitHub Release 下载：

- `edge-tts-linux-amd64-docker-offline.tar.gz`
- `SHA256SUMS.txt`

上传全部 Release 资产时可整体校验：

```bash
sha256sum -c SHA256SUMS.txt
```

只上传 Linux tar 时使用：

```bash
grep 'edge-tts-linux-amd64-docker-offline.tar.gz' SHA256SUMS.txt | sha256sum -c -
```

导入并确认标签：

```bash
gzip -dc edge-tts-linux-amd64-docker-offline.tar.gz | docker load
docker image inspect ghcr.io/jwwsjlm/edge-tts:7.5.1 >/dev/null
```

然后准备 `config.yaml`、`compose.yaml` 和 `.env`，执行：

```bash
docker compose -f compose.yaml up -d
```

`pull_policy: missing` 会优先使用已导入的本地镜像。离线 tar 仅支持 amd64，arm64 服务器应在线拉取多架构镜像，或从源码本地构建。

## 调用验证

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: 替换成config中的密钥" \
  -d '{"text":"Docker 部署测试","voice":"zh-CN-XiaoxiaoNeural"}' \
  --output speech.mp3
```

完整字段、限制和错误码见 [API 调用指南](api.md)。

查询音色及下载音频字幕 Bundle：

```bash
curl "http://127.0.0.1:5050/v1/voices?locale=zh-CN" \
  -H "X-API-Key: 替换成config中的密钥"
curl -X POST http://127.0.0.1:5050/v1/tts/bundle \
  -H "Content-Type: application/json" -H "X-API-Key: 替换成config中的密钥" \
  -d '{"text":"Docker 字幕测试","voice":"zh-CN-XiaoxiaoNeural","boundary":"SentenceBoundary"}' \
  --output speech-bundle.zip
unzip speech-bundle.zip speech.mp3 speech.srt
```

`proxy` 是音色查询和合成共用的服务端全局代理；按需在 `config.yaml` 填写绝对 HTTP/HTTPS URL，并结合 `upstream_connect_timeout_seconds`、`upstream_receive_timeout_seconds` 调整上游超时。不要把代理凭据放进 Compose 或调用正文。

配置 `mimo_api_key` 后，`POST /v1/tts` 的 `model` 可选择 `mimo-v2-tts`；MiMo 的 `preset`、`design` 和 `clone` 模式见 API 文档。

## HTTPS 与端口保护

API Key 不能代替传输加密。公网部署应在 Nginx、Caddy、Traefik、1Panel 反向代理或云负载均衡器启用 HTTPS。防火墙不要向公网开放 `5050`；只允许反向代理或可信内网访问。需要额外按 IP/用户限流时，在反向代理或 API 网关配置。

## 升级与回滚

升级前记录当前版本并备份 `config.yaml`：

```bash
cp .env .env.rollback
docker pull ghcr.io/jwwsjlm/edge-tts:7.3.4
docker compose -f compose.yaml up -d
curl http://127.0.0.1:5050/health
```

升级到后续版本时只修改 `.env` 中 `EDGE_TTS_IMAGE_TAG`。若健康检查或 TTS 验证失败，把 `.env.rollback` 恢复为 `.env`，再次执行 `docker compose -f compose.yaml up -d`。宿主机只读挂载的 `config.yaml` 不会随容器替换而丢失。
