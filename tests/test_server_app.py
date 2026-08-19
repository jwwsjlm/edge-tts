"""HTTP contract tests for the hardened FastAPI server."""

# pylint: disable=too-few-public-methods

import asyncio
import io
import logging
import subprocess
import sys
import zipfile
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Tuple, Type

import aiohttp
import httpx
import pytest

from edge_tts import exceptions
from edge_tts.typing import Voice
from edge_tts_server.app import create_app
from edge_tts_server.config import ServerConfig

CONFIG = ServerConfig(api_key="correct-secret", host="127.0.0.1", port=5050)
AUTH = {"X-API-Key": CONFIG.api_key}
VoiceListFactory = Callable[..., Awaitable[List[Voice]]]


VOICE_FIXTURES: List[Voice] = [
    {
        "Name": "Microsoft Server Speech Text to Speech Voice (en-US, GuyNeural)",
        "ShortName": "en-US-GuyNeural",
        "Gender": "Male",
        "Locale": "en-US",
        "SuggestedCodec": "audio-24khz-48kbitrate-mono-mp3",
        "FriendlyName": "Microsoft Guy Online (Natural) - English (United States)",
        "Status": "GA",
        "VoiceTag": {
            "ContentCategories": ["General"],
            "VoicePersonalities": ["Friendly"],
        },
    },
    {
        "Name": "Microsoft Server Speech Text to Speech Voice (zh-CN, XiaoxiaoNeural)",
        "ShortName": "zh-CN-XiaoxiaoNeural",
        "Gender": "Female",
        "Locale": "zh-CN",
        "SuggestedCodec": "audio-24khz-48kbitrate-mono-mp3",
        "FriendlyName": "Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)",
        "Status": "GA",
        "VoiceTag": {"ContentCategories": [], "VoicePersonalities": []},
    },
]


async def fake_list_voices(**_kwargs: Any) -> List[Voice]:
    """Return deliberately unsorted voice fixtures."""
    return VOICE_FIXTURES


class FakeCommunicator:
    """Communicator replacement that records calls and yields fake MP3 chunks."""

    calls: List[Tuple[str, str, str, str, str]] = []
    options: List[Dict[str, Any]] = []

    def __init__(
        self,
        text: str,
        voice: str,
        *,
        rate: str,
        volume: str,
        pitch: str,
        boundary: str = "SentenceBoundary",
        proxy: str | None = None,
        connect_timeout: int = 10,
        receive_timeout: int = 60,
    ) -> None:
        self.calls.append((text, voice, rate, volume, pitch))
        self.options.append(
            {
                "boundary": boundary,
                "proxy": proxy,
                "connect_timeout": connect_timeout,
                "receive_timeout": receive_timeout,
            }
        )

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


class BundleCommunicator(FakeCommunicator):
    """Communicator that returns audio and valid subtitle metadata once."""

    instances = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        type(self).instances += 1
        self.boundary = str(kwargs.get("boundary", "SentenceBoundary"))

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        yield {"type": "audio", "data": b"ID3bundle"}
        yield {
            "type": self.boundary,
            "offset": 0,
            "duration": 1_000_000,
            "text": "你好",
        }
        yield {"type": "audio", "data": b"audio"}


class SingleBlockingCommunicator(FakeCommunicator):
    """Hold one synthesis request so the shared capacity can be tested."""

    started = asyncio.Event()
    release = asyncio.Event()

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        type(self).started.set()
        await type(self).release.wait()
        yield {"type": "audio", "data": b"ok"}


