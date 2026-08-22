from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any, Callable, Sequence

import httpx

from newspaper_agent.agent_core.messages import Message


class ModelCommunicationError(RuntimeError):
    """模型通信失败。"""


@dataclass(frozen=True)
class OpenAICompatibleSettings:
    """OpenAI-compatible Chat Completions 所需的非敏感配置。"""

    provider: str
    base_url: str
    model: str
    api_key_env: str
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout: float = 60.0
    max_attempts: int = 3
    initial_retry_delay: float = 1.0
    max_retry_delay: float = 8.0


class ModelClient:
    """Provider-neutral 模型客户端边界。"""

    def complete(self, messages: Sequence[Message]) -> Message:
        raise NotImplementedError


class OpenAICompatibleClient(ModelClient):
    """调用 DeepSeek、OpenAI 或其他兼容 Chat Completions 的服务。"""

    def __init__(
        self,
        settings: OpenAICompatibleSettings,
        *,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._api_key = api_key or os.getenv(settings.api_key_env)
        if not self._api_key or not self._api_key.strip():
            raise ValueError(f"缺少模型 API key，请设置环境变量 {settings.api_key_env}")
        self._transport = transport
        self._sleep = sleep

    def complete(self, messages: Sequence[Message]) -> Message:
        payload = {
            "model": self.settings.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        endpoint = f"{self.settings.base_url.rstrip('/')}/chat/completions"
        for attempt in range(self.settings.max_attempts):
            try:
                with httpx.Client(timeout=self.settings.timeout, transport=self._transport) as client:
                    response = client.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json=payload,
                    )
                    response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if not self._should_retry(exc.response.status_code, attempt):
                    raise ModelCommunicationError(f"模型请求失败: {self.settings.provider}") from exc
                self._wait_before_retry(exc.response, attempt)
                continue
            except httpx.RequestError as exc:
                if attempt + 1 >= self.settings.max_attempts:
                    raise ModelCommunicationError(f"模型请求失败: {self.settings.provider}") from exc
                self._sleep(self._retry_delay(attempt))
                continue

            return self._parse_response(response)

        raise ModelCommunicationError(f"模型请求失败: {self.settings.provider}")

    def _should_retry(self, status_code: int, attempt: int) -> bool:
        return status_code in {408, 429, 500, 502, 503, 504} and attempt + 1 < self.settings.max_attempts

    def _retry_delay(self, attempt: int) -> float:
        return min(self.settings.initial_retry_delay * (2**attempt), self.settings.max_retry_delay)

    def _wait_before_retry(self, response: httpx.Response, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after is not None else self._retry_delay(attempt)
        except ValueError:
            delay = self._retry_delay(attempt)
        self._sleep(min(max(delay, 0.0), self.settings.max_retry_delay))

    @staticmethod
    def _parse_response(response: httpx.Response) -> Message:
        try:
            result: dict[str, Any] = response.json()
            choice = result["choices"][0]
            message = choice["message"]
            return Message(role=message["role"], content=message["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelCommunicationError("模型响应格式无效，缺少 choices[0].message") from exc
