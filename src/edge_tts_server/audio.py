"""Bounded audio format conversion for buffered HTTP responses."""

import asyncio

import imageio_ffmpeg  # type: ignore[import-untyped]


class AudioConversionError(RuntimeError):
    """Raised when the bundled FFmpeg process cannot convert audio."""


class ConvertedAudioTooLarge(AudioConversionError):
    """Raised when converted audio crosses the configured response limit."""


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
    try:
        converted, stderr = await process.communicate(audio)
    except BaseException:
        process.kill()
        await process.wait()
        raise

    if process.returncode != 0 or not converted:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise AudioConversionError(detail or "FFmpeg returned no audio")
    if len(converted) > max_bytes:
        raise ConvertedAudioTooLarge
    return converted
