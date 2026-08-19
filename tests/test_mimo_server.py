"""Contract tests for the Xiaomi MiMo multi-model integration."""

# pylint: disable=too-few-public-methods

import base64
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, cast

import aiohttp
import httpx
import pytest

from edge_tts.typing import Voice
from edge_tts_server.app import create_app
from edge_tts_server.config import ServerConfig
from edge_tts_server.mimo import (
    MIMO_PRESET_VOICES,
    MiMoAudioTooLarge,
    MiMoClient,
    MiMoRateLimitError,
    MiMoResponseError,
    MiMoSynthesisRequest,
    _payload,
)

CONFIG = ServerConfig(
    api_key="service-secret",
    host="127.0.0.1",
    mimo_api_key="mimo-secret",
    max_request_bytes=1024 * 1024,
)
AUTH = {"X-API-Key": CONFIG.api_key}
VALID_WAV = b"RIFF\x04\x00\x00\x00WAVE"


async def unused_voices(**_kwargs: Any) -> List[Voice]:
    """Fail if a static MiMo voice query contacts the Edge upstream."""
    raise AssertionError("Edge voices must not be queried")


class UnusedCommunicator:
    """Fail if a MiMo request reaches the Edge communicator."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        """Reject construction because these tests are MiMo-only."""
        raise AssertionError("Edge communicator must not be created")


class FakeMiMoClient:
    """Record validated MiMo requests and return deterministic WAV bytes."""

    def __init__(self, result: bytes = b"RIFFfake-wave") -> None:
        self.result = result
        self.requests: List[MiMoSynthesisRequest] = []
        self.error: Optional[Exception] = None

    async def synthesize(self, options: MiMoSynthesisRequest) -> bytes:
        """Return deterministic audio or the configured safe error."""
        self.requests.append(options)
        if self.error is not None:
            raise self.error
        return self.result


class FakeConverter:
    """Record format conversion without requiring valid fixture audio."""

    def __init__(self) -> None:
        self.calls: List[tuple[bytes, str, str, int]] = []

    async def __call__(
        self, audio: bytes, source: str, target: str, max_bytes: int
    ) -> bytes:
        self.calls.append((audio, source, target, max_bytes))
        return b"ID3converted"


@asynccontextmanager
async def client_for(
    mimo: Optional[FakeMiMoClient] = None,
    *,
    config: ServerConfig = CONFIG,
    converter: Optional[FakeConverter] = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Create an isolated ASGI client with provider fakes."""
    app = create_app(
        config,
        UnusedCommunicator,
        voices_factory=unused_voices,
        mimo_client=mimo,
        audio_converter=converter or FakeConverter(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_models_are_authenticated_and_describe_capabilities() -> None:
    """Callers can discover both selectable models and MiMo modes."""
    async with client_for(FakeMiMoClient()) as client:
        unauthorized = await client.get("/v1/models")
        response = await client.get("/v1/models", headers=AUTH)

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "models": [
            {
                "id": "edge-tts",
                "provider": "Microsoft Edge",
                "modes": ["preset"],
                "response_formats": ["mp3", "wav"],
                "supports_subtitles": True,
            },
            {
                "id": "mimo-v2-tts",
                "provider": "Xiaomi MiMo V2.5",
                "modes": ["preset", "design", "clone"],
                "response_formats": ["mp3", "wav"],
                "supports_subtitles": False,
            },
        ]
    }


@pytest.mark.asyncio
async def test_mimo_voices_are_static_filterable_and_complete() -> None:
    """MiMo preset voices use the existing stable voice response envelope."""
    async with client_for(FakeMiMoClient()) as client:
        response = await client.get(
            "/v1/voices?model=mimo-v2-tts&gender=Female", headers=AUTH
        )

    assert response.status_code == 200
    voices = response.json()["voices"]
    expected = [
        voice.voice_id for voice in MIMO_PRESET_VOICES if voice.gender == "Female"
    ]
    assert [voice["name"] for voice in voices] == expected
    assert all(voice["suggested_codec"] == "audio/wav;codec=pcm" for voice in voices)


@pytest.mark.asyncio
async def test_mimo_voice_catalog_matches_official_v2_5_presets() -> None:
    """The static list must use exact IDs published in the MiMo V2.5 docs."""
    async with client_for(FakeMiMoClient()) as client:
        response = await client.get("/v1/voices?model=mimo-v2-tts", headers=AUTH)

    assert response.status_code == 200
    assert [voice["name"] for voice in response.json()["voices"]] == [
        "mimo_default",
        "冰糖",
        "茉莉",
        "苏打",
        "白桦",
        "Mia",
        "Chloe",
        "Milo",
        "Dean",
    ]


@pytest.mark.asyncio
async def test_mimo_preset_returns_native_wav_and_uses_default_voice() -> None:
    """Omitted voice selects mimo_default without touching Edge defaults."""
    mimo = FakeMiMoClient()
    async with client_for(mimo) as client:
        response = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={
                "model": "mimo-v2-tts",
                "text": "你好",
                "response_format": "wav",
            },
        )

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("audio/wav")
    assert response.headers["Content-Disposition"] == 'inline; filename="speech.wav"'
    assert mimo.requests == [
        MiMoSynthesisRequest(text="你好", mode="preset", voice="mimo_default")
    ]


