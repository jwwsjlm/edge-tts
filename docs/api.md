# Edge TTS + Xiaomi MiMo HTTP API 调用指南

默认地址为 `http://127.0.0.1:5050`。`GET /v1/models`、`GET /v1/voices`、`POST /v1/tts`、`POST /v1/tts/bundle` 都必须携带 `X-API-Key`；`GET /health` 和可选 Swagger 公开。公网必须使用 HTTPS，服务不实现 OpenAI 风格路径，也不使用 HTTP 流式传输。

## GET /v1/models：模型能力

```bash
curl http://127.0.0.1:5050/v1/models -H "X-API-Key: YOUR_KEY"
```

返回 `edge-tts` 和 `mimo-v2-tts` 支持的模式、输出格式和字幕能力。

## 公共约定

- 所有响应含服务端生成的 `X-Request-ID`。
- 错误统一为 `{"error":"invalid_request","message":"...","fields":[...]}`；`fields` 仅在请求字段校验失败时出现。
- 鉴权发生在正文读取、JSON 解析和上游请求之前。
- 日志不记录 Key、正文、代理 URL 或代理凭据。

## 健康检查

```bash
curl http://127.0.0.1:5050/health
```

成功返回 `200 {"status":"ok"}`。它只检查 HTTP 进程，不调用微软上游。

## GET /v1/voices：查询音色

```bash
curl "http://127.0.0.1:5050/v1/voices?model=edge-tts&locale=zh-CN&language=zh&gender=Female" \
  -H "X-API-Key: YOUR_KEY"
```

`model` 默认为 `edge-tts`；改为 `mimo-v2-tts` 可查询 MiMo 官方预置音色：`mimo_default`、`冰糖`、`茉莉`、`苏打`、`白桦`、`Mia`、`Chloe`、`Milo`、`Dean`。`mimo_default` 的实际音色依部署集群可能不同，因此返回 `gender=Unknown`。其余三个查询参数都可省略，均为忽略大小写的精确匹配：

| 参数 | 示例 | 说明 |
| --- | --- | --- |
| `locale` | `zh-CN` | 完整区域语言 |
| `language` | `zh` | locale 的语言部分 |
| `gender` | `Female` | 仅 `Female` 或 `Male`；非法值返回 `400 invalid_request` |

无匹配返回 `200 {"voices":[]}`。Edge 音色按 `name` 排序，MiMo 音色保持官方列表顺序，完整记录如下：

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

## POST /v1/tts：完整 MP3/WAV 音频

请求头为 `Content-Type: application/json` 与 `X-API-Key`。JSON 字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `model` | string | 否 | `edge-tts` | `edge-tts` 或 `mimo-v2-tts` |
| `text` | string | 是 | 无 | 非空文本；MiMo 最多 3000 字符 |
| `voice` | string | 否 | `en-US-EmmaMultilingualNeural` | `/v1/voices` 返回的 `name` |
| `response_format` | string | 否 | `mp3` | `mp3` 或 `wav` |
| `rate` | string | 否 | `+0%` | 如 `+10%`、`-20%` |
| `volume` | string | 否 | `+0%` | 如 `+10%`、`-20%` |
| `pitch` | string | 否 | `+0Hz` | 如 `+5Hz`、`-10Hz` |
| `mimo_mode` | string | 否 | `preset` | `preset`、`design`、`clone` |
| `voice_description` | string/null | 否 | `null` | MiMo design 模式必填 |
| `reference_audio` | string/null | 否 | `null` | MiMo clone 模式必填，WAV/MP3 Base64 data URL |
| `segment_id` | string/null | 否 | `null` | 可选的分段标识，最多 128 个字符 |
| `sequence` | integer/null | 否 | `null` | 可选的 1-based 分段序号，用于客户端按顺序合并 |

未知字段、错误类型和无效参数返回 `400 invalid_request`。成功为完整 `audio/mpeg` 或 `audio/wav`。Edge 原生 MP3、MiMo 原生 WAV；请求另一种格式时使用发行包内置 FFmpeg 转换。

成功响应会返回以下关联和基础质量响应头：

```http
X-Request-ID: 服务端请求 ID
X-Text-SHA256: 输入文本 UTF-8 的 SHA-256
X-Audio-Bytes: 返回音频字节数
X-Segment-ID: 请求提供时原样返回
X-Sequence: 请求提供时原样返回
X-Quality-Status: pass 或 fail
```

