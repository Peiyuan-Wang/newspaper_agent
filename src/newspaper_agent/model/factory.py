from __future__ import annotations

from .client import ModelClient


def create_model_client(provider: str) -> ModelClient:
    raise NotImplementedError(f"未配置模型 provider: {provider}")
