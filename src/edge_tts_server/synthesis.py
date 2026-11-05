"""Shared provider validation, voice caching and reusable synthesis sessions."""

# pylint: disable=too-many-lines,too-few-public-methods
import asyncio
import time
from dataclasses import replace
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Protocol

import aiohttp

from edge_tts import Communicate, list_voices
from edge_tts.data_classes import TTSConfig
from edge_tts.submaker import SubMaker
from edge_tts.typing import Voice

from .audio import convert_audio, inspect_audio
from .config import ServerConfig
from .mimo import (
    MIMO_PRESET_VOICES,
    MiMoClient,
    MiMoSynthesisRequest,
    ReferenceAudioTooLarge,
    normalize_reference_audio,
    validate_preset_voice,
)
from .models import TTSRequest, VoiceInfo

CommunicatorFactory = Callable[..., Any]
VoicesFactory = Callable[..., Awaitable[List[Voice]]]
AudioConverter = Callable[[bytes, str, str, int], Awaitable[bytes]]
SessionFactory = Callable[[], Awaitable[aiohttp.ClientSession]]


class MiMoClientLike(Protocol):
    """Minimal injectable MiMo client contract used by the application."""

    async def synthesize(self, options: MiMoSynthesisRequest) -> bytes:
        """Return complete WAV audio."""


class RequestFieldError(ValueError):
    """Validated client error with safe field-level details."""

    def __init__(self, message: str, fields: List[Dict[str, Any]]) -> None:
        super().__init__(message)
        self.fields = fields