@pytest.mark.asyncio
async def test_mimo_mp3_output_uses_bounded_converter() -> None:
    """Default MP3 compatibility converts the native WAV inside the request slot."""
    mimo = FakeMiMoClient()
    converter = FakeConverter()
    async with client_for(mimo, converter=converter) as client:
        response = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={"model": "mimo-v2-tts", "text": "hello"},
        )

    assert response.status_code == 200
    assert response.content == b"ID3converted"
    assert response.headers["Content-Type"].startswith("audio/mpeg")
    assert converter.calls == [(b"RIFFfake-wave", "wav", "mp3", CONFIG.max_audio_bytes)]


@pytest.mark.asyncio
async def test_mimo_design_and_clone_fields_are_dispatched() -> None:
    """Design and clone modes pass only their validated provider-specific input."""
    mimo = FakeMiMoClient()
    reference = "data:audio/wav;base64," + base64.b64encode(VALID_WAV).decode()
    async with client_for(mimo) as client:
        design = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={
                "model": "mimo-v2-tts",
                "mimo_mode": "design",
                "text": "设计音色",
                "voice_description": "温柔、清晰的女声",
                "response_format": "wav",
            },
        )
        clone = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={
                "model": "mimo-v2-tts",
                "mimo_mode": "clone",
                "text": "克隆音色",
                "reference_audio": reference,
                "response_format": "wav",
            },
        )

    assert design.status_code == clone.status_code == 200
    assert mimo.requests[0] == MiMoSynthesisRequest(
        text="设计音色",
        mode="design",
        voice_description="温柔、清晰的女声",
    )
    assert mimo.requests[1].mode == "clone"
    assert mimo.requests[1].reference_audio == reference


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"model": "mimo-v2-tts", "text": "x", "voice": "unknown"},
        {
            "model": "mimo-v2-tts",
            "text": "x",
            "rate": "+10%",
        },
        {
            "model": "mimo-v2-tts",
            "mimo_mode": "design",
            "text": "x",
        },
        {
            "model": "mimo-v2-tts",
            "mimo_mode": "clone",
            "text": "x",
            "reference_audio": "not-a-data-url",
        },
        {
            "model": "mimo-v2-tts",
            "mimo_mode": "clone",
            "text": "x",
            "reference_audio": "data:audio/wav;base64,bm90LXdhdg==",
        },
        {
            "model": "mimo-v2-tts",
            "mimo_mode": "clone",
            "text": "x",
            "reference_audio": "data:audio/mpeg;base64,bm90LW1wMw==",
        },
    ],
)
async def test_invalid_mimo_combinations_return_stable_400(  # type: ignore[misc]
    payload: Dict[str, Any],
) -> None:
    """Unsupported or incomplete MiMo options must never be ignored."""
    async with client_for(FakeMiMoClient()) as client:
        response = await client.post("/v1/tts", headers=AUTH, json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.asyncio
async def test_reference_audio_limit_and_mimo_text_limit_are_enforced() -> None:
    """Provider-specific body limits return stable client errors."""
    config = ServerConfig(
        api_key="service-secret",
        mimo_api_key="mimo-secret",
        max_request_bytes=4096,
        max_reference_audio_bytes=4,
    )
    reference = "data:audio/wav;base64," + base64.b64encode(b"12345").decode()
    async with client_for(FakeMiMoClient(), config=config) as client:
        audio = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={
                "model": "mimo-v2-tts",
                "mimo_mode": "clone",
                "text": "x",
                "reference_audio": reference,
            },
        )
        text = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={"model": "mimo-v2-tts", "text": "x" * 3001},
        )

    assert audio.status_code == 413
    assert audio.json()["error"] == "reference_audio_too_large"
    assert text.status_code == 400
    assert "3000" in text.json()["message"]


