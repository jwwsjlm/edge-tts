# Authenticated HTTP Server and Releases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an API-key-protected Edge TTS HTTP service, package it as a double-clickable Windows executable, publish a multi-platform GHCR image, and create documented GitHub Releases automatically.

**Architecture:** A new `edge_tts_server` package separates YAML configuration, HTTP routing, and process startup while delegating synthesis to the existing `edge_tts.Communicate`. Tests inject a fake communicator into a real `aiohttp` test server. PyInstaller and Docker use the same console entry point, and a tag-triggered GitHub Actions workflow gates publishing on tests.

**Tech Stack:** Python 3.11+, aiohttp, PyYAML, pytest, pytest-asyncio, PyInstaller, Docker Buildx, GitHub Actions, GHCR

---

## File Map

- `src/edge_tts_server/config.py`: typed YAML configuration, validation, secure first-run creation, runtime path resolution.
- `src/edge_tts_server/app.py`: authenticated HTTP routes, request validation, MP3 synthesis, stable error mapping.
- `src/edge_tts_server/cli.py`: command-line parsing, startup logging, and `aiohttp.web.run_app` integration.
- `src/edge_tts_server/__init__.py`, `__main__.py`: package surface and module entry point.
- `tests/test_server_config.py`: configuration behavior.
- `tests/test_server_app.py`: HTTP contract using a real aiohttp test server and fake communicator.
- `tests/test_server_cli.py`: config-path and CLI startup behavior.
- `setup.py`, `setup.cfg`: runtime/dev dependencies and `edge-tts-server` console entry point.
- `packaging/windows/*`, `build_windows_release.ps1`: Windows release assets and deterministic ZIP assembly.
- `Dockerfile`, `.dockerignore`: non-root production image and health check.
- `docs/docker.md`, `README.md`: maintained local and Docker usage instructions.
- `.github/release-notes.md`: deployment text embedded in each GitHub Release.
- `.github/workflows/release.yml`: test, Windows build, GHCR publish, and GitHub Release jobs.

### Task 1: YAML configuration

**Files:**
- Create: `src/edge_tts_server/__init__.py`
- Create: `src/edge_tts_server/config.py`
- Test: `tests/test_server_config.py`
- Modify: `setup.py`
- Modify: `setup.cfg`

- [ ] **Step 1: Add failing configuration tests**

Write tests that assert a valid YAML file becomes `ServerConfig`, a missing file is created with a 40+ character random key, two generated keys differ, and blank keys, whitespace hosts, non-mapping YAML, and ports outside `1..65535` raise `ConfigError`.

```python
def test_missing_config_is_created_with_random_key(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config = load_or_create_config(path)
    assert path.exists()
    assert config.host == "127.0.0.1"
    assert config.port == 5050
    assert len(config.api_key) >= 40


@pytest.mark.parametrize("port", [0, 65536, "5050"])
def test_invalid_port_is_rejected(tmp_path: Path, port: object) -> None:
    path = write_config(tmp_path, {"api_key": "secret", "host": "127.0.0.1", "port": port})
    with pytest.raises(ConfigError):
        load_or_create_config(path)
```

- [ ] **Step 2: Run the configuration tests and verify RED**

Run: `python -m pytest tests/test_server_config.py -q`

Expected: collection fails because `edge_tts_server.config` does not exist.

- [ ] **Step 3: Implement minimal configuration support**

Add `PyYAML>=6.0,<7.0` to `install_requires`, `pytest` and `pytest-asyncio` to the dev extra, then implement:

```python
@dataclass(frozen=True)
class ServerConfig:
    api_key: str
    host: str = "127.0.0.1"
    port: int = 5050


class ConfigError(ValueError):
    """Raised when server configuration is unusable."""


def load_or_create_config(path: Path) -> ServerConfig:
    if not path.exists():
        generated = ServerConfig(api_key=secrets.token_urlsafe(32))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(asdict(generated), sort_keys=False), encoding="utf-8")
        return generated
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read configuration: {exc}") from exc
    return validate_config(raw)
```

Validation accepts only the three documented keys and rejects incorrect types, blank values, hosts containing whitespace, and invalid ports.

- [ ] **Step 4: Run configuration tests and verify GREEN**

Run: `python -m pytest tests/test_server_config.py -q`

Expected: all configuration tests pass.

- [ ] **Step 5: Commit**

```bash
git add setup.py setup.cfg src/edge_tts_server tests/test_server_config.py
git commit -m "feat: add server configuration"
```

### Task 2: Authenticated HTTP application

**Files:**
- Create: `src/edge_tts_server/app.py`
- Test: `tests/test_server_app.py`

- [ ] **Step 1: Add failing health and authentication tests**

