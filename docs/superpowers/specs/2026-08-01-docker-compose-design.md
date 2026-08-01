# Docker Compose Design

## Goal

Provide two independently runnable Docker Compose configurations: one for server deployment using the published GHCR image and one for local development using the repository Dockerfile.

## Files and Usage

`compose.yaml` is the production-oriented file. It pulls `ghcr.io/jwwsjlm/edge-tts:${EDGE_TTS_IMAGE_TAG:-latest}` so deployments can use `latest` by default or pin a release through `.env`.

`compose.dev.yaml` is the local-development file. It builds the current repository with `build: .` and tags the result as `edge-tts-http:local`.

Each file is standalone:

```console
docker compose -f compose.yaml up -d
docker compose -f compose.dev.yaml up -d --build
```

They are deliberately not an override pair, avoiding commands whose behavior depends on remembering multiple files.

## Shared Service Configuration

Both files define one service named `edge-tts` with:

- container name `edge-tts`;
- restart policy `unless-stopped`;
- port mapping `5050:5050`;
- read-only bind mount `./config.yaml:/config/config.yaml:ro`;
- custom DNS resolvers `223.5.5.5` and `119.29.29.29`;
- the health check inherited from the Docker image.

The service does not embed an API key. Users create `config.yaml` using the existing documented schema and Docker-safe `host: 0.0.0.0` value before starting Compose.

Production and development files use the same container name, port, and bind mount. Users must stop one before starting the other; documentation states this explicitly.

## Documentation

`docs/docker.md` gains Compose sections for production, version pinning through `.env`, local builds, logs, health status, and shutdown. `.github/release-notes.md` gains the production Compose deployment commands so each GitHub Release remains self-contained.

The production example shows:

```console
docker compose -f compose.yaml up -d
```

The local-build example shows:

```console
docker compose -f compose.dev.yaml up -d --build
```

## Verification

Automated tests parse both YAML files and assert:

- each contains exactly the `edge-tts` service;
- the production service uses the GHCR image with an overridable tag;
- the development service builds the local directory;
- both publish port `5050`, mount `config.yaml` read-only, use `unless-stopped`, and list both DNS resolvers in the specified order;
- maintained Docker and Release documentation contains both Compose workflows.

When Docker Compose is available with a running daemon, `docker compose config` validates both files. A local container build remains optional when the Docker daemon is unavailable.
