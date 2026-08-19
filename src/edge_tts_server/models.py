"""Validated public models for the multi-model TTS HTTP API."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from edge_tts.constants import DEFAULT_VOICE


class TTSRequest(BaseModel):
    """Strict synthesis request accepted by ``POST /v1/tts``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    model: Literal["edge-tts", "mimo-v2-tts"] = Field(
        default="edge-tts",
        description="Synthesis provider. Omit it to preserve the original Edge TTS behavior.",
    )
    text: StrictStr = Field(
        description="Non-blank text to synthesize. MiMo accepts at most 3000 characters."
    )
    voice: StrictStr = Field(
        default=DEFAULT_VOICE,
        description=(
            "Voice name returned by GET /v1/voices. It is used by Edge TTS and "
            "MiMo preset mode only."
        ),
    )
    response_format: Literal["mp3", "wav"] = Field(
        default="mp3",
        description="Complete buffered audio format returned by the server.",
    )
    rate: StrictStr = Field(
        default="+0%",
        description=(
            "Edge TTS speaking-rate adjustment as a signed integer percentage, "
            "for example -20%, +0% or +30%."
        ),
    )
    volume: StrictStr = Field(
        default="+0%",
        description=(
            "Edge TTS volume adjustment as a signed integer percentage, for "
            "example -10%, +0% or +20%."
        ),
    )
    pitch: StrictStr = Field(
        default="+0Hz",
        description=(
            "Edge TTS pitch adjustment as signed integer hertz, for example "
            "-10Hz, +0Hz or +15Hz."
        ),
    )
    mimo_mode: Literal["preset", "design", "clone"] = Field(
        default="preset",
        description="MiMo-only voice mode: preset, text voice design, or audio cloning.",
    )
    voice_description: Optional[StrictStr] = Field(
        default=None,
        description=(
            "Required only for MiMo design mode. Describe language, gender, age, "
            "tone, emotion and speaking style."
        ),
    )
    reference_audio: Optional[StrictStr] = Field(
        default=None,
        description=(
            "Required only for MiMo clone mode. A Base64 data URL containing a "
            "valid WAV or MP3 file."
        ),
    )
    segment_id: Optional[StrictStr] = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Optional caller-defined identifier for a long-text segment.",
    )
    sequence: Optional[StrictInt] = Field(
        default=None,
        ge=1,
        description="Optional 1-based sequence number used when assembling segments.",
    )


class TTSBundleRequest(TTSRequest):
    """Synthesis request that also selects subtitle boundary metadata."""

    boundary: Literal["WordBoundary", "SentenceBoundary"] = Field(
        default="SentenceBoundary",
        description=(
            "Edge subtitle granularity. SentenceBoundary creates sentence-level "
            "SRT cues; WordBoundary creates word-level cues."
        ),
    )


class ErrorResponse(BaseModel):
    """Stable error body shared by every public failure response."""

    error: str
    message: str


class VoiceInfo(BaseModel):
    """Stable public representation of one upstream Edge voice."""

    name: str
    internal_name: str
    friendly_name: str
    locale: str
    language: str
    gender: str
    status: str
    suggested_codec: str
    content_categories: List[str]
    voice_personalities: List[str]


class VoicesResponse(BaseModel):
    """Response envelope returned by ``GET /v1/voices``."""

    voices: List[VoiceInfo]


class ModelInfo(BaseModel):
    """One selectable synthesis backend and its public capabilities."""

    id: str
    provider: str
    modes: List[str]
    response_formats: List[str]
    supports_subtitles: bool


class ModelsResponse(BaseModel):
    """Response envelope returned by ``GET /v1/models``."""

    models: List[ModelInfo]
