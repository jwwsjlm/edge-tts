"""Runtime and fallback-error tests for the FastAPI server."""

import logging
import subprocess
import sys

import httpx
import pytest

from edge_tts_server.app import create_app
from edge_tts_server.config import ServerConfig


@pytest.mark.asyncio
async def test_unhandled_application_failure_is_stable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unhandled route failures still need safe JSON and a request ID."""
    caplog.set_level(logging.INFO)
    secret = "must not reach the response or logs"
    app = create_app(ServerConfig(api_key="correct-secret"))

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
