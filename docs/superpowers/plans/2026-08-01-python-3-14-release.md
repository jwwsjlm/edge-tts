# Python 3.14 and 7.3.1 Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin maintained build and release environments to Python 3.14, bump the package and deployment examples to 7.3.1, and publish a verified `v7.3.1` release without changing the supported Python floor or HTTP API.

**Architecture:** Treat the version module as the release source of truth and add static release-contract tests before changing workflows, images, or documentation. Keep application behavior and dependency ranges unchanged; validate the same Windows bundle, Docker images, offline image, and release gates under Python 3.14.

**Tech Stack:** Python 3.14, pytest, FastAPI/OpenAPI, PyInstaller, GitHub Actions, Docker Buildx, Docker Compose, GitHub CLI

---

## File map

- `tests/test_release_assets.py`: release version, Python runtime, Docker base image, workflow, and maintained-document contracts.
- `tests/test_server_app.py`: OpenAPI version contract derived from the package version.
- `src/edge_tts/version.py`: single package and release version source of truth.
- `.github/workflows/release.yml`: Python 3.14 validation and Windows x64 release jobs.
- `.github/workflows/code-quality.yml`: reproducible Python 3.14 quality job.
- `Dockerfile`: Python 3.14 builder and non-root runtime image.
- `README.md`: current release number, recommended source runtime, Windows build link, and Docker quick start.
- `docs/windows.md`: Python 3.14 x64 local and Action build instructions.
- `docs/docker.md`: current online and offline image examples, retaining 7.3.0 only where it is intentionally the rollback target.
- `docs/1panel.md`: current 7.3.1 online and offline Docker deployment examples.
- `.github/release-notes.md`: generated release instructions stating that the Windows executable embeds Python 3.14.
- `setup.cfg`: deliberately unchanged; `python_requires = >=3.10` remains the public compatibility floor.
- `compose.yaml` and `compose.dev.yaml`: deliberately unchanged; ports, DNS, security restrictions, and service contract remain stable.

### Task 1: Lock the Python 3.14 and 7.3.1 release contract

**Files:**
- Modify: `tests/test_release_assets.py`
- Modify: `tests/test_server_app.py`
- Modify: `src/edge_tts/version.py`
- Modify: `.github/workflows/release.yml`
- Modify: `.github/workflows/code-quality.yml`
- Modify: `Dockerfile`

- [ ] **Step 1: Update the release tests before production files**

In `tests/test_release_assets.py`, change the Docker base-image requirement and version test to:

```python
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


def test_release_version_is_7_3_1() -> None:
    """The package version is the source of truth for the release tag."""
    namespace = runpy.run_path(str(ROOT / "src/edge_tts/version.py"))

    assert namespace["__version__"] == "7.3.1"
```

Add this workflow contract next to the existing release workflow tests:

```python
def test_automation_pins_python_3_14() -> None:
    """Maintained CI and release jobs should use one reproducible runtime."""
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    quality = (ROOT / ".github/workflows/code-quality.yml").read_text(
        encoding="utf-8"
    )

    assert release.count('python-version: "3.14"') == 2
    assert "Set up Python 3.14" in release
    assert "Set up Python 3.14 x64" in release
    assert "uses: actions/checkout@v4" in quality
    assert "uses: actions/setup-python@v5" in quality
    assert quality.count('python-version: "3.14"') == 1
    assert "python-version: 3.x" not in quality
    assert "3.12" not in release
```

In `tests/test_server_app.py`, update the existing Swagger assertion:

```python
assert schema["info"]["version"] == "7.3.1"
```

- [ ] **Step 2: Run the focused tests and verify the new contract fails**

Run:

```powershell
py -3.14 -m pytest tests/test_release_assets.py::test_docker_assets_define_non_root_healthy_service tests/test_release_assets.py::test_automation_pins_python_3_14 tests/test_release_assets.py::test_release_version_is_7_3_1 tests/test_server_app.py::test_swagger_schema_documents_api_key_when_enabled -q
```

Expected: FAIL because the Dockerfile and workflows still contain Python 3.12 or `3.x`, and the package/OpenAPI version is still 7.3.0.

