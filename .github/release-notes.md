# Edge TTS HTTP Server __VERSION__

本次 Release 同时提供 Windows 独立 EXE 和 Docker 镜像。Windows 用户下载 `edge-tts-server-windows-x64.zip`，解压后双击 `edge-tts-server.exe`；首次启动会自动生成带随机 API Key 的 `config.yaml`。

## Docker 镜像

镜像：`__IMAGE__:__VERSION__`，支持 `linux/amd64` 和 `linux/arm64`。

```bash
docker pull __IMAGE__:__VERSION__
```

如果 Package 不是 Public，需要先执行 `docker login ghcr.io`。

## config.yaml

在部署目录创建：

```yaml
api_key: "替换成足够长的随机密钥"
host: "0.0.0.0"
port: 5050
```

`api_key` 是客户端 `X-API-Key` 请求头使用的密钥。请勿提交到 Git。修改配置或轮换 Key 后需重启容器。

## 启动

Linux/macOS：

```bash
docker run -d \
  --name edge-tts \
  --restart unless-stopped \
  -p 5050:5050 \
  --mount type=bind,source="$(pwd)/config.yaml",target=/config/config.yaml,readonly \
  __IMAGE__:__VERSION__
```

Windows PowerShell：

```powershell
$Config = (Resolve-Path .\config.yaml).Path
docker run -d `
  --name edge-tts `
  --restart unless-stopped `
  -p 5050:5050 `
  --mount "type=bind,source=$Config,target=/config/config.yaml,readonly" `
  __IMAGE__:__VERSION__
```

健康检查：

```bash
curl http://127.0.0.1:5050/health
```

调用并保存 MP3：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: 替换成config中的密钥" \
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural"}' \
  --output speech.mp3
```

## 升级与安全

拉取新版本后删除旧容器，并使用相同的只读 `config.yaml` 挂载重新运行即可。生产环境建议固定 `__IMAGE__:__VERSION__`，确认后再升级。

公网部署必须通过反向代理或负载均衡器启用 HTTPS，并限制对宿主机 `5050` 端口的直接访问。API Key 本身不能加密网络流量。

## Changes
