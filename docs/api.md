# Edge TTS HTTP API 调用指南

默认地址为 `http://127.0.0.1:5050`。除健康检查和可选 Swagger 外，语音接口必须通过 `X-API-Key` 鉴权。请使用 HTTPS 传输公网请求，避免 Key 和正文被窃听。

## 公共响应约定

- 所有响应包含服务端生成的 `X-Request-ID`，排查问题时请一并记录。
- 成功的 TTS 响应为 `audio/mpeg`，正文是完整 MP3，不是 JSON。
- 错误响应统一为：

```json
{"error":"invalid_request","message":"Request body is invalid"}
```

- 服务日志不会记录正文或 API Key。

## 健康检查

`GET /health` 无需 API Key：

```bash
curl http://127.0.0.1:5050/health
```

成功返回 `200`：

```json
{"status":"ok"}
```

健康检查只表示 HTTP 进程正常，不会消耗上游 TTS 配额。

## 语音合成

### 请求

`POST /v1/tts`

请求头：

| 名称 | 必填 | 说明 |
| --- | --- | --- |
| `Content-Type: application/json` | 是 | UTF-8 JSON |
| `X-API-Key` | 是 | 与 `config.yaml` 中 `api_key` 完全一致 |

JSON 字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `text` | string | 是 | 无 | 要合成的文本；不能只含空白，默认最多 5000 个字符 |
| `voice` | string | 否 | `en-US-EmmaMultilingualNeural` | Edge TTS 声音名称 |
| `rate` | string | 否 | `+0%` | 语速，如 `+10%`、`-20%` |
| `volume` | string | 否 | `+0%` | 音量，如 `+10%`、`-20%` |
| `pitch` | string | 否 | `+0Hz` | 音调，如 `+5Hz`、`-10Hz` |

未知字段、错误类型、无效 voice 或无效调节格式都返回 `400 invalid_request`。

### 成功响应

- 状态码：`200`
- `Content-Type: audio/mpeg`
- `Content-Disposition: inline; filename="speech.mp3"`
- 正文：完整 MP3 字节

服务会先在受限内存中聚合音频，再一次性返回；当前不提供流式响应。

## 默认资源限制

| config.yaml 字段 | 默认值 | 行为 |
| --- | ---: | --- |
| `max_text_length` | `5000` | 文本超限返回 `413 text_too_long` |
| `max_request_bytes` | `65536` | 按实际收到的 ASGI 字节统计，超限返回 `413 request_too_large` |
| `max_concurrent_requests` | `4` | 并发已满返回 `429 too_many_requests` 和 `Retry-After: 1` |
| `request_timeout_seconds` | `120` | 上游处理超时返回 `504 upstream_timeout` |
| `max_audio_bytes` | `20971520` | 聚合音频超过 20 MiB 返回 `413 audio_too_large` |
| `docs_enabled` | `false` | 是否开放 Swagger 和 OpenAPI |

认证发生在正文读取和 JSON 解析之前。错误 Key 即使携带巨大或无效正文，也会先返回 `401`。

## 错误码

| HTTP | `error` | 含义 |
| ---: | --- | --- |
| 400 | `invalid_request` | JSON、字段类型、字段值或 TTS 参数无效 |
| 401 | `unauthorized` | 缺少或错误的 `X-API-Key` |
| 404 | `not_found` | 路由不存在；Swagger 关闭时也使用此错误 |
| 405 | `method_not_allowed` | HTTP 方法不支持 |
| 413 | `request_too_large` | 实际请求正文超过 `max_request_bytes` |
| 413 | `text_too_long` | `text` 超过 `max_text_length` |
| 413 | `audio_too_large` | 生成音频超过 `max_audio_bytes` |
| 429 | `too_many_requests` | 并发槽位已满；响应带 `Retry-After: 1` |
| 500 | `internal_error` | 安全的内部错误，不返回异常详情 |
| 502 | `upstream_error` | 网络或微软 TTS 上游失败 |
| 504 | `upstream_timeout` | 上游处理超过配置超时 |

客户端遇到 `429` 可按 `Retry-After` 延迟重试；`502`、`504` 可使用带上限的指数退避。不要无界重试 `400`、`401` 或 `413`。

## curl：下载 MP3

```bash
curl --fail-with-body -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","rate":"+0%","volume":"+0%","pitch":"+0Hz"}' \
  --output speech.mp3
```

## Python：下载 MP3

先安装 HTTPX：`python -m pip install httpx`。

```python
from pathlib import Path

import httpx

response = httpx.post(
    "http://127.0.0.1:5050/v1/tts",
    headers={"X-API-Key": "YOUR_KEY"},
    json={"text": "你好，世界", "voice": "zh-CN-XiaoxiaoNeural"},
    timeout=130,
)
response.raise_for_status()
Path("speech.mp3").write_bytes(response.content)
print("request id:", response.headers["X-Request-ID"])
```

## JavaScript（Node.js 18+）：下载 MP3

```javascript
import { writeFile } from "node:fs/promises";

const response = await fetch("http://127.0.0.1:5050/v1/tts", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "YOUR_KEY",
  },
  body: JSON.stringify({
    text: "你好，世界",
    voice: "zh-CN-XiaoxiaoNeural",
  }),
});

if (!response.ok) {
  throw new Error(`${response.status}: ${await response.text()}`);
}
await writeFile("speech.mp3", Buffer.from(await response.arrayBuffer()));
console.log("request id:", response.headers.get("x-request-id"));
```

服务默认未启用跨域 CORS，上述 JavaScript 示例用于 Node.js。浏览器跨域调用建议经同源后端代理，不要把 API Key 暴露在公开前端代码中。

## PowerShell：下载 MP3

```powershell
$Headers = @{ "X-API-Key" = "YOUR_KEY" }
$Body = @{
    text = "你好，世界"
    voice = "zh-CN-XiaoxiaoNeural"
    rate = "+0%"
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://127.0.0.1:5050/v1/tts" `
    -Method Post -Headers $Headers `
    -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($Body)) `
    -OutFile "speech.mp3"
```

## Swagger 与 OpenAPI

在 `config.yaml` 设置 `docs_enabled: true` 并重启：

- Swagger UI：`/docs`
- OpenAPI Schema：`/openapi.json`

OpenAPI 声明了 `X-API-Key` Security Scheme。Swagger 使用公共 CDN 加载前端资源，Authorize 中输入的 Key 不会持久化。`docs_enabled: false` 时两个地址统一返回 `404 not_found`。