@pytest.mark.asyncio
async def test_missing_provider_bundle_and_upstream_errors_are_stable() -> None:
    """Misconfiguration, unsupported subtitles, and MiMo failures stay explicit."""
    missing = ServerConfig(api_key="service-secret")
    async with client_for(None, config=missing) as client:
        unconfigured = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={"model": "mimo-v2-tts", "text": "x"},
        )
        bundle = await client.post(
            "/v1/tts/bundle",
            headers=AUTH,
            json={"model": "mimo-v2-tts", "text": "x"},
        )

    assert unconfigured.status_code == 503
    assert unconfigured.json()["error"] == "provider_not_configured"
    assert bundle.status_code == 400
    assert bundle.json()["error"] == "unsupported_model"

    mimo = FakeMiMoClient()
    mimo.error = MiMoRateLimitError("7")
    async with client_for(mimo) as client:
        limited = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={"model": "mimo-v2-tts", "text": "x"},
        )
    assert limited.status_code == 503
    assert limited.headers["Retry-After"] == "7"
    assert limited.json()["error"] == "upstream_rate_limited"

    mimo.error = MiMoResponseError("secret upstream body")
    async with client_for(mimo) as client:
        failed = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={"model": "mimo-v2-tts", "text": "x"},
        )
    assert failed.status_code == 502
    assert failed.json() == {
        "error": "upstream_error",
        "message": "TTS upstream service failed",
    }

    mimo.error = MiMoAudioTooLarge()
    async with client_for(mimo) as client:
        oversized = await client.post(
            "/v1/tts",
            headers=AUTH,
            json={"model": "mimo-v2-tts", "text": "x"},
        )
    assert oversized.status_code == 413
    assert oversized.json()["error"] == "audio_too_large"


def test_official_mimo_payloads_use_v2_5_models() -> None:
    """Each public mode maps to the current official V2.5 upstream model."""
    preset = _payload(
        MiMoSynthesisRequest(text="hello", mode="preset", voice="mimo_default")
    )
    design = _payload(
        MiMoSynthesisRequest(
            text="hello", mode="design", voice_description="warm voice"
        )
    )
    clone = _payload(
        MiMoSynthesisRequest(
            text="hello",
            mode="clone",
            reference_audio="data:audio/wav;base64,UklGRg==",
        )
    )

    assert preset == {
        "model": "mimo-v2.5-tts",
        "messages": [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "hello"},
        ],
        "audio": {"format": "wav", "voice": "mimo_default"},
    }
    assert design == {
        "model": "mimo-v2.5-tts-voicedesign",
        "messages": [
            {"role": "user", "content": "warm voice"},
            {"role": "assistant", "content": "hello"},
        ],
        "audio": {"format": "wav"},
    }
    assert clone == {
        "model": "mimo-v2.5-tts-voiceclone",
        "messages": [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "hello"},
        ],
        "audio": {
            "format": "wav",
            "voice": "data:audio/wav;base64,UklGRg==",
        },
    }


