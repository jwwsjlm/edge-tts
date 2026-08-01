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


def test_docker_assets_define_non_root_healthy_service() -> None:
    """The production image should use the shared entry point safely."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for required in (
        "python:3.12-slim",
        "USER edge-tts",
        "EXPOSE 5050",
        "HEALTHCHECK",
        "/health",
        'ENTRYPOINT ["python", "-m", "edge_tts_server"]',
        'CMD ["--config", "/config/config.yaml"]',
    ):
        assert required in dockerfile
    assert ".git" in dockerignore
    assert "releases" in dockerignore


def test_docker_example_listens_on_all_interfaces() -> None:
    """The container example must be reachable through a published port."""
    path = ROOT / "packaging/docker/config.example.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert config == {
        "api_key": "CHANGE_ME_TO_A_LONG_RANDOM_SECRET",
        "host": "0.0.0.0",
        "port": 5050,
    }
