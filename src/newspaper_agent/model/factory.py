from __future__ import annotations

import os
from pathlib import Path

from .settings import load_provider_settings

from .client import ModelClient, OpenAICompatibleClient, OpenAICompatibleSettings


def create_model_client(
    provider: str | None = None,
    *,
    config_path: Path | None = None,
) -> ModelClient:
    """根据 YAML provider 配置创建 OpenAI-compatible 客户端。"""
    path = config_path or Path("model_config.yaml")
    settings = load_provider_settings(path, provider_name=provider)
    api_key = os.getenv(settings.api_key_env)
    return OpenAICompatibleClient(
        OpenAICompatibleSettings(
            provider=settings.name,
            base_url=settings.base_url,
            model=settings.model,
            api_key_env=settings.api_key_env,
            wire_api=settings.wire_api,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout,
            max_attempts=settings.max_attempts,
            initial_retry_delay=settings.initial_retry_delay,
            max_retry_delay=settings.max_retry_delay,
        ),
        api_key=api_key,
    )
