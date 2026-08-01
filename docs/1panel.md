# 在 1Panel 使用 Python 部署 Edge TTS HTTP 服务

本文适用于不使用 Docker、希望通过 1Panel Python 运行环境部署本项目的场景。示例路径为 `/opt/edge-tts`，配置文件单独保存在 `/opt/edge-tts-data/config.yaml`，更新代码时不会覆盖 API Key。

## 1. 上传并解压代码包

从 GitHub 下载本仓库的 Source code ZIP，或在本地把源码打成 ZIP。不要上传 Windows EXE 发布包。

在 1Panel 的“文件”页面创建 `/opt/edge-tts`，上传 ZIP 并解压。整理目录后，以下文件应直接存在：

```text
/opt/edge-tts/setup.py
/opt/edge-tts/config.example.yaml
/opt/edge-tts/src/
```

如果解压后多了一层目录，请把该目录中的源码移到 `/opt/edge-tts`。

## 2. 创建外置配置

在 1Panel 终端执行：

```bash
mkdir -p /opt/edge-tts-data
cp /opt/edge-tts/config.example.yaml /opt/edge-tts-data/config.yaml
```

使用 1Panel 文件编辑器打开 `/opt/edge-tts-data/config.yaml`：

```yaml
api_key: "替换成足够长的随机密钥"
host: "0.0.0.0"
port: 5050
```

可执行下面的命令生成随机 Key，然后填入 `api_key`：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

客户端必须在 `X-API-Key` 请求头中提交这个 Key。不要把真实的 `config.yaml` 上传到 GitHub、写入镜像或发到聊天记录。

## 3. 创建 Python 3.12 运行环境

在 1Panel 的“网站”或“运行环境”页面安装 Python 3.12。也可以在终端使用服务器已有的 Python 3.12 创建虚拟环境：

```bash
cd /opt/edge-tts
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install .
```

安装完成后，可以先在终端前台验证服务：

```bash
cd /opt/edge-tts
source .venv/bin/activate
python -m edge_tts_server --config /opt/edge-tts-data/config.yaml
```

看到服务监听 `0.0.0.0:5050` 后，按 `Ctrl+C` 停止前台进程。

## 4. 在 1Panel 中创建 Python 应用

在 1Panel 新建 Python 运行环境或 Python 应用，并填写：

- 应用目录：`/opt/edge-tts`
- Python 版本：Python 3.12
- 启动命令：`/opt/edge-tts/.venv/bin/python -m edge_tts_server --config /opt/edge-tts-data/config.yaml`
- 监听端口：`5050`
- 环境变量：`PYTHONUNBUFFERED=1`

保存并启动应用。确保运行应用的系统用户对 `/opt/edge-tts-data/config.yaml` 只有必要的读取权限。

## 5. 健康检查与接口调用

先在服务器本机检查无需 Key 的健康接口：

```bash
curl http://127.0.0.1:5050/health
```

正常响应为：

```json
{"status": "ok"}
```

再测试需要 Key 的语音接口：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: 替换成config中的密钥" \
  -d '{"text":"你好，这是 1Panel 部署测试。","voice":"zh-CN-XiaoxiaoNeural"}' \
  --output speech.mp3
```

## 6. 配置反向代理和 HTTPS

在 1Panel 的“网站”页面绑定域名，并把反向代理地址设为 `http://127.0.0.1:5050`。申请或上传 TLS 证书后启用 HTTPS，并建议开启强制 HTTPS。

完成后访问：

```bash
curl https://tts.example.com/health
```

公网客户端应调用 `https://tts.example.com/v1/tts`。如果只通过反向代理访问，请不要在云安全组或系统防火墙中向公网开放 `5050` 端口。

## 7. 更新和重启

更新前先在 1Panel 停止 Python 应用，然后上传并解压新的 Source code ZIP 到 `/opt/edge-tts`。不要删除或覆盖 `/opt/edge-tts-data/config.yaml`。

重新安装项目并启动应用：

```bash
cd /opt/edge-tts
source .venv/bin/activate
pip install --upgrade .
```

最后再次请求 `/health`，并调用一次 `/v1/tts`。修改 `config.yaml` 或轮换 `api_key` 后，也需要在 1Panel 中重启应用。
