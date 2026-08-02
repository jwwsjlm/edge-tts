"""Authenticated and resource-bounded FastAPI application."""

# pylint: disable=too-few-public-methods

import asyncio
import hmac
import io
import json
import logging
import time
import uuid
import zipfile
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

import aiohttp
from fastapi import FastAPI, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from edge_tts import Communicate, exceptions, list_voices
from edge_tts.data_classes import TTSConfig
from edge_tts.submaker import SubMaker
from edge_tts.typing import Voice
from edge_tts.version import __version__

from .config import ServerConfig
from .models import (
    ErrorResponse,
    TTSBundleRequest,
    TTSRequest,
    VoiceInfo,
    VoicesResponse,
)

CommunicatorFactory = Callable[..., Any]
VoicesFactory = Callable[..., Awaitable[List[Voice]]]
_ACCESS_LOGGER = logging.getLogger("uvicorn.error.edge_tts_server.access")
_APP_LOGGER = logging.getLogger("uvicorn.error.edge_tts_server.app")
_SYNTHESIS_ERROR_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}
_TTS_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    **_SYNTHESIS_ERROR_RESPONSES,
    200: {
        "description": "Complete MP3 audio",
        "content": {"audio/mpeg": {"schema": {"type": "string", "format": "binary"}}},
    },
}
_BUNDLE_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    **_SYNTHESIS_ERROR_RESPONSES,
    200: {
        "description": "ZIP containing speech.mp3 and speech.srt",
        "content": {
            "application/zip": {"schema": {"type": "string", "format": "binary"}}
        },
    },
}


def _error(status: int, code: str, message: str, request: Request) -> JSONResponse:
    """Create a stable error and expose its safe type to access logging."""
    request.state.error_type = code
    return JSONResponse(
        status_code=status,
        content={"error": code, "message": message},
    )


def _scope_error(scope: Scope, status: int, code: str, message: str) -> JSONResponse:
    """Create an error before a FastAPI request object exists."""
    scope.setdefault("state", {})["error_type"] = code
    return JSONResponse(status_code=status, content={"error": code, "message": message})


class RequestContextMiddleware:
    """Attach request IDs and emit secret-free structured access logs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            error_type = scope.get("state", {}).get("error_type", "-")
            _ACCESS_LOGGER.info(
                "%s",
                json.dumps(
                    {
                        "duration_ms": round(duration_ms, 2),
                        "error": error_type,
                        "method": scope.get("method", "-"),
                        "path": scope.get("path", "-"),
                        "request_id": request_id,
                        "status": status,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )


class SafeErrorMiddleware:
    """Map unhandled HTTP failures before Starlette's outer error response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if response_started:
                raise
            request_id = scope.get("state", {}).get("request_id", "-")
            _APP_LOGGER.error(
                "Unhandled application failure type=%s request_id=%s",
                type(exc).__name__,
                request_id,
            )
            response = _scope_error(
                scope,
                500,
                "internal_error",
                "Internal server error",
            )
            await response(scope, receive, tracked_send)


class APIKeyMiddleware:
    """Reject unauthorized API calls before parsing or upstream work."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        protected = (scope.get("path"), scope.get("method")) in {
            ("/v1/tts", "POST"),
            ("/v1/tts/bundle", "POST"),
            ("/v1/voices", "GET"),
        }
        if scope["type"] == "http" and protected:
            supplied = ""
            for name, value in scope.get("headers", []):
                if name.lower() == b"x-api-key":
                    supplied = value.decode("utf-8", errors="replace")
                    break
            if not hmac.compare_digest(
                supplied.encode("utf-8"), self.api_key.encode("utf-8")
            ):
                response = _scope_error(
                    scope,
                    401,
                    "unauthorized",
                    "Missing or invalid API key",
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    """Enforce request bytes using both headers and actual ASGI chunks."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (
            scope["type"] == "http"
            and scope.get("path") in {"/v1/tts", "/v1/tts/bundle"}
            and scope.get("method") == "POST"
        ):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        response = _scope_error(
                            scope,
                            413,
                            "request_too_large",
                            "Request body exceeds configured limit",
                        )
                        await response(scope, receive, send)
                        return
                except ValueError:
                    pass

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                response = _scope_error(
                    scope,
                    413,
                    "request_too_large",
                    "Request body exceeds configured limit",
                )
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_receive, send)


class AudioTooLarge(Exception):
    """Raised when buffered synthesis output crosses its configured limit."""


def _normalize_voice(voice: Voice) -> VoiceInfo:
    """Map upstream title-cased fields to the stable HTTP schema."""
    voice_tag = voice["VoiceTag"]
    return VoiceInfo(
        name=voice["ShortName"],
        internal_name=voice["Name"],
        friendly_name=voice["FriendlyName"],
        locale=voice["Locale"],
        language=voice["Locale"].split("-", maxsplit=1)[0],
        gender=voice["Gender"],
        status=voice["Status"],
        suggested_codec=voice["SuggestedCodec"],
        content_categories=list(voice_tag["ContentCategories"]),
        voice_personalities=list(voice_tag["VoicePersonalities"]),
    )


