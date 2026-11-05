# 语音工作台

运行任一 7.6.0 Release 产物后打开 `http://127.0.0.1:5050/`。服务器把 `host` 设置为 `0.0.0.0` 后，可使用服务器 IP 和端口访问，无需域名。页面及前端资源随运行包提供，不依赖外部 CDN，也不需要 Node.js。

## 使用

1. 粘贴文本，选择渠道和音色，设置需要的参数。
2. 点击“先试听一下”生成前 120 字，或点击“生成语音”提交全文。
3. 在生成记录中查看进度。关闭或刷新页面不会停止后台任务。
4. 完成后播放、拖动进度、调整播放音量，或下载所选的 MP3/WAV。Edge 可同时下载 SRT 字幕。

Edge 支持语言、音色、语速、音调和音量。MiMo 提供预置、文字设计、参考音频克隆三种模式，语速风格和情绪通过自然语言指令控制，实际效果取决于模型。WAV 是 PCM 容器；Edge 的原始语音为 MP3，转 WAV 不会补回原始压缩损失。

克隆模式接受 WAV/MP3，原始文件最多 7.5 MiB，Base64 编码后最多 10 MiB。参考音频仅用于该任务。长文本按自然段和句子分段合成，再合并成一个文件；分段间的语气或设计音色可能存在变化。

## 渠道管理

打开 `/admin`，输入 `config.yaml` 的 `api_key`。管理员密钥只保留在当前页面内存中，刷新后重新输入。

- 新增多个 Edge 或 MiMo 渠道，设置名称、默认音色、代理、启停状态。
- MiMo 额外设置 API 基础地址和 API 密钥，默认地址为 `https://api.xiaomimimo.com/v1`。
- 编辑时密钥留空保留原值，填写新值则替换；管理接口不回显完整密钥。
- 保存后新任务立即使用配置。已提交任务继续使用提交时的渠道快照，即使该渠道被修改或删除。
- 测试已保存配置：Edge 查询音色，MiMo 实际合成一段短音频，使用对应上游额度。

首次创建数据库时，从已有 YAML 导入默认 Edge 和 MiMo 渠道。没有 MiMo 密钥时该渠道默认停用。数据库创建后，网页渠道配置是新工作台的配置来源；旧 `/v1` API 继续使用 YAML 中的旧配置。

## 任务与数据

SQLite 数据库、文本、参考音频和生成文件位于 `data_dir`。相对路径基于 `config.yaml` 所在目录，默认 `data`。EXE 和 Python 包默认在配置旁创建数据目录；程序目录须允许写入。

任务通过 HttpOnly 匿名浏览器 Cookie 隔离。清除 Cookie、换浏览器或轮换管理员密钥后，原浏览器任务不能再通过页面访问。其他访客无法列出或下载这些任务。私密文件不作为静态资源公开。

默认在任务结束后保存 7 天，页面显示到期时间；每 5 分钟清理一次到期数据。修改保留天数只影响随后结束的任务，不提前删除已经承诺保留的结果。用户也可主动删除任务。

网络超时、限流或上游临时错误最多自动重试两次。`Retry-After` 超过 60 秒时停止自动重试，允许稍后手动继续。失败、取消及服务重启会保留已完成片段，点击“继续生成”恢复。重启后不会自动重复调用上游。

资源配置保护服务器，不设置会员或每日额度：

```yaml
data_dir: "data"
job_retention_days: 7
max_pending_jobs: 100
max_storage_bytes: 10737418240
```

`max_text_length` 和 `max_audio_bytes` 继续约束单段合成，整篇文章不会套用旧 API 的单次字数/音频上限。`max_request_bytes` 约束提交请求体；长文结果写入磁盘。磁盘不足或队列已满返回可重试错误。使用单个 Uvicorn 进程；不要让多个实例共用同一数据目录。

## Docker 数据持久化与升级

提供的 Compose 自动挂载命名卷 `tts-data:/config/data`，配置文件继续只读。直接 `docker run` 时添加 `--mount type=volume,source=tts-data,target=/config/data`。若自定义 `data_dir`，同时调整挂载位置和目录权限。

升级前停止服务并备份 `config.yaml` 和整个数据卷/数据目录，再启动新版本。备份中包含渠道密钥和用户文本，应按私有数据保存。不要执行 `docker compose down -v`，除非明确要删除全部工作台数据。回滚使用旧版本镜像及对应的数据库备份。

## 新增接口

| 接口 | 用途 |
| --- | --- |
| `GET /api/session` | 初始化匿名浏览器身份及保留时间 |
| `GET /api/channels` | 公开可用渠道和动态表单能力 |
| `GET /api/channels/{id}/voices` | 渠道音色 |
| `POST /api/jobs` | 创建全文或试听任务，返回 202 和任务 ID |
| `GET /api/jobs?offset=0`、`GET /api/jobs/{id}` | 当前浏览器的记录及进度 |
| `GET /api/jobs/{id}/audio?attachment=true` | 播放或下载音频，支持 Range 请求 |
| `GET /api/jobs/{id}/subtitles` | 下载已生成的 SRT |
| `POST /api/jobs/{id}/cancel`、`/retry` | 取消或继续 |
| `DELETE /api/jobs/{id}` | 删除当前浏览器的任务 |
| `/api/admin/channels`、`/api/admin/settings` | 管理员渠道及结果保留配置 |

写入请求需要 `X-Requested-With: tts-workspace`，管理接口另需 `X-API-Key`，浏览器写入限制为同源。任务请求为 `{"channel_id":"edge-default","text":"你好","options":{"voice":"zh-CN-XiaoxiaoNeural","response_format":"mp3"},"subtitles":true,"preview":false}`。字段和选项由渠道接口返回，服务端会再次验证。

原 `/v1/*` 接口保持认证和完整音频响应，服务发现信息迁至 `/api/service`。MiMo 上游目前标注限时免费，上游用量和服务政策由供应商决定。
