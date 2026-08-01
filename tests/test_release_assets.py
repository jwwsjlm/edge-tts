"""Static contracts for distributable release assets."""

import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parents[1]
HARDENED_LIMITS = {
    "max_text_length": 5000,
    "max_request_bytes": 65536,
    "max_concurrent_requests": 4,
    "request_timeout_seconds": 120,
    "max_audio_bytes": 20971520,
    "docs_enabled": False,
}


def load_yaml_mapping(path: Path) -> Dict[str, Any]:
    """Load a YAML fixture and require a mapping root."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


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


def test_win32_playback_typechecks_on_release_platforms() -> None:
    """Platform guards must type-check on Linux CI and Windows builders."""
    for platform in ("linux", "win32"):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--pretty",
                "--platform",
                platform,
                "src/edge_playback/win32_playback.py",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


def test_windows_example_config_is_safe_for_local_first_use() -> None:
    """The Windows example should not expose the service publicly by default."""
    path = ROOT / "packaging/windows/config.example.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert config == {
        "api_key": "CHANGE_ME_TO_A_LONG_RANDOM_SECRET",
        "host": "127.0.0.1",
        "port": 5050,
        **HARDENED_LIMITS,
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


def test_windows_builder_cleans_pyinstaller_child_processes_by_path() -> None:
    """One-file bootloader children must not keep the release EXE locked."""
    script = (ROOT / "build_windows_release.ps1").read_text(encoding="utf-8")

    assert "Win32_Process" in script
    assert "ExecutablePath" in script
    assert "Stop-ReleaseProcesses" in script


def test_pyinstaller_entry_uses_package_absolute_import() -> None:
    """PyInstaller executes the entry file without a package parent."""
    entry = (ROOT / "src/edge_tts_server/__main__.py").read_text(encoding="utf-8")

    assert "from edge_tts_server.cli import main" in entry


def test_pyinstaller_collects_fastapi_runtime_modules() -> None:
    """The standalone EXE must include framework modules loaded dynamically."""
    spec = (ROOT / "edge-tts-server.spec").read_text(encoding="utf-8")

    for package in ("fastapi", "pydantic", "uvicorn"):
        assert f'collect_submodules("{package}")' in spec


def test_windows_instructions_cover_double_click_and_api_key() -> None:
    """Archive-local help should be enough for first-time use."""
    readme = (ROOT / "packaging/windows/README.txt").read_text(encoding="utf-8")
    example = (ROOT / "packaging/windows/call-example.ps1").read_text(encoding="utf-8")

    assert "edge-tts-server.exe" in readme
    assert "config.yaml" in readme
    assert "X-API-Key" in readme
    assert "X-API-Key" in example
    assert "speech.mp3" in example
    assert "无需安装 Python" in readme
    assert "无需联网" in readme


def test_docker_assets_define_non_root_healthy_service() -> None:
    """The production image should use the shared entry point safely."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for required in (
        "FROM python:3.14-slim AS builder",
        "FROM python:3.14-slim AS runtime",
        "COPY --from=builder",
        "groupadd --gid 10001 edge-tts",
        "useradd --uid 10001",
        "USER edge-tts",
        "EXPOSE 5050",
        "STOPSIGNAL SIGTERM",
        "HEALTHCHECK",
        "/health",
        'ENTRYPOINT ["python", "-m", "edge_tts_server"]',
        'CMD ["--config", "/config/config.yaml"]',
        "yaml.safe_load",
    ):
        assert required in dockerfile
    assert dockerfile.count("FROM python:3.14-slim") == 2
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
        **HARDENED_LIMITS,
    }


def test_docker_guide_covers_configuration_and_operations() -> None:
    """The maintained guide should cover a complete secure deployment."""
    guide = (ROOT / "docs/docker.md").read_text(encoding="utf-8")

    for required in (
        "ghcr.io/jwwsjlm/edge-tts",
        "config.yaml",
        "0.0.0.0",
        "X-API-Key",
        "docker run",
        "/health",
        "linux/amd64",
        "linux/arm64",
        "edge-tts-server-linux-amd64.tar.gz",
        "docker load",
        "read_only",
        "chown 10001:10001 config.yaml",
        "HTTPS",
        "docker pull",
    ):
        assert required in guide


def test_release_notes_are_a_self_contained_docker_guide() -> None:
    """GitHub Release users should not need to find separate deployment docs."""
    notes = (ROOT / ".github/release-notes.md").read_text(encoding="utf-8")

    for required in (
        "__IMAGE__",
        "__VERSION__",
        "config.yaml",
        "0.0.0.0",
        "X-API-Key",
        "docker run",
        "edge-tts-server-windows-x64.zip",
        "edge-tts-server-linux-amd64.tar.gz",
        "sha256sum -c SHA256SUMS.txt",
        "docker load",
        "1Panel",
        "POST /v1/tts",
        "/health",
        "HTTPS",
    ):
        assert required in notes