@asynccontextmanager
async def client_for(
    communicator: Type[FakeCommunicator] = FakeCommunicator,
    config: ServerConfig = CONFIG,
    voices_factory: VoiceListFactory = fake_list_voices,
) -> AsyncIterator[httpx.AsyncClient]:
    """Run an isolated ASGI client for the configured application."""
    transport = httpx.ASGITransport(
        app=create_app(config, communicator, voices_factory=voices_factory)
    )
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
async def test_voices_authentication_happens_before_upstream_call() -> None:
    """Unauthorized voice queries must not contact Microsoft."""
    calls = 0

    async def tracked_factory(**_kwargs: Any) -> List[Voice]:
        nonlocal calls
        calls += 1
        return VOICE_FIXTURES

    async with client_for(voices_factory=tracked_factory) as client:
        response = await client.get("/v1/voices")

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
    assert calls == 0


@pytest.mark.asyncio
async def test_voices_are_fully_mapped_and_sorted() -> None:
    """The public response should expose stable fields sorted by short name."""
    async with client_for() as client:
        response = await client.get("/v1/voices", headers=AUTH)

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.json() == {
        "voices": [
            {
                "name": "en-US-GuyNeural",
                "internal_name": (
                    "Microsoft Server Speech Text to Speech Voice " "(en-US, GuyNeural)"
                ),
                "friendly_name": (
                    "Microsoft Guy Online (Natural) - English (United States)"
                ),
                "locale": "en-US",
                "language": "en",
                "gender": "Male",
                "status": "GA",
                "suggested_codec": "audio-24khz-48kbitrate-mono-mp3",
                "content_categories": ["General"],
                "voice_personalities": ["Friendly"],
            },
            {
                "name": "zh-CN-XiaoxiaoNeural",
                "internal_name": (
                    "Microsoft Server Speech Text to Speech Voice "
                    "(zh-CN, XiaoxiaoNeural)"
                ),
                "friendly_name": (
                    "Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)"
                ),
                "locale": "zh-CN",
                "language": "zh",
                "gender": "Female",
                "status": "GA",
                "suggested_codec": "audio-24khz-48kbitrate-mono-mp3",
                "content_categories": [],
                "voice_personalities": [],
            },
        ]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("locale=ZH-cn", ["zh-CN-XiaoxiaoNeural"]),
        ("language=EN", ["en-US-GuyNeural"]),
        ("gender=fEmAlE", ["zh-CN-XiaoxiaoNeural"]),
        (
            "locale=zh-cn&language=ZH&gender=FEMALE",
            ["zh-CN-XiaoxiaoNeural"],
        ),
        ("locale=fr-FR", []),
    ],
)
async def test_voices_filters_are_case_insensitive_exact_matches(
    query: str, expected: List[str]
) -> None:
    """Each supported filter should match exactly without case sensitivity."""
    async with client_for() as client:
        response = await client.get(f"/v1/voices?{query}", headers=AUTH)

    assert response.status_code == 200
    assert [voice["name"] for voice in response.json()["voices"]] == expected


@pytest.mark.asyncio
async def test_invalid_voice_gender_returns_stable_400() -> None:
    """Only the genders exposed by upstream are accepted."""
    async with client_for() as client:
        response = await client.get("/v1/voices?gender=other", headers=AUTH)

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "message": "gender must be Female or Male",
    }


@pytest.mark.asyncio
async def test_voice_cache_hits_and_forwards_global_proxy() -> None:
    """Fresh cache entries avoid repeated upstream calls and use global proxy."""
    calls: List[Dict[str, Any]] = []

    async def tracked_factory(**kwargs: Any) -> List[Voice]:
        calls.append(kwargs)
        return VOICE_FIXTURES

    config = ServerConfig(api_key="secret", proxy="http://proxy.example:8080")
    async with client_for(config=config, voices_factory=tracked_factory) as client:
        first = await client.get("/v1/voices", headers={"X-API-Key": "secret"})
        second = await client.get("/v1/voices", headers={"X-API-Key": "secret"})

    assert first.status_code == second.status_code == 200
    assert calls == [{"proxy": "http://proxy.example:8080"}]


