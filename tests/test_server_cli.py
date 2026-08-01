"""Tests for the HTTP server command-line launcher."""

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

from edge_tts_server.cli import main, resolve_config_path


def test_explicit_config_path_wins(tmp_path: Path) -> None:
    """The CLI argument should support Docker and custom layouts."""
    path = tmp_path / "custom.yaml"

    assert resolve_config_path(str(path)) == path.resolve()


def test_source_mode_uses_current_directory(tmp_path: Path, monkeypatch: Any) -> None:
    """Source launches should use config.yaml from the working directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert resolve_config_path(None) == tmp_path / "config.yaml"


def test_frozen_mode_uses_executable_directory(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Double-click releases should keep configuration beside the EXE."""
    executable = tmp_path / "release" / "edge-tts-server.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert resolve_config_path(None) == executable.parent / "config.yaml"


def test_main_loads_config_and_starts_aiohttp(tmp_path: Path, capsys: Any) -> None:
    """The launcher should bind exactly to values from config.yaml."""
    path = tmp_path / "config.yaml"
    path.write_text(
        'api_key: "secret"\nhost: "127.0.0.1"\nport: 6123\n', encoding="utf-8"
    )
    captured: Dict[str, Any] = {}

    def runner(
        app: web.Application,
        *,
        host: str,
        port: int,
        print: Optional[Any],  # pylint: disable=redefined-builtin
    ) -> None:
        captured.update(app=app, host=host, port=port, print=print)

    main(["--config", str(path)], runner=runner)

    output = capsys.readouterr().out
    assert str(path.resolve()) in output
    assert "http://127.0.0.1:6123" in output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 6123
    assert captured["print"] is None
    assert isinstance(captured["app"], web.Application)