def test_main_readme_links_http_and_docker_usage() -> None:
    """The project landing page should expose the new server entry points."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "edge-tts-server" in readme
    assert "POST /v1/tts" in readme
    assert "docs/docker.md" in readme
    assert "docs/api.md" in readme
    assert "docs/windows.md" in readme
    assert "docs/1panel.md" in readme
    assert "Windows 双击运行" in readme
    assert "Linux Docker" in readme
    assert "离线部署" in readme
    assert "python -m edge_tts_server --config config.yaml" in readme


def test_release_workflow_gates_all_publish_jobs() -> None:
    """Tag releases should test before publishing every target."""
    path = ROOT / ".github/workflows/release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert set(jobs) == {"validate", "windows", "docker", "release"}
    assert jobs["windows"]["needs"] == "validate"
    assert jobs["docker"]["needs"] == "validate"
    assert set(jobs["release"]["needs"]) == {"validate", "windows", "docker"}
    assert jobs["docker"]["permissions"]["packages"] == "write"
    assert jobs["release"]["permissions"]["contents"] == "write"


def test_automation_pins_python_3_14() -> None:
    """Maintained automation should use the release build Python version."""
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    quality = (ROOT / ".github/workflows/code-quality.yml").read_text(encoding="utf-8")

    assert release.count('python-version: "3.14"') == 2
    assert "Set up Python 3.14" in release
    assert "Set up Python 3.14 x64" in release
    assert "3.12" not in release
    assert "actions/checkout@v4" in quality
    assert "actions/setup-python@v5" in quality
    assert quality.count('python-version: "3.14"') == 1
    assert "python-version: 3.x" not in quality


def test_release_workflow_builds_expected_targets_and_notes() -> None:
    """The workflow should publish the agreed assets and deployment notes."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for required in (
        "v*.*.*",
        "^v[0-9]+\\.[0-9]+\\.[0-9]+$",
        "runpy.run_path",
        "src/edge_tts/version.py",
        "build_windows_release.ps1",
        "edge-tts-server-windows-x64.zip",
        "linux/amd64,linux/arm64",
        "ghcr.io/${{ github.repository }}",
        "edge-tts-server-linux-amd64.tar.gz",
        "SHA256SUMS.txt",
        "docker load",
        "Smoke-test native amd64 image",
        "Smoke-test reloaded offline image",
        "401",
        ".github/release-notes.md",
        "gh release create",
        "--verify-tag",
    ):
        assert required in workflow


def test_compose_files_have_shared_runtime_contract() -> None:
    """Production and development Compose files should run independently."""
    for filename in ("compose.yaml", "compose.dev.yaml"):
        compose = load_yaml_mapping(ROOT / filename)
        assert set(compose["services"]) == {"edge-tts"}
        service = compose["services"]["edge-tts"]
        assert service["container_name"] == "edge-tts"
        assert service["restart"] == "unless-stopped"
        assert service["ports"] == ["5050:5050"]
        assert service["volumes"] == ["./config.yaml:/config/config.yaml:ro"]
        assert service["dns"] == ["223.5.5.5", "119.29.29.29"]
        assert service["read_only"] is True
        assert service["tmpfs"] == ["/tmp:size=64m,mode=1777"]
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["init"] is True
        assert service["stop_grace_period"] == "30s"


def test_production_compose_uses_overridable_ghcr_image() -> None:
    """Server deployments should pull a published, optionally pinned image."""
    service = load_yaml_mapping(ROOT / "compose.yaml")["services"]["edge-tts"]

    assert service["image"] == (
        "ghcr.io/jwwsjlm/edge-tts:${EDGE_TTS_IMAGE_TAG:-latest}"
    )
    assert service["pull_policy"] == "missing"
    assert "build" not in service


def test_development_compose_builds_local_dockerfile() -> None:
    """Local development should build the current checkout."""
    service = load_yaml_mapping(ROOT / "compose.dev.yaml")["services"]["edge-tts"]

    assert service["build"] == {"context": ".", "dockerfile": "Dockerfile"}
    assert service["image"] == "edge-tts-http:local"


def test_docker_guide_documents_both_compose_workflows() -> None:
    """The maintained guide should explain standalone Compose operations."""
    guide = (ROOT / "docs/docker.md").read_text(encoding="utf-8")

    for required in (
        "docker compose -f compose.yaml up -d",
        "chown 10001:10001 config.yaml",
        "docker compose -f compose.dev.yaml up -d --build",
        "EDGE_TTS_IMAGE_TAG",
        "docker compose -f compose.yaml ps",
        "docker compose -f compose.yaml logs -f",
        "docker compose -f compose.yaml down",
        "223.5.5.5",
        "119.29.29.29",
    ):
        assert required in guide