class VoiceCache:  # pylint: disable=too-many-instance-attributes
    """TTL cache with one shared asynchronous refresh and stale fallback."""

    def __init__(
        self,
        factory: VoicesFactory,
        proxy: Optional[str],
        ttl_seconds: int,
        request_timeout_seconds: int,
    ) -> None:
        self._factory = factory
        self._proxy = proxy
        self._ttl_seconds = ttl_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._voices: Optional[List[VoiceInfo]] = None
        self._loaded_at = 0.0
        self._refresh_task: Optional[asyncio.Task[List[VoiceInfo]]] = None
        self._lock = asyncio.Lock()

    def _fresh(self) -> bool:
        return self._voices is not None and (
            time.monotonic() - self._loaded_at < self._ttl_seconds
        )

    async def _refresh(self) -> List[VoiceInfo]:
        raw_voices = await asyncio.wait_for(
            self._factory(proxy=self._proxy),
            timeout=self._request_timeout_seconds,
        )
        normalized = sorted(
            (_normalize_voice(voice) for voice in raw_voices),
            key=lambda voice: voice.name,
        )
        self._voices = normalized
        self._loaded_at = time.monotonic()
        return normalized

    async def get(self) -> List[VoiceInfo]:
        """Return fresh voices, or stale voices if refresh fails."""
        if self._fresh():
            assert self._voices is not None
            return self._voices

        async with self._lock:
            if self._fresh():
                assert self._voices is not None
                return self._voices
            if self._refresh_task is not None and self._refresh_task.done():
                self._refresh_task = None
            if self._refresh_task is None:
                self._refresh_task = asyncio.create_task(self._refresh())
            task = self._refresh_task

        try:
            return await asyncio.shield(task)
        except Exception:  # pylint: disable=broad-exception-caught
            if self._voices is not None:
                return self._voices
            raise
        finally:
            async with self._lock:
                if self._refresh_task is task and task.done():
                    self._refresh_task = None


async def _collect_synthesis(
    options: Dict[str, str],
    communicator_factory: CommunicatorFactory,
    config: ServerConfig,
    *,
    include_subtitles: bool,
) -> tuple[bytes, str]:
    """Collect one upstream synthesis into bounded audio and optional SRT."""
    communicator = communicator_factory(
        options["text"],
        options["voice"],
        rate=options["rate"],
        volume=options["volume"],
        pitch=options["pitch"],
        boundary=options["boundary"],
        proxy=config.proxy,
        connect_timeout=config.upstream_connect_timeout_seconds,
        receive_timeout=config.upstream_receive_timeout_seconds,
    )
    audio = bytearray()
    submaker = SubMaker() if include_subtitles else None
    async for chunk in communicator.stream():
        if chunk["type"] == "audio":
            data = chunk["data"]
            if len(audio) + len(data) > config.max_audio_bytes:
                raise AudioTooLarge
            audio.extend(data)
            continue
        if submaker is not None:
            submaker.feed(chunk)
    subtitles = submaker.get_srt() if submaker is not None else ""
    return bytes(audio), subtitles


def _validated_options(
    payload: TTSRequest,
    boundary: Literal["WordBoundary", "SentenceBoundary"] = "SentenceBoundary",
) -> Dict[str, str]:
    """Reuse edge-tts option validation without coercing API values."""
    validated = TTSConfig(
        voice=payload.voice,
        rate=payload.rate,
        volume=payload.volume,
        pitch=payload.pitch,
        boundary=boundary,
    )
    return {
        "text": payload.text,
        "voice": validated.voice,
        "rate": payload.rate,
        "volume": payload.volume,
        "pitch": payload.pitch,
        "boundary": validated.boundary,
    }


