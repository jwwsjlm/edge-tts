# 在 1Panel 部署 Edge TTS（Docker-only）

本项目在 1Panel 只维护 Docker-only 部署，不再使用面板的语言运行时。这样线上版本、依赖和 GitHub Action 验证过的镜像保持一致。在线镜像支持 amd64/arm64；离线 tar 只支持 amd64。

## 准备目录和文件

在 1Panel“文件”中创建 `/opt/edge-tts`，上传仓库中的 `compose.yaml`、`config.example.yaml`，然后复制示例：

```bash
cd /opt/edge-tts
cp config.example.yaml config.yaml
chown 10001:10001 config.yaml
chmod 600 config.yaml
```

镜像内服务用户固定为 UID/GID `10001`，上述所有权设置可让非 root 容器读取只读挂载的 Key。

编辑 `config.yaml`：

```yaml
api_key: "替换成至少 32 字节的随机密钥"
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

可在 1Panel 终端生成 Key：

```bash
openssl rand -base64 32
```

不要把真实 Key 写入 `compose.yaml`、镜像、Git 仓库或聊天记录。

## 方式一：在线拉取 GHCR

固定版本，避免 `latest` 意外升级：

```bash
cd /opt/edge-tts
echo 'EDGE_TTS_IMAGE_TAG=7.4.1' > .env
docker pull ghcr.io/jwwsjlm/edge-tts:7.4.1
docker compose -f compose.yaml up -d
```

镜像支持 `linux/amd64` 和 `linux/arm64`。如果 GHCR Package 不是 Public，先在终端使用有 `read:packages` 权限的 GitHub Token 执行 `docker login ghcr.io`。

## 方式二：上传离线 amd64 镜像

从 GitHub Release 下载并通过 1Panel“文件”上传到 `/opt/edge-tts`：

- `edge-tts-server-linux-amd64.tar.gz`
- `SHA256SUMS.txt`

如果同时上传了 Release 的所有资产，可完整校验：

```bash
cd /opt/edge-tts
sha256sum -c SHA256SUMS.txt
```

只上传 Linux tar 时：

```bash
grep 'edge-tts-server-linux-amd64.tar.gz' SHA256SUMS.txt | sha256sum -c -
```

导入并检查镜像：

```bash
gzip -dc edge-tts-server-linux-amd64.tar.gz | docker load
docker image inspect ghcr.io/jwwsjlm/edge-tts:7.4.1 >/dev/null
```

创建 `.env` 并启动：

```bash
echo 'EDGE_TTS_IMAGE_TAG=7.4.1' > .env
docker compose -f compose.yaml up -d
```

Compose 的 `pull_policy: missing` 会使用刚导入的本地镜像。arm64 服务器不能使用此 tar，请在线拉取镜像。

## 状态、日志与健康检查

```bash
cd /opt/edge-tts
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs -f --tail=200
curl http://127.0.0.1:5050/health
```

健康响应：

```json
{"status":"ok"}
```

测试语音：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: 替换成config中的密钥' \
  -d '{"text":"1Panel Docker 部署成功","voice":"zh-CN-XiaoxiaoNeural"}' \
  --output speech.mp3
```

容器内部使用端口 `5050`。Compose 还设置了 DNS `223.5.5.5`、`119.29.29.29`、只读文件系统、非 root、能力清空和 `no-new-privileges`。

## 反向代理与 HTTPS

1. 在 1Panel“网站”中创建反向代理网站并绑定域名。
2. 上游地址填 `http://127.0.0.1:5050`。
3. 申请或上传证书，开启 HTTPS 和强制 HTTPS。
4. 在云安全组和系统防火墙中禁止公网直接访问 `5050`，只开放 `80/443`。
5. 如需额外防盗刷，可在反向代理按 IP 设置频率和并发限制；服务本身仍会校验 `X-API-Key`。

代理完成后验证：

```bash
curl https://tts.example.com/health
```

## 升级

先备份当前标签与配置：

```bash
cd /opt/edge-tts
cp .env .env.rollback
cp config.yaml config.yaml.backup
```

在线升级时拉取新版本、修改 `.env`，再重建容器：

```bash
docker pull ghcr.io/jwwsjlm/edge-tts:NEW_VERSION
echo 'EDGE_TTS_IMAGE_TAG=NEW_VERSION' > .env
docker compose -f compose.yaml up -d
curl http://127.0.0.1:5050/health
```

离线升级则先校验并 `docker load` 新 tar，再执行相同的 Compose 命令。升级后调用一次 `/v1/tts`，不要只看容器状态。

## 回滚

新版本异常时恢复旧标签：

```bash
cd /opt/edge-tts
cp .env.rollback .env
docker compose -f compose.yaml up -d
docker compose -f compose.yaml logs --tail=200
curl http://127.0.0.1:5050/health
```

只读挂载的 `config.yaml` 独立于容器，升级和回滚不会覆盖 API Key。确认旧版本正常后再清理不用的镜像。

## 音色、字幕与代理验证

```bash
curl "http://127.0.0.1:5050/v1/voices?locale=zh-CN&gender=Female" \
  -H "X-API-Key: 替换成config中的密钥"
curl -X POST http://127.0.0.1:5050/v1/tts/bundle \
  -H "Content-Type: application/json" -H "X-API-Key: 替换成config中的密钥" \
  -d '{"text":"1Panel 字幕验证","voice":"zh-CN-XiaoxiaoNeural","boundary":"SentenceBoundary"}' \
  --output speech-bundle.zip
unzip -l speech-bundle.zip
```

ZIP 应只列出 `speech.mp3`、`speech.srt`。服务器访问微软上游需要代理时，在挂载的 `config.yaml` 设置 `proxy`，同时可调整 `upstream_connect_timeout_seconds` 和 `upstream_receive_timeout_seconds`；修改后用 Compose 重建容器。代理 URL 及凭据不得复制到面板公开日志或客户端参数。
