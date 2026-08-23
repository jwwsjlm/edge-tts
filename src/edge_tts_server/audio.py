"""Bounded audio format conversion for buffered HTTP responses."""

import asyncio
import struct
from dataclasses import dataclass
from typing import List

import imageio_ffmpeg  # type: ignore[import-untyped]

_PIPE_CHUNK_BYTES = 64 * 1024
_STDERR_CAPTURE_BYTES = 64 * 1024
_MP3_HEADER_SCAN_BYTES = 64 * 1024
_MP3_MAX_FRAME_BYTES = 2048
_UNKNOWN_RIFF_SIZE = 0xFFFFFFFF

_MP3_SAMPLE_RATES = {
    3: (44100, 48000, 32000),  # MPEG-1
    2: (22050, 24000, 16000),  # MPEG-2
    0: (11025, 12000, 8000),  # MPEG-2.5
}
_MP3_LAYER_III_BITRATES = {
    3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    0: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}


@dataclass(frozen=True)
class AudioInfo:
    """Cheap metadata used for response integrity headers."""

    valid: bool
    duration_ms: int | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    bits_per_sample: int | None = None


class AudioConversionError(RuntimeError):
    """Raised when the bundled FFmpeg process cannot convert audio."""


class ConvertedAudioTooLarge(AudioConversionError):
    """Raised when converted audio crosses the configured response limit."""


def _wav_info(audio: bytes) -> AudioInfo:
    """Read PCM WAV chunks, including pipe output with unknown RIFF sizes."""
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return AudioInfo(valid=False)
    riff_size = struct.unpack_from("<I", audio, 4)[0]
    if riff_size == _UNKNOWN_RIFF_SIZE:
        container_end = len(audio)
    else:
        container_end = riff_size + 8
        if container_end > len(audio):
            return AudioInfo(valid=False)
    audio_format: int | None = None
    channels: int | None = None
    sample_rate: int | None = None
    byte_rate: int | None = None
    block_align: int | None = None
    bits_per_sample: int | None = None
    data_bytes: int | None = None
    offset = 12
    while offset + 8 <= container_end:
        chunk_id = audio[offset : offset + 4]
        declared_size = struct.unpack_from("<I", audio, offset + 4)[0]
        payload_offset = offset + 8
        available = container_end - payload_offset
        if chunk_id == b"fmt " and declared_size >= 16 and available >= 16:
            (
                audio_format,
                channels,
                sample_rate,
                byte_rate,
                block_align,
                bits_per_sample,
            ) = struct.unpack_from("<HHIIHH", audio, payload_offset)
        elif chunk_id == b"data":
            # A streaming WAV cannot seek back to fill its final data size, so
            # FFmpeg writes 0xffffffff. The complete HTTP buffer gives us the
            # authoritative number of bytes that actually followed the header.
            if declared_size == _UNKNOWN_RIFF_SIZE:
                data_bytes = available
            elif declared_size > available:
                return AudioInfo(valid=False)
            else:
                data_bytes = declared_size
            break
        if declared_size == _UNKNOWN_RIFF_SIZE or declared_size > available:
            break
        offset = payload_offset + declared_size + (declared_size & 1)

    required = (
        audio_format,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        data_bytes,
    )
    if any(value is None for value in required):
        return AudioInfo(valid=False)
    assert audio_format is not None
    assert channels is not None
    assert sample_rate is not None
    assert byte_rate is not None
    assert block_align is not None
    assert bits_per_sample is not None
    assert data_bytes is not None
    if (
        sample_rate <= 0
        or byte_rate <= 0
        or block_align <= 0
        or data_bytes <= 0
        or data_bytes % block_align != 0
    ):
        return AudioInfo(valid=False)
    frames = data_bytes // block_align
    duration_ms = max(1, round(frames * 1000 / sample_rate)) if frames else None
    return AudioInfo(
        valid=(
            audio_format == 1
            and channels == 1
            and bits_per_sample == 16
            and sample_rate == 24000
            and block_align == 2
            and byte_rate == sample_rate * block_align
            and duration_ms is not None
        ),
        duration_ms=duration_ms,
        sample_rate_hz=sample_rate,
        channels=channels,
        bits_per_sample=bits_per_sample,
    )


