"""Channel configuration and the safe, declarative public capability catalog."""

import re
from dataclasses import replace
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import ServerConfig
from .mimo import MIMO_PRESET_VOICES, validate_preset_voice
from .models import TTSRequest
from .synthesis import RequestFieldError, _validate_provider_request


class ChannelInput(BaseModel):
    """Administrator-only channel fields; omitted keys keep their existing value."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    provider: Literal["edge", "mimo"]
    enabled: bool = True
    api_key: str | None = Field(default=None, min_length=1, max_length=2048)
    base_url: str | None = None
    proxy: str | None = None
    default_voice: str | None = None
    default_mode: Literal["preset", "design", "clone"] = "preset"

    @model_validator(mode="after")
    def validate_channel(self) -> "ChannelInput":
        """Reject unusable connections and defaults without echoing credentials."""
        # pylint: disable=no-member,not-an-iterable
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Channel name must not be blank")
        for value in (self.base_url, self.proxy):
            if value is None:
                continue
            try:
                parsed = urlsplit(value)
                _ = parsed.port
                valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                valid = (
                    valid
                    and not parsed.fragment
                    and not any(c.isspace() for c in value)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Connection address must be an absolute HTTP(S) URL")
        if self.api_key is not None and not self.api_key.strip():
            raise ValueError("API key must not be blank")
        if self.provider == "edge":
            if self.api_key or self.base_url or self.default_mode != "preset":
                raise ValueError(
                    "Edge does not accept API keys, base URLs or MiMo modes"
                )
        elif self.default_voice:
            validate_preset_voice(self.default_voice)
        return self


def channel_config(config: ServerConfig, channel: dict[str, Any]) -> ServerConfig:
    """Create an immutable provider configuration for a submitted task."""
    return replace(
        config,
        proxy=channel.get("proxy"),
        mimo_api_key=channel.get("api_key"),
        mimo_base_url=channel.get("base_url") or "https://api.xiaomimimo.com/v1",
    )


def admin_channel(channel: dict[str, Any]) -> dict[str, Any]:
    """Expose editable connection metadata, never the stored API secret."""
    return {
        **{key: value for key, value in channel.items() if key != "api_key"},
        "has_api_key": bool(channel.get("api_key")),
    }


def capabilities(channel: dict[str, Any], config: ServerConfig) -> dict[str, Any]:
    """Describe only controls the selected provider can actually honor."""
    edge = channel["provider"] == "edge"
    fields: list[dict[str, Any]] = []
    if not edge:
        fields.append(
            {
                "name": "mimo_mode",
                "label": "音色模式",
                "type": "select",
                "default": channel.get("default_mode", "preset"),
                "options": [
                    {"value": "preset", "label": "预置音色"},
                    {"value": "design", "label": "设计音色"},
                    {"value": "clone", "label": "克隆音色"},
                ],
            }
        )
    fields.append(
        {
            "name": "voice",
            "label": "音色",
            "type": "voice",
            "modes": ["preset"],
            "default": channel.get("default_voice")
            or ("zh-CN-XiaoxiaoNeural" if edge else "mimo_default"),
        }
    )
    if edge:
        for name, label, suffix, low, high in (
            ("rate", "语速", "%", -50, 100),
            ("pitch", "音调", "Hz", -50, 50),
            ("volume", "音量", "%", -100, 100),
        ):
            fields.append(
                {
                    "name": name,
                    "label": label,
                    "type": "range",
                    "default": "+0" + suffix,
                    "min": low,
                    "max": high,
                    "step": 1,
                    "suffix": suffix,
                }
            )
    else:
        fields.extend(
            [
                {
                    "name": "voice_description",
                    "label": "音色描述",
                    "type": "textarea",
                    "modes": ["design"],
                    "required": True,
                    "default": "",
                    "placeholder": "例如：温柔明亮的年轻女声，咬字清楚，像在向朋友讲故事。",
                },
                {
                    "name": "reference_audio",
                    "label": "参考音频",
                    "type": "audio",
                    "modes": ["clone"],
                    "required": True,
                    "default": "",
                    "accept": ".wav,.mp3",
                    "max_bytes": min(config.max_reference_audio_bytes, 7864320),
                    "hint": "上传清晰的单人 WAV / MP3 音频，仅用于本次任务。",
                },
                {
                    "name": "speaking_rate",
                    "label": "语速风格",
                    "type": "select",
                    "default": "normal",
                    "options": [
                        {"value": value, "label": label}
                        for value, label in (
                            ("very_slow", "很慢"),
                            ("slow", "稍慢"),
                            ("normal", "自然"),
                            ("fast", "稍快"),
                            ("very_fast", "很快"),
                        )
                    ],
                    "hint": "通过风格指令控制，实际节奏由模型理解。",
                },
                {
                    "name": "instructions",
                    "label": "表达与情绪",
                    "type": "textarea",
                    "default": "",
                    "max_length": 4000,
                    "placeholder": "例如：轻松自然，带着微笑，句尾稍作停顿。",
                },
            ]
        )
    fields.append(
        {
            "name": "response_format",
            "label": "下载格式",
            "type": "select",
            "default": "mp3",
            "options": [
                {"value": "mp3", "label": "MP3 · 通用音频"},
                {"value": "wav", "label": "WAV · PCM 格式"},
            ],
        }
    )
    return {
        "id": channel["id"],
        "name": channel["name"],
        "provider": channel["provider"],
        "supports_subtitles": edge,
        "fields": fields,
        "description": "多语言 · 精准语速调节" if edge else "自然表达 · 音色设计与克隆",
    }


def job_payload(
    channel: dict[str, Any], config: ServerConfig, text: str, options: dict[str, Any]
) -> TTSRequest:
    """Apply channel defaults and reject stale or invented client-side controls."""
    public = capabilities(channel, config)
    names = {field["name"] for field in public["fields"]}
    if set(options) - names:
        raise ValueError("Unsupported channel option")
    mode = options.get("mimo_mode", channel.get("default_mode", "preset"))
    values: dict[str, Any] = {}
    for field in public["fields"]:
        applicable = "modes" not in field or mode in field["modes"]
        value = options.get(field["name"], field.get("default"))
        if not applicable:
            if field["name"] in options:
                raise ValueError("Option is not supported in the selected mode")
            continue
        if field.get("required") and not value:
            raise RequestFieldError(
                "Required option is missing",
                [{"field": field["name"], "reason": "required"}],
            )
        if value not in (None, ""):
            if field["type"] == "range":
                match = (
                    re.fullmatch(r"([+-]\d+)" + re.escape(field["suffix"]), value)
                    if isinstance(value, str)
                    else None
                )
                if match is None or not field["min"] <= int(match[1]) <= field["max"]:
                    raise ValueError("Control is outside the channel's supported range")
            values[field["name"]] = value
    payload = TTSRequest.model_validate(
        {
            "model": "edge-tts" if channel["provider"] == "edge" else "mimo-v2-tts",
            "text": text,
            **values,
        }
    )
    _validate_provider_request(payload, config)
    return payload


def preset_voices() -> list[dict[str, str]]:
    """Publish MiMo voice names in the same shape as Edge voices."""
    return [
        {
            "name": v.voice_id,
            "friendly_name": v.voice_id,
            "locale": v.locale,
            "language": v.language,
            "gender": v.gender,
        }
        for v in MIMO_PRESET_VOICES
    ]
