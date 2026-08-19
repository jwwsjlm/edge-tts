"""Static contracts for distributable release assets."""

import runpy
import subprocess
import sys
import tarfile
import types
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HARDENED_LIMITS = {
    "max_text_length": 5000,
    "max_request_bytes": 15728640,
    "max_concurrent_requests": 4,
    "request_timeout_seconds": 120,
    "max_audio_bytes": 67108864,
    "docs_enabled": False,
    "voices_cache_ttl_seconds": 3600,
    "proxy": None,
    "upstream_connect_timeout_seconds": 10,
    "upstream_receive_timeout_seconds": 60,
    "mimo_api_key": None,
    "mimo_base_url": "https://api.xiaomimimo.com/v1",
    "mimo_request_timeout_seconds": 120,
    "max_reference_audio_bytes": 10485760,
    "mimo_recommended_max_text_length": 600,
}


def load_yaml_mapping(path: Path) -> Dict[str, Any]:
    """Load a YAML fixture and require a mapping root."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_python_bundle_runner_is_path_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped runner should load vendored libraries and a sibling config."""
    runner_path = ROOT / "packaging/python/run.py"

    assert runner_path.is_file()
    monkeypatch.setattr(sys, "path", sys.path.copy())
    namespace = runpy.run_path(str(runner_path))
    bundle_root = runner_path.resolve().parent
    assert namespace["BUNDLE_ROOT"] == bundle_root
    assert namespace["LIBS"] == bundle_root / "libs"
    assert sys.path[0] == str(bundle_root / "libs")

    captured: list[list[str]] = []
    cli_module = types.ModuleType("edge_tts_server.cli")
    cli_module.main = lambda argv: captured.append(list(argv))  # type: ignore[attr-defined]
    package = types.ModuleType("edge_tts_server")
    monkeypatch.setitem(sys.modules, "edge_tts_server", package)
    monkeypatch.setitem(sys.modules, "edge_tts_server.cli", cli_module)
    monkeypatch.setattr(sys, "argv", [str(runner_path)])

    namespace["main"]()

    assert captured == [["--config", str(bundle_root / "config.yaml")]]


