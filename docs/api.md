# Edge TTS HTTP API 调用指南

默认地址为 `http://127.0.0.1:5050`。`GET /v1/voices`、`POST /v1/tts`、`POST /v1/tts/bundle` 都必须携带 `X-API-Key`；`GET /health` 和可选 Swagger 公开。公网必须使用 HTTPS，服务不实现 OpenAI 风格路径，也不使用 HTTP 流式传输。

## 公共约定

- 所有响应含服务端生成的 `X-Request-ID`。
- 错误统一为 `{"error":"invalid_request","message":"..."}`。
- 鉴权发生在正文读取、JSON 解析和上游请求之前。
- 日志不记录 Key、正文、代理 URL 或代理凭据。

## 健康检查

```bash
curl http://127.0.0.1:5050/health
```

成功返回 `200 {"status":"ok"}`。它只检查 HTTP 进程，不调用微软上游。

## GET /v1/voices：查询音色

```bash
curl "http://127.0.0.1:5050/v1/voices?locale=zh-CN&language=zh&gender=Female" \
  -H "X-API-Key: YOUR_KEY"
```

三个查询参数都可省略，均为忽略大小写的精确匹配：

| 参数 | 示例 | 说明 |
| --- | --- | --- |
| `locale` | `zh-CN` | 完整区域语言 |
| `language` | `zh` | locale 的语言部分 |
| `gender` | `Female` | 仅 `Female` 或 `Male`；非法值返回 `400 invalid_request` |

无匹配返回 `200 {"voices":[]}`。结果按 `name` 排序，完整记录如下：

```json
{
  "voices": [{
    "name": "zh-CN-XiaoxiaoNeural",
    "internal_name": "Microsoft Server Speech Text to Speech Voice (...)",
    "friendly_name": "Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)",
    "locale": "zh-CN",
    "language": "zh",
    "gender": "Female",
    "status": "GA",
    "suggested_codec": "audio-24khz-48kbitrate-mono-mp3",
    "content_categories": [],
    "voice_personalities": []
  }]
}
```

将返回的 `name` 原样填入合成请求的 `voice`。音色列表默认缓存 3600 秒；过期刷新失败时返回旧缓存，首次加载失败返回 `502 upstream_error`。

## POST /v1/tts：完整 MP3

请求头为 `Content-Type: application/json` 与 `X-API-Key`。JSON 字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `text` | string | 是 | 无 | 非空文本，默认最多 5000 字符 |
| `voice` | string | 否 | `en-US-EmmaMultilingualNeural` | `/v1/voices` 返回的 `name` |
| `rate` | string | 否 | `+0%` | 如 `+10%`、`-20%` |
| `volume` | string | 否 | `+0%` | 如 `+10%`、`-20%` |
| `pitch` | string | 否 | `+0Hz` | 如 `+5Hz`、`-10Hz` |

未知字段、错误类型和无效原版参数返回 `400 invalid_request`。成功为 `200 audio/mpeg`、`Content-Disposition: inline; filename="speech.mp3"`，正文是完整 MP3 字节。

```bash
curl --fail-with-body -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" \
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","rate":"+10%","volume":"+0%","pitch":"+2Hz"}' \
  --output speech.mp3
```

## POST /v1/tts/bundle：MP3 + SRT

请求包含 `/v1/tts` 的全部字段，并增加：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `boundary` | string | 否 | `SentenceBoundary` | 只允许 `WordBoundary`、`SentenceBoundary` |

```bash
curl --fail-with-body -X POST http://127.0.0.1:5050/v1/tts/bundle \
  -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" \
  -d '{"text":"你好，世界","voice":"zh-CN-XiaoxiaoNeural","boundary":"SentenceBoundary"}' \
  --output speech-bundle.zip
unzip speech-bundle.zip
```

成功为 `200 application/zip`，固定只含 `speech.mp3`、`speech.srt`。服务在同一次上游 `Communicate` 调用中收集音频和 Boundary，再完整返回 ZIP；不会重复合成、Base64 编码或向客户端流式发送。

## Python

```python
from pathlib import Path
import httpx

base = "http://127.0.0.1:5050"
headers = {"X-API-Key": "YOUR_KEY"}

voices = httpx.get(
    f"{base}/v1/voices", params={"locale": "zh-CN"}, headers=headers
).json()["voices"]
voice = voices[0]["name"]

response = httpx.post(
    f"{base}/v1/tts",
    headers=headers,
    json={"text": "你好，世界", "voice": voice, "rate": "+0%"},
    timeout=130,
)
response.raise_for_status()
Path("speech.mp3").write_bytes(response.content)
print(response.headers["X-Request-ID"])
```

