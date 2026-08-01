"""Authenticated HTTP service for edge-tts."""

from .config import ConfigError, ServerConfig, load_or_create_config

__all__ = ["ConfigError", "ServerConfig", "load_or_create_config"]
