"""Tests for the retrying and segment-aware Python client example."""

import argparse
import hashlib
import io
import struct
import time
import urllib.error
import urllib.request
import wave
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from edge_tts_server.text import split_text
from examples import multi_model_tts


def _wave_bytes(value: int = 1000, frames: int = 240) -> bytes:
    output = io.BytesIO()
    with wave.Wave_write(output) as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(struct.pack("<h", value) * frames)
    return output.getvalue()


def test_split_text_prefers_boundaries_and_respects_limit() -> None:
    """Long stories are deterministically divided into bounded non-empty segments."""
    text = "第一句很长。第二句也很长。第三句结束。"
    segments = split_text(text, 9)

    assert all(0 < len(segment) <= 9 for segment in segments)
    assert "".join(segments) == text


@pytest.mark.parametrize(
    ("text", "max_chars"),
    [
        ("a。b", 1),
        ("  第一段。\n 第二段  ", 4),
        ("alpha, beta", 5),
    ],
)
def test_split_text_preserves_whitespace_and_hard_boundaries(
    text: str, max_chars: int
) -> None:
    """Splitting preserves every character and never exceeds a positive limit."""
    segments = split_text(text, max_chars)

    assert "".join(segments) == text
    assert all(0 < len(segment) <= max_chars for segment in segments)


@pytest.mark.parametrize("text", ["", " \n\t "])
def test_split_text_rejects_blank_input(text: str) -> None:
    """Empty and whitespace-only source text cannot produce valid requests."""
    with pytest.raises(ValueError, match="must not be blank"):
        split_text(text, 10)


def test_merge_segments_uses_the_supplied_sequence_order(tmp_path: Path) -> None:
    """The bundled FFmpeg merge preserves the caller's sorted segment order."""
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    output = tmp_path / "merged.wav"
    first.write_bytes(_wave_bytes(1000))
    second.write_bytes(_wave_bytes(2000))

    multi_model_tts.merge_segments([first, second], output)

    with wave.open(str(output), "rb") as stream:
        samples = struct.unpack(
            f"<{stream.getnframes()}h", stream.readframes(stream.getnframes())
        )
    assert samples[0] == 1000
    assert samples[-1] == 2000


class _Response:
    def __init__(self, text: str, segment_id: str, sequence: int) -> None:
        self.headers = Message()
        self.headers["X-Text-SHA256"] = hashlib.sha256(text.encode()).hexdigest()
        self.headers["X-Segment-ID"] = segment_id
        self.headers["X-Sequence"] = str(sequence)
        self.headers["X-Quality-Status"] = "pass"
        self.headers["X-Request-ID"] = "request-1"

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    @staticmethod
    def read() -> bytes:
        """Return a valid in-memory WAV response body."""
        return _wave_bytes()


def test_request_segment_retries_once_and_verifies_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient failures retry finitely before hash and sequence verification."""
    text = "需要重试的一段"
    calls = 0

    def fake_urlopen(*_args: Any, **_kwargs: Any) -> _Response:
        """Fail once, then return a response with valid correlation headers."""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.URLError("temporary")
        return _Response(text, "story-000001", 1)

    sleeps: list[float] = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    args = argparse.Namespace(
        base_url="http://127.0.0.1:5050",
        api_key="test-key",
        model="mimo-v2-tts",
        mimo_mode="preset",
        response_format="wav",
        rate="+0%",
        volume="+0%",
        pitch="+0Hz",
        voice="白桦",
        voice_description=None,
        retries=1,
        retry_base_seconds=0.25,
        timeout=1.0,
    )

    audio, headers = multi_model_tts.request_segment(
        args, text, "story-000001", 1, None
    )

    assert calls == 2
    assert sleeps == [0.25]
    assert audio.startswith(b"RIFF")
    assert headers["x-request-id"] == "request-1"