下载 Bundle 时只需把路径改为 `/v1/tts/bundle`、JSON 增加 `boundary`，并把响应写入 `speech-bundle.zip`。

## JavaScript（Node.js 18+）

```javascript
import { writeFile } from "node:fs/promises";

const response = await fetch("http://127.0.0.1:5050/v1/tts", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-API-Key": "YOUR_KEY" },
  body: JSON.stringify({
    text: "你好，世界",
    voice: "zh-CN-XiaoxiaoNeural",
    rate: "+0%",
    volume: "+0%",
    pitch: "+0Hz",
  }),
});
if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
await writeFile("speech.mp3", Buffer.from(await response.arrayBuffer()));
```

服务未启用 CORS；不要把 Key 放进公开网页前端，浏览器应用应通过自己的同源后端调用。

## PowerShell

```powershell
$Headers = @{ "X-API-Key" = "YOUR_KEY" }
$Body = @{
    text = "你好，世界"
    voice = "zh-CN-XiaoxiaoNeural"
    boundary = "WordBoundary"
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://127.0.0.1:5050/v1/tts/bundle" `
    -Method Post -Headers $Headers -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($Body)) -OutFile "speech-bundle.zip"
```

## 配置与资源限制

| config.yaml 字段 | 默认值 | 行为 |
| --- | ---: | --- |
| `max_text_length` | `5000` | 超限为 `413 text_too_long` |
| `max_request_bytes` | `65536` | 按实际 ASGI 字节统计；超限为 `413 request_too_large` |
| `max_concurrent_requests` | `4` | 两个合成接口共用；满载为 `429 too_many_requests`、`Retry-After: 1` |
| `request_timeout_seconds` | `120` | 单次请求总超时；超时为 `504 upstream_timeout` |
| `max_audio_bytes` | `20971520` | MP3 聚合上限；超限为 `413 audio_too_large` |
| `voices_cache_ttl_seconds` | `3600` | 音色内存缓存有效期 |
| `proxy` | `null` | 音色与合成共用的全局 HTTP/HTTPS 代理 |
| `upstream_connect_timeout_seconds` | `10` | 传给原版 `Communicate.connect_timeout` |
| `upstream_receive_timeout_seconds` | `60` | 传给原版 `Communicate.receive_timeout` |
| `docs_enabled` | `false` | 是否开放 Swagger/OpenAPI |

代理只能由服务端配置，客户端不能传入；代理地址与凭据不会进入安全日志。

## 错误码

| HTTP | `error` | 含义 |
| ---: | --- | --- |
| 400 | `invalid_request` | JSON、筛选、字段或原版 TTS 参数无效 |
| 401 | `unauthorized` | 缺少或错误 Key |
| 404 | `not_found` | 路由不存在 |
| 405 | `method_not_allowed` | HTTP 方法不支持 |
| 413 | `request_too_large` | 请求正文过大 |
| 413 | `text_too_long` | 文本过长 |
| 413 | `audio_too_large` | 生成音频过大 |
| 429 | `too_many_requests` | 合成并发槽已满 |
| 500 | `internal_error` | 安全内部错误 |
| 502 | `upstream_error` | 微软上游或网络失败 |
| 504 | `upstream_timeout` | 请求总超时 |

`429` 按 `Retry-After` 重试；`502`、`504` 可有限指数退避。不要重试 `400`、`401`、`413`。

## 原版功能对应表

| 原版 `rany2/edge-tts` | HTTP 映射 |
| --- | --- |
| `list_voices()`、`--list-voices` | `GET /v1/voices` |
| voice/rate/volume/pitch | 两个合成接口同名字段 |
| WordBoundary/SentenceBoundary、SubMaker/SRT | `/v1/tts/bundle` |
| proxy、connect_timeout、receive_timeout | `config.yaml` 全局配置 |
| 完整 MP3 | `/v1/tts` 或 ZIP 内 `speech.mp3` |

`edge-playback`、客户端文件读取、底层 aiohttp connector、实时 chunk stream 不公开为 HTTP API。

## Swagger 与 OpenAPI

设置 `docs_enabled: true` 并重启后访问 `/docs` 或 `/openapi.json`。三个 `/v1/*` 操作均声明 `X-API-Key` Security Scheme，请求与响应模型包含音色和 Bundle Schema。关闭时两个文档地址返回 `404 not_found`。
