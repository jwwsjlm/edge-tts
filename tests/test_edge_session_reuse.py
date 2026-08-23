"""Tests for reusable Edge TTS upstream sessions."""

from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, cast

import aiohttp
import httpx
import pytest

from edge_tts import Communicate
from edge_tts import communicate as communicate_module
from edge_tts_server.app import create_app
from edge_tts_server.config import ServerConfig

CONFIG = ServerConfig(api_key="service-secret", host="127.0.0.1")
AUTH = {"X-API-Key": CONFIG.api_key}


@pytest.mark.asyncio
async def test_default_edge_client_reuses_one_upstream_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated Edge requests should share DNS cache and close it on shutdown."""
    sessions: List[aiohttp.ClientSession] = []

    async def fake_stream(self: Communicate) -> AsyncIterator[Dict[str, Any]]:
        assert self.session is not None
        sessions.append(self.session)
        yield {"type": "audio", "data": b"ID3shared-session"}

    monkeypatch.setattr(Communicate, "stream", fake_stream)
    app = create_app(CONFIG)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        first = await client.post("/v1/tts", json={"text": "one"}, headers=AUTH)
        second = await client.post("/v1/tts", json={"text": "two"}, headers=AUTH)

    assert first.status_code == second.status_code == 200
    assert len(sessions) == 2
    assert sessions[0] is sessions[1]
    assert not sessions[0].closed
    for handler in app.router.on_shutdown:
        result = handler()
        if result is not None:
            await result
    assert sessions[0].closed


@pytest.mark.asyncio
async def test_communicate_rejects_conflicting_or_closed_session() -> None:
    """The reusable-session API must reject ambiguous ownership."""
    session = aiohttp.ClientSession()
    connector = aiohttp.TCPConnector()
    try:
        with pytest.raises(ValueError, match="cannot be used together"):
            Communicate("hello", connector=connector, session=session)
    finally:
        await connector.close()
        await session.close()

    with pytest.raises(ValueError, match="must not be closed"):
        Communicate("hello", session=session)


@pytest.mark.asyncio
async def test_shared_session_passes_receive_timeout_to_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared session must not silently disable the WebSocket receive timeout."""
    captured: Dict[str, Any] = {}

    @asynccontextmanager
    async def websocket_context() -> AsyncIterator[aiohttp.ClientWebSocketResponse]:
        yield cast(aiohttp.ClientWebSocketResponse, object())

    def fake_ws_connect(
        _session: aiohttp.ClientSession, _url: str, **kwargs: Any
    ) -> Any:
        captured.update(kwargs)
        return websocket_context()

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    async with aiohttp.ClientSession() as session:
        communicator = Communicate("hello", session=session, receive_timeout=23)
        open_websocket = cast(
            Callable[[AsyncExitStack], Awaitable[aiohttp.ClientWebSocketResponse]],
            getattr(communicator, "_Communicate__open_websocket"),
        )
        async with AsyncExitStack() as stack:
            await open_websocket(stack)

    timeout = captured["timeout"]
    assert isinstance(timeout, aiohttp.ClientWSTimeout)
    assert timeout.ws_receive == 23
    assert timeout.ws_close == 10.0
    assert "receive_timeout" not in captured


def test_receive_timeout_falls_back_for_older_aiohttp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """aiohttp releases before ClientWSTimeout use receive_timeout directly."""
    monkeypatch.delattr(aiohttp, "ClientWSTimeout")

    options_factory = cast(
        Callable[[float], Dict[str, Any]],
        getattr(communicate_module, "_websocket_timeout_options"),
    )
    options = options_factory(17)

    assert options == {"receive_timeout": 17}