def create_app(
    config: ServerConfig,
    communicator_factory: CommunicatorFactory = Communicate,
    *,
    voices_factory: VoicesFactory = list_voices,
) -> FastAPI:
    """Build an isolated FastAPI application with deployment safeguards."""
    docs_url = "/docs" if config.docs_enabled else None
    openapi_url = "/openapi.json" if config.docs_enabled else None
    app = FastAPI(
        title="Edge TTS HTTP Server",
        version=__version__,
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
        swagger_ui_parameters={"persistAuthorization": False},
    )
    api_key_header = APIKeyHeader(
        name="X-API-Key", scheme_name="APIKeyHeader", auto_error=False
    )
    capacity = asyncio.Semaphore(config.max_concurrent_requests)
    voices_cache = VoiceCache(
        voices_factory,
        proxy=config.proxy,
        ttl_seconds=config.voices_cache_ttl_seconds,
        request_timeout_seconds=config.request_timeout_seconds,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return _error(400, "invalid_request", "Request body is invalid", request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error(404, "not_found", "Route not found", request)
        if exc.status_code == 405:
            return _error(405, "method_not_allowed", "Method not allowed", request)
        return _error(exc.status_code, "http_error", "HTTP request failed", request)

    @app.get("/health", tags=["service"])
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/v1/voices",
        tags=["voices"],
        response_model=VoicesResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def voices(
        request: Request,
        locale: Optional[str] = Query(default=None),
        language: Optional[str] = Query(default=None),
        gender: Optional[str] = Query(default=None),
        _api_key: Optional[str] = Security(api_key_header),
    ) -> VoicesResponse | JSONResponse:
        if gender is not None and gender.casefold() not in {"female", "male"}:
            return _error(
                400,
                "invalid_request",
                "gender must be Female or Male",
                request,
            )
        try:
            available = await voices_cache.get()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _APP_LOGGER.error(
                "Voice list refresh failed type=%s request_id=%s",
                type(exc).__name__,
                request.state.request_id,
            )
            return _error(
                502,
                "upstream_error",
                "TTS upstream service failed",
                request,
            )

        filters = {
            "locale": locale.casefold() if locale is not None else None,
            "language": language.casefold() if language is not None else None,
            "gender": gender.casefold() if gender is not None else None,
        }
        filtered = [
            voice
            for voice in available
            if all(
                expected is None or getattr(voice, field).casefold() == expected
                for field, expected in filters.items()
            )
        ]
        return VoicesResponse(voices=filtered)

    async def run_synthesis(  # pylint: disable=too-many-return-statements
        payload: TTSRequest,
        request: Request,
        boundary: Literal["WordBoundary", "SentenceBoundary"],
        *,
        include_subtitles: bool,
    ) -> tuple[bytes, str] | JSONResponse:
        """Apply the shared validation, limits and error contract."""
        if not payload.text.strip():
            return _error(400, "invalid_request", "text must not be blank", request)
        if len(payload.text) > config.max_text_length:
            return _error(
                413,
                "text_too_long",
                "text exceeds configured limit",
                request,
            )
        try:
            options = _validated_options(payload, boundary)
        except (TypeError, ValueError) as exc:
            return _error(400, "invalid_request", str(exc), request)

        if capacity.locked():
            response = _error(
                429,
                "too_many_requests",
                "Too many concurrent requests",
                request,
            )
            response.headers["Retry-After"] = "1"
            return response

        await capacity.acquire()
        try:
            try:
                return await asyncio.wait_for(
                    _collect_synthesis(
                        options,
                        communicator_factory,
                        config,
                        include_subtitles=include_subtitles,
                    ),
                    timeout=config.request_timeout_seconds,
                )
            except asyncio.TimeoutError:
                return _error(
                    504,
                    "upstream_timeout",
                    "TTS upstream request timed out",
                    request,
                )
            except AudioTooLarge:
                return _error(
                    413,
                    "audio_too_large",
                    "Generated audio exceeds configured limit",
                    request,
                )
            except (exceptions.EdgeTTSException, aiohttp.ClientError):
                return _error(
                    502,
                    "upstream_error",
                    "TTS upstream service failed",
                    request,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                _APP_LOGGER.error(
                    "Unexpected synthesis failure type=%s request_id=%s",
                    type(exc).__name__,
                    request.state.request_id,
                )
                return _error(
                    500,
                    "internal_error",
                    "Internal server error",
                    request,
                )
        finally:
            capacity.release()

    @app.post(
        "/v1/tts",
        tags=["tts"],
        response_class=Response,
        responses=_TTS_RESPONSES,
    )
    async def synthesize(
        payload: TTSRequest,
        request: Request,
        _api_key: Optional[str] = Security(api_key_header),
    ) -> Response:
        result = await run_synthesis(
            payload,
            request,
            "SentenceBoundary",
            include_subtitles=False,
        )
        if isinstance(result, JSONResponse):
            return result
        audio, _subtitles = result
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={"Content-Disposition": 'inline; filename="speech.mp3"'},
        )

    @app.post(
        "/v1/tts/bundle",
        tags=["tts"],
        response_class=Response,
        responses=_BUNDLE_RESPONSES,
    )
    async def synthesize_bundle(
        payload: TTSBundleRequest,
        request: Request,
        _api_key: Optional[str] = Security(api_key_header),
    ) -> Response:
        result = await run_synthesis(
            payload,
            request,
            payload.boundary,
            include_subtitles=True,
        )
        if isinstance(result, JSONResponse):
            return result
        audio, subtitles = result
        bundle = io.BytesIO()
        with zipfile.ZipFile(
            bundle, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("speech.mp3", audio)
            archive.writestr("speech.srt", subtitles.encode("utf-8"))
        return Response(
            content=bundle.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="speech-bundle.zip"'},
        )

    app.add_middleware(BodyLimitMiddleware, max_bytes=config.max_request_bytes)
    app.add_middleware(APIKeyMiddleware, api_key=config.api_key)
    app.add_middleware(SafeErrorMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app
