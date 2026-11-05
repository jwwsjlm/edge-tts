"""Command-line launcher for the authenticated Edge TTS server."""

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional, Sequence

import uvicorn

from .app import create_app
from .config import load_or_create_config

Runner = Callable[..., None]


def resolve_config_path(explicit: Optional[str]) -> Path:
    """Resolve configuration beside the EXE or in the current source directory."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.yaml"
    return Path.cwd() / "config.yaml"


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    """Parse server command-line arguments."""
    parser = argparse.ArgumentParser(description="Authenticated Edge TTS HTTP server")
    parser.add_argument(
        "--config",
        help="path to config.yaml (default: beside the EXE or in the current directory)",
    )
    return parser.parse_args(argv)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    runner: Runner = uvicorn.run,
) -> None:
    """Load configuration and run the HTTP service."""
    args = _parse_args(argv)
    config_path = resolve_config_path(args.config)
    existed = config_path.exists()
    config = load_or_create_config(config_path)
    data_path = Path(config.data_dir).expanduser()
    if not data_path.is_absolute():
        data_path = config_path.parent / data_path
    config = replace(config, data_dir=str(data_path.resolve()))

    print(f"Config: {config_path}")
    if not existed:
        print("Created config.yaml with a random API key. Keep it secret.")
    print(f"Listening on http://{config.host}:{config.port}")
    print("Press Ctrl+C to stop the server.")
    runner(
        create_app(config),
        host=config.host,
        port=config.port,
        access_log=False,
    )
