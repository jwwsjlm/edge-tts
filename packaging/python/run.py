"""Run the vendored Edge TTS HTTP service."""

import sys
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parent
LIBS = BUNDLE_ROOT / "libs"
sys.path.insert(0, str(LIBS))


def main() -> None:
    """Run with a bundle-relative config unless explicit arguments are supplied."""
    from edge_tts_server.cli import main as server_main

    arguments = sys.argv[1:]
    if not arguments:
        arguments = ["--config", str(BUNDLE_ROOT / "config.yaml")]
    server_main(arguments)


if __name__ == "__main__":
    main()
