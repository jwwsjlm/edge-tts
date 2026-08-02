"""Tests for HTTP server configuration."""

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from edge_tts_server.config import ConfigError, ServerConfig, load_or_create_config


def write_config(tmp_path: Path, content: str) -> Path:
    """Write a configuration fixture."""
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_valid_config_is_loaded(tmp_path: Path) -> None:
    """A complete valid YAML mapping should become ServerConfig."""
    path = write_config(
        tmp_path,
        'api_key: "test-secret"\nhost: "0.0.0.0"\nport: 8080\n',
    )

    assert load_or_create_config(path) == ServerConfig(
        api_key="test-secret", host="0.0.0.0", port=8080
    )


def test_legacy_config_receives_hardened_defaults(tmp_path: Path) -> None:
    """Existing three-field files should remain valid after the upgrade."""
    path = write_config(
        tmp_path,
        'api_key: "secret"\nhost: "127.0.0.1"\nport: 5050\n',
    )

    config = load_or_create_config(path)

    assert config.max_text_length == 5000
    assert config.max_request_bytes == 65536
    assert config.max_concurrent_requests == 4
    assert config.request_timeout_seconds == 120
    assert config.max_audio_bytes == 20971520
    assert config.docs_enabled is False
    assert config.voices_cache_ttl_seconds == 3600
    assert config.proxy is None
    assert config.upstream_connect_timeout_seconds == 10
    assert config.upstream_receive_timeout_seconds == 60


def test_hardened_limits_are_loaded(tmp_path: Path) -> None:
    """Operators should be able to tune all resource controls."""
    path = write_config(
        tmp_path,
        "\n".join(
            (
                'api_key: "secret"',
                'host: "0.0.0.0"',
                "port: 5050",
                "max_text_length: 100",
                "max_request_bytes: 2048",
                "max_concurrent_requests: 2",
                "request_timeout_seconds: 30",
                "max_audio_bytes: 4096",
                "docs_enabled: true",
                "voices_cache_ttl_seconds: 600",
                'proxy: "http://proxy-user:proxy-pass@proxy.example:8080"',
                "upstream_connect_timeout_seconds: 4",
                "upstream_receive_timeout_seconds: 20",
            )
        ),
    )

    assert load_or_create_config(path) == ServerConfig(
        api_key="secret",
        host="0.0.0.0",
        port=5050,
        max_text_length=100,
        max_request_bytes=2048,
        max_concurrent_requests=2,
        request_timeout_seconds=30,
        max_audio_bytes=4096,
        docs_enabled=True,
        voices_cache_ttl_seconds=600,
        proxy="http://proxy-user:proxy-pass@proxy.example:8080",
        upstream_connect_timeout_seconds=4,
        upstream_receive_timeout_seconds=20,
    )


def test_missing_config_is_created_with_random_key(tmp_path: Path) -> None:
    """First launch should create a usable, secret configuration."""
    path = tmp_path / "nested" / "config.yaml"

    config = load_or_create_config(path)

    assert path.exists()
    assert config.host == "127.0.0.1"
    assert config.port == 5050
    assert len(config.api_key) >= 40
    assert config.api_key in path.read_text(encoding="utf-8")


def test_generated_keys_are_not_reused(tmp_path: Path) -> None:
    """Separate first launches should not share credentials."""
    first = load_or_create_config(tmp_path / "first.yaml")
    second = load_or_create_config(tmp_path / "second.yaml")

    assert first.api_key != second.api_key


@pytest.mark.parametrize(
    "content",
    [
        'api_key: ""\nhost: "127.0.0.1"\nport: 5050\n',
        'api_key: "secret"\nhost: "bad host"\nport: 5050\n',
        'api_key: "secret"\nhost: "127.0.0.1"\nport: 0\n',
        'api_key: "secret"\nhost: "127.0.0.1"\nport: 65536\n',
        'api_key: "secret"\nhost: "127.0.0.1"\nport: "5050"\n',
        'api_key: "secret"\nhost: "127.0.0.1"\nport: 5050\nextra: true\n',
        'api_key: "secret"\nmax_text_length: 0\n',
        'api_key: "secret"\nmax_request_bytes: 0\n',
        'api_key: "secret"\nmax_concurrent_requests: 0\n',
        'api_key: "secret"\nrequest_timeout_seconds: 0\n',
        'api_key: "secret"\nmax_audio_bytes: 0\n',
        'api_key: "secret"\ndocs_enabled: "yes"\n',
        'api_key: "secret"\nvoices_cache_ttl_seconds: 0\n',
        'api_key: "secret"\nupstream_connect_timeout_seconds: 0\n',
        'api_key: "secret"\nupstream_receive_timeout_seconds: 0\n',
        'api_key: "secret"\nproxy: "proxy.example:8080"\n',
        'api_key: "secret"\nproxy: "ftp://proxy.example"\n',
        'api_key: "secret"\nproxy: "http:///missing-host"\n',
        "- not\n- a\n- mapping\n",
        "api_key: [unterminated\n",
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, content: str) -> None:
    """Invalid YAML and schema values should stop startup."""
    path = write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_or_create_config(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_key", 123),
        ("host", None),
        ("port", True),
        ("max_text_length", True),
        ("docs_enabled", 1),
        ("voices_cache_ttl_seconds", True),
        ("proxy", 123),
        ("upstream_connect_timeout_seconds", 1.5),
        ("upstream_receive_timeout_seconds", False),
    ],
)
def test_invalid_scalar_types_are_rejected(  # type: ignore[misc]
    tmp_path: Path, field: str, value: Any
) -> None:
    """Boolean and coercible scalar values must not bypass schema validation."""
    values: Dict[str, Any] = {
        "api_key": "secret",
        "host": "127.0.0.1",
        "port": 5050,
    }
    values[field] = value
    content = yaml.safe_dump(values)
    path = write_config(tmp_path, content)

    with pytest.raises(ConfigError):
        load_or_create_config(path)
