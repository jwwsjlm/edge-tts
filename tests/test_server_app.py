"""HTTP contract tests for the hardened FastAPI server."""

# pylint: disable=too-few-public-methods

import asyncio
import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Tuple, Type

import aiohttp
import httpx
import pytest

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
        raise exceptions.NoAudioReceived("upstream detail")
        yield {}  # type: ignore[unreachable]  # pylint: disable=unreachable


class NetworkCommunicator(FakeCommunicator):
    """Communicator that simulates a network failure."""

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        raise aiohttp.ClientConnectionError("network detail")
        yield {}  # type: ignore[unreachable]  # pylint: disable=unreachable


class BrokenCommunicator(FakeCommunicator):
    """Communicator that simulates an unexpected implementation failure."""

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        raise RuntimeError(f"traceback must hide {CONFIG.api_key}")
        yield {}  # type: ignore[unreachable]  # pylint: disable=unreachable


class HangingCommunicator(FakeCommunicator):
    """Communicator that never produces audio unless cancelled."""

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        await asyncio.Event().wait()
        yield {}


class LargeAudioCommunicator(FakeCommunicator):
    """Communicator that exceeds a configured response limit."""

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        yield {"type": "audio", "data": b"1234"}
        yield {"type": "audio", "data": b"56"}


class BlockingCommunicator(FakeCommunicator):
    """Communicator used to hold all configured concurrency slots."""

    active = 0
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        type(self).active += 1
        if type(self).active == 2:
            type(self).all_started.set()
        await type(self).release.wait()
        yield {"type": "audio", "data": b"ok"}


@asynccontextmanager
async def client_for(
    communicator: Type[FakeCommunicator] = FakeCommunicator,
    config: ServerConfig = CONFIG,
) -> AsyncIterator[httpx.AsyncClient]:
    """Run an isolated ASGI client for the configured application."""
    transport = httpx.ASGITransport(app=create_app(config, communicator))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_health_is_public_and_has_request_id() -> None:
    """Health checks remain public and every response is traceable."""
    async with client_for() as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers["X-Request-ID"]) == 32


