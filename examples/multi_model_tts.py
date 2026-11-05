"""Call Edge/MiMo once or synthesize, verify, retry, and merge text segments."""

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import imageio_ffmpeg  # type: ignore[import-untyped]

from edge_tts_server.text import split_text

_RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
_SEGMENT_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")


def reference_data_url(path: Path) -> str:
    """Encode a local WAV/MP3 file for MiMo voice cloning."""
    media_type = mimetypes.guess_type(path.name)[0]
    if media_type not in {"audio/wav", "audio/x-wav", "audio/mpeg"}:
        raise ValueError("Reference audio must be a WAV or MP3 file")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _valid_audio(audio: bytes, audio_format: str) -> bool:
    """Reject empty or obviously malformed downloads before merging them."""
    if audio_format == "wav":
        return len(audio) >= 44 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"
    if audio_format == "mp3":
        if audio.startswith(b"ID3"):
            return True
        scan = audio[: 64 * 1024]
        return any(
            scan[index] == 0xFF and scan[index + 1] & 0xE0 == 0xE0
            for index in range(max(0, len(scan) - 1))
        )
    return False


def _payload(
    args: argparse.Namespace,
    text: str,
    segment_id: Optional[str],
    sequence: Optional[int],
    reference_audio: Optional[str],
) -> Dict[str, Any]:
    """Build one request without silently dropping provider-specific fields."""
    payload: Dict[str, Any] = {
        "model": args.model,
        "mimo_mode": args.mimo_mode,
        "text": text,
        "response_format": args.response_format,
        "rate": args.rate,
        "volume": args.volume,
        "pitch": args.pitch,
    }
    if args.voice:
        payload["voice"] = args.voice
    if args.voice_description:
        payload["voice_description"] = args.voice_description
    if reference_audio:
        payload["reference_audio"] = reference_audio
    if segment_id is not None:
        payload["segment_id"] = segment_id
    if sequence is not None:
        payload["sequence"] = sequence
    return payload


