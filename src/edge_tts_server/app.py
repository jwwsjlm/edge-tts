"""Authenticated and resource-bounded FastAPI application."""

# pylint: disable=too-few-public-methods,too-many-statements,too-many-lines,too-many-locals

import asyncio
import hashlib
import hmac
import io
import json
import logging
import time
import uuid
import zipfile
from typing import Any, Callable, Dict, List, Literal, Optional
from urllib.parse import urlsplit

import aiohttp
from fastapi import FastAPI, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from edge_tts import Communicate, exceptions, list_voices
from edge_tts.version import __version__

from .audio import (
    AudioConversionError,
    ConvertedAudioTooLarge,
    convert_audio,
    inspect_audio,
)
from .config import ServerConfig
from .mimo import (
    MIMO_PUBLIC_MODEL,
    MiMoAudioTooLarge,
    MiMoError,
    MiMoRateLimitError,
    ReferenceAudioTooLarge,
)
from .models import (
    ErrorResponse,
    ModelInfo,
    ModelsResponse,
    TTSBundleRequest,
    TTSRequest,
    VoicesResponse,
)
from .synthesis import (
    AudioConverter,
    AudioTooLarge,
    CommunicatorFactory,
    MiMoClientLike,
    RequestFieldError,
    SynthesisEngine,
    VoicesFactory,
    _field_detail,
    _mimo_voices,
    _validate_provider_request,
)
from .web import mount_workspace