def test_python_bundle_runner_forwards_explicit_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit CLI arguments should override the bundle-relative default."""
    runner_path = ROOT / "packaging/python/run.py"
    monkeypatch.setattr(sys, "path", sys.path.copy())
    namespace = runpy.run_path(str(runner_path))
    captured: list[list[str]] = []
    cli_module = types.ModuleType("edge_tts_server.cli")
    cli_module.main = lambda argv: captured.append(list(argv))  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules, "edge_tts_server", types.ModuleType("edge_tts_server")
    )
    monkeypatch.setitem(sys.modules, "edge_tts_server.cli", cli_module)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(runner_path), "--config", "/srv/edge/config.yaml"],
    )

    namespace["main"]()

    assert captured == [["--config", "/srv/edge/config.yaml"]]


def test_python_bundle_builder_creates_clean_single_root_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The builder should keep only runtime files and remove forbidden content."""
    namespace = runpy.run_path(str(ROOT / "packaging/python/build_bundle.py"))

    def fake_install(libs: Path) -> None:
        (libs / "edge_tts").mkdir(parents=True)
        (libs / "edge_tts_server").mkdir()
        (libs / "edge_playback").mkdir()
        (libs / "bin").mkdir()
        (libs / "bin" / "edge-tts-server").write_text("remove", encoding="utf-8")
        (libs / "dependency" / "__pycache__").mkdir(parents=True)
        (libs / "dependency" / "README.md").write_text("remove", encoding="utf-8")
        (libs / "dependency" / "module.pyc").write_bytes(b"remove")
        (libs / "dependency" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    monkeypatch.setitem(
        namespace["build_bundle"].__globals__, "_install_runtime", fake_install
    )
    archive = namespace["build_bundle"](tmp_path)
    bundle = tmp_path / "edge-tts-linux-amd64-python314"

    assert archive == tmp_path / "edge-tts-linux-amd64-python314.tar.gz"
    assert archive.is_file()
    assert {path.name for path in bundle.iterdir()} == {
        "libs",
        "config.example.yaml",
        "run.py",
        "LICENSE",
    }
    assert not (bundle / "libs/edge_playback").exists()
    assert not (bundle / "libs/bin").exists()
    assert not list(bundle.rglob("*.md"))
    assert not list(bundle.rglob("*.pyc"))
    assert not list(bundle.rglob("__pycache__"))
    with tarfile.open(archive, "r:gz") as packaged:
        roots = {member.name.split("/", 1)[0] for member in packaged.getmembers()}
    assert roots == {"edge-tts-linux-amd64-python314"}


def test_windows_release_assets_are_complete() -> None:
    """The Windows single-file build inputs should be version-controlled."""
    expected = [
        ROOT / "edge-tts-server.spec",
        ROOT / "build_windows_release.ps1",
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


def test_windows_builder_defines_expected_single_file_layout() -> None:
    """The builder should create and smoke-test one Windows ZIP."""
    script = (ROOT / "build_windows_release.ps1").read_text(encoding="utf-8")

    for required in (
        "edge-tts-windows-x64.exe",
        "edge-tts-windows-x64.zip",
        "config.example.yaml",
        "Compress-Archive",
        "Copy-Item",
        "config.yaml",
        "/health",
    ):
        assert required in script


def test_windows_builder_cleans_pyinstaller_child_processes_by_path() -> None:
    """One-file bootloader children must not keep the release EXE locked."""
    script = (ROOT / "build_windows_release.ps1").read_text(encoding="utf-8")

    assert "Win32_Process" in script
    assert "ExecutablePath" in script
    assert "Stop-PackagedProcess" in script


def test_pyinstaller_entry_uses_package_absolute_import() -> None:
    """PyInstaller executes the entry file without a package parent."""
    entry = (ROOT / "src/edge_tts_server/__main__.py").read_text(encoding="utf-8")

    assert "from edge_tts_server.cli import main" in entry


def test_pyinstaller_collects_fastapi_runtime_modules() -> None:
    """The standalone EXE must include framework modules loaded dynamically."""
    spec = (ROOT / "edge-tts-server.spec").read_text(encoding="utf-8")

    for package in ("fastapi", "pydantic", "uvicorn", "imageio_ffmpeg"):
        assert f'collect_submodules("{package}")' in spec
    assert 'collect_data_files("imageio_ffmpeg")' in spec

    for development_only in (
        "IPython",
        "_pytest",
        "astroid",
        "black",
        "mypy",
        "pkg_resources",
        "pylint",
        "pytest",
        "setuptools",
    ):
        assert f'"{development_only}"' in spec


def test_windows_instructions_cover_double_click_and_api_key() -> None:
    """The Windows guide should explain the single-file first-use flow."""
    guide = (ROOT / "docs/windows.md").read_text(encoding="utf-8")

    assert "edge-tts-windows-x64.exe" in guide
    assert "config.yaml" in guide
    assert "无需安装 Python" in guide


def test_release_asset_labels_make_platform_and_start_action_explicit() -> None:
    """GitHub Release labels must prevent users from downloading the wrong asset."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for label in (
        "Windows x64 | ZIP | EXE + config.example.yaml",
        "Linux amd64 | Python 3.14 bundle",
        "Linux amd64 | Docker offline image | 1Panel",
    ):
        assert label in workflow


def test_release_workflow_rejects_unexpected_assets() -> None:
    """The public Release must contain only the documented platform archives."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    expected = (
        "edge-tts-windows-x64.zip",
        "edge-tts-linux-amd64-python314.tar.gz",
        "edge-tts-linux-amd64-docker-offline.tar.gz",
    )
    for filename in expected:
        assert filename in workflow
    assert "Unexpected release files:" in workflow
    assert "find . -maxdepth 1 -type f" in workflow
    assert "edge-tts-windows-x64.exe#" not in workflow
    assert "edge-tts-windows-x64-standalone.zip" not in workflow


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
    path = ROOT / "config.example.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert config == {
        "api_key": "CHANGE_ME_TO_A_LONG_RANDOM_SECRET",
        "host": "0.0.0.0",
        "port": 5050,
        **HARDENED_LIMITS,
    }


def test_repository_has_one_canonical_config_example() -> None:
    """All build and deployment paths should use the root config example."""
    assert (ROOT / "config.example.yaml").is_file()
    assert not (ROOT / "packaging/docker/config.example.yaml").exists()


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
        "edge-tts-linux-amd64-docker-offline.tar.gz",
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
        "edge-tts-linux-amd64-docker-offline.tar.gz",
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

    assert "edge-tts-windows-x64.exe" in readme
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

    assert set(jobs) == {
        "validate",
        "windows",
        "python_bundle",
        "docker",
        "release",
    }
    assert jobs["windows"]["needs"] == "validate"
    assert jobs["python_bundle"]["needs"] == "validate"
    assert jobs["docker"]["needs"] == "validate"
    assert set(jobs["release"]["needs"]) == {
        "validate",
        "windows",
        "python_bundle",
        "docker",
    }
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
    assert "actions/checkout@v7" in quality
    assert "actions/setup-python@v7" in quality
    assert quality.count('python-version: "3.14"') == 1
    assert "python-version: 3.x" not in quality