@pytest.mark.asyncio
async def test_voice_cache_refresh_is_single_flight() -> None:
    """Concurrent cache misses should await one shared upstream refresh."""
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_factory(**_kwargs: Any) -> List[Voice]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return VOICE_FIXTURES

    async with client_for(voices_factory=blocked_factory) as client:
        requests = [
            asyncio.create_task(client.get("/v1/voices", headers=AUTH))
            for _index in range(3)
        ]
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0)
        release.set()
        responses = await asyncio.gather(*requests)

    assert calls == 1
    assert all(response.status_code == 200 for response in responses)


@pytest.mark.asyncio
async def test_expired_voice_cache_refreshes() -> None:
    """A zero-age cache policy should fetch a new snapshot per request."""
    calls = 0

    async def tracked_factory(**_kwargs: Any) -> List[Voice]:
        nonlocal calls
        calls += 1
        return VOICE_FIXTURES

    config = ServerConfig(api_key="secret", voices_cache_ttl_seconds=1)
    async with client_for(config=config, voices_factory=tracked_factory) as client:
        first = await client.get("/v1/voices", headers={"X-API-Key": "secret"})
        await asyncio.sleep(1.01)
        second = await client.get("/v1/voices", headers={"X-API-Key": "secret"})

    assert first.status_code == second.status_code == 200
    assert calls == 2


@pytest.mark.asyncio
async def test_voice_cache_uses_stale_data_after_refresh_failure() -> None:
    """An expired successful snapshot should survive an upstream outage."""
    calls = 0

    async def flaky_factory(**_kwargs: Any) -> List[Voice]:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise aiohttp.ClientConnectionError("proxy-user:proxy-pass")
        return VOICE_FIXTURES

    config = ServerConfig(api_key="secret", voices_cache_ttl_seconds=1)
    async with client_for(config=config, voices_factory=flaky_factory) as client:
        first = await client.get("/v1/voices", headers={"X-API-Key": "secret"})
        await asyncio.sleep(1.01)
        stale = await client.get("/v1/voices", headers={"X-API-Key": "secret"})

    assert first.status_code == stale.status_code == 200
    assert stale.json() == first.json()
    assert calls == 2


