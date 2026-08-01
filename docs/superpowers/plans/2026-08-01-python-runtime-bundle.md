# Python 3.14 Clean Runtime Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a clean, pip-free Linux amd64 Python 3.14 runtime tarball on every stable release, with all source and third-party dependencies under `libs/` and no Markdown or shell launcher inside the artifact.

**Architecture:** A Python build utility runs inside `python:3.14-slim`, installs the current package into a staging `libs/`, removes non-server and forbidden files, and creates a single-root tarball. A tiny `run.py` prepends the vendored directory to `sys.path` and invokes the existing server CLI with a bundle-relative config; GitHub Actions smoke-tests the result with networking disabled before permitting Release creation.

**Tech Stack:** Python 3.14, pathlib/shutil/tarfile, pytest, Docker, GitHub Actions, GHCR, GitHub CLI

---

## File map

- `packaging/python/run.py`: runtime entry point shipped in the clean bundle.
- `packaging/python/build_bundle.py`: deterministic staging, cleanup, audit, and tar creation.
- `tests/test_release_assets.py`: runner, bundle layout, workflow, release asset, documentation, and version contracts.
- `tests/test_server_app.py`: OpenAPI version contract for 7.3.2.
- `.github/workflows/release.yml`: build, offline smoke-test, upload, checksum, and Release gates.
- `.github/release-notes.md`: clean Python bundle requirements and commands.
- `src/edge_tts/version.py`: package and stable Tag source of truth, updated to 7.3.2.
- `README.md`, `docs/docker.md`, `docs/1panel.md`: current 7.3.2 examples; Docker rollback uses 7.3.1.

### Task 1: Add the path-independent Python bundle runner

**Files:**
- Create: `packaging/python/run.py`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Write failing runner asset and behavior tests**

Add to `tests/test_release_assets.py`:

```python
def test_python_bundle_runner_is_path_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped runner should load vendored libraries and a sibling config."""
    runner_path = ROOT / "packaging/python/run.py"

    assert runner_path.is_file()
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
    namespace = runpy.run_path(str(runner_path))
    captured: list[list[str]] = []
    cli_module = types.ModuleType("edge_tts_server.cli")
    cli_module.main = lambda argv: captured.append(list(argv))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edge_tts_server", types.ModuleType("edge_tts_server"))
    monkeypatch.setitem(sys.modules, "edge_tts_server.cli", cli_module)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(runner_path), "--config", "/srv/edge/config.yaml"],
    )

    namespace["main"]()

    assert captured == [["--config", "/srv/edge/config.yaml"]]
```

Add `import sys` and `import types` beside the existing imports.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
py -3.14 -m pytest tests/test_release_assets.py::test_python_bundle_runner_is_path_independent tests/test_release_assets.py::test_python_bundle_runner_forwards_explicit_arguments -q
```

Expected: both tests FAIL because `packaging/python/run.py` does not exist.

- [ ] **Step 3: Implement the minimal runner**

Create `packaging/python/run.py`:

```python
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
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run the Step 2 command.

Expected: `2 passed`.

- [ ] **Step 5: Commit the runner**

```powershell
git add -- packaging/python/run.py tests/test_release_assets.py
git diff --cached --check
git commit -m "feat: add clean Python bundle runner"
```

### Task 2: Build and audit the clean runtime tarball

**Files:**
- Create: `packaging/python/build_bundle.py`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Write a failing bundle layout test**

Add to `tests/test_release_assets.py`:

```python
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
        (libs / "dependency" / "__pycache__").mkdir(parents=True)
        (libs / "dependency" / "README.md").write_text("remove", encoding="utf-8")
        (libs / "dependency" / "module.pyc").write_bytes(b"remove")
        (libs / "dependency" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    monkeypatch.setitem(
        namespace["build_bundle"].__globals__, "_install_runtime", fake_install
    )
    archive = namespace["build_bundle"](tmp_path)
    bundle = tmp_path / "edge-tts-server-python314-linux-amd64"

    assert archive == tmp_path / "edge-tts-server-python314-linux-amd64.tar.gz"
    assert archive.is_file()
    assert {path.name for path in bundle.iterdir()} == {
        "libs",
        "config.example.yaml",
        "run.py",
        "LICENSE",
    }
    assert not (bundle / "libs/edge_playback").exists()
    assert not list(bundle.rglob("*.md"))
    assert not list(bundle.rglob("*.pyc"))
    assert not list(bundle.rglob("__pycache__"))
    with tarfile.open(archive, "r:gz") as packaged:
        roots = {member.name.split("/", 1)[0] for member in packaged.getmembers()}
    assert roots == {"edge-tts-server-python314-linux-amd64"}
```

