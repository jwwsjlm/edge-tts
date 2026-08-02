"""Validated public models for the Edge TTS HTTP API."""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, StrictStr

from edge_tts.constants import DEFAULT_VOICE


class TTSRequest(BaseModel):
    """Strict synthesis request accepted by ``POST /v1/tts``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: StrictStr
    voice: StrictStr = DEFAULT_VOICE
    rate: StrictStr = "+0%"
    volume: StrictStr = "+0%"
    pitch: StrictStr = "+0Hz"


class TTSBundleRequest(TTSRequest):
    """Synthesis request that also selects subtitle boundary metadata."""

    boundary: Literal["WordBoundary", "SentenceBoundary"] = "SentenceBoundary"


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