def test_workflows_use_current_node24_action_majors() -> None:
    """Maintained workflows should avoid deprecated Node.js action runtimes."""
    workflows = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
    )

    for action in (
        "actions/checkout@v7",
        "actions/setup-python@v7",
        "actions/upload-artifact@v7",
        "actions/download-artifact@v8",
        "github/codeql-action/init@v4",
        "github/codeql-action/autobuild@v4",
        "github/codeql-action/analyze@v4",
        "docker/setup-qemu-action@v4",
        "docker/setup-buildx-action@v4",
        "docker/login-action@v4",
        "docker/build-push-action@v7",
    ):
        assert action in workflows

    for deprecated in (
        "actions/checkout@v3",
        "actions/checkout@v4",
        "actions/setup-python@v5",
        "actions/upload-artifact@v4",
        "actions/download-artifact@v4",
        "github/codeql-action/init@v2",
        "github/codeql-action/autobuild@v2",
        "github/codeql-action/analyze@v2",
        "docker/setup-qemu-action@v3",
        "docker/setup-buildx-action@v3",
        "docker/login-action@v3",
        "docker/build-push-action@v6",
    ):
        assert deprecated not in workflows


def test_release_workflow_builds_expected_targets_and_notes() -> None:
    """The workflow should publish the agreed assets and deployment notes."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for required in (
        "v*.*.*",
        "^v[0-9]+\\.[0-9]+\\.[0-9]+$",
        "runpy.run_path",
        "src/edge_tts/version.py",
        "build_windows_release.ps1",
        "edge-tts-windows-x64.zip",
        "Build clean Python 3.14 runtime bundle",
        "Smoke-test clean Python bundle without network",
        "python:3.14-slim",
        "--platform linux/amd64",
        "--network none",
        "python3.14 run.py",
        "edge-tts-linux-amd64-python314.tar.gz",
        "linux/amd64,linux/arm64",
        "ghcr.io/${{ github.repository }}",
        "edge-tts-linux-amd64-docker-offline.tar.gz",
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


def test_release_notes_document_clean_python_bundle() -> None:
    """The generated Release must explain the pip-free Python asset."""
    notes = (ROOT / ".github/release-notes.md").read_text(encoding="utf-8")
    for required in (
        "edge-tts-linux-amd64-python314.tar.gz",
        "Python 3.14",
        "Linux amd64",
        "glibc",
        "不支持 Alpine",
        "config.example.yaml",
        "python3.14 run.py",
    ):
        assert required in notes
    assert "start.sh" not in notes


def test_python_bundle_smoke_removes_staging_before_extracting() -> None:
    """The smoke test must not extract over the root-owned build staging tree."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    cleanup = "rm -rf edge-tts-linux-amd64-python314"
    extract = "tar -xzf edge-tts-linux-amd64-python314.tar.gz"

    assert cleanup in workflow
    assert workflow.index(cleanup) < workflow.index(extract)


def test_python_bundle_build_writes_as_runner_user() -> None:
    """The build container must not leave root-owned files in the workspace."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    build_step = workflow.split("- name: Build clean Python 3.14 runtime bundle", 1)[
        1
    ].split("- name: Smoke-test clean Python bundle without network", 1)[0]

    assert '--user "$(id -u):$(id -g)"' in build_step


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


def test_release_version_is_7_5_3() -> None:
    """The package version is the source of truth for the release tag."""
    namespace = runpy.run_path(str(ROOT / "src/edge_tts/version.py"))

    assert namespace["__version__"] == "7.5.3"


def test_root_secret_config_is_ignored_without_hiding_example() -> None:
    """A real API key should not be committed accidentally."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "/config.yaml" in ignore
    assert "/config.example.yaml" not in ignore
    assert "/edge-tts-server-source.zip" in ignore

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
        "ghcr.io/jwwsjlm/edge-tts:7.5.3",
        "edge-tts-linux-amd64-docker-offline.tar.gz",
        "sha256sum -c SHA256SUMS.txt",
        "docker load",
        "EDGE_TTS_IMAGE_TAG=7.5.3",
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
        "GET /v1/voices",
        "GET /v1/models",
        "POST /v1/tts",
        "POST /v1/tts/bundle",
        "X-API-Key",
        "text",
        "voice",
        "rate",
        "volume",
        "pitch",
        "5000",
        "15728640",
        "67108864",
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
        "speech.srt",
        "WordBoundary",
        "SentenceBoundary",
        "voices_cache_ttl_seconds",
        "upstream_connect_timeout_seconds",
        "upstream_receive_timeout_seconds",
        "proxy",
        "原版功能",
        "/docs",
        "/openapi.json",
        "mimo-v2-tts",
        "mimo_api_key",
    ):
        assert required in guide