def _field_detail(
    field: str, reason: str, allowed: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Build one secret-free field error entry."""
    detail: Dict[str, Any] = {"field": field, "reason": reason}
    if allowed is not None:
        detail["allowed"] = allowed
    return detail


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
    session_factory: Optional[SessionFactory] = None,
) -> tuple[bytes, str, Optional[int]]:
    """Collect one upstream synthesis into bounded audio and optional SRT."""
    communicator_options: Dict[str, Any] = {
        "rate": options["rate"],
        "volume": options["volume"],
        "pitch": options["pitch"],
        "boundary": options["boundary"],
        "proxy": config.proxy,
        "connect_timeout": config.upstream_connect_timeout_seconds,
        "receive_timeout": config.upstream_receive_timeout_seconds,
    }
    if session_factory is not None:
        communicator_options["session"] = await session_factory()
    communicator = communicator_factory(
        options["text"], options["voice"], **communicator_options
    )
    audio_chunks: List[bytes] = []
    audio_size = 0
    duration_ms: Optional[int] = None
    submaker = SubMaker() if include_subtitles else None
    async for chunk in communicator.stream():
        if chunk["type"] == "audio":
            data = chunk["data"]
            audio_size += len(data)
            if audio_size > config.max_audio_bytes:
                raise AudioTooLarge
            audio_chunks.append(data)
            continue
        offset = chunk.get("offset")
        duration = chunk.get("duration")
        if isinstance(offset, (int, float)) and isinstance(duration, (int, float)):
            boundary_end_ms = max(0, round((offset + duration) / 10_000))
            duration_ms = max(duration_ms or 0, boundary_end_ms)
        if submaker is not None:
            submaker.feed(chunk)
    subtitles = submaker.get_srt() if submaker is not None else ""
    return b"".join(audio_chunks), subtitles, duration_ms


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


def _validate_edge_request(
    payload: TTSRequest,
    boundary: Literal["WordBoundary", "SentenceBoundary"],
) -> Dict[str, str]:
    """Validate Edge-specific fields and return normalized synthesis options."""
    fields: List[Dict[str, Any]] = []
    if payload.mimo_mode != "preset":
        fields.append(
            _field_detail("mimo_mode", "is only supported by mimo-v2-tts", ["preset"])
        )
    for field in ("voice_description", "reference_audio", "instructions"):
        if getattr(payload, field) is not None:
            fields.append(_field_detail(field, "is not supported by edge-tts"))
    if payload.speaking_rate != "normal":
        fields.append(
            _field_detail("speaking_rate", "is only supported by mimo-v2-tts")
        )
    if fields:
        raise RequestFieldError(
            "MiMo-only fields are not supported by edge-tts", fields
        )

    for field, pattern in (
        ("rate", r"^[+-]\d+%$"),
        ("volume", r"^[+-]\d+%$"),
        ("pitch", r"^[+-]\d+Hz$"),
    ):
        try:
            TTSConfig.validate_string_param(field, getattr(payload, field), pattern)
        except (TypeError, ValueError) as exc:
            raise RequestFieldError(str(exc), [_field_detail(field, str(exc))]) from exc
    try:
        return _validated_options(payload, boundary)
    except (TypeError, ValueError) as exc:
        raise RequestFieldError(str(exc), [_field_detail("voice", str(exc))]) from exc


def _validate_mimo_common_request(payload: TTSRequest, config: ServerConfig) -> None:
    """Validate MiMo constraints shared by every synthesis mode."""
    if len(payload.text) > min(config.max_text_length, 3000):
        raise RequestFieldError(
            "MiMo text exceeds the 3000 character limit",
            [_field_detail("text", "must contain at most 3000 characters")],
        )
    unsupported_controls = [
        field
        for field, default in (
            ("rate", "+0%"),
            ("volume", "+0%"),
            ("pitch", "+0Hz"),
        )
        if getattr(payload, field) != default
    ]
    if unsupported_controls:
        raise RequestFieldError(
            "rate, volume and pitch are not supported by mimo-v2-tts",
            [
                _field_detail(field, "is not supported by mimo-v2-tts")
                for field in unsupported_controls
            ],
        )


def _validate_mimo_preset_request(payload: TTSRequest) -> MiMoSynthesisRequest:
    """Validate and build a MiMo preset-voice request."""
    fields = [
        _field_detail(field, "is not accepted in preset mode")
        for field in ("voice_description", "reference_audio")
        if getattr(payload, field) is not None
    ]
    if fields:
        raise RequestFieldError(
            "preset mode does not accept design or clone fields", fields
        )
    voice = payload.voice if "voice" in payload.model_fields_set else "mimo_default"
    try:
        validate_preset_voice(voice)
    except ValueError as exc:
        raise RequestFieldError(
            str(exc),
            [
                _field_detail(
                    "voice",
                    str(exc),
                    [preset.voice_id for preset in MIMO_PRESET_VOICES],
                )
            ],
        ) from exc
    return MiMoSynthesisRequest(
        text=payload.text,
        mode="preset",
        voice=voice,
    )


def _validate_mimo_design_request(payload: TTSRequest) -> MiMoSynthesisRequest:
    """Validate and build a MiMo voice-design request."""
    if payload.reference_audio is not None:
        raise RequestFieldError(
            "design mode does not accept reference_audio",
            [_field_detail("reference_audio", "is not accepted in design mode")],
        )
    if payload.voice_description is None or not payload.voice_description.strip():
        raise RequestFieldError(
            "voice_description is required for MiMo design mode",
            [_field_detail("voice_description", "is required in design mode")],
        )
    return MiMoSynthesisRequest(
        text=payload.text,
        mode="design",
        voice_description=payload.voice_description,
    )


def _validate_mimo_clone_request(
    payload: TTSRequest, config: ServerConfig
) -> MiMoSynthesisRequest:
    """Validate and build a MiMo voice-cloning request."""
    if payload.voice_description is not None:
        raise RequestFieldError(
            "clone mode does not accept voice_description",
            [_field_detail("voice_description", "is not accepted in clone mode")],
        )
    if payload.reference_audio is None:
        raise RequestFieldError(
            "reference_audio is required for MiMo clone mode",
            [_field_detail("reference_audio", "is required in clone mode")],
        )
    try:
        reference_audio = normalize_reference_audio(
            payload.reference_audio,
            config.max_reference_audio_bytes,
        )
    except ReferenceAudioTooLarge:
        raise
    except ValueError as exc:
        raise RequestFieldError(
            str(exc), [_field_detail("reference_audio", str(exc))]
        ) from exc
    return MiMoSynthesisRequest(
        text=payload.text,
        mode="clone",
        reference_audio=reference_audio,
    )


def _validate_mimo_request(
    payload: TTSRequest, config: ServerConfig
) -> MiMoSynthesisRequest:
    """Validate common MiMo rules and dispatch to the selected mode."""
    _validate_mimo_common_request(payload, config)
    if payload.mimo_mode != "preset" and "voice" in payload.model_fields_set:
        raise RequestFieldError(
            "voice is only supported by MiMo preset mode",
            [_field_detail("voice", "is only supported in preset mode")],
        )
    if payload.mimo_mode == "preset":
        options = _validate_mimo_preset_request(payload)
    elif payload.mimo_mode == "design":
        options = _validate_mimo_design_request(payload)
    else:
        options = _validate_mimo_clone_request(payload, config)
    speed = {
        "very_slow": "语速很慢，舒缓从容。",
        "slow": "语速稍慢，吐字清晰。",
        "normal": "",
        "fast": "语速稍快，表达流畅。",
        "very_fast": "语速很快，节奏紧凑。",
    }[payload.speaking_rate]
    instructions = "\n".join(part for part in (payload.instructions, speed) if part)
    return replace(options, instructions=instructions or None)


def _validate_provider_request(
    payload: TTSRequest,
    config: ServerConfig,
    boundary: Literal["WordBoundary", "SentenceBoundary"] = "SentenceBoundary",
) -> tuple[Optional[Dict[str, str]], Optional[MiMoSynthesisRequest]]:
    """Validate model-specific options without silently ignoring any field."""
    if payload.model == "edge-tts":
        return _validate_edge_request(payload, boundary), None
    return None, _validate_mimo_request(payload, config)


class SynthesisEngine:  # pylint: disable=too-many-instance-attributes
    """Own provider sessions while sharing synthesis behavior with every API."""

    def __init__(
        self,
        config: ServerConfig,
        communicator_factory: CommunicatorFactory = Communicate,
        *,
        voices_factory: VoicesFactory = list_voices,
        mimo_client: Optional[MiMoClientLike] = None,
        audio_converter: AudioConverter = convert_audio,
    ) -> None:
        self.config = config
        self.communicator_factory = communicator_factory
        self.audio_converter = audio_converter
        self.voices_cache = VoiceCache(
            voices_factory,
            config.proxy,
            config.voices_cache_ttl_seconds,
            config.request_timeout_seconds,
        )
        self.session: Optional[aiohttp.ClientSession] = None
        self.owned_mimo: Optional[MiMoClient] = None
        if mimo_client is None and config.mimo_api_key:
            self.owned_mimo = MiMoClient(
                config.mimo_api_key,
                config.mimo_base_url,
                config.mimo_request_timeout_seconds,
                config.max_audio_bytes,
                config.proxy,
            )
            mimo_client = self.owned_mimo
        self.mimo_client = mimo_client

    async def get_session(self) -> aiohttp.ClientSession:
        """Reuse Edge connections and DNS state across requests or task segments."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    limit=self.config.max_concurrent_requests,
                    limit_per_host=self.config.max_concurrent_requests,
                    ttl_dns_cache=300,
                ),
                trust_env=True,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    connect=None,
                    sock_connect=self.config.upstream_connect_timeout_seconds,
                    sock_read=self.config.upstream_receive_timeout_seconds,
                ),
            )
        return self.session

    async def synthesize(
        self,
        payload: TTSRequest,
        boundary: Literal["WordBoundary", "SentenceBoundary"] = "SentenceBoundary",
        *,
        include_subtitles: bool = False,
    ) -> tuple[bytes, str, str, Optional[int]]:
        """Generate one bounded segment; scheduling belongs to the caller."""
        edge, mimo = _validate_provider_request(payload, self.config, boundary)

        async def generate() -> tuple[bytes, str, str, Optional[int]]:
            if edge is not None:
                audio, subtitles, duration = await _collect_synthesis(
                    edge,
                    self.communicator_factory,
                    self.config,
                    include_subtitles=include_subtitles,
                    session_factory=(
                        self.get_session
                        if self.communicator_factory is Communicate
                        else None
                    ),
                )
                source_format = "mp3"
            else:
                if self.mimo_client is None or mimo is None:
                    raise ValueError("MiMo provider is not configured")
                audio = await self.mimo_client.synthesize(mimo)
                subtitles = ""
                source_format = "wav"
                duration = inspect_audio(audio, source_format).duration_ms
            if len(audio) > self.config.max_audio_bytes:
                raise AudioTooLarge
            if source_format != payload.response_format:
                audio = await self.audio_converter(
                    audio,
                    source_format,
                    payload.response_format,
                    self.config.max_audio_bytes,
                )
                source_format = payload.response_format
            return audio, subtitles, source_format, duration

        return await asyncio.wait_for(generate(), self.config.request_timeout_seconds)

    async def close(self) -> None:
        """Release only clients owned by this engine."""
        if self.session is not None and not self.session.closed:
            await self.session.close()
        if self.owned_mimo is not None:
            await self.owned_mimo.close()
