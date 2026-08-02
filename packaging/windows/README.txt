Edge TTS HTTP Server - Windows 使用说明
========================================

此压缩包包含 Python 解释器和全部运行库，目标电脑无需安装 Python，运行时无需联网安装依赖。

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

修改 config.yaml 后需要重启 EXE。除非明确需要局域网访问，否则不要把 host 改成 0.0.0.0。
服务器与 Docker 部署请查看项目仓库中的 docs/docker.md，并在公网部署时使用 HTTPS 反向代理。

音色与字幕
----------

带 X-API-Key 调用 /v1/voices?locale=zh-CN 可查询音色；把返回的 name 用于 /v1/tts 或 /v1/tts/bundle。Bundle 的 boundary 可选 WordBoundary 或 SentenceBoundary，下载 ZIP 固定只含 speech.mp3 和 speech.srt。

proxy 是服务访问微软上游的全局 HTTP/HTTPS 代理，只能在 config.yaml 配置，不能由客户端请求传入。不要公开包含账号密码的代理地址。