_ACCESS_LOGGER = logging.getLogger("uvicorn.error.edge_tts_server.access")
_APP_LOGGER = logging.getLogger("uvicorn.error.edge_tts_server.app")
_PROTECTED_ROUTES = frozenset(
    {
        ("/v1/tts", "POST"),
        ("/v1/tts/bundle", "POST"),
        ("/v1/voices", "GET"),
        ("/v1/models", "GET"),
    }
)
_FIELD_ALLOWED_VALUES = {
    "model": ["edge-tts", "mimo-v2-tts"],
    "response_format": ["mp3", "wav"],
    "mimo_mode": ["preset", "design", "clone"],
    "boundary": ["WordBoundary", "SentenceBoundary"],
}
_SYNTHESIS_ERROR_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}
_TTS_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    **_SYNTHESIS_ERROR_RESPONSES,
    200: {
        "description": "Complete MP3 or WAV audio",
        "content": {
            "audio/mpeg": {"schema": {"type": "string", "format": "binary"}},
            "audio/wav": {"schema": {"type": "string", "format": "binary"}},
        },
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


def _field_error(
    status: int,
    code: str,
    message: str,
    request: Request,
    fields: List[Dict[str, Any]],
) -> JSONResponse:
    """Create a stable field-level validation error without exposing secrets."""
    request.state.error_type = code
    return JSONResponse(
        status_code=status,
        content={"error": code, "message": message, "fields": fields},
    )


def _scope_error(scope: Scope, status: int, code: str, message: str) -> JSONResponse:
    """Create an error before a FastAPI request object exists."""
    scope.setdefault("state", {})["error_type"] = code
    return JSONResponse(status_code=status, content={"error": code, "message": message})


def _request_metadata_headers(
    payload: TTSRequest, config: ServerConfig
) -> Dict[str, str]:
    """Build safe request correlation headers for success and failure responses."""
    headers = {
        "X-Text-SHA256": hashlib.sha256(payload.text.encode("utf-8")).hexdigest(),
        "X-Text-Length": str(len(payload.text)),
    }
    if payload.segment_id is not None:
        headers["X-Segment-ID"] = payload.segment_id
    if payload.sequence is not None:
        headers["X-Sequence"] = str(payload.sequence)
    if (
        payload.model == "mimo-v2-tts"
        and len(payload.text) > config.mimo_recommended_max_text_length
    ):
        headers["X-Text-Length-Warning"] = "recommended_limit_exceeded"
        headers["X-Recommended-Max-Text-Length"] = str(
            config.mimo_recommended_max_text_length
        )
    return headers


def _audio_headers(
    payload: TTSRequest,
    audio: bytes,
    audio_format: str,
    config: ServerConfig,
    duration_ms: Optional[int] = None,
) -> Dict[str, str]:
    """Build safe correlation and basic quality metadata headers."""
    headers = _request_metadata_headers(payload, config)
    headers["X-Audio-Bytes"] = str(len(audio))
    audio_info = inspect_audio(audio, audio_format, duration_ms)
    if audio_info.duration_ms is not None:
        headers["X-Audio-Duration-Ms"] = str(audio_info.duration_ms)
    if audio_info.sample_rate_hz is not None:
        headers["X-Audio-Sample-Rate-Hz"] = str(audio_info.sample_rate_hz)
    if audio_info.channels is not None:
        headers["X-Audio-Channels"] = str(audio_info.channels)
    if audio_info.bits_per_sample is not None:
        headers["X-Audio-Bits-Per-Sample"] = str(audio_info.bits_per_sample)
    headers["X-Quality-Status"] = "pass" if audio_info.valid else "fail"
    return headers


def _with_request_metadata(
    response: JSONResponse, payload: TTSRequest, config: ServerConfig
) -> JSONResponse:
    """Make failed segments as easy to correlate as successful audio."""
    response.headers.update(_request_metadata_headers(payload, config))
    return response


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
            if _ACCESS_LOGGER.isEnabledFor(logging.INFO):
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
        self.api_key = api_key.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        protected = (
            scope.get("path"),
            scope.get("method"),
        ) in _PROTECTED_ROUTES or scope.get("path", "").startswith("/api/admin/")
        if scope["type"] == "http" and protected:
            supplied = b""
            for name, value in scope.get("headers", []):
                if name.lower() == b"x-api-key":
                    supplied = value
                    break
            if not hmac.compare_digest(supplied, self.api_key):
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
            and (
                scope.get("path") in {"/v1/tts", "/v1/tts/bundle"}
                or scope.get("path", "").startswith("/api/")
            )
            and scope.get("method") in {"POST", "PUT", "PATCH", "DELETE"}
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

        chunks: List[bytes] = []
        body_size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            body_size += len(chunk)
            if body_size > self.max_bytes:
                response = _scope_error(
                    scope,
                    413,
                    "request_too_large",
                    "Request body exceeds configured limit",
                )
                await response(scope, receive, send)
                return
            if chunk:
                chunks.append(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        await self.app(scope, replay_receive, send)


class WorkspaceGuardMiddleware:
    """Require same-origin browser writes and prevent caching private API metadata."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        if scope.get("method") in {"POST", "PUT", "PATCH", "DELETE"}:
            headers = dict(scope.get("headers", []))
            origin = headers.get(b"origin", b"").decode("latin-1")
            host = headers.get(b"host", b"").decode("latin-1")
            if headers.get(b"x-requested-with") != b"tts-workspace" or (
                origin and urlsplit(origin).netloc != host
            ):
                await _scope_error(
                    scope,
                    403,
                    "invalid_origin",
                    "Same-origin workspace request required",
                )(scope, receive, send)
                return

        async def no_cache(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"cache-control"
                ]
                message["headers"] = [
                    *headers,
                    (b"cache-control", b"private, no-store"),
                ]
            await send(message)

        await self.app(scope, receive, no_cache)


def create_app(
    config: ServerConfig,
    communicator_factory: CommunicatorFactory = Communicate,
    *,
    voices_factory: VoicesFactory = list_voices,
    mimo_client: Optional[MiMoClientLike] = None,
    audio_converter: AudioConverter = convert_audio,
    workspace_engine_factory: Optional[
        Callable[[ServerConfig], SynthesisEngine]
    ] = None,
) -> FastAPI:
    """Build an isolated FastAPI application with deployment safeguards."""
    docs_url = "/docs" if config.docs_enabled else None
    openapi_url = "/openapi.json" if config.docs_enabled else None
    app = FastAPI(
        title="Edge TTS + MiMo HTTP Server",
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
    engine = SynthesisEngine(
        config,
        communicator_factory,
        voices_factory=voices_factory,
        mimo_client=mimo_client,
        audio_converter=audio_converter,
    )
    app.router.add_event_handler("shutdown", engine.close)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields: List[Dict[str, Any]] = []
        for item in exc.errors():
            location = [str(part) for part in item.get("loc", []) if part != "body"]
            field = ".".join(location) or "body"
            fields.append(
                _field_detail(
                    field,
                    str(item.get("msg", "invalid value")),
                    _FIELD_ALLOWED_VALUES.get(field),
                )
            )
        return _field_error(
            400,
            "invalid_request",
            "Request body is invalid",
            request,
            fields,
        )

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

    @app.get("/api/service", tags=["service"])
    async def service_info() -> Dict[str, Optional[str]]:
        """Return a small discovery document for operators and first-time users."""
        return {
            "service": "Edge TTS + MiMo HTTP Server",
            "version": __version__,
            "docs": "/docs" if config.docs_enabled else None,
            "health": "/health",
            "models": "/v1/models",
        }

    @app.get(
        "/v1/models",
        tags=["models"],
        summary="List supported synthesis models",
        description=(
            "Returns the available providers, voice modes, output formats and "
            "subtitle support. Requires X-API-Key."
        ),
        response_model=ModelsResponse,
        responses={401: {"model": ErrorResponse}},
    )
    async def models(
        _api_key: Optional[str] = Security(api_key_header),
    ) -> ModelsResponse:
        return ModelsResponse(
            models=[
                ModelInfo(
                    id="edge-tts",
                    provider="Microsoft Edge",
                    modes=["preset"],
                    response_formats=["mp3", "wav"],
                    supports_subtitles=True,
                ),
                ModelInfo(
                    id=MIMO_PUBLIC_MODEL,
                    provider="Xiaomi MiMo V2.5",
                    modes=["preset", "design", "clone"],
                    response_formats=["mp3", "wav"],
                    supports_subtitles=False,
                ),
            ]
        )

    @app.get(
        "/v1/voices",
        tags=["voices"],
        summary="List and filter voices",
        description=(
            "Returns Edge or MiMo preset voices. locale, language and gender are "
            "case-insensitive exact-match filters. Requires X-API-Key."
        ),
        response_model=VoicesResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def voices(
        request: Request,
        locale: Optional[str] = Query(
            default=None,
            description="Exact locale filter, for example zh-CN or en-US.",
        ),
        language: Optional[str] = Query(
            default=None,
            description="Exact language-code filter, for example zh or en.",
        ),
        gender: Optional[str] = Query(
            default=None,
            description="Exact gender filter. Only Female or Male is accepted.",
        ),
        model: Literal["edge-tts", "mimo-v2-tts"] = Query(
            default="edge-tts",
            description="Voice provider. Omit it to list Edge TTS voices.",
        ),
        _api_key: Optional[str] = Security(api_key_header),
    ) -> VoicesResponse | JSONResponse:
        if gender is not None and gender.casefold() not in {"female", "male"}:
            return _error(
                400,
                "invalid_request",
                "gender must be Female or Male",
                request,
            )
        if model == MIMO_PUBLIC_MODEL:
            available = _mimo_voices()
        else:
            try:
                available = await engine.voices_cache.get()
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

    async def run_synthesis(  # pylint: disable=too-many-return-statements,too-many-branches
        payload: TTSRequest,
        request: Request,
        boundary: Literal["WordBoundary", "SentenceBoundary"],
        *,
        include_subtitles: bool,
    ) -> tuple[bytes, str, str, Optional[int]] | JSONResponse:
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
            _edge_options, mimo_options = _validate_provider_request(
                payload, config, boundary
            )
            if include_subtitles:
                if payload.model != "edge-tts":
                    return _error(
                        400,
                        "unsupported_model",
                        "Subtitles are only supported by edge-tts",
                        request,
                    )
                if payload.response_format != "mp3":
                    return _error(
                        400,
                        "invalid_request",
                        "Bundle response_format must be mp3",
                        request,
                    )
            if mimo_options is not None and engine.mimo_client is None:
                return _error(
                    503,
                    "provider_not_configured",
                    "MiMo provider is not configured",
                    request,
                )
        except ReferenceAudioTooLarge:
            return _error(
                413,
                "reference_audio_too_large",
                "Reference audio exceeds configured limit",
                request,
            )
        except RequestFieldError as exc:
            return _field_error(400, "invalid_request", str(exc), request, exc.fields)
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

                return await engine.synthesize(
                    payload, boundary, include_subtitles=include_subtitles
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
            except MiMoAudioTooLarge:
                return _error(
                    413,
                    "audio_too_large",
                    "Generated audio exceeds configured limit",
                    request,
                )
            except ConvertedAudioTooLarge:
                return _error(
                    413,
                    "audio_too_large",
                    "Generated audio exceeds configured limit",
                    request,
                )
            except MiMoRateLimitError as exc:
                response = _error(
                    503,
                    "upstream_rate_limited",
                    "MiMo upstream rate limit exceeded",
                    request,
                )
                if exc.retry_after is not None:
                    response.headers["Retry-After"] = exc.retry_after
                return response
            except MiMoError:
                return _error(
                    502,
                    "upstream_error",
                    "TTS upstream service failed",
                    request,
                )
            except AudioConversionError as exc:
                _APP_LOGGER.error(
                    "Audio conversion failed type=%s request_id=%s",
                    type(exc).__name__,
                    request.state.request_id,
                )
                return _error(
                    500,
                    "internal_error",
                    "Internal server error",
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
        summary="Synthesize complete audio",
        description=(
            "Synthesizes with Edge TTS or Xiaomi MiMo and returns one complete MP3 "
            "or WAV response. This endpoint does not use HTTP streaming."
        ),
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
            return _with_request_metadata(result, payload, config)
        audio, _subtitles, audio_format, duration_ms = result
        media_type = "audio/mpeg" if audio_format == "mp3" else "audio/wav"
        headers = _audio_headers(
            payload, audio, audio_format, config, duration_ms=duration_ms
        )
        headers["Content-Disposition"] = f'inline; filename="speech.{audio_format}"'
        return Response(
            content=audio,
            media_type=media_type,
            headers=headers,
        )

    @app.post(
        "/v1/tts/bundle",
        tags=["tts"],
        summary="Synthesize Edge audio and SRT subtitles",
        description=(
            "Returns a ZIP containing speech.mp3 and speech.srt from one Edge TTS "
            "request. MiMo and WAV output are not supported by this endpoint."
        ),
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
            return _with_request_metadata(result, payload, config)
        audio, subtitles, _audio_format, duration_ms = result
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, mode="w") as archive:
            archive.writestr("speech.mp3", audio, compress_type=zipfile.ZIP_STORED)
            archive.writestr(
                "speech.srt",
                subtitles.encode("utf-8"),
                compress_type=zipfile.ZIP_DEFLATED,
            )
        headers = _audio_headers(payload, audio, "mp3", config, duration_ms=duration_ms)
        headers["Content-Disposition"] = 'attachment; filename="speech-bundle.zip"'
        return Response(
            content=bundle.getvalue(),
            media_type="application/zip",
            headers=headers,
        )

    if workspace_engine_factory is None:

        def workspace_engine_factory(channel_config: ServerConfig) -> SynthesisEngine:
            return SynthesisEngine(
                channel_config,
                communicator_factory,
                voices_factory=voices_factory,
                mimo_client=mimo_client,
                audio_converter=audio_converter,
            )

    mount_workspace(app, config, capacity, workspace_engine_factory)
    app.add_middleware(BodyLimitMiddleware, max_bytes=config.max_request_bytes)
    app.add_middleware(WorkspaceGuardMiddleware)
    app.add_middleware(APIKeyMiddleware, api_key=config.api_key)
    app.add_middleware(SafeErrorMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app