Use `aiohttp.test_utils.TestClient` with a fake communicator factory. Assert `/health` returns `{"status": "ok"}`, missing/wrong keys return the documented `401`, and authentication occurs before malformed JSON is parsed.

```python
async def test_wrong_key_is_rejected(aiohttp_client: AiohttpClient) -> None:
    client = await aiohttp_client(create_app(CONFIG, FakeCommunicator))
    response = await client.post("/v1/tts", json={"text": "hello"}, headers={"X-API-Key": "wrong"})
    assert response.status == 401
    assert await response.json() == {
        "error": "unauthorized",
        "message": "Missing or invalid API key",
    }
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_server_app.py -q`

Expected: collection fails because `edge_tts_server.app` does not exist.

- [ ] **Step 3: Implement the routes and constant-time authentication**

Create `create_app(config, communicator_factory=Communicate)` with routes and a JSON helper:

```python
def authorized(request: web.Request, api_key: str) -> bool:
    supplied = request.headers.get("X-API-Key", "")
    return hmac.compare_digest(supplied.encode(), api_key.encode())


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})
```

Store the immutable config and communicator factory on typed `web.AppKey` keys.

- [ ] **Step 4: Verify health/auth GREEN**

Run: `python -m pytest tests/test_server_app.py -q`

Expected: current health and authentication tests pass.

- [ ] **Step 5: Add failing request-validation tests**

Cover non-JSON content, invalid JSON, non-object JSON, missing/blank `text`, unknown keys, non-string options, and invalid voice/rate/volume/pitch syntax. Require stable `400` JSON with `error: invalid_request`.

- [ ] **Step 6: Run validation tests and verify RED**

Run: `python -m pytest tests/test_server_app.py -q`

Expected: new validation assertions fail because synthesis validation is absent.

- [ ] **Step 7: Implement request parsing and MP3 output**

Validate the allowed keys and types, construct the communicator inside a `try` block so existing `TTSConfig` validation is authoritative, collect only `audio` chunks, and return:

```python
return web.Response(
    body=b"".join(audio_chunks),
    content_type="audio/mpeg",
    headers={"Content-Disposition": 'inline; filename="speech.mp3"'},
)
```

- [ ] **Step 8: Add and satisfy error-mapping tests**

Fake communicators raise `EdgeTTSException` and `aiohttp.ClientError` to require `502 upstream_error`; another raises `RuntimeError` to require `500 internal_error`. Ensure response bodies contain neither traceback text nor the API key.

Run: `python -m pytest tests/test_server_app.py -q`

Expected: all HTTP tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/edge_tts_server/app.py tests/test_server_app.py
git commit -m "feat: add authenticated TTS HTTP API"
```

### Task 3: Double-clickable server entry point

**Files:**
- Create: `src/edge_tts_server/cli.py`
- Create: `src/edge_tts_server/__main__.py`
- Test: `tests/test_server_cli.py`
- Modify: `setup.cfg`

- [ ] **Step 1: Write failing path-resolution and CLI tests**

Assert `--config` wins, source mode defaults to `cwd/config.yaml`, frozen mode defaults beside `sys.executable`, and `main()` loads configuration then calls the injected runner with the configured host and port.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_server_cli.py -q`

Expected: collection fails because `edge_tts_server.cli` does not exist.

- [ ] **Step 3: Implement CLI and entry points**

```python
def resolve_config_path(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    return base / "config.yaml"


def main() -> None:
    args = parse_args()
    path = resolve_config_path(args.config)
    config = load_or_create_config(path)
    print(f"Config: {path}")
    print(f"Listening on http://{config.host}:{config.port}")
    web.run_app(create_app(config), host=config.host, port=config.port, print=None)
```