def test_windows_build_guide_is_complete() -> None:
    """Maintainers should be able to reproduce the standalone x64 bundle."""
    guide = (ROOT / "docs/windows.md").read_text(encoding="utf-8")

    for required in (
        "Python 3.14",
        "python -m venv",
        'pip install -e ".[dev]"',
        "PyInstaller",
        "build_windows_release.ps1",
        "releases/windows/edge-tts-windows-x64.zip",
        "无需安装 Python",
        "/health",
    ):
        assert required in guide


def test_current_release_docs_use_python_3_14_and_version_7_5_3() -> None:
    """Current quick starts should match the runtime and release being published."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    windows = (ROOT / "docs/windows.md").read_text(encoding="utf-8")
    docker = (ROOT / "docs/docker.md").read_text(encoding="utf-8")
    panel = (ROOT / "docs/1panel.md").read_text(encoding="utf-8")
    notes = (ROOT / ".github/release-notes.md").read_text(encoding="utf-8")

    assert "# Edge TTS + Xiaomi MiMo 7.5.3" in readme
    assert "推荐 Python 3.14" in readme
    assert "edge-tts-windows-x64.zip" in readme
    assert "edge-tts-windows-x64.exe" in readme
    assert "EDGE_TTS_IMAGE_TAG=7.5.3" in readme
    assert "Python 3.12" not in readme
    assert "Python 3.14 x64" in windows
    assert "Python 3.12" not in windows
    assert "ghcr.io/jwwsjlm/edge-tts:7.5.3" in docker
    assert "EDGE_TTS_IMAGE_TAG=7.5.3" in docker
    assert "docker pull ghcr.io/jwwsjlm/edge-tts:NEW_VERSION" in docker
    assert "ghcr.io/jwwsjlm/edge-tts:7.5.3" in panel
    assert "EDGE_TTS_IMAGE_TAG=7.5.3" in panel
    assert "Python 3.14" in notes


def test_deployment_docs_copy_the_root_config_example() -> None:
    """Deployment instructions should use the discoverable root example."""
    docker_guide = (ROOT / "docs/docker.md").read_text(encoding="utf-8")
    release_notes = (ROOT / ".github/release-notes.md").read_text(encoding="utf-8")

    assert "cp config.example.yaml config.yaml" in docker_guide
    assert "Copy-Item .\\config.example.yaml .\\config.yaml" in docker_guide
    assert "cp config.example.yaml config.yaml" in release_notes
    for text in (docker_guide, release_notes):
        assert "packaging/docker/config.example.yaml" not in text


def test_distribution_docs_cover_7_4_original_capabilities() -> None:
    """Every shipped guide should expose voices, subtitles and network config."""
    paths = (
        ROOT / "README.md",
        ROOT / "docs/docker.md",
        ROOT / "docs/1panel.md",
        ROOT / "docs/windows.md",
        ROOT / ".github/release-notes.md",
    )
    for path in paths:
        content = path.read_text(encoding="utf-8")
        for required in (
            "/v1/voices",
            "/v1/tts/bundle",
            "speech.srt",
            "proxy",
        ):
            assert required in content, f"{path} is missing {required}"


def test_distribution_docs_cover_multi_model_mimo() -> None:
    """Shipped documentation should explain model selection and MiMo setup."""
    paths = (
        ROOT / "README.md",
        ROOT / "docs/api.md",
        ROOT / "docs/docker.md",
        ROOT / "docs/1panel.md",
        ROOT / "docs/windows.md",
        ROOT / ".github/release-notes.md",
    )
    for path in paths:
        content = path.read_text(encoding="utf-8")
        for required in ("mimo-v2-tts", "mimo_api_key"):
            assert required in content, f"{path} is missing {required}"
    assert "/v1/models" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "/v1/models" in (ROOT / "docs/api.md").read_text(encoding="utf-8")


def test_multi_model_example_is_shipped() -> None:
    """Users should have a Python client for all model-specific fields."""
    example = (ROOT / "examples/multi_model_tts.py").read_text(encoding="utf-8")
    for required in (
        "--model",
        "--mimo-mode",
        "--voice-description",
        "--reference-audio",
        "--response-format",
        "X-API-Key",
    ):
        assert required in example