Add `import tarfile` to the test module imports.

- [ ] **Step 2: Run the bundle test and verify RED**

Run:

```powershell
py -3.14 -m pytest tests/test_release_assets.py::test_python_bundle_builder_creates_clean_single_root_archive -q
```

Expected: FAIL because `packaging/python/build_bundle.py` does not exist.

- [ ] **Step 3: Implement deterministic bundle construction**

Create `packaging/python/build_bundle.py` with these public constants and functions:

```python
"""Build the clean Linux amd64 Python 3.14 runtime bundle."""

import argparse
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_NAME = "edge-tts-server-python314-linux-amd64"
ALLOWED_TOP_LEVEL = {"libs", "config.example.yaml", "run.py", "LICENSE"}


def _install_runtime(libs: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--target",
            str(libs),
            str(ROOT),
        ],
        check=True,
    )


def _remove_forbidden(root: Path) -> None:
    playback = root / "edge_playback"
    if playback.exists():
        shutil.rmtree(playback)
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.exists():
            continue
        if path.is_dir() and path.name == "__pycache__":
            shutil.rmtree(path)
        elif path.is_file() and (path.suffix.lower() in {".md", ".pyc"}):
            path.unlink()


def _audit(bundle: Path) -> None:
    names = {path.name for path in bundle.iterdir()}
    if names != ALLOWED_TOP_LEVEL:
        raise RuntimeError(f"Unexpected bundle root files: {sorted(names)}")
    forbidden = [
        path
        for path in bundle.rglob("*")
        if path.name == "__pycache__"
        or (path.is_file() and path.suffix.lower() in {".md", ".pyc"})
    ]
    if forbidden:
        raise RuntimeError(f"Forbidden bundle files: {forbidden}")


def build_bundle(output_root: Path) -> Path:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    bundle = output_root / BUNDLE_NAME
    archive = output_root / f"{BUNDLE_NAME}.tar.gz"
    if bundle.exists():
        shutil.rmtree(bundle)
    archive.unlink(missing_ok=True)
    libs = bundle / "libs"
    libs.mkdir(parents=True)
    _install_runtime(libs)
    _remove_forbidden(libs)
    shutil.copy2(ROOT / "packaging/python/run.py", bundle / "run.py")
    shutil.copy2(ROOT / "config.example.yaml", bundle / "config.example.yaml")
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
    _audit(bundle)
    with tarfile.open(archive, "w:gz") as packaged:
        packaged.add(bundle, arcname=BUNDLE_NAME)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(build_bundle(args.output))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the bundle test and verify GREEN**

Run the Step 2 command.

Expected: `1 passed`.

- [ ] **Step 5: Run formatting and commit**

```powershell
py -3.14 -m black packaging/python tests/test_release_assets.py
py -3.14 -m isort packaging/python tests/test_release_assets.py
py -3.14 -m pytest tests/test_release_assets.py::test_python_bundle_builder_creates_clean_single_root_archive -q
git add -- packaging/python/build_bundle.py tests/test_release_assets.py
git diff --cached --check
git commit -m "build: create clean Linux Python runtime bundle"
```

### Task 3: Gate and publish the bundle in GitHub Actions

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `.github/release-notes.md`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Update workflow and release-note tests before the Action**

Update the existing expected job set and dependencies in `tests/test_release_assets.py`:

```python
assert set(jobs) == {"validate", "windows", "python_bundle", "docker", "release"}
assert jobs["python_bundle"]["needs"] == "validate"
assert set(jobs["release"]["needs"]) == {
    "validate",
    "windows",
    "python_bundle",
    "docker",
}
```

Add to the required workflow strings:

```python
        "Build clean Python 3.14 runtime bundle",
        "Smoke-test clean Python bundle without network",
        "python:3.14-slim",
        "--platform linux/amd64",
        "--network none",
        "python3.14 run.py",
        "edge-tts-server-python314-linux-amd64.tar.gz",
