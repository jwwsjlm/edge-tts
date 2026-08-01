# Root Configuration Example Design

## Goal

Make server configuration immediately discoverable after downloading the repository while preventing users from committing a real API key.

## Changes

Add `config.example.yaml` at the repository root with the server-safe example:

```yaml
api_key: "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"
host: "0.0.0.0"
port: 5050
```

Add `/config.yaml` to `.gitignore`. This ignores only the repository-root secret file and does not hide other intentionally versioned examples.

Update the Docker guide and GitHub Release notes to copy the root example instead of the deeper `packaging/docker/config.example.yaml` path. The existing packaging examples remain because the Windows release builder and Docker-specific asset checks use them.

For 1Panel, documentation will show copying the example to `/opt/edge-tts-data/config.yaml`, editing the key, and starting the service with:

```console
python -m edge_tts_server --config /opt/edge-tts-data/config.yaml
```

## Verification

Automated tests assert that the root example parses to the documented mapping, `/config.yaml` is ignored, the example remains tracked, and deployment documentation references the root example and 1Panel target path. The complete test and formatting suites run before pushing.
