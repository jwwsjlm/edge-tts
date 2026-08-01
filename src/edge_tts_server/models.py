"""Validated public models for the Edge TTS HTTP API."""

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


class ErrorResponse(BaseModel):
    """Stable error body shared by every public failure response."""

    error: str
    message: str