MiMo 的 3,000 字符是上游硬限制。`mimo_recommended_max_text_length` 默认值为 600，超过后仍会生成，但响应会增加：

```http
X-Text-Length-Warning: recommended_limit_exceeded
X-Recommended-Max-Text-Length: 600
```

推荐在 500～600 字左右切分恐怖故事等长文本，并用 `segment_id`、`sequence` 和 `X-Text-SHA256` 记录每段。服务端不会自动拆分或合并音频。

### Edge 参数如何调节

以下四个参数只对 `model=edge-tts` 生效。它们都是**字符串**，必须保留符号和单位，不能写成 JSON 数字。服务接受原版 `edge-tts` 的格式：

| 参数 | 格式 | 调小的效果 | 调大的效果 | 推荐起步值 |
| --- | --- | --- | --- | --- |
| `voice` | 完整音色名 | 不适用 | 不适用 | 先从 `/v1/voices` 查询 |
| `rate` | `+整数%` 或 `-整数%` | 说话更慢、时长更长 | 说话更快、时长更短 | 慢速 `-20%`；正常 `+0%`；快速 `+20%` |
| `volume` | `+整数%` 或 `-整数%` | 音量更小 | 音量更大 | 柔和 `-10%`；正常 `+0%`；响亮 `+10%` |
| `pitch` | `+整数Hz` 或 `-整数Hz` | 音调更低沉 | 音调更高亮 | 低沉 `-10Hz`；自然 `+0Hz`；明亮 `+10Hz` |

合法写法：`"rate":"-20%"`、`"volume":"+5%"`、`"pitch":"-8Hz"`。常见错误写法包括 `"rate":"1.2"`、`"volume":10`、`"pitch":"high"`，这些都会返回 `400 invalid_request`。

这些参数会直接交给 Microsoft Edge TTS 上游。程序只校验带符号的整数格式，不在本地强行限定数值范围；过大的绝对值可能被上游拒绝或产生不自然的结果，因此建议从上表范围小步调整。

#### 常用调音方案

自然旁白：

```json
{
  "text": "这是一段自然、清晰的旁白。",
  "voice": "zh-CN-XiaoxiaoNeural",
  "rate": "-5%",
  "volume": "+0%",
  "pitch": "+0Hz"
}
```

短视频快速解说：

```json
{
  "text": "接下来快速介绍今天的主要内容。",
  "voice": "zh-CN-YunxiNeural",
  "rate": "+25%",
  "volume": "+5%",
  "pitch": "+2Hz"
}
```

沉稳播报：

```json
{
  "text": "现在播报一则重要通知。",
  "voice": "zh-CN-YunjianNeural",
  "rate": "-12%",
  "volume": "+3%",
  "pitch": "-10Hz"
}
```

儿童或轻快风格应优先选择合适的 `voice`，再少量提高 `pitch` 和 `rate`；不要仅靠极端音调改变音色。不同音色对同一参数的听感会不同，应以生成结果为准。

### Edge 完整调用示例

MP3 是 Edge 原生输出，速度最快且文件较小：

```bash
curl --fail-with-body -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"model":"edge-tts","text":"你好，欢迎使用语音服务","voice":"zh-CN-XiaoxiaoNeural","response_format":"mp3","rate":"-5%","volume":"+0%","pitch":"+2Hz"}' \
  --output speech.mp3
```

如需 WAV，将 `response_format` 改为 `wav`。服务会在内存中完成 MP3 到 WAV 的转换后一次性返回，因此 WAV 通常更大、占用更多内存：

```bash
curl --fail-with-body -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"text":"导出为 WAV 音频","voice":"zh-CN-XiaoxiaoNeural","response_format":"wav"}' \
  --output speech.wav
```

### MiMo 三种模式

选择 MiMo 时必须配置服务端 `config.yaml` 中的 `mimo_api_key`。MiMo 不支持 `rate`、`volume`、`pitch`，必须省略它们或保持默认 `+0%`、`+0%`、`+0Hz`，否则服务明确返回 `400 invalid_request`。

