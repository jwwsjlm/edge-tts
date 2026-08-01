"""Authenticated and resource-bounded FastAPI application."""

# pylint: disable=too-few-public-methods

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

import aiohttp
from fastapi import FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from edge_tts import Communicate, exceptions
from edge_tts.data_classes import TTSConfig
from edge_tts.version import __version__

from .config import ServerConfig
from .models import ErrorResponse, TTSRequest

CommunicatorFactory = Callable[..., Any]
_ACCESS_LOGGER = logging.getLogger("uvicorn.error.edge_tts_server.access")
_APP_LOGGER = logging.getLogger("uvicorn.error.edge_tts_server.app")


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
    """Reject unauthorized synthesis calls before reading their body."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("path") == "/v1/tts"
            and scope.get("method") == "POST"
        ):
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
            and scope.get("path") == "/v1/tts"
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


async def _collect_audio(
    options: Dict[str, str],
    communicator_factory: CommunicatorFactory,
    max_audio_bytes: int,
) -> bytes:
    """Collect only audio chunks while enforcing a hard memory boundary."""
    communicator = communicator_factory(
        options["text"],
        options["voice"],
        rate=options["rate"],
        volume=options["volume"],
        pitch=options["pitch"],
    )
    audio = bytearray()
    async for chunk in communicator.stream():
        if chunk["type"] != "audio":
            continue
        data = chunk["data"]
        if len(audio) + len(data) > max_audio_bytes:
            raise AudioTooLarge
        audio.extend(data)
    return bytes(audio)


def _validated_options(payload: TTSRequest) -> Dict[str, str]:
    """Reuse edge-tts option validation without coercing API values."""
    validated = TTSConfig(
        voice=payload.voice,
        rate=payload.rate,
        volume=payload.volume,
        pitch=payload.pitch,
        boundary="SentenceBoundary",
    )
    return {
        "text": payload.text,
        "voice": validated.voice,
        "rate": payload.rate,
        "volume": payload.volume,
        "pitch": payload.pitch,
    }


def create_app(
    config: ServerConfig,
    communicator_factory: CommunicatorFactory = Communicate,
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

    @app.post(
        "/v1/tts",
        tags=["tts"],
        response_class=Response,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            504: {"model": ErrorResponse},
        },
    )
    async def synthesize(  # pylint: disable=too-many-return-statements
        payload: TTSRequest,
        request: Request,
        _api_key: Optional[str] = Security(api_key_header),
    ) -> Response:
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
            options = _validated_options(payload)
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
                audio = await asyncio.wait_for(
                    _collect_audio(
                        options, communicator_factory, config.max_audio_bytes
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

        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={"Content-Disposition": 'inline; filename="speech.mp3"'},
        )

    app.add_middleware(BodyLimitMiddleware, max_bytes=config.max_request_bytes)
    app.add_middleware(APIKeyMiddleware, api_key=config.api_key)
    app.add_middleware(SafeErrorMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app