```

Add a release-note contract:

```python
def test_release_notes_document_clean_python_bundle() -> None:
    """The generated Release must explain the pip-free Python asset."""
    notes = (ROOT / ".github/release-notes.md").read_text(encoding="utf-8")
    for required in (
        "edge-tts-server-python314-linux-amd64.tar.gz",
        "Python 3.14",
        "Linux amd64",
        "glibc",
        "不支持 Alpine",
        "config.example.yaml",
        "python3.14 run.py",
    ):
        assert required in notes
    assert "start.sh" not in notes
```

- [ ] **Step 2: Run the workflow tests and verify RED**

Run:

```powershell
py -3.14 -m pytest tests/test_release_assets.py::test_release_workflow_gates_all_publish_jobs tests/test_release_assets.py::test_release_workflow_builds_expected_targets_and_notes tests/test_release_assets.py::test_release_notes_document_clean_python_bundle -q
```

Expected: FAIL because the new job, asset, smoke-test, and notes are absent.

- [ ] **Step 3: Add the Python bundle Action job**

Add `python_bundle` after the Windows job in `.github/workflows/release.yml`. It must:

```yaml
  python_bundle:
    name: Build clean Python 3.14 runtime bundle
    needs: validate
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Build clean Python 3.14 runtime bundle
        shell: bash
        run: |
          mkdir -p release-assets
          docker run --rm --platform linux/amd64 \
            --volume "$PWD:/workspace" --workdir /workspace \
            python:3.14-slim \
            python3.14 packaging/python/build_bundle.py release-assets

      - name: Smoke-test clean Python bundle without network
        shell: bash
        run: |
          set -euo pipefail
          cd release-assets
          tar -xzf edge-tts-server-python314-linux-amd64.tar.gz
          cp edge-tts-server-python314-linux-amd64/config.example.yaml \
            edge-tts-server-python314-linux-amd64/config.yaml
          docker run --rm --network none --platform linux/amd64 \
            --volume "$PWD/edge-tts-server-python314-linux-amd64:/bundle" \
            --workdir /bundle python:3.14-slim sh -euc '
              python3.14 run.py >/tmp/server.log 2>&1 &
              server_pid=$!
              trap "kill $server_pid 2>/dev/null || true" EXIT
              for attempt in $(seq 1 40); do
                if python3.14 -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:5050/health\", timeout=1).read()"; then
                  exit 0
                fi
                sleep 0.25
              done
              cat /tmp/server.log
              exit 1
            '

      - name: Upload clean Python bundle
        uses: actions/upload-artifact@v4
        with:
          name: python-runtime-release
          path: release-assets/edge-tts-server-python314-linux-amd64.tar.gz
          if-no-files-found: error
