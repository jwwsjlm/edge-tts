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
