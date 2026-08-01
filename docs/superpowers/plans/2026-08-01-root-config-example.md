# Root Configuration Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a discoverable root `config.example.yaml`, protect real root configuration from Git, and document its use on 1Panel.

**Architecture:** Keep the example as a static YAML asset validated by the existing release-asset test suite. Store deployment secrets outside the source tree on 1Panel, while retaining a root ignore rule as defense against accidental local commits.

**Tech Stack:** YAML, pytest, Git ignore rules, Markdown

---

### Task 1: Root example and secret protection

**Files:**
- Create: `config.example.yaml`
- Modify: `.gitignore`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Write failing asset tests**

```python
def test_root_config_example_is_server_ready() -> None:
    path = ROOT / "config.example.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "api_key": "CHANGE_ME_TO_A_LONG_RANDOM_SECRET",
        "host": "0.0.0.0",
        "port": 5050,
    }


def test_root_secret_config_is_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/config.yaml" in ignore
    assert "/config.example.yaml" not in ignore
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: both new assertions fail because the root example and ignore rule are missing.

- [ ] **Step 3: Add the example and ignore rule**

Create:

```yaml
api_key: "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"
host: "0.0.0.0"
port: 5050
```

Add the exact root-only rule `/config.yaml` to `.gitignore`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: all asset tests pass.

- [ ] **Step 5: Commit**

```bash
git add config.example.yaml .gitignore tests/test_release_assets.py
git commit -m "feat: add root server config example"
```

### Task 2: 1Panel and deployment documentation

**Files:**
- Create: `docs/1panel.md`
- Modify: `README.md`
- Modify: `docs/docker.md`
- Modify: `.github/release-notes.md`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Write failing documentation tests**

Require `docs/1panel.md` to contain `config.example.yaml`, `/opt/edge-tts-data/config.yaml`, Python 3.12, `pip install .`, the full server command, port `5050`, `/health`, and HTTPS. Require README to link `docs/1panel.md`. Require Docker and Release docs to copy root `config.example.yaml` rather than the packaging path.

- [ ] **Step 2: Run documentation tests and verify RED**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: tests fail because the 1Panel guide is absent and old copy paths remain.

- [ ] **Step 3: Write the focused 1Panel guide**

Document code ZIP upload, extraction under `/opt/edge-tts`, creation of `/opt/edge-tts-data/config.yaml`, Python 3.12 installation commands, startup command, health check, reverse proxy, HTTPS, and update/restart steps.

- [ ] **Step 4: Update links and copy commands**

Link the guide from README. Replace `packaging/docker/config.example.yaml` copy commands in the Docker guide and Release notes with root `config.example.yaml`.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: all documentation tests pass.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/1panel.md docs/docker.md .github/release-notes.md tests/test_release_assets.py
git commit -m "docs: add 1Panel Python deployment guide"
```

### Task 3: Final verification and publish

**Files:**
- Modify only files required by verification findings.

- [ ] **Step 1: Verify tests and quality**

Run:

```powershell
python -m pytest -q
python -m black --check --diff .
python -m isort --check-only --diff .
python -m mypy --pretty src/edge_tts_server tests
python -m pylint src examples tests
```

Expected: all commands exit zero.

- [ ] **Step 2: Verify Git ignore behavior**

Run:

```powershell
git check-ignore -v config.yaml
git check-ignore config.example.yaml
```

Expected: `config.yaml` matches `/config.yaml`; `config.example.yaml` is not ignored.

- [ ] **Step 3: Merge and verify on master**

Fast-forward the feature branch into `master`, reinstall the editable package, and run `python -m pytest -q` again.

- [ ] **Step 4: Push**

Run: `git push origin master`.

Expected: local and remote `master` resolve to the same commit.