def _id3_payload_offset(audio: bytes) -> int:
    """Return the first byte after an ID3v2 header, if present."""
    if len(audio) < 10 or not audio.startswith(b"ID3"):
        return 0
    size_bytes = audio[6:10]
    if any(value & 0x80 for value in size_bytes):
        return 0
    payload_size = (
        (size_bytes[0] << 21)
        | (size_bytes[1] << 14)
        | (size_bytes[2] << 7)
        | size_bytes[3]
    )
    footer_size = 10 if audio[5] & 0x10 else 0
    return min(len(audio), 10 + payload_size + footer_size)


def _mp3_frame(
    audio: bytes, offset: int, audio_end: int
) -> tuple[int, int, int, int] | None:
    """Return version, sample rate, channel mode and size for one complete frame."""
    if offset < 0 or offset + 4 > audio_end:
        return None
    header = int.from_bytes(audio[offset : offset + 4], "big")
    if header >> 21 != 0x7FF:
        return None
    version = (header >> 19) & 0x3
    layer = (header >> 17) & 0x3
    bitrate_index = (header >> 12) & 0xF
    sample_rate_index = (header >> 10) & 0x3
    padding = (header >> 9) & 0x1
    if (
        version not in _MP3_SAMPLE_RATES
        or layer != 1
        or bitrate_index in {0, 15}
        or sample_rate_index == 3
    ):
        return None
    sample_rate = _MP3_SAMPLE_RATES[version][sample_rate_index]
    bitrate_kbps = _MP3_LAYER_III_BITRATES[version][bitrate_index]
    coefficient = 144_000 if version == 3 else 72_000
    frame_bytes = coefficient * bitrate_kbps // sample_rate + padding
    if frame_bytes <= 4 or offset + frame_bytes > audio_end:
        return None
    return version, sample_rate, (header >> 6) & 0x3, frame_bytes


