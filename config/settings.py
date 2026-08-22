from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

import yaml


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    base_url: str
    model: str
    api_key_env: str
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout: float = 60.0
    max_attempts: int = 3
    initial_retry_delay: float = 1.0
    max_retry_delay: float = 8.0


def load_provider_settings(path: Path, provider_name: str | None = None) -> ProviderSettings:
    """从 YAML 加载 provider 元数据，不读取或保存真实密钥。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    selected_name = provider_name or raw["default_provider"]
    values = dict(raw["providers"][selected_name])
    values.pop("name", None)
    values.pop("protocol", None)
    return ProviderSettings(name=selected_name, **values)


def database_url() -> str:
    return os.getenv("NEWSPAPER_AGENT_DATABASE_URL", "sqlite:///data/newspaper_agent.sqlite3")