| `mimo_mode` | 使用场景 | 必须提供 | 禁止提供 |
| --- | --- | --- | --- |
| `preset` | 使用官方预置音色 | 可选 `voice`，省略时为 `mimo_default` | `voice_description`、`reference_audio` |
| `design` | 用文字设计新音色 | `voice_description` | `voice`、`reference_audio` |
| `clone` | 模仿本地参考音频 | `reference_audio` | `voice`、`voice_description` |

#### MiMo 预置音色

先通过 `GET /v1/voices?model=mimo-v2-tts` 查询，然后使用返回的 `name`：

```bash
curl --fail-with-body -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"model":"mimo-v2-tts","mimo_mode":"preset","text":"你好，这是小米 MiMo 预置音色。","voice":"茉莉","response_format":"wav"}' \
  --output mimo-preset.wav
```

当前服务内置的预置 ID 为 `mimo_default`、`冰糖`、`茉莉`、`苏打`、`白桦`、`Mia`、`Chloe`、`Milo`、`Dean`。中文或英文音色应与文本语言合理搭配。

#### MiMo 音色设计

`voice_description` 应描述希望得到的声音，而不是待朗读内容。建议包含语言、性别、年龄感、音色、情绪、语速感觉和使用场景，例如：`普通话年轻女声，温柔清晰，语气自然亲切，适合有声书旁白`。

MiMo 音色设计示例：

```bash
curl -X POST http://127.0.0.1:5050/v1/tts \
  -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" \
  -d '{"model":"mimo-v2-tts","mimo_mode":"design","text":"欢迎使用语音服务","voice_description":"温柔、清晰、自然的年轻女声","response_format":"wav"}' \
  --output mimo.wav
```

#### MiMo 音色克隆

克隆模式将 `reference_audio` 设置为 `data:audio/wav;base64,...` 或 `data:audio/mpeg;base64,...`。解码后默认最多 10 MiB，建议至少 10 秒、单人、清晰、无背景噪音。服务会检查 Base64、文件头和大小，不接受普通本地路径或网络 URL。

不建议手工拼接大段 Base64。项目提供的 `examples/multi_model_tts.py` 会读取本地 WAV/MP3 并自动编码：

```bash
python examples/multi_model_tts.py \
  --base-url http://127.0.0.1:5050 \
  --api-key YOUR_KEY \
  --model mimo-v2-tts \
  --mimo-mode clone \
  --text "这是使用参考音频克隆出的声音。" \
  --reference-audio reference.wav \
  --response-format wav \
  --output cloned.wav
```

设计和克隆模式不要显式传 `voice`。由于请求模型中 `voice` 有 Edge 默认值，服务会根据字段是否由调用者显式提交来判断；只要不在 JSON 中写入 `voice` 即可。

### 参数兼容矩阵

| 参数/能力 | Edge TTS | MiMo preset | MiMo design | MiMo clone |
| --- | :---: | :---: | :---: | :---: |
| `voice` | 必须为 Edge 音色 | 可选预置 ID | 禁止 | 禁止 |
| `rate` / `volume` / `pitch` | 支持 | 仅默认值 | 仅默认值 | 仅默认值 |
| `voice_description` | 禁止 | 禁止 | 必填 | 禁止 |
| `reference_audio` | 禁止 | 禁止 | 禁止 | 必填 |
| `response_format=mp3` | 支持 | 支持，需转换 | 支持，需转换 | 支持，需转换 |
| `response_format=wav` | 支持，需转换 | 支持，原生 | 支持，原生 | 支持，原生 |
| `/v1/tts/bundle` 字幕 | 支持 | 不支持 | 不支持 | 不支持 |

### 返回音频

`POST /v1/tts` 不返回 JSON，也不使用 HTTP 流式传输，而是在生成和必要的格式转换完成后返回整个音频文件：

| 格式 | `Content-Type` | 建议扩展名 | 说明 |
| --- | --- | --- | --- |
| `mp3` | `audio/mpeg` | `.mp3` | 体积较小，Edge 原生格式 |
| `wav` | `audio/wav` | `.wav` | 未压缩、体积较大，MiMo 原生格式 |

响应还包含 `Content-Disposition` 和 `X-Request-ID`。排查问题时请记录 `X-Request-ID`，不要记录或公开 API Key。

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

