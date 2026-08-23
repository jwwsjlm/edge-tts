"""Tests for the bundled FFmpeg audio conversion helper."""

import asyncio
import io
import math
import struct
import wave

import pytest

from edge_tts_server import audio as audio_module
from edge_tts_server.audio import ConvertedAudioTooLarge, convert_audio, inspect_audio


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
    mp3_info = inspect_audio(mp3, "mp3", duration_ms=100)
    wav_info = inspect_audio(restored, "wav")
    assert mp3_info.valid
    assert mp3_info.duration_ms == 100
    assert mp3_info.sample_rate_hz == 24000
    assert mp3_info.channels == 1
    assert wav_info.valid
    # MP3 encoder delay/padding makes the decoded container slightly longer
    # than the original 100 ms PCM fixture.
    assert 100 <= (wav_info.duration_ms or 0) <= 200
    assert wav_info.sample_rate_hz == 24000
    assert wav_info.channels == 1
    assert wav_info.bits_per_sample == 16


def test_inspection_rejects_truncated_wav_payload() -> None:
    """A regular WAV cannot claim more PCM bytes than the buffer contains."""
    truncated = make_wave()[:-2]

    info = inspect_audio(truncated, "wav")

    assert not info.valid
    assert info.duration_ms is None


@pytest.mark.asyncio
async def test_inspection_rejects_truncated_mp3_frames() -> None:
    """An MPEG sync header alone or an incomplete next frame is not valid MP3."""
    mp3 = await convert_audio(make_wave(), "wav", "mp3", 1024 * 1024)
    id3_end = audio_module._id3_payload_offset(mp3)  # pylint: disable=protected-access
    first_header_only = mp3[: id3_end + 4]

    assert not inspect_audio(first_header_only, "mp3", duration_ms=100).valid
    assert not inspect_audio(mp3[:-1], "mp3", duration_ms=100).valid


@pytest.mark.asyncio
async def test_conversion_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overflow stops FFmpeg while output is still being consumed."""
    stopped = False
    stop_process = audio_module._stop_process  # pylint: disable=protected-access

    async def record_stop(  # pylint: disable=no-member
        process: asyncio.subprocess.Process,
    ) -> None:
        nonlocal stopped
        stopped = True
        await stop_process(process)

    monkeypatch.setattr(audio_module, "_stop_process", record_stop)
    with pytest.raises(ConvertedAudioTooLarge):
        await convert_audio(make_wave(), "wav", "mp3", 10)
    assert stopped
