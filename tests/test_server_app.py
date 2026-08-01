"""HTTP contract tests for the edge-tts server."""

# pylint: disable=too-few-public-methods

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Tuple, Type

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from edge_tts import exceptions
from edge_tts_server.app import create_app
from edge_tts_server.config import ServerConfig

CONFIG = ServerConfig(api_key="correct-secret", host="127.0.0.1", port=5050)
AUTH = {"X-API-Key": CONFIG.api_key}


class FakeCommunicator:
    """Communicator replacement that records calls and yields fake MP3 chunks."""

    calls: List[Tuple[str, str, str, str, str]] = []

    def __init__(
        self,
        text: str,
        voice: str,
        *,
        rate: str,
        volume: str,
        pitch: str,
    ) -> None:
        self.calls.append((text, voice, rate, volume, pitch))

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        """Yield metadata between two audio chunks."""
        yield {"type": "audio", "data": b"ID3first"}
        yield {"type": "SentenceBoundary", "offset": 0}
        yield {"type": "audio", "data": b"second"}


class UpstreamCommunicator(FakeCommunicator):
    """Communicator that simulates a known edge-tts failure."""

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        if CONFIG.port:
            raise exceptions.NoAudioReceived("upstream detail")
        yield {}


class NetworkCommunicator(FakeCommunicator):
    """Communicator that simulates a network failure."""

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        if CONFIG.port:
            raise aiohttp.ClientConnectionError("network detail")
        yield {}


class BrokenCommunicator(FakeCommunicator):
    """Communicator that simulates an unexpected implementation failure."""

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        if CONFIG.port:
            raise RuntimeError(f"traceback must hide {CONFIG.api_key}")
        yield {}


@asynccontextmanager
async def client_for(
    communicator: Type[FakeCommunicator] = FakeCommunicator,
) -> AsyncIterator[TestClient[web.Request, web.Application]]:
    """Run an application using an isolated aiohttp test server."""
    client = TestClient(TestServer(create_app(CONFIG, communicator)))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_health_is_public() -> None:
    """Container health checks must not need a secret."""
    async with client_for() as client:
        response = await client.get("/health")
        body = await response.json()

    assert response.status == 200
    assert body == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
async def test_missing_or_wrong_key_is_rejected(headers: Dict[str, str]) -> None:
    """TTS calls require the configured key."""
    async with client_for() as client:
        response = await client.post("/v1/tts", json={"text": "hello"}, headers=headers)
        body = await response.json()

    assert response.status == 401
    assert body == {
        "error": "unauthorized",
        "message": "Missing or invalid API key",
    }


@pytest.mark.asyncio
async def test_authentication_happens_before_body_parsing() -> None:
    """Unauthorized clients should not reach request processing."""
    async with client_for() as client:
        response = await client.post(
            "/v1/tts",
            data="{",
            headers={"Content-Type": "application/json", "X-API-Key": "wrong"},
        )

    assert response.status == 401


@pytest.mark.asyncio
async def test_tts_returns_mp3_and_passes_options() -> None:
    """Audio chunks should be combined and returned without metadata."""
    FakeCommunicator.calls.clear()
    body = {
        "text": "你好",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+10%",
        "volume": "-5%",
        "pitch": "+2Hz",
    }
    async with client_for() as client:
        response = await client.post("/v1/tts", json=body, headers=AUTH)
        audio = await response.read()

    assert response.status == 200
    assert response.headers["Content-Type"] == "audio/mpeg"
    assert response.headers["Content-Disposition"] == 'inline; filename="speech.mp3"'
    assert audio == b"ID3firstsecond"
    assert FakeCommunicator.calls[-1][0] == "你好"
    assert FakeCommunicator.calls[-1][2:] == ("+10%", "-5%", "+2Hz")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"text": ""},
        {"text": "   "},
        {"text": 123},
        {"text": "hello", "unknown": True},
        {"text": "hello", "rate": 1},
        {"text": "hello", "rate": "fast"},
        {"text": "hello", "volume": "loud"},
        {"text": "hello", "pitch": "high"},
        {"text": "hello", "voice": "invalid"},
    ],
)
async def test_invalid_payload_is_rejected(payload: object) -> None:
    """Invalid request shapes and synthesis options should return 400."""
    async with client_for() as client:
        response = await client.post("/v1/tts", json=payload, headers=AUTH)
        result = await response.json()

    assert response.status == 400
    assert result["error"] == "invalid_request"


@pytest.mark.asyncio
async def test_malformed_json_is_rejected() -> None:
    """Syntax errors should use the same stable invalid-request response."""
    async with client_for() as client:
        response = await client.post(
            "/v1/tts",
            data="{",
            headers={**AUTH, "Content-Type": "application/json"},
        )
        body = await response.json()

    assert response.status == 400
    assert body["error"] == "invalid_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("communicator", [UpstreamCommunicator, NetworkCommunicator])
async def test_upstream_failures_return_502(
    communicator: Type[FakeCommunicator],
) -> None:
    """Known service and network failures should be safe gateway errors."""
    async with client_for(communicator) as client:
        response = await client.post("/v1/tts", json={"text": "hello"}, headers=AUTH)
        body = await response.text()

    assert response.status == 502
    assert (await response.json())["error"] == "upstream_error"
    assert "upstream detail" not in body
    assert "network detail" not in body


@pytest.mark.asyncio
async def test_unexpected_failures_return_safe_500() -> None:
    """Internal details and credentials must not leak to clients."""
    async with client_for(BrokenCommunicator) as client:
        response = await client.post("/v1/tts", json={"text": "hello"}, headers=AUTH)
        body = await response.text()

    assert response.status == 500
    assert (await response.json())["error"] == "internal_error"
    assert CONFIG.api_key not in body
    assert "traceback" not in body