@pytest.mark.asyncio
async def test_access_log_escapes_control_characters(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Untrusted paths must not inject forged access-log lines."""
    caplog.set_level(logging.INFO)
    async with client_for() as client:
        await client.get("/forged%0Astatus=200")

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name.endswith("edge_tts_server.access")
    ]
    assert len(messages) == 1
    assert "\n" not in messages[0]
    assert "\\n" in messages[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
async def test_missing_or_wrong_key_is_rejected(headers: Dict[str, str]) -> None:
    """TTS calls require the configured key."""
    async with client_for() as client:
        response = await client.post("/v1/tts", json={"text": "hello"}, headers=headers)

    assert response.status_code == 401
    assert response.json() == {
        "error": "unauthorized",
        "message": "Missing or invalid API key",
    }


@pytest.mark.asyncio
async def test_authentication_happens_before_body_limits_and_parsing() -> None:
    """Unauthorized clients must not make the service consume their body."""
    config = ServerConfig(api_key="secret", max_request_bytes=8)
    async with client_for(config=config) as client:
        response = await client.post(
            "/v1/tts",
            content=b"{" + (b"x" * 100),
            headers={"Content-Type": "application/json", "X-API-Key": "wrong"},
        )

    assert response.status_code == 401


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

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "audio/mpeg"
    assert response.headers["Content-Disposition"] == 'inline; filename="speech.mp3"'
    assert response.content == b"ID3firstsecond"
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
    """Invalid request shapes and synthesis options should return stable 400."""
    async with client_for() as client:
        response = await client.post("/v1/tts", json=payload, headers=AUTH)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.asyncio
async def test_malformed_json_is_rejected() -> None:
    """Syntax errors should use the same stable invalid-request response."""
    async with client_for() as client:
        response = await client.post(
            "/v1/tts",
            content="{",
            headers={**AUTH, "Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.asyncio
async def test_actual_streamed_body_size_is_limited() -> None:
    """Chunked requests cannot bypass the configured byte limit."""
    config = ServerConfig(api_key="secret", max_request_bytes=20)

    async def content() -> AsyncIterator[bytes]:
        yield b'{"text":"'
        yield b"x" * 50
        yield b'"}'

    async with client_for(config=config) as client:
        response = await client.post(
            "/v1/tts",
            content=content(),
            headers={"Content-Type": "application/json", "X-API-Key": "secret"},
        )

    assert response.status_code == 413
    assert response.json()["error"] == "request_too_large"


@pytest.mark.asyncio
async def test_text_length_is_limited() -> None:
    """Validated JSON still obeys the configured text length."""
    config = ServerConfig(api_key="secret", max_text_length=3)
    async with client_for(config=config) as client:
        response = await client.post(
            "/v1/tts", json={"text": "four"}, headers={"X-API-Key": "secret"}
        )

    assert response.status_code == 413
    assert response.json()["error"] == "text_too_long"


@pytest.mark.asyncio
async def test_generated_audio_size_is_limited() -> None:
    """Buffered audio cannot grow beyond the configured limit."""
    config = ServerConfig(api_key="secret", max_audio_bytes=5)
    async with client_for(LargeAudioCommunicator, config) as client:
        response = await client.post(
            "/v1/tts", json={"text": "hello"}, headers={"X-API-Key": "secret"}
        )

    assert response.status_code == 413
    assert response.json()["error"] == "audio_too_large"


@pytest.mark.asyncio
async def test_concurrency_limit_rejects_excess_work() -> None:
    """A saturated server should reject rather than queue unbounded work."""
    BlockingCommunicator.active = 0
    BlockingCommunicator.all_started = asyncio.Event()
    BlockingCommunicator.release = asyncio.Event()
    config = ServerConfig(api_key="secret", max_concurrent_requests=2)
    async with client_for(BlockingCommunicator, config) as client:
        requests = [
            asyncio.create_task(
                client.post(
                    "/v1/tts",
                    json={"text": str(index)},
                    headers={"X-API-Key": "secret"},
                )
            )
            for index in range(2)
        ]
        await asyncio.wait_for(BlockingCommunicator.all_started.wait(), timeout=1)
        excess = await client.post(
            "/v1/tts", json={"text": "excess"}, headers={"X-API-Key": "secret"}
        )
        BlockingCommunicator.release.set()
        completed = await asyncio.gather(*requests)

    assert excess.status_code == 429
    assert excess.headers["Retry-After"] == "1"
    assert excess.json()["error"] == "too_many_requests"
    assert all(response.status_code == 200 for response in completed)


@pytest.mark.asyncio
async def test_upstream_timeout_is_stable() -> None:
    """Overall synthesis time is bounded and cancellation returns 504."""
    config = ServerConfig(api_key="secret", request_timeout_seconds=1)
    async with client_for(HangingCommunicator, config) as client:
        response = await client.post(
            "/v1/tts", json={"text": "hello"}, headers={"X-API-Key": "secret"}
        )

    assert response.status_code == 504
    assert response.json()["error"] == "upstream_timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("communicator", [UpstreamCommunicator, NetworkCommunicator])
async def test_upstream_failures_return_502(
    communicator: Type[FakeCommunicator],
) -> None:
    """Known service and network failures should be safe gateway errors."""
    async with client_for(communicator) as client:
        response = await client.post("/v1/tts", json={"text": "hello"}, headers=AUTH)

    body = response.text
    assert response.status_code == 502
    assert response.json()["error"] == "upstream_error"
    assert "upstream detail" not in body
    assert "network detail" not in body


@pytest.mark.asyncio
async def test_unexpected_failures_return_safe_500(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Internal details, credentials and request text must not leak."""
    caplog.set_level(logging.INFO)
    secret_text = "private request text"
    async with client_for(BrokenCommunicator) as client:
        response = await client.post(
            "/v1/tts", json={"text": secret_text}, headers=AUTH
        )

    body = response.text
    logs = caplog.text
    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"
    assert CONFIG.api_key not in body
    assert CONFIG.api_key not in logs
    assert secret_text not in logs
    assert "traceback" not in body
    assert response.headers["X-Request-ID"] in logs


@pytest.mark.asyncio
async def test_unhandled_application_failure_is_stable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unhandled route failures still need safe JSON and a request ID."""
    caplog.set_level(logging.INFO)
    secret = "must not reach the response or logs"
    app = create_app(CONFIG)

    @app.get("/unhandled")
    async def unhandled() -> None:
        raise RuntimeError(secret)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/unhandled")

    assert response.status_code == 500
    assert response.json() == {
        "error": "internal_error",
        "message": "Internal server error",
    }
    assert len(response.headers["X-Request-ID"]) == 32
    assert secret not in response.text
    assert secret not in caplog.text
    assert response.headers["X-Request-ID"] in caplog.text


@pytest.mark.asyncio
async def test_swagger_is_disabled_by_default() -> None:
    """Deployment documentation routes should be opt-in."""
    async with client_for() as client:
        docs = await client.get("/docs")
        schema = await client.get("/openapi.json")

    assert docs.status_code == 404
    assert docs.json()["error"] == "not_found"
    assert schema.status_code == 404


@pytest.mark.asyncio
async def test_swagger_schema_documents_api_key_when_enabled() -> None:
    """Enabled Swagger UI should expose the authenticated contract."""
    config = ServerConfig(api_key="secret", docs_enabled=True)
    async with client_for(config=config) as client:
        docs = await client.get("/docs")
        schema_response = await client.get("/openapi.json")

    schema = schema_response.json()
    assert docs.status_code == 200
    assert "Swagger UI" in docs.text
    assert schema_response.status_code == 200
    assert schema["info"]["version"] == "7.3.4"
    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }
    assert schema["paths"]["/v1/tts"]["post"]["security"] == [{"APIKeyHeader": []}]


def test_uvicorn_defaults_emit_the_safe_access_log() -> None:
    """The production Uvicorn logger should emit the custom access record."""
    script = """
import asyncio
import httpx
import uvicorn
from edge_tts_server.app import create_app
from edge_tts_server.config import ServerConfig

async def main():
    app = create_app(ServerConfig(api_key="secret"))
    uvicorn.Config(app, access_log=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    print(response.headers["X-Request-ID"])

asyncio.run(main())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    request_id = completed.stdout.strip()
    assert len(request_id) == 32
    assert request_id in completed.stderr
