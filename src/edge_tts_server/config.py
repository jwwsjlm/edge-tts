"""Load and validate the HTTP server YAML configuration."""

import ipaddress
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

_ALLOWED_KEYS = frozenset(("api_key", "host", "port"))
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class ConfigError(ValueError):
    """Raised when server configuration is unusable."""


@dataclass(frozen=True)
class ServerConfig:
    """Validated server configuration."""

    api_key: str
    host: str = "127.0.0.1"
    port: int = 5050


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

    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigError("api_key must be a non-empty string")
    if not isinstance(host, str) or not _valid_host(host):
        raise ConfigError("host must be a valid IP address or hostname")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError("port must be an integer between 1 and 65535")

    return ServerConfig(api_key=api_key, host=host, port=port)


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
