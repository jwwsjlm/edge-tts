# Edge TTS HTTP Server and Release Design

## Goal

Extend this `edge-tts` fork with an authenticated HTTP service that launches as a self-contained Windows executable, runs unchanged in Docker, and is published automatically through GitHub Actions.

## Scope

The first release provides one synthesis endpoint and one health endpoint. Generated MP3 bytes are returned directly and are not stored. User management, a browser UI, billing, multiple keys, and rate limiting are outside this release.

## Architecture

The server uses `aiohttp.web`, matching the existing asynchronous implementation and dependency set. A focused server package owns configuration loading, authentication, request validation, synthesis orchestration, and HTTP error mapping. The existing `Communicate` class remains responsible for TTS.

The same entry point serves both targets:

- PyInstaller creates a one-file Windows console executable.
- Docker runs the Python module in a lightweight Linux image.

FastAPI was rejected because automatic OpenAPI documentation does not justify its extra dependencies and executable size for two endpoints. Flask was rejected because its synchronous request model adds unnecessary bridging around the existing asynchronous TTS pipeline.

## Configuration

The service reads `config.yaml` beside the executable. From source or Docker, `--config` can specify another path.

```yaml
api_key: "replace-with-a-long-random-secret"
host: "127.0.0.1"
port: 5050
```

When the file is missing, the application creates it with `host: 127.0.0.1`, `port: 5050`, and a cryptographically random API key. It logs the configuration path and continues startup. Invalid YAML, a blank key, an invalid host, or a port outside `1..65535` stops startup with an actionable console error.

For Docker, documentation uses `host: 0.0.0.0` and mounts the configuration read-only. Secrets are never embedded in source, executables, images, or release notes.

## HTTP API

### `POST /v1/tts`

Requests authenticate with `X-API-Key`, compared in constant time. A missing or incorrect value returns HTTP `401`:

```json
{"error":"unauthorized","message":"Missing or invalid API key"}
```

The JSON body is:

```json
{
  "text": "你好，世界",
  "voice": "zh-CN-XiaoxiaoNeural",
  "rate": "+0%",
  "volume": "+0%",
  "pitch": "+0Hz"
}
```

`text` is a required non-empty string. The other fields are optional strings and use current edge-tts defaults when omitted. Unknown fields are rejected.

Success returns MP3 bytes with `Content-Type: audio/mpeg` and `Content-Disposition: inline; filename="speech.mp3"`. Malformed requests return `400`, upstream Edge TTS failures return `502`, and unexpected failures return `500`. Errors use stable JSON objects and never expose keys or tracebacks.

### `GET /health`

This endpoint requires no key and returns HTTP `200` with `{"status":"ok"}`. It verifies only the process and event loop, without calling the upstream service.

## Runtime Flow

1. The launcher resolves, creates if needed, validates, and loads `config.yaml`.
2. The server binds to the configured address and prints its URL and configuration path.
3. Each synthesis request passes authentication before its JSON body is handled.
4. Validated options create an independent `Communicate` instance.
5. Audio chunks are collected in memory and returned without disk writes; timing metadata is ignored.

Requests share no mutable synthesis state and can run concurrently through the event loop.

## Windows Release

PyInstaller produces `edge-tts-server.exe` as a one-file console program. The visible console shows startup status and actionable fatal errors for double-click users.

The downloadable ZIP contains:

```text
edge-tts-server-windows-x64/
  edge-tts-server.exe
  config.example.yaml
  README.txt
  call-example.ps1
```

The example script sends an authenticated request and saves `speech.mp3`. Generated `releases/` content is ignored by Git.

## Docker Image

The repository includes `Dockerfile`, `.dockerignore`, and Docker deployment documentation. The image exposes port `5050`, runs the server as a non-root user, and defines a `/health` health check. A user-supplied `config.yaml` is bind-mounted read-only.

GitHub Actions publishes multi-platform images for `linux/amd64` and `linux/arm64` to:

- `ghcr.io/jwwsjlm/edge-tts:<version>`
- `ghcr.io/jwwsjlm/edge-tts:latest`

The package is built with GitHub Buildx and authenticated using `GITHUB_TOKEN`. Repository documentation notes that the GHCR package must be public for anonymous pulls, or users must authenticate with `docker login ghcr.io`.

## GitHub Actions Release Workflow

`.github/workflows/release.yml` runs when a tag matching `v*` is pushed. It grants only the required `contents: write` and `packages: write` permissions and performs these gated jobs:

1. Run the full automated test suite.
2. On a Windows runner, install PyInstaller, build the executable, assemble the release directory, and upload the ZIP artifact.
3. On Linux, build both target architectures and push the version and `latest` image tags to GHCR.
4. Create a non-draft GitHub Release only after all required jobs succeed, attaching the Windows ZIP.

The release body combines generated commit notes with maintained deployment instructions. It includes:

- image pull command;
- complete `config.yaml` example using `host: 0.0.0.0`;
- read-only configuration mount and port mapping commands for PowerShell and POSIX shells;
- `/health` check;
- authenticated `POST /v1/tts` examples that save MP3 output;
- version pinning and upgrade instructions;
- a warning to use HTTPS/reverse proxy and protect the API key on an Internet-facing server.

The release workflow derives the version from the pushed tag and rejects an empty or malformed tag version rather than publishing ambiguous image tags.

## Repository Documentation

The main README gains a concise HTTP server section linked to a focused Docker deployment guide. The guide is the maintained source for configuration and deployment details; the GitHub Release body carries the same essential commands so users can deploy without browsing the repository.

## Testing and Verification

Tests use `aiohttp` test utilities and inject synthesis so HTTP behavior is covered without spending upstream TTS quota. They cover:

- valid configuration loading and secure first-run generation;
- malformed and invalid configuration rejection;
- public health checks;
- missing and incorrect keys;
- malformed JSON, empty text, unknown fields, and invalid option types;
- MP3 bytes and response headers;
- upstream and unexpected failure mapping.

Verification includes the full tests and existing format, type, and lint checks. The Windows build must complete, and the built executable must start and answer `/health`. The Docker image must build and the container must become healthy when Docker is available locally. The workflow YAML and release asset layout are checked before publishing.

## Security and Operations

The API key only protects the endpoint while it remains secret. An Internet deployment should use HTTPS through a reverse proxy, restrict inbound ports, mount configuration read-only, and use a long random key. The health endpoint exposes no configuration. Rate limiting and online key rotation remain future work.
