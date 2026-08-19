"""Asynchronous Xiaomi MiMo V2.5 speech synthesis client."""

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import aiohttp

MIMO_PUBLIC_MODEL = "mimo-v2-tts"
MIMO_UPSTREAM_MODELS = {
    "preset": "mimo-v2.5-tts",
    "design": "mimo-v2.5-tts-voicedesign",
    "clone": "mimo-v2.5-tts-voiceclone",
}


@dataclass(frozen=True)
class MiMoPresetVoice:
    """One preset voice published by the MiMo V2.5 API."""

    voice_id: str
    locale: str
    language: str
    gender: str


MIMO_PRESET_VOICES: Tuple[MiMoPresetVoice, ...] = (
    MiMoPresetVoice("mimo_default", "multilingual", "multi", "Unknown"),
    MiMoPresetVoice("冰糖", "zh-CN", "zh", "Female"),
    MiMoPresetVoice("茉莉", "zh-CN", "zh", "Female"),
    MiMoPresetVoice("苏打", "zh-CN", "zh", "Male"),
    MiMoPresetVoice("白桦", "zh-CN", "zh", "Male"),
    MiMoPresetVoice("Mia", "en-US", "en", "Female"),
    MiMoPresetVoice("Chloe", "en-US", "en", "Female"),
    MiMoPresetVoice("Milo", "en-US", "en", "Male"),
    MiMoPresetVoice("Dean", "en-US", "en", "Male"),
)
_PRESET_VOICE_IDS = frozenset(voice.voice_id for voice in MIMO_PRESET_VOICES)
_REFERENCE_AUDIO = re.compile(
    r"^data:audio/(?P<format>wav|x-wav|mpeg|mp3);base64,(?P<data>[A-Za-z0-9+/=]+)$",
    re.IGNORECASE,
)


class MiMoError(RuntimeError):
    """Base class for safe MiMo failures."""


class MiMoRateLimitError(MiMoError):
    """Raised when the MiMo service rejects a request for rate or quota limits."""

    def __init__(self, retry_after: Optional[str] = None) -> None:
        super().__init__("MiMo upstream rate limit")
        self.retry_after = retry_after


class MiMoResponseError(MiMoError):
    """Raised when MiMo returns a malformed or unsuccessful response."""


class MiMoAudioTooLarge(MiMoError):
    """Raised before returning a decoded response that exceeds its limit."""


class ReferenceAudioTooLarge(ValueError):
    """Raised when decoded voice-cloning audio exceeds its configured limit."""


@dataclass(frozen=True)
class MiMoSynthesisRequest:
    """Validated provider-specific synthesis options."""

    text: str
    mode: str
    voice: Optional[str] = None
    voice_description: Optional[str] = None
    reference_audio: Optional[str] = None


def validate_preset_voice(voice: str) -> None:
    """Reject voice IDs not published for MiMo V2.5 preset synthesis."""
    if voice not in _PRESET_VOICE_IDS:
        raise ValueError("voice is not a supported MiMo preset voice")


def normalize_reference_audio(value: str, max_bytes: int) -> str:
    """Validate and normalize a WAV/MP3 Base64 data URL without logging it."""
    match = _REFERENCE_AUDIO.fullmatch(value)
    if match is None:
        raise ValueError("reference_audio must be a WAV/MP3 Base64 data URL")
    try:
        decoded = base64.b64decode(match.group("data"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("reference_audio contains invalid Base64") from exc
    if not decoded:
        raise ValueError("reference_audio must not be empty")
    if len(decoded) > max_bytes:
        raise ReferenceAudioTooLarge("reference_audio exceeds configured limit")
    is_wav = match.group("format").casefold() in {"wav", "x-wav"}
    if is_wav and not (
        len(decoded) >= 12 and decoded[:4] == b"RIFF" and decoded[8:12] == b"WAVE"
    ):
        raise ValueError("reference_audio is not a valid WAV file")
    if not is_wav and not (
        decoded.startswith(b"ID3")
        or (len(decoded) >= 2 and decoded[0] == 0xFF and decoded[1] & 0xE0 == 0xE0)
    ):
        raise ValueError("reference_audio is not a valid MP3 file")
    media_type = "audio/wav" if is_wav else "audio/mpeg"
    encoded = base64.b64encode(decoded).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _payload(options: MiMoSynthesisRequest) -> Dict[str, Any]:
    """Build the exact official non-streaming Chat Completions payload."""
    upstream_model = MIMO_UPSTREAM_MODELS[options.mode]
    audio: Dict[str, str] = {"format": "wav"}
    instruction = ""

    if options.mode == "preset":
        assert options.voice is not None
        audio["voice"] = options.voice
    elif options.mode == "design":
        assert options.voice_description is not None
        instruction = options.voice_description
    else:
        assert options.reference_audio is not None
        audio["voice"] = options.reference_audio

    return {
        "model": upstream_model,
        "messages": [
            {"role": "user", "content": instruction},
            {"role": "assistant", "content": options.text},
        ],
        "audio": audio,
    }


class MiMoClient:
    """Reusable aiohttp client for Xiaomi MiMo speech synthesis."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout_seconds: int,
        max_audio_bytes: int,
        proxy: Optional[str] = None,
    ) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_audio_bytes = max_audio_bytes
        self._proxy = proxy
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def synthesize(self, options: MiMoSynthesisRequest) -> bytes:
        """Return complete WAV bytes from a non-streaming MiMo response."""
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }
        async with session.post(
            self._url,
            headers=headers,
            json=_payload(options),
            proxy=self._proxy,
        ) as response:
            if response.status == 429:
                raise MiMoRateLimitError(response.headers.get("Retry-After"))
            if response.status < 200 or response.status >= 300:
                await response.read()
                raise MiMoResponseError(f"MiMo upstream HTTP {response.status}")
            try:
                body = await response.json(content_type=None)
                encoded = body["choices"][0]["message"]["audio"]["data"]
                if not isinstance(encoded, str):
                    raise TypeError("audio data is not a string")
                if len(encoded) > ((self._max_audio_bytes + 2) // 3) * 4 + 2:
                    raise MiMoAudioTooLarge
                audio = base64.b64decode(encoded, validate=True)
            except (KeyError, IndexError, TypeError, ValueError, binascii.Error) as exc:
                raise MiMoResponseError("MiMo returned malformed audio data") from exc
            if not audio:
                raise MiMoResponseError("MiMo returned empty audio data")
            if len(audio) > self._max_audio_bytes:
                raise MiMoAudioTooLarge
            if not (
                len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"
            ):
                raise MiMoResponseError("MiMo returned invalid WAV audio")
            return audio

    async def close(self) -> None:
        """Close the lazily-created session during application shutdown."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