成功为 `200 application/zip`，固定只含 `speech.mp3`、`speech.srt`。该接口仅支持 `model=edge-tts` 和 `response_format=mp3`；MiMo 返回 `400 unsupported_model`。服务在同一次上游 `Communicate` 调用中收集音频和 Boundary，再完整返回 ZIP。

- `SentenceBoundary`：每条字幕通常对应一句话，字幕数量少，适合播放器和视频字幕。
- `WordBoundary`：每条字幕通常对应一个词或词段，时间粒度更细，适合逐词高亮，但 SRT 条目会明显增多。
- Boundary 由上游返回，字幕时间并不是服务端按字符平均估算。

## Python

项目根目录的 `run.py` 是统一服务启动脚本，不需要 Docker：

```bash
python run.py --config config.yaml
```

服务端 API 兼容原有 Edge TTS 调用；在同一个接口中将 `model` 设置为 `edge-tts` 或 `mimo-v2-tts` 即可切换后端。

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

调用 MiMo 预置模式：

```python
response = httpx.post(
    f"{base}/v1/tts",
    headers=headers,
    json={
        "model": "mimo-v2-tts",
        "mimo_mode": "preset",
        "text": "你好，这是 MiMo 语音。",
        "voice": "冰糖",
        "response_format": "wav",
    },
    timeout=180,
)
response.raise_for_status()
Path("mimo.wav").write_bytes(response.content)
```

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
| `max_request_bytes` | `15728640` | 为 Base64 克隆请求预留空间 |
| `max_concurrent_requests` | `4` | 两个合成接口共用；满载为 `429 too_many_requests`、`Retry-After: 1` |
| `request_timeout_seconds` | `120` | 单次请求总超时；超时为 `504 upstream_timeout` |
| `max_audio_bytes` | `67108864` | MP3/WAV 聚合和转换上限 |
| `mimo_api_key` | `null` | MiMo 上游密钥；为空时只启用 Edge |
| `mimo_base_url` | `https://api.xiaomimimo.com/v1` | MiMo API 根地址 |
| `mimo_request_timeout_seconds` | `120` | MiMo aiohttp 请求超时 |
| `max_reference_audio_bytes` | `10485760` | 克隆参考音频解码后上限 |
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
| 503 | `provider_not_configured` | 未配置 MiMo 上游密钥 |
| 503 | `upstream_rate_limited` | MiMo 限流或额度限制 |
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

设置 `docs_enabled: true` 并重启后访问 `/docs` 或 `/openapi.json`。当前四个 `/v1/*` 操作均声明 `X-API-Key` Security Scheme，请求模型会显示字段默认值、用途和参数格式。

Swagger 页面调用步骤：

1. 打开 `http://127.0.0.1:5050/docs`，公网部署则换成自己的 HTTPS 域名。
2. 点击右上角 **Authorize**。
3. 在 `X-API-Key` 输入框中只填写 Key 本身，不要加 `Bearer`。
4. 打开目标接口，点击 **Try it out**，修改参数后点击 **Execute**。
5. 音频接口在 Swagger 中显示二进制响应；日常下载音频更推荐使用 curl、Python 或 PowerShell 示例。

关闭 `docs_enabled` 时 `/docs` 和 `/openapi.json` 都返回 `404 not_found`。

## 排错清单

- `401 unauthorized`：确认请求头名称是 `X-API-Key`，值与服务所读取的 `config.yaml` 完全一致。
- `400 invalid_request`：检查 JSON 类型、未知字段、参数单位以及模型专属字段；错误消息通常会指出具体组合问题。
- Edge 改速无效：确认 `model` 是 `edge-tts`，并使用字符串形式，例如 `"rate":"+20%"`。
- MiMo 返回 `provider_not_configured`：在服务端 `config.yaml` 设置 `mimo_api_key` 后重启 EXE 或 Python 服务。
- 克隆请求为 `413`：同时检查 `max_reference_audio_bytes` 和 `max_request_bytes`；Base64 通常比原文件大约多三分之一。
- WAV 返回 `audio_too_large`：提高 `max_audio_bytes`，或改用 MP3、缩短文本。
- `429 too_many_requests`：读取 `Retry-After`，等待后重试，或按服务器资源谨慎提高 `max_concurrent_requests`。
- `502 upstream_error` / `504 upstream_timeout`：检查服务器网络、DNS、代理和上游可用性，使用 `X-Request-ID` 对照安全日志。