def test_release_notes_include_production_compose_deployment() -> None:
    """Each Release should include the simplest production deployment path."""
    notes = (ROOT / ".github/release-notes.md").read_text(encoding="utf-8")

    for required in (
        "compose.yaml",
        "docker compose -f compose.yaml up -d",
        "EDGE_TTS_IMAGE_TAG",
        "223.5.5.5",
        "119.29.29.29",
    ):
        assert required in notes


def test_root_config_example_is_server_ready() -> None:
    """The repository-root example should work for Docker and 1Panel."""
    path = ROOT / "config.example.yaml"

    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "api_key": "CHANGE_ME_TO_A_LONG_RANDOM_SECRET",
        "host": "0.0.0.0",
        "port": 5050,
        **HARDENED_LIMITS,
    }


def test_release_version_is_7_3_1() -> None:
    """The package version is the source of truth for the release tag."""
    namespace = runpy.run_path(str(ROOT / "src/edge_tts/version.py"))

    assert namespace["__version__"] == "7.3.1"


def test_root_secret_config_is_ignored_without_hiding_example() -> None:
    """A real API key should not be committed accidentally."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "/config.yaml" in ignore
    assert "/config.example.yaml" not in ignore

    ignore_statuses = {
        path: subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", "--", path],
            cwd=ROOT,
            check=False,
        ).returncode
        for path in ("config.yaml", "config.example.yaml", "nested/config.yaml")
    }
    assert ignore_statuses == {
        "config.yaml": 0,
        "config.example.yaml": 1,
        "nested/config.yaml": 1,
    }


def test_1panel_guide_documents_docker_only_deployment() -> None:
    """1Panel users should have complete online and offline Docker paths."""
    path = ROOT / "docs/1panel.md"

    assert path.is_file()
    guide = path.read_text(encoding="utf-8")
    for required in (
        "Docker-only",
        "ghcr.io/jwwsjlm/edge-tts:7.3.0",
        "edge-tts-server-linux-amd64.tar.gz",
        "sha256sum -c SHA256SUMS.txt",
        "docker load",
        "EDGE_TTS_IMAGE_TAG=7.3.0",
        "chown 10001:10001 config.yaml",
        "docker compose -f compose.yaml up -d",
        "5050",
        "/health",
        "反向代理",
        "HTTPS",
        "升级",
        "回滚",
        "docker compose -f compose.yaml logs",
    ):
        assert required in guide
    assert "Python 运行环境" not in guide
    assert "pip install ." not in guide


def test_readme_links_1panel_docker_deployment() -> None:
    """The 1Panel guide should be discoverable from the README."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "(docs/1panel.md)" in readme


def test_api_guide_documents_complete_contract_and_clients() -> None:
    """Callers should not need source code to integrate with the API."""
    guide = (ROOT / "docs/api.md").read_text(encoding="utf-8")

    for required in (
        "POST /v1/tts",
        "X-API-Key",
        "text",
        "voice",
        "rate",
        "volume",
        "pitch",
        "5000",
        "65536",
        "20971520",
        "request_too_large",
        "text_too_long",
        "audio_too_large",
        "too_many_requests",
        "upstream_timeout",
        "X-Request-ID",
        "curl",
        "Python",
        "JavaScript",
        "PowerShell",
        "speech.mp3",
        "/docs",
        "/openapi.json",
    ):
        assert required in guide


def test_windows_build_guide_is_complete() -> None:
    """Maintainers should be able to reproduce the standalone x64 bundle."""
    guide = (ROOT / "docs/windows.md").read_text(encoding="utf-8")

    for required in (
        "Python 3.12",
        "python -m venv",
        'pip install -e ".[dev]"',
        "PyInstaller",
        "build_windows_release.ps1",
        "releases/windows/edge-tts-server-windows-x64.zip",
        "无需安装 Python",
        "/health",
    ):
        assert required in guide


def test_deployment_docs_copy_the_root_config_example() -> None:
    """Deployment instructions should use the discoverable root example."""
    docker_guide = (ROOT / "docs/docker.md").read_text(encoding="utf-8")
    release_notes = (ROOT / ".github/release-notes.md").read_text(encoding="utf-8")

    assert "cp config.example.yaml config.yaml" in docker_guide
    assert "Copy-Item .\\config.example.yaml .\\config.yaml" in docker_guide
    assert "cp config.example.yaml config.yaml" in release_notes
    for text in (docker_guide, release_notes):
        assert "packaging/docker/config.example.yaml" not in text