def _compatible_mp3_frames(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> bool:
    """Return whether two frames belong to the same basic audio stream."""
    return first[:3] == second[:3]


def _terminal_mp3_frame(
    audio: bytes,
    start: int,
    audio_end: int,
    stream_frame: tuple[int, int, int, int],
) -> int | None:
    """Find a compatible complete frame ending exactly at the audio boundary."""
    tail_start = max(start, audio_end - _MP3_MAX_FRAME_BYTES)
    for offset in range(audio_end - 4, tail_start - 1, -1):
        frame = _mp3_frame(audio, offset, audio_end)
        if (
            frame is not None
            and _compatible_mp3_frames(stream_frame, frame)
            and offset + frame[3] == audio_end
        ):
            return offset
    return None


def _mp3_info(audio: bytes, duration_ms: int | None) -> AudioInfo:
    """Inspect complete consecutive MPEG Layer III frames and the audio tail."""
    start = _id3_payload_offset(audio)
    audio_end = len(audio) - 128 if audio[-128:-125] == b"TAG" else len(audio)
    stop = min(audio_end - 4, start + _MP3_HEADER_SCAN_BYTES)
    for offset in range(start, max(start, stop) + 1):
        first_frame = _mp3_frame(audio, offset, audio_end)
        if first_frame is None:
            continue
        next_offset = offset + first_frame[3]
        second_frame = _mp3_frame(audio, next_offset, audio_end)
        if second_frame is None or not _compatible_mp3_frames(
            first_frame, second_frame
        ):
            continue
        terminal_offset = _terminal_mp3_frame(
            audio, next_offset, audio_end, first_frame
        )
        if terminal_offset is None:
            continue
        if terminal_offset != next_offset:
            preceding_start = max(next_offset, terminal_offset - _MP3_MAX_FRAME_BYTES)
            if not any(
                (frame := _mp3_frame(audio, candidate, terminal_offset)) is not None
                and _compatible_mp3_frames(first_frame, frame)
                and candidate + frame[3] == terminal_offset
                for candidate in range(preceding_start, terminal_offset)
            ):
                continue
        _version, sample_rate, channel_mode, _frame_bytes = first_frame
        return AudioInfo(
            valid=(sample_rate == 24000 and channel_mode == 3),
            duration_ms=duration_ms,
            sample_rate_hz=sample_rate,
            channels=1 if channel_mode == 3 else 2,
        )
    return AudioInfo(valid=False, duration_ms=duration_ms)


def inspect_audio(
    audio: bytes, audio_format: str, duration_ms: int | None = None
) -> AudioInfo:
    """Return bounded-cost metadata for the two public audio formats."""
    if audio_format == "wav":
        return _wav_info(audio)
    if audio_format == "mp3":
        return _mp3_info(audio, duration_ms)
    return AudioInfo(valid=False, duration_ms=duration_ms)


async def _write_input(
    stream: asyncio.StreamWriter, audio: bytes, chunk_size: int = _PIPE_CHUNK_BYTES
) -> None:
    """Feed FFmpeg without making another full-size copy of the input buffer."""
    view = memoryview(audio)
    try:
        for offset in range(0, len(view), chunk_size):
            stream.write(view[offset : offset + chunk_size])
            await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        # FFmpeg may close stdin first when it rejects malformed input. Its exit
        # status and stderr below remain the authoritative conversion error.
        pass
    finally:
        stream.close()
        try:
            await stream.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass


async def _read_output(
    stream: asyncio.StreamReader,
    max_bytes: int,
    chunk_size: int = _PIPE_CHUNK_BYTES,
) -> bytes:
    """Read FFmpeg output incrementally and stop at the configured hard limit."""
    chunks: List[bytes] = []
    total = 0
    while chunk := await stream.read(chunk_size):
        total += len(chunk)
        if total > max_bytes:
            raise ConvertedAudioTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_stderr(
    stream: asyncio.StreamReader,
    capture_bytes: int = _STDERR_CAPTURE_BYTES,
    chunk_size: int = _PIPE_CHUNK_BYTES,
) -> bytes:
    """Drain stderr to avoid deadlocks while retaining only a safe diagnostic cap."""
    captured: List[bytes] = []
    remaining = capture_bytes
    while chunk := await stream.read(chunk_size):
        if remaining > 0:
            kept = chunk[:remaining]
            captured.append(kept)
            remaining -= len(kept)
    return b"".join(captured)


async def _stop_process(  # pylint: disable=no-member
    process: asyncio.subprocess.Process,
) -> None:
    """Terminate a converter after cancellation, overflow, or pipe failure."""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def convert_audio(
    audio: bytes,
    source_format: str,
    target_format: str,
    max_bytes: int,
) -> bytes:
    """Convert a complete audio buffer with the bundled FFmpeg executable."""
    if source_format == target_format:
        if len(audio) > max_bytes:
            raise ConvertedAudioTooLarge
        return audio

    output_args: tuple[str, ...]
    if (source_format, target_format) == ("mp3", "wav"):
        output_args = ("-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1", "-f", "wav")
    elif (source_format, target_format) == ("wav", "mp3"):
        output_args = ("-acodec", "libmp3lame", "-b:a", "128k", "-f", "mp3")
    else:
        raise AudioConversionError(
            f"Unsupported audio conversion: {source_format} to {target_format}"
        )

    process = await asyncio.create_subprocess_exec(
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        source_format,
        "-i",
        "pipe:0",
        *output_args,
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    input_task = asyncio.create_task(_write_input(process.stdin, audio))
    output_task = asyncio.create_task(_read_output(process.stdout, max_bytes))
    stderr_task = asyncio.create_task(_read_stderr(process.stderr))
    tasks = (input_task, output_task, stderr_task)
    try:
        _unused, converted, stderr = await asyncio.gather(*tasks)
        await process.wait()
    except BaseException:
        await _stop_process(process)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    if process.returncode != 0 or not converted:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise AudioConversionError(detail or "FFmpeg returned no audio")
    if len(converted) > max_bytes:
        raise ConvertedAudioTooLarge
    return converted