def _retry_delay(headers: Any, attempt: int, base_seconds: float) -> float:
    """Honor an integer Retry-After, otherwise use bounded exponential backoff."""
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after is not None:
        try:
            return min(60.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return float(min(30.0, base_seconds * (2**attempt)))


def request_segment(
    args: argparse.Namespace,
    text: str,
    segment_id: Optional[str],
    sequence: Optional[int],
    reference_audio: Optional[str],
) -> Tuple[bytes, Dict[str, str]]:
    """Download one complete segment with finite retry and integrity validation."""
    expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    body = json.dumps(
        _payload(args, text, segment_id, sequence, reference_audio),
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/v1/tts",
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": args.api_key,
        },
        method="POST",
    )

    for attempt in range(args.retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                audio = response.read()
                headers = {
                    key.casefold(): value for key, value in response.headers.items()
                }
            if headers.get("x-text-sha256") != expected_hash:
                raise ValueError(
                    "Server text hash does not match the submitted segment"
                )
            if segment_id is not None and headers.get("x-segment-id") != segment_id:
                raise ValueError(
                    "Server segment ID does not match the submitted segment"
                )
            if sequence is not None and headers.get("x-sequence") != str(sequence):
                raise ValueError("Server sequence does not match the submitted segment")
            if headers.get("x-quality-status") != "pass" or not _valid_audio(
                audio, args.response_format
            ):
                raise ValueError("Server returned audio that failed integrity checks")
            return audio, headers
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in _RETRYABLE_HTTP_STATUS or attempt >= args.retries:
                print(
                    f"Segment failed status={exc.code} "
                    f"request_id={exc.headers.get('X-Request-ID')} "
                    f"segment_id={segment_id} sequence={sequence}: {detail}",
                    file=sys.stderr,
                )
                raise
            delay = _retry_delay(exc.headers, attempt, args.retry_base_seconds)
            print(
                f"Segment retry status={exc.code} "
                f"request_id={exc.headers.get('X-Request-ID')} after={delay}s",
                file=sys.stderr,
            )
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            if attempt >= args.retries:
                raise
            delay = _retry_delay(None, attempt, args.retry_base_seconds)
            print(f"Segment retry after {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(delay)

    raise RuntimeError("unreachable retry state")


def merge_segments(segment_paths: Sequence[Path], output: Path) -> None:
    """Merge same-format segments in sequence order with bundled FFmpeg."""
    if not segment_paths:
        raise ValueError("No segments to merge")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="edge-tts-concat-") as temporary:
        work = Path(temporary)
        local_segments: List[Path] = []
        for sequence, source in enumerate(segment_paths, start=1):
            target = work / f"{sequence:06d}{source.suffix}"
            target.write_bytes(source.read_bytes())
            local_segments.append(target)
        concat_file = work / "segments.txt"
        concat_file.write_text(
            "".join(f"file '{path.name}'\n" for path in local_segments),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:a",
                "copy",
                "-y",
                str(output.resolve()),
            ],
            cwd=work,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(
                result.stderr.strip() or "FFmpeg failed to merge segments"
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5050")
    parser.add_argument("--api-key", required=True)
    parser.add_argument(
        "--model", choices=("edge-tts", "mimo-v2-tts"), default="edge-tts"
    )
    parser.add_argument(
        "--mimo-mode", choices=("preset", "design", "clone"), default="preset"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file", type=Path)
    parser.add_argument("--voice")
    parser.add_argument("--voice-description")
    parser.add_argument("--reference-audio", type=Path)
    parser.add_argument("--response-format", choices=("mp3", "wav"), default="mp3")
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--volume", default="+0%")
    parser.add_argument("--pitch", default="+0Hz")
    parser.add_argument("--segment-size", type=int, default=0)
    parser.add_argument("--segment-id-prefix", default="segment")
    parser.add_argument("--sequence-start", type=int, default=1)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-base-seconds", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    """Parse arguments, call the buffered API, and optionally merge segments."""
    args = _parser().parse_args()
    if (
        args.retries < 0
        or args.sequence_start < 1
        or args.retry_base_seconds < 0
        or args.segment_size < 0
        or args.timeout <= 0
    ):
        raise SystemExit(
            "retries, segment size and retry delay must be non-negative; "
            "sequence and timeout must be positive"
        )
    if not _SEGMENT_PREFIX.fullmatch(args.segment_id_prefix):
        raise SystemExit(
            "segment-id-prefix must use only ASCII letters, digits, dot, "
            "underscore, colon, or hyphen"
        )
    text = (
        args.text
        if args.text is not None
        else args.text_file.read_text(encoding="utf-8")
    )
    segment_size = args.segment_size
    if args.text_file is not None and segment_size == 0:
        segment_size = 600 if args.model == "mimo-v2-tts" else 2000
    segments = split_text(text, segment_size)
    reference_audio = (
        reference_data_url(args.reference_audio) if args.reference_audio else None
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="edge-tts-segments-") as temporary:
        work = Path(temporary)
        completed: List[Tuple[int, Path]] = []
        manifest: List[Dict[str, Any]] = []
        for index, segment in enumerate(segments):
            sequence = args.sequence_start + index
            segment_id = (
                f"{args.segment_id_prefix}-{sequence:06d}"
                if len(segments) > 1
                else None
            )
            audio, headers = request_segment(
                args,
                segment,
                segment_id,
                sequence if segment_id else None,
                reference_audio,
            )
            path = work / f"{sequence:06d}.{args.response_format}"
            path.write_bytes(audio)
            completed.append((sequence, path))
            manifest.append(
                {
                    "segment_id": segment_id,
                    "sequence": sequence,
                    "text_sha256": headers["x-text-sha256"],
                    "request_id": headers.get("x-request-id"),
                    "audio_bytes": len(audio),
                    "duration_ms": (
                        int(headers["x-audio-duration-ms"])
                        if "x-audio-duration-ms" in headers
                        else None
                    ),
                    "status": "completed",
                }
            )
            print(
                f"Completed sequence={sequence} request_id={headers.get('x-request-id')}"
            )

        ordered_paths = [path for _sequence, path in sorted(completed)]
        if len(ordered_paths) == 1:
            args.output.write_bytes(ordered_paths[0].read_bytes())
        else:
            merge_segments(ordered_paths, args.output)
        args.output.with_suffix(args.output.suffix + ".segments.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"Saved {args.output} segments={len(segments)}")


if __name__ == "__main__":
    main()