@pytest.mark.asyncio
async def test_initial_voice_refresh_failure_returns_safe_502(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cold-cache outage should return no upstream or proxy details."""
    caplog.set_level(logging.INFO)

    async def failing_factory(**_kwargs: Any) -> List[Voice]:
        raise aiohttp.ClientConnectionError("proxy-user:proxy-pass")

    config = ServerConfig(
        api_key="secret",
        proxy="http://proxy-user:proxy-pass@proxy.example:8080",
    )
    async with client_for(config=config, voices_factory=failing_factory) as client:
        response = await client.get("/v1/voices", headers={"X-API-Key": "secret"})

    assert response.status_code == 502
    assert response.json() == {
        "error": "upstream_error",
        "message": "TTS upstream service failed",
    }
    assert "proxy-user" not in response.text
    assert "proxy-pass" not in response.text
    assert "proxy-user" not in caplog.text
    assert "proxy-pass" not in caplog.text


@pytest.mark.asyncio
async def test_voice_refresh_obeys_total_request_timeout() -> None:
    """A stuck voice-list upstream must not outlive the configured total timeout."""

    async def hanging_factory(**_kwargs: Any) -> List[Voice]:
        await asyncio.Event().wait()
        return []

    config = ServerConfig(api_key="secret", request_timeout_seconds=1)
    async with client_for(config=config, voices_factory=hanging_factory) as client:
        response = await asyncio.wait_for(
            client.get("/v1/voices", headers={"X-API-Key": "secret"}), timeout=2
        )

    assert response.status_code == 502
    assert response.json()["error"] == "upstream_error"


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
@pytest.mark.parametrize("boundary", ["WordBoundary", "SentenceBoundary"])
async def test_bundle_returns_one_synthesis_as_mp3_and_srt(boundary: str) -> None:
    """Bundle must buffer one upstream call into exactly two ZIP members."""
    BundleCommunicator.instances = 0
    FakeCommunicator.options.clear()
    body = {
        "text": "你好",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+10%",
        "volume": "-5%",
        "pitch": "+2Hz",
        "boundary": boundary,
    }
    async with client_for(BundleCommunicator) as client:
        response = await client.post("/v1/tts/bundle", json=body, headers=AUTH)

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/zip"
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="speech-bundle.zip"'
    )
    assert BundleCommunicator.instances == 1
    assert FakeCommunicator.options[-1]["boundary"] == boundary
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["speech.mp3", "speech.srt"]
        assert archive.read("speech.mp3") == b"ID3bundleaudio"
        subtitles = archive.read("speech.srt").decode("utf-8")
    assert "00:00:00,000 --> 00:00:00,100" in subtitles
    assert "你好" in subtitles


@pytest.mark.asyncio
async def test_bundle_defaults_to_sentence_boundary() -> None:
    """Omitting boundary should request SentenceBoundary from Edge."""
    FakeCommunicator.options.clear()
    async with client_for(BundleCommunicator) as client:
        response = await client.post(
            "/v1/tts/bundle", json={"text": "hello"}, headers=AUTH
        )

    assert response.status_code == 200
    assert FakeCommunicator.options[-1]["boundary"] == "SentenceBoundary"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["word", "Sentence", "", 1, None])
async def test_invalid_bundle_boundary_is_rejected(boundary: object) -> None:
    """Bundle accepts only the two boundary names supported by edge-tts."""
    async with client_for(BundleCommunicator) as client:
        response = await client.post(
            "/v1/tts/bundle",
            json={"text": "hello", "boundary": boundary},
            headers=AUTH,
        )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/tts", "/v1/tts/bundle"])
async def test_synthesis_forwards_global_proxy_and_upstream_timeouts(
    path: str,
) -> None:
    """Both synthesis routes must use only deployment-level network settings."""
    FakeCommunicator.options.clear()
    config = ServerConfig(
        api_key="secret",
        proxy="http://proxy-user:proxy-pass@proxy.example:8080",
        upstream_connect_timeout_seconds=4,
        upstream_receive_timeout_seconds=20,
    )
    communicator = BundleCommunicator if path.endswith("bundle") else FakeCommunicator
    async with client_for(communicator, config) as client:
        response = await client.post(
            path, json={"text": "hello"}, headers={"X-API-Key": "secret"}
        )

    assert response.status_code == 200
    assert FakeCommunicator.options[-1] == {
        "boundary": "SentenceBoundary",
        "proxy": "http://proxy-user:proxy-pass@proxy.example:8080",
        "connect_timeout": 4,
        "receive_timeout": 20,
    }


@pytest.mark.asyncio
async def test_bundle_authentication_precedes_body_reading() -> None:
    """Unauthorized Bundle requests must be rejected before their body is consumed."""
    config = ServerConfig(api_key="secret", max_request_bytes=8)
    async with client_for(BundleCommunicator, config) as client:
        response = await client.post(
            "/v1/tts/bundle",
            content=b"{" + (b"x" * 100),
            headers={"Content-Type": "application/json", "X-API-Key": "wrong"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bundle_shares_synthesis_concurrency_capacity() -> None:
    """Bundle and MP3 synthesis must compete for the same bounded slots."""
    SingleBlockingCommunicator.started = asyncio.Event()
    SingleBlockingCommunicator.release = asyncio.Event()
    config = ServerConfig(api_key="secret", max_concurrent_requests=1)
    async with client_for(SingleBlockingCommunicator, config) as client:
        running = asyncio.create_task(
            client.post(
                "/v1/tts", json={"text": "first"}, headers={"X-API-Key": "secret"}
            )
        )
        await asyncio.wait_for(SingleBlockingCommunicator.started.wait(), timeout=1)
        excess = await client.post(
            "/v1/tts/bundle",
            json={"text": "second"},
            headers={"X-API-Key": "secret"},
        )
        SingleBlockingCommunicator.release.set()
        completed = await running

    assert completed.status_code == 200
    assert excess.status_code == 429
    assert excess.headers["Retry-After"] == "1"
    assert excess.json()["error"] == "too_many_requests"


@pytest.mark.asyncio
async def test_bundle_obeys_text_and_audio_limits() -> None:
    """Bundle uses the same configured input and generated-audio boundaries."""
    text_config = ServerConfig(api_key="secret", max_text_length=3)
    async with client_for(BundleCommunicator, text_config) as client:
        text_response = await client.post(
            "/v1/tts/bundle",
            json={"text": "four"},
            headers={"X-API-Key": "secret"},
        )

    audio_config = ServerConfig(api_key="secret", max_audio_bytes=5)
    async with client_for(LargeAudioCommunicator, audio_config) as client:
        audio_response = await client.post(
            "/v1/tts/bundle",
            json={"text": "ok"},
            headers={"X-API-Key": "secret"},
        )

    assert text_response.status_code == 413
    assert text_response.json()["error"] == "text_too_long"
    assert audio_response.status_code == 413
    assert audio_response.json()["error"] == "audio_too_large"


@pytest.mark.asyncio
async def test_bundle_uses_total_timeout_and_safe_upstream_errors() -> None:
    """Bundle maps timeout and known Edge failures before returning ZIP bytes."""
    timeout_config = ServerConfig(api_key="secret", request_timeout_seconds=1)
    async with client_for(HangingCommunicator, timeout_config) as client:
        timeout_response = await client.post(
            "/v1/tts/bundle",
            json={"text": "hello"},
            headers={"X-API-Key": "secret"},
        )

    async with client_for(UpstreamCommunicator) as client:
        upstream_response = await client.post(
            "/v1/tts/bundle", json={"text": "hello"}, headers=AUTH
        )

    assert timeout_response.status_code == 504
    assert timeout_response.json()["error"] == "upstream_timeout"
    assert upstream_response.status_code == 502
    assert upstream_response.json()["error"] == "upstream_error"


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
@pytest.mark.parametrize("path", ["/v1/tts", "/v1/tts/bundle"])
async def test_actual_streamed_body_size_is_limited(path: str) -> None:
    """Chunked requests cannot bypass the configured byte limit."""
    config = ServerConfig(api_key="secret", max_request_bytes=20)

    async def content() -> AsyncIterator[bytes]:
        yield b'{"text":"'
        yield b"x" * 50
        yield b'"}'

    async with client_for(config=config) as client:
        response = await client.post(
            path,
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
    assert schema["info"]["version"] == "7.5.2"
    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }
    for path, method in (
        ("/v1/tts", "post"),
        ("/v1/tts/bundle", "post"),
        ("/v1/voices", "get"),
        ("/v1/models", "get"),
    ):
        assert schema["paths"][path][method]["security"] == [{"APIKeyHeader": []}]
    bundle_schema = schema["components"]["schemas"]["TTSBundleRequest"]
    assert bundle_schema["properties"]["boundary"]["default"] == "SentenceBoundary"
    assert (
        "audio/mpeg"
        in schema["paths"]["/v1/tts"]["post"]["responses"]["200"]["content"]
    )
    assert (
        "audio/wav" in schema["paths"]["/v1/tts"]["post"]["responses"]["200"]["content"]
    )
    tts_schema = schema["components"]["schemas"]["TTSRequest"]
    assert tts_schema["properties"]["model"]["default"] == "edge-tts"
    assert tts_schema["properties"]["response_format"]["default"] == "mp3"
    assert (
        "application/zip"
        in schema["paths"]["/v1/tts/bundle"]["post"]["responses"]["200"]["content"]
    )


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
