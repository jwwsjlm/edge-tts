"""Load and validate the HTTP server YAML configuration."""

import ipaddress
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml

_ALLOWED_KEYS = frozenset(
    (
        "api_key",
        "host",
        "port",
        "max_text_length",
        "max_request_bytes",
        "max_concurrent_requests",
        "request_timeout_seconds",
        "max_audio_bytes",
        "docs_enabled",
        "voices_cache_ttl_seconds",
        "proxy",
        "upstream_connect_timeout_seconds",
        "upstream_receive_timeout_seconds",
    )
)
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class ConfigError(ValueError):
    """Raised when server configuration is unusable."""


@dataclass(frozen=True)
class ServerConfig:  # pylint: disable=too-many-instance-attributes
    """Validated server configuration."""

    api_key: str
    host: str = "127.0.0.1"
    port: int = 5050
    max_text_length: int = 5000
    max_request_bytes: int = 65536
    max_concurrent_requests: int = 4
    request_timeout_seconds: int = 120
    max_audio_bytes: int = 20971520
    docs_enabled: bool = False
    voices_cache_ttl_seconds: int = 3600
    proxy: str | None = None
    upstream_connect_timeout_seconds: int = 10
    upstream_receive_timeout_seconds: int = 60


def _valid_host(host: str) -> bool:
    """Return whether host is an IP address or a valid DNS-style name."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        hostname = host[:-1] if host.endswith(".") else host
        return (
            bool(hostname)
            and len(hostname) <= 253
            and all(_HOST_LABEL.fullmatch(label) for label in hostname.split("."))
        )


def _valid_proxy(proxy: str) -> bool:
    """Return whether proxy is a supported absolute HTTP(S) URL."""
    if not proxy or any(character.isspace() for character in proxy):
        return False
    try:
        parsed = urlsplit(proxy)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and not parsed.fragment
    )


def _validate_config(raw: Any) -> ServerConfig:
    """Validate parsed YAML without coercing caller values."""
    if not isinstance(raw, Mapping):
        raise ConfigError("Configuration must be a YAML mapping")

    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise ConfigError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")

    api_key = raw.get("api_key")
    host = raw.get("host", "127.0.0.1")
    port = raw.get("port", 5050)
    max_text_length = raw.get("max_text_length", 5000)
    max_request_bytes = raw.get("max_request_bytes", 65536)
    max_concurrent_requests = raw.get("max_concurrent_requests", 4)
    request_timeout_seconds = raw.get("request_timeout_seconds", 120)
    max_audio_bytes = raw.get("max_audio_bytes", 20971520)
    docs_enabled = raw.get("docs_enabled", False)
    voices_cache_ttl_seconds = raw.get("voices_cache_ttl_seconds", 3600)
    proxy = raw.get("proxy")
    upstream_connect_timeout_seconds = raw.get("upstream_connect_timeout_seconds", 10)
    upstream_receive_timeout_seconds = raw.get("upstream_receive_timeout_seconds", 60)

    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigError("api_key must be a non-empty string")
    if not isinstance(host, str) or not _valid_host(host):
        raise ConfigError("host must be a valid IP address or hostname")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError("port must be an integer between 1 and 65535")
    limits = {
        "max_text_length": max_text_length,
        "max_request_bytes": max_request_bytes,
        "max_concurrent_requests": max_concurrent_requests,
        "request_timeout_seconds": request_timeout_seconds,
        "max_audio_bytes": max_audio_bytes,
        "voices_cache_ttl_seconds": voices_cache_ttl_seconds,
        "upstream_connect_timeout_seconds": upstream_connect_timeout_seconds,
        "upstream_receive_timeout_seconds": upstream_receive_timeout_seconds,
    }
    for name, value in limits.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(f"{name} must be a positive integer")
    if not isinstance(docs_enabled, bool):
        raise ConfigError("docs_enabled must be a boolean")
    if proxy is not None and (not isinstance(proxy, str) or not _valid_proxy(proxy)):
        raise ConfigError("proxy must be null or an absolute HTTP(S) URL")

    return ServerConfig(
        api_key=api_key,
        host=host,
        port=port,
        max_text_length=max_text_length,
        max_request_bytes=max_request_bytes,
        max_concurrent_requests=max_concurrent_requests,
        request_timeout_seconds=request_timeout_seconds,
        max_audio_bytes=max_audio_bytes,
        docs_enabled=docs_enabled,
        voices_cache_ttl_seconds=voices_cache_ttl_seconds,
        proxy=proxy,
        upstream_connect_timeout_seconds=upstream_connect_timeout_seconds,
        upstream_receive_timeout_seconds=upstream_receive_timeout_seconds,
    )


def load_or_create_config(path: Path) -> ServerConfig:
    """Load a configuration file, creating a secure local default if absent."""
    if not path.exists():
        generated = ServerConfig(api_key=secrets.token_urlsafe(32))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                yaml.safe_dump(asdict(generated), sort_keys=False),
                encoding="utf-8",
            )
        except OSError as exc:
            raise ConfigError(f"Cannot create configuration: {exc}") from exc
        return generated

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read configuration: {exc}") from exc
    return _validate_config(raw)
