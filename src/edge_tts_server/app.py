"""Authenticated aiohttp application for Edge TTS synthesis."""

import hmac
import json
import logging
from typing import Any, Callable, Dict, List

import aiohttp
from aiohttp import web

from edge_tts import Communicate, exceptions
from edge_tts.constants import DEFAULT_VOICE
from edge_tts.data_classes import TTSConfig

from .config import ServerConfig

CommunicatorFactory = Callable[..., Any]
_ALLOWED_FIELDS = frozenset(("text", "voice", "rate", "volume", "pitch"))
_LOGGER = logging.getLogger(__name__)


def _error(status: int, code: str, message: str) -> web.Response:
    """Create a stable JSON error response."""
    return web.json_response({"error": code, "message": message}, status=status)


def _authorized(request: web.Request, api_key: str) -> bool:
    """Compare credentials without leaking matching-prefix timing."""
    supplied = request.headers.get("X-API-Key", "")
    return hmac.compare_digest(supplied.encode("utf-8"), api_key.encode("utf-8"))


async def _parse_request(request: web.Request) -> Dict[str, str]:
    """Parse and validate a synthesis request."""
    if request.content_type != "application/json":
        raise ValueError("Content-Type must be application/json")
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")

    unknown = set(payload) - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"Unknown request fields: {', '.join(sorted(unknown))}")

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")

    options: Dict[str, str] = {
        "text": text,
        "voice": payload.get("voice", DEFAULT_VOICE),
        "rate": payload.get("rate", "+0%"),
        "volume": payload.get("volume", "+0%"),
        "pitch": payload.get("pitch", "+0Hz"),
    }
    for name, value in options.items():
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")

    validated = TTSConfig(
        voice=options["voice"],
        rate=options["rate"],
        volume=options["volume"],
        pitch=options["pitch"],
        boundary="SentenceBoundary",
    )
    options["voice"] = validated.voice
    return options


def create_app(
    config: ServerConfig,
    communicator_factory: CommunicatorFactory = Communicate,
) -> web.Application:
    """Build an isolated HTTP application."""

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def synthesize(request: web.Request) -> web.Response:
        if not _authorized(request, config.api_key):
            return _error(401, "unauthorized", "Missing or invalid API key")

        try:
            options = await _parse_request(request)
        except (TypeError, ValueError) as exc:
            return _error(400, "invalid_request", str(exc))

        try:
            communicator = communicator_factory(
                options["text"],
                options["voice"],
                rate=options["rate"],
                volume=options["volume"],
                pitch=options["pitch"],
            )
            audio_chunks: List[bytes] = []
            async for chunk in communicator.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
        except (exceptions.EdgeTTSException, aiohttp.ClientError):
            return _error(502, "upstream_error", "TTS upstream service failed")
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Unexpected TTS request failure")
            return _error(500, "internal_error", "Internal server error")

        return web.Response(
            body=b"".join(audio_chunks),
            content_type="audio/mpeg",
            headers={"Content-Disposition": 'inline; filename="speech.mp3"'},
        )

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/v1/tts", synthesize)
    return app
