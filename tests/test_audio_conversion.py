"""Tests for the bundled FFmpeg audio conversion helper."""

import io
import math
import struct
import wave

import pytest

from edge_tts_server.audio import ConvertedAudioTooLarge, convert_audio


def make_wave() -> bytes:
    """Create a short deterministic 24 kHz mono PCM WAV fixture."""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        # Pylint infers Wave_read for an in-memory stream despite mode="wb".
        audio.setnchannels(1)  # pylint: disable=no-member
        audio.setsampwidth(2)  # pylint: disable=no-member
        audio.setframerate(24000)  # pylint: disable=no-member
        frames = [
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * index / 24000)))
            for index in range(2400)
        ]
        audio.writeframes(b"".join(frames))  # pylint: disable=no-member
    return output.getvalue()


@pytest.mark.asyncio
async def test_bundled_ffmpeg_converts_wav_to_mp3_and_back() -> None:
    """Both public formats should be usable without a system FFmpeg install."""
    source = make_wave()
    mp3 = await convert_audio(source, "wav", "mp3", 1024 * 1024)
    restored = await convert_audio(mp3, "mp3", "wav", 1024 * 1024)

    assert mp3.startswith(b"ID3")
    assert restored.startswith(b"RIFF")
    assert b"WAVE" in restored[:16]


@pytest.mark.asyncio
async def test_conversion_limit_is_enforced() -> None:
    """Converted output cannot bypass the configured audio ceiling."""
    with pytest.raises(ConvertedAudioTooLarge):
        await convert_audio(make_wave(), "wav", "mp3", 10)
