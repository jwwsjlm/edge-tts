Edge TTS HTTP Server - Windows 使用说明
========================================

1. 双击 edge-tts-server.exe。
2. 首次启动会在 EXE 同目录生成 config.yaml，并自动生成随机 API Key。
3. 请妥善保存 config.yaml，不要把 api_key 发给其他人。
4. 默认地址为 http://127.0.0.1:5050，仅本机可以访问。
5. 保持服务窗口开启。关闭窗口或按 Ctrl+C 即停止服务。

调用方式
--------

在此目录打开 PowerShell，运行：

    powershell -ExecutionPolicy Bypass -File .\call-example.ps1 -Text "你好，世界"

脚本会从 config.yaml 读取 api_key，通过 X-API-Key 请求头调用接口，并生成 speech.mp3。

配置说明
--------

    api_key: "你的长随机密钥"
    host: "127.0.0.1"
    port: 5050

修改 config.yaml 后需要重启 EXE。除非明确需要局域网访问，否则不要把 host 改成 0.0.0.0。
服务器与 Docker 部署请查看项目仓库中的 docs/docker.md，并在公网部署时使用 HTTPS 反向代理。