- [ ] **Step 3: Apply the minimal runtime, workflow, and version changes**

Set both Docker stages in `Dockerfile` exactly as follows, leaving all subsequent build and security instructions unchanged:

```dockerfile
FROM python:3.14-slim AS builder
```

```dockerfile
FROM python:3.14-slim AS runtime
```

In `.github/workflows/release.yml`, replace the two setup blocks with:

```yaml
      - name: Set up Python 3.14
        uses: actions/setup-python@v5
        with:
          python-version: "3.14"
```

```yaml
      - name: Set up Python 3.14 x64
        uses: actions/setup-python@v5
        with:
          python-version: "3.14"
          architecture: x64
```

In `.github/workflows/code-quality.yml`, use supported checkout/setup actions and pin the setup input:

```yaml
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.14"
```

In `src/edge_tts/version.py`, use:

```python
"""Version information for the edge_tts package."""

__version__ = "7.3.1"
__version_info__ = tuple(int(num) for num in __version__.split("."))
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run the same command from Step 2.

Expected: `4 passed`.

- [ ] **Step 5: Confirm the public Python compatibility floor did not change**

Run:

```powershell
Select-String -LiteralPath setup.cfg -Pattern '^python_requires = >=3\.10$'
```

Expected: one match on `python_requires = >=3.10`.

- [ ] **Step 6: Commit the release-runtime change**

```powershell
git add -- tests/test_release_assets.py tests/test_server_app.py src/edge_tts/version.py .github/workflows/release.yml .github/workflows/code-quality.yml Dockerfile
git diff --cached --check
git commit -m "ci: build 7.3.1 releases with Python 3.14"
```

Expected: one commit containing only the listed tests, version, workflow, and Docker files.

### Task 2: Update maintained deployment and build documentation

**Files:**
- Modify: `tests/test_release_assets.py`
- Modify: `README.md`
- Modify: `docs/windows.md`
- Modify: `docs/docker.md`
- Modify: `docs/1panel.md`
- Modify: `.github/release-notes.md`

- [ ] **Step 1: Update and add documentation-contract tests first**

In `tests/test_release_assets.py`, update the existing 1Panel expectations to:

```python
        "ghcr.io/jwwsjlm/edge-tts:7.3.1",
```

```python
        "EDGE_TTS_IMAGE_TAG=7.3.1",
```

Update the existing Windows guide requirement from `"Python 3.12"` to:

```python
        "Python 3.14",
