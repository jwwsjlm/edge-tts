"""Authenticated and resource-bounded FastAPI application."""

# pylint: disable=too-few-public-methods,too-many-statements

import asyncio
import hmac
import io
import json
import logging
import time
import uuid
import zipfile
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Protocol

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

from .audio import AudioConversionError, ConvertedAudioTooLarge, convert_audio
from .config import ServerConfig
from .mimo import (
    MIMO_PRESET_VOICES,
    MIMO_PUBLIC_MODEL,
    MiMoAudioTooLarge,
    MiMoClient,
    MiMoError,
    MiMoRateLimitError,
    MiMoSynthesisRequest,
    ReferenceAudioTooLarge,
    normalize_reference_audio,
    validate_preset_voice,
)
from .models import (
    ErrorResponse,
    ModelInfo,
    ModelsResponse,
    TTSBundleRequest,
    TTSRequest,
    VoiceInfo,
    VoicesResponse,
)

CommunicatorFactory = Callable[..., Any]
VoicesFactory = Callable[..., Awaitable[List[Voice]]]
AudioConverter = Callable[[bytes, str, str, int], Awaitable[bytes]]


class MiMoClientLike(Protocol):
    """Minimal injectable MiMo client contract used by the application."""

    async def synthesize(self, options: MiMoSynthesisRequest) -> bytes:
        """Return complete WAV audio."""


_ACCESS_LOGGER = logging.getLogger("uvicorn.error.edge_tts_server.access")
_APP_LOGGER = logging.getLogger("uvicorn.error.edge_tts_server.app")
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
            ("/v1/models", "GET"),
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


def _mimo_voices() -> List[VoiceInfo]:
    """Return the official static MiMo V2.5 preset voice catalog."""
    return [
        VoiceInfo(
            name=voice.voice_id,
            internal_name=voice.voice_id,
            friendly_name=voice.voice_id,
            locale=voice.locale,
            language=voice.language,
            gender=voice.gender,
            status="GA",
            suggested_codec="audio/wav;codec=pcm",
            content_categories=["Preset"],
            voice_personalities=[],
        )
        for voice in MIMO_PRESET_VOICES
    ]


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


def _validate_provider_request(
    payload: TTSRequest,
    config: ServerConfig,
    boundary: Literal["WordBoundary", "SentenceBoundary"] = "SentenceBoundary",
) -> tuple[Optional[Dict[str, str]], Optional[MiMoSynthesisRequest]]:
    """Validate model-specific options without silently ignoring any field."""
    if payload.model == "edge-tts":
        if payload.mimo_mode != "preset":
            raise ValueError("mimo_mode is only supported by mimo-v2-tts")
        if payload.voice_description is not None or payload.reference_audio is not None:
            raise ValueError("MiMo-only fields are not supported by edge-tts")
        return _validated_options(payload, boundary), None

    if len(payload.text) > min(config.max_text_length, 3000):
        raise ValueError("MiMo text exceeds the 3000 character limit")
    if payload.rate != "+0%" or payload.volume != "+0%" or payload.pitch != "+0Hz":
        raise ValueError("rate, volume and pitch are not supported by mimo-v2-tts")

    explicitly_set = payload.model_fields_set
    if payload.mimo_mode == "preset":
        if payload.voice_description is not None or payload.reference_audio is not None:
            raise ValueError("preset mode does not accept design or clone fields")
        voice = payload.voice if "voice" in explicitly_set else "mimo_default"
        validate_preset_voice(voice)
        return None, MiMoSynthesisRequest(
            text=payload.text,
            mode="preset",
            voice=voice,
        )

    if "voice" in explicitly_set:
        raise ValueError("voice is only supported by MiMo preset mode")
    if payload.mimo_mode == "design":
        if payload.reference_audio is not None:
            raise ValueError("design mode does not accept reference_audio")
        if payload.voice_description is None or not payload.voice_description.strip():
            raise ValueError("voice_description is required for MiMo design mode")
        return None, MiMoSynthesisRequest(
            text=payload.text,
            mode="design",
            voice_description=payload.voice_description,
        )

    if payload.voice_description is not None:
        raise ValueError("clone mode does not accept voice_description")
    if payload.reference_audio is None:
        raise ValueError("reference_audio is required for MiMo clone mode")
    reference_audio = normalize_reference_audio(
        payload.reference_audio,
        config.max_reference_audio_bytes,
    )
    return None, MiMoSynthesisRequest(
        text=payload.text,
        mode="clone",
        reference_audio=reference_audio,
    )


def create_app(
    config: ServerConfig,
    communicator_factory: CommunicatorFactory = Communicate,
    *,
    voices_factory: VoicesFactory = list_voices,
    mimo_client: Optional[MiMoClientLike] = None,
    audio_converter: AudioConverter = convert_audio,
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
    voices_cache = VoiceCache(
        voices_factory,
        proxy=config.proxy,
        ttl_seconds=config.voices_cache_ttl_seconds,
        request_timeout_seconds=config.request_timeout_seconds,
    )
    owned_mimo_client: Optional[MiMoClient] = None
    if mimo_client is None and config.mimo_api_key is not None:
        owned_mimo_client = MiMoClient(
            config.mimo_api_key,
            config.mimo_base_url,
            config.mimo_request_timeout_seconds,
            config.max_audio_bytes,
            config.proxy,
        )
        mimo_client = owned_mimo_client

    if owned_mimo_client is not None:
        app.router.add_event_handler("shutdown", owned_mimo_client.close)

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

    async def run_synthesis(  # pylint: disable=too-many-return-statements,too-many-branches
        payload: TTSRequest,
        request: Request,
        boundary: Literal["WordBoundary", "SentenceBoundary"],
        *,
        include_subtitles: bool,
    ) -> tuple[bytes, str, str] | JSONResponse:
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
            edge_options, mimo_options = _validate_provider_request(
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
            if mimo_options is not None and mimo_client is None:
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

                async def generate() -> tuple[bytes, str, str]:
                    if edge_options is not None:
                        audio, subtitles = await _collect_synthesis(
                            edge_options,
                            communicator_factory,
                            config,
                            include_subtitles=include_subtitles,
                        )
                        source_format = "mp3"
                    else:
                        assert mimo_client is not None
                        assert mimo_options is not None
                        audio = await mimo_client.synthesize(mimo_options)
                        subtitles = ""
                        source_format = "wav"
                        if len(audio) > config.max_audio_bytes:
                            raise AudioTooLarge
                    if (
                        not include_subtitles
                        and source_format != payload.response_format
                    ):
                        audio = await audio_converter(
                            audio,
                            source_format,
                            payload.response_format,
                            config.max_audio_bytes,
                        )
                        source_format = payload.response_format
                    return audio, subtitles, source_format

                return await asyncio.wait_for(
                    generate(),
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
            return result
        audio, _subtitles, audio_format = result
        media_type = "audio/mpeg" if audio_format == "mp3" else "audio/wav"
        return Response(
            content=audio,
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="speech.{audio_format}"'
            },
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
            return result
        audio, subtitles, _audio_format = result
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
