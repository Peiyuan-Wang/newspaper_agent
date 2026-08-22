from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

import yaml


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    model: str
    api_key_env: str | None = None
    temperature: float = 0.2
    max_tokens: int = 2048


def load_provider_settings(path: Path) -> ProviderSettings:
    """从 YAML 加载 provider 配置，不读取或保存真实密钥。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    provider_name = raw["default_provider"]
    values = raw["providers"][provider_name]
    return ProviderSettings(name=provider_name, **values)


def database_url() -> str:
    return os.getenv("NEWSPAPER_AGENT_DATABASE_URL", "sqlite:///data/newspaper_agent.sqlite3")