class FakeUpstreamResponse:
    """Minimal aiohttp response context manager for client contract tests."""

    def __init__(
        self,
        status: int,
        payload: Any,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.headers = headers or {}

    async def __aenter__(self) -> "FakeUpstreamResponse":
        """Enter the fake response context."""
        return self

    async def __aexit__(self, *_args: Any) -> None:
        """Exit the fake response context."""
        return None

    async def json(self, **_kwargs: Any) -> Any:
        """Return the configured JSON payload."""
        return self.payload

    async def read(self) -> bytes:
        """Return a body used only by non-success tests."""
        return b"upstream body must not escape"


class FakeUpstreamSession:
    """Record MiMo request details without making an external HTTP call."""

    def __init__(self, response: FakeUpstreamResponse) -> None:
        self.closed = False
        self.response = response
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeUpstreamResponse:
        """Record a POST call and return the configured response."""
        self.calls.append({"url": url, **kwargs})
        return self.response

    async def close(self) -> None:
        """Mark the fake session as closed."""
        self.closed = True


def _client_with_session(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeUpstreamResponse,
    *,
    max_audio_bytes: int = 1024,
) -> tuple[MiMoClient, FakeUpstreamSession]:
    """Inject a deterministic session into the reusable MiMo client."""
    client = MiMoClient(
        "upstream-secret",
        "https://api.xiaomimimo.com/v1",
        120,
        max_audio_bytes,
    )
    session = FakeUpstreamSession(response)

    async def get_session() -> aiohttp.ClientSession:
        return cast(aiohttp.ClientSession, session)

    monkeypatch.setattr(client, "_get_session", get_session)
    return client, session


@pytest.mark.asyncio
async def test_mimo_client_uses_bearer_and_decodes_buffered_wav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upstream call follows the official non-streaming response contract."""
    encoded = base64.b64encode(VALID_WAV).decode("ascii")
    response = FakeUpstreamResponse(
        200, {"choices": [{"message": {"audio": {"data": encoded}}}]}
    )
    client, session = _client_with_session(monkeypatch, response)
    options = MiMoSynthesisRequest(text="hello", mode="preset", voice="mimo_default")

    result = await client.synthesize(options)

    assert result == VALID_WAV
    assert session.calls == [
        {
            "url": "https://api.xiaomimimo.com/v1/chat/completions",
            "headers": {
                "Authorization": "Bearer upstream-secret",
                "api-key": "upstream-secret",
                "Content-Type": "application/json",
            },
            "json": _payload(options),
            "proxy": None,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"audio": {"data": "%%%"}}}]},
        {"choices": [{"message": {"audio": {"data": ""}}}]},
    ],
)
async def test_mimo_client_rejects_malformed_or_empty_audio(  # type: ignore[misc]
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    """Malformed successful responses become safe provider errors."""
    client, _session = _client_with_session(
        monkeypatch, FakeUpstreamResponse(200, payload)
    )
    with pytest.raises(MiMoResponseError):
        await client.synthesize(
            MiMoSynthesisRequest(text="hello", mode="preset", voice="mimo_default")
        )


@pytest.mark.asyncio
async def test_mimo_client_rejects_oversized_decoded_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large Base64 output is stopped before it reaches the public response."""
    encoded = base64.b64encode(b"12345").decode("ascii")
    client, _session = _client_with_session(
        monkeypatch,
        FakeUpstreamResponse(
            200, {"choices": [{"message": {"audio": {"data": encoded}}}]}
        ),
        max_audio_bytes=4,
    )
    with pytest.raises(MiMoAudioTooLarge):
        await client.synthesize(
            MiMoSynthesisRequest(text="hello", mode="preset", voice="mimo_default")
        )
