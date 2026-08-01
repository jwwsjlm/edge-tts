# Docker Compose Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add standalone production and local-build Docker Compose files with fixed custom DNS resolvers and fully documented deployment commands.

**Architecture:** `compose.yaml` pulls the published GHCR image with an optional tag override, while `compose.dev.yaml` builds the current Dockerfile. Both repeat a small shared service definition so either file can run independently and both consume the existing read-only `config.yaml` contract.

**Tech Stack:** Docker Compose Specification, Docker Engine, PyYAML, pytest, Markdown

---

### Task 1: Standalone production and development Compose files

**Files:**
- Create: `compose.yaml`
- Create: `compose.dev.yaml`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Write failing Compose contract tests**

Add a helper that parses YAML and parameterized tests proving both files define exactly one `edge-tts` service with the required shared settings:

```python
@pytest.mark.parametrize("filename", ["compose.yaml", "compose.dev.yaml"])
def test_compose_service_has_shared_runtime_contract(filename: str) -> None:
    compose = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"edge-tts"}
    service = compose["services"]["edge-tts"]
    assert service["container_name"] == "edge-tts"
    assert service["restart"] == "unless-stopped"
    assert service["ports"] == ["5050:5050"]
    assert service["volumes"] == ["./config.yaml:/config/config.yaml:ro"]
    assert service["dns"] == ["223.5.5.5", "119.29.29.29"]
```

Add focused assertions that production uses `ghcr.io/jwwsjlm/edge-tts:${EDGE_TTS_IMAGE_TAG:-latest}` without `build`, and development uses `build.context: .`, `build.dockerfile: Dockerfile`, and `edge-tts-http:local`.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: new tests fail because both Compose files are absent.

- [ ] **Step 3: Implement the production Compose file**

Create `compose.yaml`:

```yaml
services:
  edge-tts:
    image: ghcr.io/jwwsjlm/edge-tts:${EDGE_TTS_IMAGE_TAG:-latest}
    container_name: edge-tts
    restart: unless-stopped
    ports:
      - "5050:5050"
    volumes:
      - ./config.yaml:/config/config.yaml:ro
    dns:
      - 223.5.5.5
      - 119.29.29.29
```

- [ ] **Step 4: Implement the development Compose file**

Create `compose.dev.yaml` with the same runtime fields and:

```yaml
    build:
      context: .
      dockerfile: Dockerfile
    image: edge-tts-http:local
```

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: every release-asset and Compose contract test passes.

- [ ] **Step 6: Commit**

```bash
git add compose.yaml compose.dev.yaml tests/test_release_assets.py
git commit -m "feat: add production and development Compose files"
```

### Task 2: Compose deployment documentation

**Files:**
- Modify: `docs/docker.md`
- Modify: `.github/release-notes.md`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Write failing documentation tests**

Require the maintained guide to include both standalone commands, DNS values, `.env` tag pinning, logs, status, and shutdown. Require the Release template to include the production Compose command, configuration copy step, and both DNS addresses.

```python
def test_docker_guide_documents_both_compose_workflows() -> None:
    guide = (ROOT / "docs/docker.md").read_text(encoding="utf-8")
    for required in (
        "docker compose -f compose.yaml up -d",
        "docker compose -f compose.dev.yaml up -d --build",
        "EDGE_TTS_IMAGE_TAG",
        "docker compose -f compose.yaml logs -f",
        "docker compose -f compose.yaml down",
        "223.5.5.5",
        "119.29.29.29",
    ):
        assert required in guide
```

- [ ] **Step 2: Run documentation tests and verify RED**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: tests fail because the existing documentation has no Compose commands.

- [ ] **Step 3: Document production and local workflows**

Add to `docs/docker.md`:

- copy `packaging/docker/config.example.yaml` to `config.yaml` and replace the key;
- run production with `docker compose -f compose.yaml up -d`;
- pin a tag using `.env` with `EDGE_TTS_IMAGE_TAG=...`;
- view `ps`, `logs -f`, and stop with `down`;
- stop production before starting `docker compose -f compose.dev.yaml up -d --build` because both use the same container name and port;
- note both configured DNS resolvers.

Add a concise self-contained production Compose section to `.github/release-notes.md`, retaining the existing direct `docker run` alternative.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: all documentation assertions pass.

- [ ] **Step 5: Commit**

```bash
git add docs/docker.md .github/release-notes.md tests/test_release_assets.py
git commit -m "docs: add Docker Compose deployment"
```

### Task 3: Compose and repository verification

**Files:**
- Modify only files required by verification findings.

- [ ] **Step 1: Render both Compose models**

Run:

```powershell
docker compose -f compose.yaml config
docker compose -f compose.dev.yaml config
```

Expected: both commands exit zero, production resolves the default `latest` image, and development contains the local build definition and both DNS addresses. This validation does not require a running Docker daemon.

- [ ] **Step 2: Run the complete tests**

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Run quality checks**

Run:

```powershell
python -m black --check --diff .
python -m isort --check-only --diff .
python -m mypy --pretty src/edge_tts_server tests
python -m pylint src examples tests
```

Expected: every command exits zero and Pylint reports 10/10.

- [ ] **Step 4: Inspect final state**

Run: `git diff --check`, `git status --short --branch`, and `git log -5 --oneline`.

Expected: the feature branch is clean and contains only scoped Compose, test, and documentation commits.
