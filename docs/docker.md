# Docker 部署 Edge TTS HTTP 服务

官方 Action 会将 `linux/amd64` 和 `linux/arm64` 镜像发布到 `ghcr.io/jwwsjlm/edge-tts`。建议服务器固定使用明确版本号，不要只依赖 `latest`。

## 1. 准备 config.yaml

在服务器新建一个只允许管理员读取的目录，并创建 `config.yaml`：

```yaml
api_key: "替换成足够长的随机密钥"
host: "0.0.0.0"
port: 5050
```

- `api_key`：客户端通过 `X-API-Key` 请求头提交的密钥。不要提交到 Git 或发到聊天记录。
- `host`：Docker 内必须使用 `0.0.0.0`，否则宿主机端口无法访问服务。
- `port`：容器内监听端口。下列示例使用 `5050`。

修改配置后需重启容器。更换 `api_key` 后，旧 Key 会立即失效。

## 2. 使用 Docker Compose（推荐）

仓库提供两份可独立运行的配置：

- `compose.yaml`：服务器生产部署，拉取 GHCR 镜像；
- `compose.dev.yaml`：本地开发，从当前源码构建镜像。

先复制示例配置并修改随机密钥：

```bash
cp packaging/docker/config.example.yaml config.yaml
```

PowerShell 使用：

```powershell
Copy-Item .\packaging\docker\config.example.yaml .\config.yaml
```

生产环境启动：

```bash
docker compose -f compose.yaml up -d
```

生产版默认拉取 `latest`。建议在 `.env` 中固定 Release 版本：

```dotenv
EDGE_TTS_IMAGE_TAG=1.0.0
```

查看状态和日志：

```bash
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs -f
```

停止并删除容器（宿主机上的 `config.yaml` 不会删除）：

```bash
docker compose -f compose.yaml down
```

本地源码构建与启动：

```bash
docker compose -f compose.dev.yaml up -d --build
```

两份配置使用相同的容器名和 `5050` 端口，启动另一份前应先停止当前服务。两份 Compose 均设置 DNS `223.5.5.5` 和 `119.29.29.29`，用于容器内的域名解析。

## 3. 拉取镜像与 docker run

```bash
docker pull ghcr.io/jwwsjlm/edge-tts:latest
```

如果 GHCR Package 尚未设为 Public，先使用有 `read:packages` 权限的 Token 登录：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin
```

## 4. 直接启动容器

Linux/macOS shell：

```bash
docker run -d \
  --name edge-tts \
  --restart unless-stopped \
  -p 5050:5050 \
  --mount type=bind,source="$(pwd)/config.yaml",target=/config/config.yaml,readonly \
  ghcr.io/jwwsjlm/edge-tts:latest
```

Windows PowerShell：

```powershell
$Config = (Resolve-Path .\config.yaml).Path
docker run -d `
  --name edge-tts `
  --restart unless-stopped `
  -p 5050:5050 `
  --mount "type=bind,source=$Config,target=/config/config.yaml,readonly" `
  ghcr.io/jwwsjlm/edge-tts:latest
```

确认容器健康：

```bash
docker ps --filter name=edge-tts
curl http://127.0.0.1:5050/health
```

正常响应为：

```json
{"status":"ok"}
```

## 5. 调用 TTS

curl：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: 替换成config中的密钥" \
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","rate":"+0%","volume":"+0%","pitch":"+0Hz"}' \
  --output speech.mp3
```

PowerShell：

```powershell
$Headers = @{ "X-API-Key" = "替换成config中的密钥" }
$Body = @{ text = "你好，世界"; voice = "zh-CN-XiaoxiaoNeural" } | ConvertTo-Json
Invoke-WebRequest -Uri http://127.0.0.1:5050/v1/tts -Method Post `
  -Headers $Headers -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($Body)) -OutFile speech.mp3
```

`text` 必填；`voice`、`rate`、`volume`、`pitch` 可选。成功响应的 `Content-Type` 是 `audio/mpeg`。

## 6. 固定版本与升级

生产环境建议使用 Release 中的明确版本，例如：

```bash
docker pull ghcr.io/jwwsjlm/edge-tts:1.0.0
docker rm -f edge-tts
docker run -d --name edge-tts --restart unless-stopped -p 5050:5050 \
  --mount type=bind,source="$(pwd)/config.yaml",target=/config/config.yaml,readonly \
  ghcr.io/jwwsjlm/edge-tts:1.0.0
```

配置文件在宿主机上，删除或升级容器不会丢失 Key。

## 7. 公网部署安全

API Key 不能代替传输加密。部署到公网时，应在服务前配置 Nginx、Caddy、Traefik 或云负载均衡器提供 HTTPS，并通过防火墙限制直接访问 `5050`。不要将容器内端口直接暴露给整个互联网。需要限制调用频率时，应在反向代理或 API 网关增加 rate limit。

## 8. 本地构建

```bash
docker build -t edge-tts-http:local .
```

启动方法与上文一致，只需把镜像名换成 `edge-tts-http:local`。