```

Add the following contract after `test_windows_build_guide_is_complete`:

```python
def test_current_release_docs_use_python_3_14_and_version_7_3_1() -> None:
    """Current quick starts should match the runtime and release being published."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    windows = (ROOT / "docs/windows.md").read_text(encoding="utf-8")
    docker = (ROOT / "docs/docker.md").read_text(encoding="utf-8")
    panel = (ROOT / "docs/1panel.md").read_text(encoding="utf-8")
    notes = (ROOT / ".github/release-notes.md").read_text(encoding="utf-8")

    assert "# Edge TTS 7.3.1" in readme
    assert "推荐 Python 3.14" in readme
    assert "EDGE_TTS_IMAGE_TAG=7.3.1" in readme
    assert "Python 3.12" not in readme
    assert "Python 3.14 x64" in windows
    assert "Python 3.12" not in windows
    assert "ghcr.io/jwwsjlm/edge-tts:7.3.1" in docker
    assert "EDGE_TTS_IMAGE_TAG=7.3.1" in docker
    assert "ghcr.io/jwwsjlm/edge-tts:7.3.1" in panel
    assert "EDGE_TTS_IMAGE_TAG=7.3.1" in panel
    assert "Python 3.14" in notes
```

- [ ] **Step 2: Run the documentation tests and verify they fail**

Run:

```powershell
py -3.14 -m pytest tests/test_release_assets.py::test_1panel_guide_documents_docker_only_deployment tests/test_release_assets.py::test_windows_build_guide_is_complete tests/test_release_assets.py::test_current_release_docs_use_python_3_14_and_version_7_3_1 -q
```

Expected: FAIL because current documentation still points to Python 3.12 and release 7.3.0.

- [ ] **Step 3: Update README and Windows build instructions**

Make these exact substitutions in `README.md`:

```text
# Edge TTS 7.3.1
[Windows 本地构建](docs/windows.md)：Python 3.14、PyInstaller 和产物验证
需要 Python 3.10 或更高版本，推荐 Python 3.14：
echo "EDGE_TTS_IMAGE_TAG=7.3.1" > .env
```

Make these exact substitutions in `docs/windows.md`:

```text
正式发布固定使用 Python 3.14 x64
Python 3.14 x64
py -3.14 -m venv .venv
# 已确认当前 python 是 3.14 时也可执行：python -m venv .venv
在 `windows-2022` 上使用 Python 3.14 x64 重建并冒烟测试
```

- [ ] **Step 4: Update Docker and 1Panel current-version examples**

In `docs/docker.md`, change the release image at the top, online Compose example, `docker pull`, `docker run`, and offline `docker image inspect` examples from `7.3.0` to `7.3.1`. Keep the final rollback command on `7.3.0`, because it demonstrates reverting from 7.3.1 to the previous successful release.

In `docs/1panel.md`, change every current image and `EDGE_TTS_IMAGE_TAG` example from `7.3.0` to `7.3.1`.

- [ ] **Step 5: Document the embedded Python runtime in generated release notes**

In `.github/release-notes.md`, change the Windows asset bullet to:

```markdown
- `edge-tts-server-windows-x64.zip`：使用 Python 3.14 x64 构建，可双击运行，目标电脑无需安装 Python 或联网安装依赖。
```

Do not replace `__IMAGE__` or `__VERSION__`; the release workflow renders those placeholders from the validated Tag.

- [ ] **Step 6: Run the focused documentation tests and verify they pass**

Run the same command from Step 2.

Expected: `3 passed`.

- [ ] **Step 7: Scan maintained files for stale Python 3.12 references**

Run:

```powershell
rg -n "Python 3\.12|python:3\.12|python-version: 3\.x|python-version: \"3\.12\"" README.md docs/windows.md docs/docker.md docs/1panel.md .github/release-notes.md .github/workflows Dockerfile tests/test_release_assets.py
```

Expected: no output. Historical files under `docs/superpowers/plans` and `docs/superpowers/specs` are intentionally excluded.

- [ ] **Step 8: Commit the documentation change**

```powershell
git add -- tests/test_release_assets.py README.md docs/windows.md docs/docker.md docs/1panel.md .github/release-notes.md
git diff --cached --check
git commit -m "docs: prepare Python 3.14 deployment guides"
```

Expected: one commit containing only the listed test and maintained-document files.

### Task 3: Run Python 3.14, Windows bundle, and Docker verification

**Files:**
- Verify only; no source changes expected.

- [ ] **Step 1: Create a clean Python 3.14 validation environment**

Run:

```powershell
py -3.14 -m venv .venv-py314
& .\.venv-py314\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-py314\Scripts\python.exe -m pip install -e ".[dev]" pyinstaller
& .\.venv-py314\Scripts\python.exe --version
```

Expected: the final line begins with `Python 3.14.` and dependency installation succeeds.

- [ ] **Step 2: Run the full automated test suite**

```powershell
& .\.venv-py314\Scripts\python.exe -m pytest -q
```

Expected: all tests pass with no failures or errors.

- [ ] **Step 3: Run formatting and import-order checks**

```powershell
& .\.venv-py314\Scripts\python.exe -m black --check --diff .
& .\.venv-py314\Scripts\python.exe -m isort --check-only --diff .
```

Expected: Black reports all files unchanged and isort reports no incorrectly sorted files.

- [ ] **Step 4: Run Linux and Windows type checks**

```powershell
& .\.venv-py314\Scripts\python.exe -m mypy --pretty --platform linux src examples tests
& .\.venv-py314\Scripts\python.exe -m mypy --pretty --platform win32 src examples tests
```

Expected: both commands report `Success: no issues found`.

- [ ] **Step 5: Run lint checks**

```powershell
& .\.venv-py314\Scripts\python.exe -m pylint src examples tests
```

Expected: exit code 0 with no blocking Pylint findings.

- [ ] **Step 6: Validate both Compose files without starting services**

```powershell
docker compose -f compose.yaml config --quiet
docker compose -f compose.dev.yaml config --quiet
```

Expected: both commands exit 0 with no output; existing DNS entries, read-only filesystem, tmpfs, dropped capabilities, `no-new-privileges`, and init settings remain accepted.

- [ ] **Step 7: Build and smoke-test the standalone Windows x64 package**

```powershell
.\build_windows_release.ps1 -Python .\.venv-py314\Scripts\python.exe
Get-Item -LiteralPath releases/windows/edge-tts-server-windows-x64.zip
```

Expected: the script reports `Windows release created`, its temporary server answers `/health`, and the ZIP exists. Do not stage generated `build/`, `dist/`, or `releases/` files.

- [ ] **Step 8: Build the Docker image when the local daemon is available**

Run:

```powershell
if ((Get-Command docker -ErrorAction SilentlyContinue) -and (docker info 2>$null)) {
    docker build --tag edge-tts-http:7.3.1 .
    if ($LASTEXITCODE -ne 0) { throw "Docker build failed" }
} else {
    Write-Host "Docker daemon unavailable; GitHub Release native amd64 and multi-arch jobs remain the required image gate."
}
```

Expected: a local `edge-tts-http:7.3.1` image is built, or the explicit daemon-unavailable message is printed. The release workflow still performs the mandatory native amd64 health test, offline reload test, and multi-architecture build.

- [ ] **Step 9: Confirm the worktree contains no accidental release assets**

```powershell
git status --short
git diff --check HEAD
```

Expected: `edge-tts-server-source.zip` remains untracked and no generated Windows release files are staged. Any `.venv-py314`, `build`, `dist`, and `releases` paths are ignored or left unstaged.

### Task 4: Push master and publish v7.3.1 through the gated Action

**Files:**
- External Git refs and GitHub release state only.

- [ ] **Step 1: Verify the final commit and Tag preconditions**

```powershell
git status --short
git log -3 --oneline
& .\.venv-py314\Scripts\python.exe -c "import runpy; print(runpy.run_path('src/edge_tts/version.py')['__version__'])"
git tag --list v7.3.1
```

Expected: only the preserved untracked `edge-tts-server-source.zip` is present, the version prints `7.3.1`, and the Tag command prints nothing.

- [ ] **Step 2: Push the implementation commits to master**

```powershell
git push origin master
```

Expected: GitHub accepts the new commits on `master`.

- [ ] **Step 3: Wait for the master code-quality workflow**

```powershell
gh run list --workflow code-quality.yml --branch master --limit 1
gh run watch (gh run list --workflow code-quality.yml --branch master --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
```

Expected: the Python 3.14 code-quality run completes successfully before a Tag is created.

- [ ] **Step 4: Create and push the stable release Tag**

```powershell
git tag v7.3.1
git push origin v7.3.1
```

Expected: the new Tag points to the tested master commit. Do not alter or delete `v7.3.0`.

- [ ] **Step 5: Monitor the gated release workflow to completion**

```powershell
gh run list --workflow release.yml --limit 1
gh run watch (gh run list --workflow release.yml --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
```

Expected: validate, Windows x64, native amd64 smoke test, offline image reload smoke test, multi-architecture push, checksum generation, and GitHub Release creation all succeed.

- [ ] **Step 6: Verify published assets and image instructions**

```powershell
gh release view v7.3.1 --json url,name,assets --jq '{url: .url, name: .name, assets: [.assets[].name]}'
```

Expected assets:

```text
edge-tts-server-windows-x64.zip
edge-tts-server-linux-amd64.tar.gz
SHA256SUMS.txt
```

Open the release notes and confirm they contain the rendered `7.3.1` GHCR image, Windows instructions, online Docker instructions, offline 1Panel instructions, `config.yaml`, Swagger, and the API quick call.
