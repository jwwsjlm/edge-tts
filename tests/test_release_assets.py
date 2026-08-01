"""Static contracts for distributable release assets."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_windows_release_assets_are_complete() -> None:
    """The Windows archive inputs should all be version-controlled."""
    expected = [
        ROOT / "edge-tts-server.spec",
        ROOT / "build_windows_release.ps1",
        ROOT / "packaging/windows/config.example.yaml",
        ROOT / "packaging/windows/README.txt",
        ROOT / "packaging/windows/call-example.ps1",
    ]

    assert all(path.is_file() for path in expected)


def test_windows_example_config_is_safe_for_local_first_use() -> None:
    """The Windows example should not expose the service publicly by default."""
    path = ROOT / "packaging/windows/config.example.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert config == {
        "api_key": "CHANGE_ME_TO_A_LONG_RANDOM_SECRET",
        "host": "127.0.0.1",
        "port": 5050,
    }


def test_windows_builder_defines_expected_archive_layout() -> None:
    """The builder should create the documented ZIP and smoke-test health."""
    script = (ROOT / "build_windows_release.ps1").read_text(encoding="utf-8")

    for required in (
        "edge-tts-server-windows-x64",
        "edge-tts-server.exe",
        "config.example.yaml",
        "README.txt",
        "call-example.ps1",
        "Compress-Archive",
        "/health",
    ):
        assert required in script


def test_pyinstaller_entry_uses_package_absolute_import() -> None:
    """PyInstaller executes the entry file without a package parent."""
    entry = (ROOT / "src/edge_tts_server/__main__.py").read_text(encoding="utf-8")

    assert "from edge_tts_server.cli import main" in entry


def test_windows_instructions_cover_double_click_and_api_key() -> None:
    """Archive-local help should be enough for first-time use."""
    readme = (ROOT / "packaging/windows/README.txt").read_text(encoding="utf-8")
    example = (ROOT / "packaging/windows/call-example.ps1").read_text(encoding="utf-8")

    assert "edge-tts-server.exe" in readme
    assert "config.yaml" in readme
    assert "X-API-Key" in readme
    assert "X-API-Key" in example
    assert "speech.mp3" in example