```

- [ ] **Step 4: Gate Release creation and include checksums/assets**

Add `python_bundle` to `release.needs`, download `python-runtime-release`, add the tar name to `sha256sum`, and add this `gh release create` asset:

```bash
"release-assets/edge-tts-server-python314-linux-amd64.tar.gz#Linux amd64 Python 3.14 clean runtime bundle"
```

- [ ] **Step 5: Add Release Notes instructions**

Add an asset bullet and a section that explicitly states glibc Linux amd64, system Python 3.14, no `pip install`, no Alpine, and these commands:

```bash
tar -xzf edge-tts-server-python314-linux-amd64.tar.gz
cd edge-tts-server-python314-linux-amd64
cp config.example.yaml config.yaml
python3.14 run.py
```

- [ ] **Step 6: Run the workflow tests and verify GREEN**

Run the Step 2 command.

Expected: `3 passed`.

- [ ] **Step 7: Commit Action and Release documentation**

```powershell
git add -- .github/workflows/release.yml .github/release-notes.md tests/test_release_assets.py
git diff --cached --check
git commit -m "ci: publish clean Python runtime bundle"
```

### Task 4: Bump 7.3.2 and update current release examples

**Files:**
- Modify: `src/edge_tts/version.py`
- Modify: `tests/test_release_assets.py`
- Modify: `tests/test_server_app.py`
- Modify: `README.md`
- Modify: `docs/docker.md`
- Modify: `docs/1panel.md`

- [ ] **Step 1: Update version and maintained-document tests first**

Change the package version test and Swagger assertion to `7.3.2`. Update the current-document contract to require `# Edge TTS 7.3.2`, `EDGE_TTS_IMAGE_TAG=7.3.2`, and `ghcr.io/jwwsjlm/edge-tts:7.3.2`.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
py -3.14 -m pytest tests/test_release_assets.py::test_release_version_is_7_3_2 tests/test_release_assets.py::test_current_release_docs_use_python_3_14_and_version_7_3_2 tests/test_server_app.py::test_swagger_schema_documents_api_key_when_enabled -q
```

Expected: FAIL because source and documents still identify 7.3.1.

- [ ] **Step 3: Update source and current examples**

Set `src/edge_tts/version.py` to `7.3.2`. Update README, Docker and 1Panel current image examples to 7.3.2. In `docs/docker.md`, change the rollback target from 7.3.0 to 7.3.1.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command.

Expected: `3 passed`.

- [ ] **Step 5: Commit the version bump**

```powershell
git add -- src/edge_tts/version.py tests/test_release_assets.py tests/test_server_app.py README.md docs/docker.md docs/1panel.md
git diff --cached --check
git commit -m "release: prepare version 7.3.2"
```

### Task 5: Verify, push, Tag, and inspect v7.3.2

**Files:**
- Verification and external GitHub state only.

- [ ] **Step 1: Run the complete Python 3.14 quality gate**

```powershell
py -3.14 -m pytest -q
py -3.14 -m black --check --diff --exclude '\.venv-py314' .
py -3.14 -m isort --check-only --diff --skip .venv-py314 .
py -3.14 -m mypy --pretty --platform linux src examples tests
py -3.14 -m mypy --pretty --platform win32 src examples tests
py -3.14 -m pylint src examples tests
docker compose -f compose.yaml config --quiet
docker compose -f compose.dev.yaml config --quiet
```

Expected: all commands exit 0.

- [ ] **Step 2: Build and audit the real bundle locally when Docker is available**

```powershell
if ((Get-Command docker -ErrorAction SilentlyContinue) -and (docker info 2>$null)) {
    New-Item -ItemType Directory -Force release-assets | Out-Null
    docker run --rm --platform linux/amd64 --volume "${PWD}:/workspace" --workdir /workspace python:3.14-slim python3.14 packaging/python/build_bundle.py release-assets
    tar -tzf release-assets/edge-tts-server-python314-linux-amd64.tar.gz
} else {
    Write-Host "Docker daemon unavailable; the Tag Action remains the mandatory bundle smoke-test gate."
}
```

Expected: a single-root clean archive, or the explicit daemon-unavailable message.

- [ ] **Step 3: Push master and wait for Code Quality**

```powershell
git status --short
git push origin master
gh run watch (gh run list --workflow code-quality.yml --branch master --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
```

Expected: only the preserved untracked `edge-tts-server-source.zip` is present and Code Quality succeeds.

- [ ] **Step 4: Create and push the stable Tag**

```powershell
git tag v7.3.2
git push origin v7.3.2
```

Expected: the new Tag points to the tested master commit; v7.3.1 remains unchanged.

- [ ] **Step 5: Monitor the Release Action**

```powershell
gh run watch (gh run list --workflow release.yml --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
```

Expected: validate, Windows, Python bundle, Docker, and Release jobs all succeed.

- [ ] **Step 6: Verify published assets and GHCR tags**

```powershell
gh release view v7.3.2 --json url,name,assets
gh api /users/jwwsjlm/packages/container/edge-tts/versions --paginate --jq '.[] | select(.metadata.container.tags | index("7.3.2")) | .metadata.container.tags'
```

Expected Release assets:

```text
edge-tts-server-windows-x64.zip
edge-tts-server-linux-amd64.tar.gz
edge-tts-server-python314-linux-amd64.tar.gz
SHA256SUMS.txt
```

Expected GHCR tags include `7.3.2` and `latest`.