Register `edge-tts-server = edge_tts_server.cli:main` and make `python -m edge_tts_server` call it.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_server_cli.py -q`

Expected: all CLI tests pass.

- [ ] **Step 5: Commit**

```bash
git add setup.cfg src/edge_tts_server/cli.py src/edge_tts_server/__main__.py tests/test_server_cli.py
git commit -m "feat: add HTTP server launcher"
```

### Task 4: Windows release builder

**Files:**
- Create: `edge-tts-server.spec`
- Create: `build_windows_release.ps1`
- Create: `packaging/windows/config.example.yaml`
- Create: `packaging/windows/README.txt`
- Create: `packaging/windows/call-example.ps1`
- Modify: `.gitignore`

- [ ] **Step 1: Add release layout assertions to the build script**

The PowerShell script must fail on missing PyInstaller output or assets, recreate only `releases/windows`, copy the four files into `edge-tts-server-windows-x64`, and create `edge-tts-server-windows-x64.zip`.

- [ ] **Step 2: Add PyInstaller and release assets**

The spec points at `src/edge_tts_server/__main__.py`, produces a console executable named `edge-tts-server`, and collects PyYAML/aiohttp metadata if required. The example call reads the key from YAML and uses `Invoke-WebRequest` to save `speech.mp3`.

- [ ] **Step 3: Build and smoke-test the executable**

Run:

```powershell
python -m pip install -e ".[dev]" pyinstaller
./build_windows_release.ps1
```

Expected: `releases/windows/edge-tts-server-windows-x64.zip` exists and contains exactly the documented layout. Launch the EXE with a temporary config, request `/health`, and stop the process in a `finally` block.

- [ ] **Step 4: Commit**

```bash
git add .gitignore edge-tts-server.spec build_windows_release.ps1 packaging/windows
git commit -m "build: package Windows HTTP server"
```

### Task 5: Docker runtime

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `packaging/docker/config.example.yaml`

- [ ] **Step 1: Create the production container definition**

Use `python:3.12-slim`, install the package without dev tools, create an unprivileged `edge-tts` user, expose `5050`, run `python -m edge_tts_server --config /config/config.yaml`, and use Python's standard library for the `/health` health check.

- [ ] **Step 2: Build and smoke-test when Docker is available**

Run: `docker build -t edge-tts-http:test .`

Then start with the example config mounted read-only, wait for healthy status, call `/health`, and remove the container. Expected: image build exits zero and health becomes `healthy`. If Docker is unavailable, record that fact and rely on workflow validation.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile .dockerignore packaging/docker/config.example.yaml
git commit -m "build: add Docker runtime"
```

### Task 6: User and release documentation

**Files:**
- Create: `docs/docker.md`
- Create: `.github/release-notes.md`
- Modify: `README.md`

- [ ] **Step 1: Document local API and Docker deployment**

Document first-run config generation, the complete YAML schema, `X-API-Key`, PowerShell/curl MP3 calls, GHCR visibility/login, bind mounts for PowerShell and POSIX, health checks, version pinning, upgrades, HTTPS/reverse proxy guidance, and key rotation by editing the mounted config followed by restart.

- [ ] **Step 2: Add release-note deployment template**

Use `__IMAGE__` and `__VERSION__` tokens. Include runnable Docker commands and the full server config:

```yaml
api_key: "replace-with-a-long-random-secret"
host: "0.0.0.0"
port: 5050
```

- [ ] **Step 3: Verify documentation has required content**

Run `rg -n "config.yaml|0.0.0.0|X-API-Key|ghcr.io|docker run|/health|HTTPS" README.md docs/docker.md .github/release-notes.md`.

Expected: every deployment topic appears in the focused Docker guide and essential commands appear in the release template.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/docker.md .github/release-notes.md
git commit -m "docs: explain HTTP and Docker deployment"
```

### Task 7: Automated GitHub Release and GHCR publishing

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Implement the tag-gated workflow**

Trigger on `v*`, set `contents: write` and `packages: write`, and define:

- `test` on Ubuntu running `python -m pytest -q`;
- `windows` after test, running `build_windows_release.ps1` and uploading the ZIP;
- `docker` after test, logging into GHCR, enabling QEMU/Buildx, and pushing `linux/amd64,linux/arm64` version and latest tags;
- `release` after both builds, downloading the ZIP, substituting release-note tokens, and running `gh release create "$GITHUB_REF_NAME" --verify-tag --notes-file ...` with generated commit notes appended.

Extract the version only when `$GITHUB_REF_NAME` matches `^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$`; otherwise fail before any push.

- [ ] **Step 2: Validate workflow structure locally**

Parse the YAML with `yaml.safe_load`, assert the four jobs and dependencies exist, and inspect the diff for accidental literal secrets.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: publish Windows and Docker releases"
```

### Task 8: Full verification

**Files:**
- Modify only files required by verification findings.

- [ ] **Step 1: Run automated tests**

Run: `python -m pytest -q`

Expected: all tests pass with no warnings.

- [ ] **Step 2: Run existing quality gates**

Run:

```powershell
python -m mypy --pretty src examples tests
python -m pylint src examples tests
python -m isort --check-only --diff .
python -m black --check --diff .
```

Expected: every command exits zero.

- [ ] **Step 3: Rebuild Windows release from a clean release directory**

Run: `./build_windows_release.ps1`

Expected: ZIP creation exits zero, its contents match the release layout, and the executable health smoke test returns `{"status":"ok"}`.

- [ ] **Step 4: Verify Docker if locally available**

Run: `docker version`, followed by the Task 5 build/health smoke test when available.

Expected: container becomes healthy; otherwise report Docker verification as unavailable rather than claiming it passed.

- [ ] **Step 5: Review repository state and requirements**

Run: `git status --short`, `git log --oneline --decorate -10`, and `git diff --check HEAD~7..HEAD`.

Expected: only intentional generated `releases/` artifacts remain ignored, commits are scoped, and every design requirement maps to a verified file or command.
