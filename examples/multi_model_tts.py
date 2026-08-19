"""Call the Edge TTS + Xiaomi MiMo HTTP service without extra SDKs."""

import argparse
import base64
import json
import mimetypes
import sys
import urllib.error
import urllib.request
from pathlib import Path


def reference_data_url(path: Path) -> str:
    """Encode a local WAV/MP3 file for MiMo voice cloning."""
    media_type = mimetypes.guess_type(path.name)[0]
    if media_type not in {"audio/wav", "audio/x-wav", "audio/mpeg"}:
        raise ValueError("Reference audio must be a WAV or MP3 file")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def main() -> None:
    """Parse arguments, call the non-streaming API, and save complete audio."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5050")
    parser.add_argument("--api-key", required=True)
    parser.add_argument(
        "--model", choices=("edge-tts", "mimo-v2-tts"), default="edge-tts"
    )
    parser.add_argument(
        "--mimo-mode", choices=("preset", "design", "clone"), default="preset"
    )
    parser.add_argument("--text", required=True)
    parser.add_argument("--voice")
    parser.add_argument("--voice-description")
    parser.add_argument("--reference-audio", type=Path)
    parser.add_argument("--response-format", choices=("mp3", "wav"), default="mp3")
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--volume", default="+0%")
    parser.add_argument("--pitch", default="+0Hz")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = {
        "model": args.model,
        "mimo_mode": args.mimo_mode,
        "text": args.text,
        "response_format": args.response_format,
        "rate": args.rate,
        "volume": args.volume,
        "pitch": args.pitch,
    }
    if args.voice:
        payload["voice"] = args.voice
    if args.voice_description:
        payload["voice_description"] = args.voice_description
    if args.reference_audio:
        payload["reference_audio"] = reference_data_url(args.reference_audio)

    request = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/v1/tts",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": args.api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            args.output.write_bytes(response.read())
            print(
                f"Saved {args.output} request_id={response.headers.get('X-Request-ID')}"
            )
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
