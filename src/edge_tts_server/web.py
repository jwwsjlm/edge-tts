"""Public workspace routes and authenticated channel administration."""

# pylint: disable=too-many-statements,too-many-locals

import asyncio
import hashlib
import hmac
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .channels import (
    ChannelInput,
    admin_channel,
    capabilities,
    channel_config,
    preset_voices,
)
from .config import ServerConfig
from .jobs import EngineFactory, Workspace, WorkspaceBusy, public_job
from .models import TTSRequest
from .synthesis import SynthesisEngine, VoiceCache, _validate_provider_request

COOKIE = "tts_browser"


class WebError(Exception):
    """A safe client-facing workspace failure."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class JobInput(BaseModel):
    """A whole-document job; options are validated against channel capabilities."""

    model_config = ConfigDict(strict=True, extra="forbid")
    channel_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)
    subtitles: bool = False
    preview: bool = False


class SettingsInput(BaseModel):
    """Retention is applied when a task reaches a terminal state."""

    model_config = ConfigDict(strict=True, extra="forbid")
    retention_days: int = Field(ge=1, le=3650)


def mount_workspace(
    app: FastAPI,
    config: ServerConfig,
    capacity: asyncio.Semaphore,
    engine_factory: EngineFactory = SynthesisEngine,
) -> None:
    """Install the workspace without altering the legacy authenticated API."""
    workspace = Workspace(config, capacity, engine_factory)
    app.state.workspace = workspace
    app.router.add_event_handler("startup", workspace.start)
    app.router.add_event_handler("shutdown", workspace.close)
    voice_caches: dict[str, tuple[float, VoiceCache]] = {}
    signing_key = hashlib.sha256(
        config.api_key.encode("utf-8") + b"workspace-cookie"
    ).digest()

    def owner(request: Request) -> str:
        cookie = request.cookies.get(COOKIE, "")
        nonce, separator, signature = cookie.partition(".")
        expected = hmac.new(
            signing_key, nonce.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if (
            not separator
            or len(nonce) != 43
            or not hmac.compare_digest(signature, expected)
        ):
            raise WebError(
                401, "browser_session_required", "浏览器会话已失效，请刷新页面后重试。"
            )
        return hashlib.sha256(nonce.encode("utf-8")).hexdigest()

    async def channel_by_id(channel_id: str, *, public: bool = True) -> dict[str, Any]:
        await workspace.start()
        channel = workspace.store.channel(channel_id)
        if channel is None or (
            public
            and (
                not channel["enabled"]
                or (channel["provider"] == "mimo" and not channel.get("api_key"))
            )
        ):
            raise WebError(
                404, "channel_not_found", "该渠道已停用或不存在，请选择其他渠道。"
            )
        return channel

    async def owned_job(request: Request, job_id: str) -> dict[str, Any]:
        await workspace.start()
        job = workspace.store.job(job_id, owner(request))
        if job is None:
            raise WebError(404, "job_not_found", "任务不存在或已到期。")
        if job["expires_at"] is not None and job["expires_at"] <= time.time():
            raise WebError(410, "job_expired", "任务已到期，文件已进入清理队列。")
        return job

    @app.exception_handler(WebError)
    async def web_error(request: Request, exc: WebError) -> JSONResponse:
        request.state.error_type = exc.code
        return JSONResponse(
            {"error": exc.code, "message": exc.message},
            status_code=exc.status,
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(WorkspaceBusy)
    async def workspace_busy(request: Request, exc: WorkspaceBusy) -> JSONResponse:
        request.state.error_type = "workspace_busy"
        return JSONResponse(
            {"error": "workspace_busy", "message": str(exc)},
            status_code=503,
            headers={"Retry-After": "10", "Cache-Control": "no-store"},
        )

    @app.get("/api/session", include_in_schema=False)
    async def session(request: Request) -> JSONResponse:
        await workspace.start()
        response = JSONResponse(
            {"retention_days": workspace.store.retention_days()},
            headers={"Cache-Control": "no-store"},
        )
        try:
            owner(request)
        except WebError:
            nonce = secrets.token_urlsafe(32)
            signature = hmac.new(
                signing_key, nonce.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            response.set_cookie(
                COOKIE,
                f"{nonce}.{signature}",
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                max_age=365 * 86400,
            )
        return response

    @app.get("/api/channels", include_in_schema=False)
    async def channels() -> JSONResponse:
        await workspace.start()
        return JSONResponse(
            {
                "channels": [
                    capabilities(c, config)
                    for c in workspace.store.channels()
                    if c["enabled"] and (c["provider"] != "mimo" or c.get("api_key"))
                ]
            }
        )

    @app.get("/api/channels/{channel_id}/voices", include_in_schema=False)
    async def channel_voices(channel_id: str) -> JSONResponse:
        channel = await channel_by_id(channel_id)
        if channel["provider"] == "mimo":
            return JSONResponse({"voices": preset_voices()})
        cached = voice_caches.get(channel_id)
        if cached is None or cached[0] != channel["updated_at"]:
            engine = engine_factory(channel_config(config, channel))
            cached = (channel["updated_at"], engine.voices_cache)
            voice_caches[channel_id] = cached
            await engine.close()
        try:
            return JSONResponse(
                {"voices": [voice.model_dump() for voice in await cached[1].get()]}
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise WebError(
                502, "voice_list_unavailable", "暂时无法加载音色，请重试或切换渠道。"
            ) from exc

    @app.post("/api/jobs", status_code=202, include_in_schema=False)
    async def create_job(payload: JobInput, request: Request) -> JSONResponse:
        identity = owner(request)
        channel = await channel_by_id(payload.channel_id)
        try:
            result = await workspace.submit(
                identity,
                channel,
                payload.text,
                payload.options,
                payload.subtitles,
                payload.preview,
            )
            return JSONResponse(result, status_code=202)
        except (ValueError, ValidationError) as exc:
            raise WebError(
                400, "invalid_options", "请检查文本、音色及当前模式的必填参数。"
            ) from exc

    @app.get("/api/jobs", include_in_schema=False)
    async def jobs(
        request: Request, offset: int = Query(default=0, ge=0)
    ) -> JSONResponse:
        await workspace.start()
        results = workspace.store.jobs(owner(request), offset)
        return JSONResponse(
            {
                "jobs": [public_job(job) for job in results],
                "next_offset": offset + 50 if len(results) == 50 else None,
            }
        )

    @app.get("/api/jobs/{job_id}", include_in_schema=False)
    async def job(request: Request, job_id: str) -> JSONResponse:
        return JSONResponse(public_job(await owned_job(request, job_id)))

    @app.post("/api/jobs/{job_id}/cancel", include_in_schema=False)
    async def cancel_job(request: Request, job_id: str) -> JSONResponse:
        current = await owned_job(request, job_id)
        if current["status"] in {"queued", "running", "merging"}:
            await workspace.cancel(job_id)
        return JSONResponse(public_job(await owned_job(request, job_id)))

    @app.post("/api/jobs/{job_id}/retry", status_code=202, include_in_schema=False)
    async def retry_job(request: Request, job_id: str) -> JSONResponse:
        current = await owned_job(request, job_id)
        if current["status"] not in {"failed", "cancelled", "interrupted"}:
            raise WebError(
                409, "job_not_retryable", "只有失败、中断或取消的任务可以继续。"
            )
        await workspace.retry(job_id)
        return JSONResponse(
            public_job(await owned_job(request, job_id)), status_code=202
        )

    @app.delete("/api/jobs/{job_id}", status_code=204, include_in_schema=False)
    async def delete_job(request: Request, job_id: str) -> Response:
        current = await owned_job(request, job_id)
        if current["status"] in {"queued", "running", "merging"}:
            await workspace.cancel(job_id)
        import shutil  # pylint: disable=import-outside-toplevel

        directory = workspace.directory(job_id)
        if directory.exists():
            await asyncio.to_thread(shutil.rmtree, directory)
        workspace.store.delete_job(job_id)
        return Response(status_code=204)

    async def download(
        request: Request, job_id: str, subtitles: bool, attachment: bool
    ) -> FileResponse:
        current = await owned_job(request, job_id)
        if current["status"] != "completed" or (
            subtitles and not current["payload"]["subtitles"]
        ):
            raise WebError(409, "result_unavailable", "该文件尚未生成。")
        extension = (
            "srt"
            if subtitles
            else current["payload"]["options"].get("response_format", "mp3")
        )
        path = workspace.directory(job_id) / f"speech.{extension}"
        if not path.is_file():
            raise WebError(404, "result_missing", "文件已被清理，请重新生成。")
        media_type = {
            "srt": "application/x-subrip",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
        }[extension]
        return FileResponse(
            path,
            media_type=media_type,
            filename=f"speech-{job_id[:8]}.{extension}",
            content_disposition_type="attachment" if attachment else "inline",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/jobs/{job_id}/audio", include_in_schema=False)
    async def job_audio(
        request: Request, job_id: str, attachment: bool = False
    ) -> FileResponse:
        return await download(request, job_id, False, attachment)

    @app.get("/api/jobs/{job_id}/subtitles", include_in_schema=False)
    async def job_subtitles(request: Request, job_id: str) -> FileResponse:
        return await download(request, job_id, True, True)

    @app.get("/api/admin/channels", include_in_schema=False)
    async def admin_channels() -> JSONResponse:
        await workspace.start()
        return JSONResponse(
            {"channels": [admin_channel(c) for c in workspace.store.channels()]}
        )

    async def save_channel(
        payload: ChannelInput, channel_id: str | None = None
    ) -> dict[str, Any]:
        await workspace.start()
        data = payload.model_dump()
        if channel_id:
            existing = await channel_by_id(channel_id, public=False)
            if existing["provider"] != data["provider"]:
                raise WebError(
                    400, "provider_immutable", "渠道类型不可更改，请新建渠道。"
                )
            if payload.api_key is None:
                data["api_key"] = existing.get("api_key")
            data["id"] = channel_id
        if data["provider"] == "mimo" and data["enabled"] and not data.get("api_key"):
            raise WebError(400, "api_key_required", "启用 MiMo 渠道前请填写 API 密钥。")
        if data["provider"] == "edge":
            try:
                _validate_provider_request(
                    TTSRequest(
                        text="test",
                        voice=data["default_voice"] or "zh-CN-XiaoxiaoNeural",
                    ),
                    config,
                )
            except ValueError as exc:
                raise WebError(400, "invalid_voice", "默认音色格式无效。") from exc
        saved = workspace.store.save_channel(data)
        voice_caches.pop(saved["id"], None)
        return admin_channel(saved)

    @app.post("/api/admin/channels", status_code=201, include_in_schema=False)
    async def add_channel(payload: ChannelInput) -> JSONResponse:
        return JSONResponse(await save_channel(payload), status_code=201)

    @app.put("/api/admin/channels/{channel_id}", include_in_schema=False)
    async def edit_channel(channel_id: str, payload: ChannelInput) -> JSONResponse:
        return JSONResponse(await save_channel(payload, channel_id))

    @app.delete(
        "/api/admin/channels/{channel_id}", status_code=204, include_in_schema=False
    )
    async def remove_channel(channel_id: str) -> Response:
        await channel_by_id(channel_id, public=False)
        workspace.store.delete_channel(channel_id)
        voice_caches.pop(channel_id, None)
        return Response(status_code=204)

    @app.post("/api/admin/channels/{channel_id}/test", include_in_schema=False)
    async def test_channel(channel_id: str) -> dict[str, str]:
        channel = await channel_by_id(channel_id, public=False)
        engine = engine_factory(channel_config(config, channel))
        try:
            async with capacity:
                if channel["provider"] == "edge":
                    await engine.voices_cache.get()
                else:
                    await engine.synthesize(
                        TTSRequest(
                            model="mimo-v2-tts",
                            text="连接测试。",
                            voice="mimo_default",
                            response_format="wav",
                        )
                    )
            return {"status": "ok", "message": "渠道连接正常。"}
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise WebError(
                502, "channel_test_failed", "连接失败，请检查地址、密钥和网络。"
            ) from exc
        finally:
            await engine.close()

    @app.get("/api/admin/settings", include_in_schema=False)
    async def settings() -> dict[str, int]:
        await workspace.start()
        return {"retention_days": workspace.store.retention_days()}

    @app.put("/api/admin/settings", include_in_schema=False)
    async def save_settings(payload: SettingsInput) -> dict[str, int]:
        await workspace.start()
        workspace.store.set_retention_days(payload.retention_days)
        return {"retention_days": payload.retention_days}

    static = Path(__file__).parent / "static"
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/admin", include_in_schema=False)
    async def frontend() -> Response:
        if not (static / "index.html").is_file():
            return Response(
                "Frontend assets are missing. Run npm ci && npm run build in web/.",
                status_code=503,
            )
        return FileResponse(
            static / "index.html",
            headers={
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
                    "frame-ancestors 'none'; base-uri 'self'"
                ),
            },
        )
