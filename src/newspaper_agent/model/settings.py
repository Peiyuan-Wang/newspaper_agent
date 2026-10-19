from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml


class ModelConfigurationError(ValueError):
    """Invalid non-secret model configuration."""


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    base_url: str
    model: str
    api_key_env: str
    temperature: float | None = 0.2
    max_tokens: int | None = 2048
    timeout: float = 60.0
    max_attempts: int = 3
    initial_retry_delay: float = 1.0
    max_retry_delay: float = 8.0
    wire_api: str = "chat"


def load_provider_settings(path: Path, provider_name: str | None = None) -> ProviderSettings:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModelConfigurationError("无法读取模型配置文件，请检查 --config 路径和文件权限") from exc
    except (yaml.YAMLError, UnicodeError) as exc:
        raise ModelConfigurationError("模型配置文件必须是 UTF-8 编码的合法 YAML") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("providers"), dict):
        raise ModelConfigurationError("模型配置缺少 providers 映射")
    selected = provider_name if provider_name is not None else raw.get("default_provider")
    if not isinstance(selected, str) or selected not in raw["providers"]:
        raise ModelConfigurationError("provider 未定义，请检查 --provider 和 default_provider")
    values = raw["providers"][selected]
    if not isinstance(values, dict):
        raise ModelConfigurationError("provider 配置必须是映射")
    values = dict(values)
    values.pop("name", None)
    protocol = values.pop("protocol", "openai-compatible")
    if protocol != "openai-compatible":
        raise ModelConfigurationError("仅支持 openai-compatible 协议")
    if any(not isinstance(values.get(key), str) or not values[key].strip()
           for key in ("base_url", "model", "api_key_env")):
        raise ModelConfigurationError("base_url、model、api_key_env 必须是非空字符串")
    try:
        url = urlsplit(values["base_url"])
        valid_url = url.scheme in {"http", "https"} and bool(url.hostname) and url.port != 0
    except ValueError:
        valid_url = False
    if not valid_url or url.username is not None or url.password is not None or url.query or url.fragment:
        raise ModelConfigurationError("base_url 必须是 HTTP(S) 地址，且不能含用户名、密码、查询参数或片段")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", values["api_key_env"]):
        raise ModelConfigurationError("api_key_env 必须是合法的环境变量名")
    try:
        settings = ProviderSettings(name=selected, **values)
    except TypeError as exc:
        raise ModelConfigurationError("provider 包含不支持的配置字段；密钥只能通过环境变量注入") from exc
    if not isinstance(settings.wire_api, str) or settings.wire_api not in {"chat", "responses"}:
        raise ModelConfigurationError("wire_api 只能是 chat 或 responses")
    for key in ("max_tokens", "max_attempts"):
        value = getattr(settings, key)
        if key == "max_tokens" and value is None:
            continue
        if type(value) is not int or value <= 0:
            raise ModelConfigurationError(f"{key} 必须是正整数")
    for key in ("temperature", "timeout", "initial_retry_delay", "max_retry_delay"):
        value = getattr(settings, key)
        if key == "temperature" and value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ModelConfigurationError(f"{key} 必须是有限的非负数")
    if settings.timeout == 0 or (settings.temperature is not None and settings.temperature > 2):
        raise ModelConfigurationError("timeout 必须大于 0，temperature 必须在 0 到 2 之间")
    return settings
