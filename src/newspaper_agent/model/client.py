from __future__ import annotations

from dataclasses import dataclass
import os
import json
import time
from typing import Any, Callable, Sequence

import httpx

from newspaper_agent.agent_core.messages import Message, ToolDefinition
from .tool_wire import encode_messages, parse_tool_reply
from .diagnostics import transport_failure, read_bounded


class ModelCommunicationError(RuntimeError):
    """模型通信失败。"""


class ModelRequestFailure(ModelCommunicationError):
    def __init__(self, code, message, diagnostics):
        self.code, self.diagnostics = code, dict(diagnostics)
        super().__init__(message + f' [诊断：{code}]')


@dataclass(frozen=True)
class OpenAICompatibleSettings:
    """OpenAI-compatible Chat Completions / Responses 的非敏感配置。"""

    provider: str
    base_url: str
    model: str
    api_key_env: str
    temperature: float | None = 0.2
    max_tokens: int | None = 2048
    timeout: float | None = 60.0
    max_attempts: int = 3
    initial_retry_delay: float = 1.0
    max_retry_delay: float = 8.0
    wire_api: str = "chat"
    retry_statuses: tuple[int, ...] = (408, 429, 500, 502, 503, 504)
    retry_transport_errors: bool = True
    retry_transport_codes: tuple[str, ...] = ()


class ModelClient:
    """Provider-neutral 模型客户端边界。"""

    def complete(self, messages: Sequence[Message], *, tools: Sequence[ToolDefinition] = ()) -> Message:
        raise NotImplementedError


class OpenAICompatibleClient(ModelClient):
    """按 wire_api 调用兼容 Chat Completions 或 Responses 的文本服务。"""

    def __init__(
        self,
        settings: OpenAICompatibleSettings,
        *,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        if not isinstance(settings.wire_api, str) or settings.wire_api not in {"chat", "responses"}:
            raise ValueError("wire_api 只能是 chat 或 responses")
        self._api_key = api_key or os.getenv(settings.api_key_env)
        if not self._api_key or not self._api_key.strip():
            raise ValueError(f"缺少模型 API key，请设置环境变量 {settings.api_key_env}")
        self._transport = transport
        self._sleep = sleep
        self.control = None
        self.last_diagnostic = {}
        self.last_usage = {}

    def fork(self):
        """Independent diagnostics/state for bounded parallel text requests."""
        other = OpenAICompatibleClient(self.settings, api_key=self._api_key,
                                       transport=self._transport, sleep=self._sleep)
        other.control = self.control
        return other

    @staticmethod
    def usage_counts(result):
        raw = result.get('usage') if isinstance(result, dict) else None
        if not isinstance(raw, dict):
            return {}
        found = {}
        for target, names in {'input_tokens': ('input_tokens','prompt_tokens'),
                              'output_tokens': ('output_tokens','completion_tokens'),
                              'total_tokens': ('total_tokens',)}.items():
            value = next((raw[k] for k in names if k in raw), None)
            if type(value) is int and value >= 0:
                found[target] = value
        for target, keys, field in (
                ('cached_input_tokens', ('input_tokens_details','prompt_tokens_details'), 'cached_tokens'),
                ('reasoning_tokens', ('output_tokens_details','completion_tokens_details'), 'reasoning_tokens')):
            detail = next((raw[k] for k in keys if isinstance(raw.get(k),dict)), {})
            value = detail.get(field)
            if type(value) is int and value >= 0:
                found[target] = value
        if 'total_tokens' not in found and 'input_tokens' in found and 'output_tokens' in found:
            found['total_tokens'] = found['input_tokens'] + found['output_tokens']
        return found

    def request_payload(self, messages, tools=()):
        use_responses = self.settings.wire_api == "responses"
        payload = {
            "model": self.settings.model,
            "input" if use_responses else "messages": encode_messages(messages, use_responses),
        }
        if tools:
            functions = [{"name": tool.name, "description": tool.description,
                          "parameters": tool.parameters, "strict": True} for tool in tools]
            payload["tools"] = ([{"type": "function", **function} for function in functions]
                                if use_responses else
                                [{"type": "function", "function": function} for function in functions])
            if use_responses:
                payload["include"] = ["reasoning.encrypted_content"]
        if self.settings.temperature is not None:
            payload["temperature"] = self.settings.temperature
        if self.settings.max_tokens is not None:
            token_field = "max_output_tokens" if use_responses else "max_tokens"
            payload[token_field] = self.settings.max_tokens
        if use_responses:
            payload["store"] = False
            payload["stream"] = False
        return payload

    def request_characters(self, messages, tools=()):
        return len(json.dumps(self.request_payload(messages, tools), ensure_ascii=False, separators=(',', ':')))

    def complete(self, messages: Sequence[Message], *, tools: Sequence[ToolDefinition] = ()) -> Message:
        self.last_usage = {}
        payload = self.request_payload(messages, tools)
        use_responses = self.settings.wire_api == 'responses'
        route = "responses" if use_responses else "chat/completions"
        endpoint = f"{self.settings.base_url.rstrip('/')}/{route}"
        started = time.monotonic()
        self.last_diagnostic = {'wire_api': self.settings.wire_api, 'tools': len(tools),
            'request_characters': self.request_characters(messages, tools), 'http_status': None}
        def failure(code, text):
            self.last_diagnostic.update(code=code, elapsed_seconds=round(time.monotonic() - started, 2))
            return ModelRequestFailure(code, text, self.last_diagnostic)
        for attempt in range(self.settings.max_attempts):
            if self.control:
                self.control.check()
            self.last_diagnostic.update(attempts=attempt + 1, http_status=None)
            try:
                timeout = (httpx.Timeout(None) if self.settings.timeout is None else
                           httpx.Timeout(self.settings.timeout, connect=min(10, self.settings.timeout)))
                with httpx.Client(timeout=timeout, transport=self._transport) as client:
                    response = read_bounded(client, endpoint,
                        headers={"Authorization": f"Bearer {self._api_key}"}, payload=payload,
                        timeout=self.settings.timeout, control=self.control)
                self.last_diagnostic['http_status'] = response.status_code
            except ValueError as exc:
                if str(exc) == 'response_too_large':
                    raise failure('response_too_large', '模型响应超过4MB，已停止读取。') from None
                if str(exc) == 'unexpected_event_stream':
                    raise failure('unexpected_stream', '中转站返回了事件流，但此连接请求的是非流式JSON；请检查中转站的Responses兼容性。') from None
                if str(exc) == 'response_deadline':
                    raise failure('response_deadline', '模型响应未在总等待预算内完成，已停止接收；不是密钥错误。') from None
                raise
            except httpx.HTTPStatusError as exc:
                self.last_diagnostic['http_status'] = exc.response.status_code
                if not self._should_retry(exc.response.status_code, attempt):
                    status = exc.response.status_code
                    hint = {
                        400: "检查请求参数和模型兼容性",
                        401: "检查 API key 是否有效",
                        403: "检查账户和模型访问权限",
                        404: "检查 base_url 和 model",
                        429: "请求限流或配额不足，请稍后重试并检查账户额度",
                    }.get(status, "服务暂时不可用，请稍后重试")
                    if status in (502, 503, 504):
                        hint = f"服务商网关或上游服务未能完成请求，已尝试{attempt + 1}次；可稍后重试，不代表密钥无效"
                    self.last_diagnostic['http_status'] = status
                    raise failure(f'http_{status}', f"模型请求失败: {self.settings.provider}，HTTP {status}；{hint}") from None
                if self.control:
                    self.control.emit('model_retry', status=exc.response.status_code,
                                      attempt=attempt + 2, total=self.settings.max_attempts)
                self._wait_before_retry(exc.response, attempt)
                continue
            except httpx.RequestError as exc:
                code, hint = transport_failure(exc)
                retryable = self.settings.retry_transport_errors or code in self.settings.retry_transport_codes
                if not retryable or attempt + 1 >= self.settings.max_attempts:
                    raise failure(code, hint) from None
                if self.control:
                    self.control.emit('model_retry', code=code,
                                      attempt=attempt + 2, total=self.settings.max_attempts)
                self._pause(self._retry_delay(attempt))
                continue

            try:
                result = response.json()
            except ValueError:
                raise failure('invalid_json', '模型响应格式无效：服务未返回完整 JSON；可能为中转站协议不兼容。') from None
            self.last_usage = self.usage_counts(result)
            if isinstance(result, dict) and use_responses and result.get('status') == 'incomplete':
                details = result.get('incomplete_details')
                reason = details.get('reason') if isinstance(details, dict) else None
                if reason == 'max_output_tokens':
                    raise failure('output_limit', '模型输出额度耗尽（含推理 token），并非网络错误；请提高单次输出额度后重试。')
                raise failure('incomplete_response', '模型工具响应或 Responses 响应未完成，不能当作完整回答或执行其中的工具。')
            choices = result.get('choices') if isinstance(result, dict) else None
            if not use_responses and isinstance(choices, list) and any(
                    isinstance(c, dict) and c.get('finish_reason') == 'length' for c in choices):
                raise failure('output_limit', '模型输出额度耗尽；请提高单次输出额度，未执行截断的工具请求。')
            self.last_diagnostic.update(code='completed', elapsed_seconds=round(time.monotonic() - started, 2))
            if tools:
                try:
                    return parse_tool_reply(result, use_responses)
                except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
                    raise failure('invalid_tool_response', '模型工具响应格式无效或未完成；请检查服务的工具调用协议兼容性。') from None
            try:
                if use_responses:
                    return self._parse_responses_response(response)
                return self._parse_response(response)
            except ModelCommunicationError as exc:
                raise failure('invalid_response', str(exc)) from None

        raise ModelCommunicationError(f"模型请求失败: {self.settings.provider}")

    def _should_retry(self, status_code: int, attempt: int) -> bool:
        return status_code in self.settings.retry_statuses and attempt + 1 < self.settings.max_attempts

    def _retry_delay(self, attempt: int) -> float:
        return min(self.settings.initial_retry_delay * (2**attempt), self.settings.max_retry_delay)

    def _wait_before_retry(self, response: httpx.Response, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after is not None else self._retry_delay(attempt)
        except ValueError:
            delay = self._retry_delay(attempt)
        self._pause(min(max(delay, 0.0), self.settings.max_retry_delay))

    def _pause(self, delay):
        if not self.control:
            self._sleep(delay)
            return
        # Check cancellation during backoff, not just during the next response.
        while delay > 0:
            self.control.check()
            step = min(delay, .1)
            self._sleep(step)
            delay -= step
        self.control.check()

    @staticmethod
    def _parse_responses_response(response: httpx.Response) -> Message:
        """仅提取已完成的 assistant 文本，不将推理或工具输出当作回复。"""
        try:
            result: dict[str, Any] = response.json()
            if result.get("status") != "completed":
                raise ValueError("Response is not completed")
            output = result["output"]
            if not isinstance(output, list):
                raise ValueError("Expected output array")
            texts = []
            for item in output:
                if not isinstance(item, dict):
                    raise ValueError("Invalid output item")
                if item.get("type") != "message" or item.get("role") != "assistant":
                    continue
                if item.get("status") != "completed":
                    raise ValueError("Message is not completed")
                if not isinstance(item.get("content"), list):
                    raise ValueError("Expected message content array")
                for part in item["content"]:
                    if not isinstance(part, dict) or part.get("type") != "output_text":
                        raise ValueError("Expected output_text")
                    if not isinstance(part.get("text"), str):
                        raise ValueError("Expected text")
                    texts.append(part["text"])
            text = "".join(texts)
            if not text.strip():
                raise ValueError("Missing assistant text")
            return Message(role="assistant", content=text)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ModelCommunicationError(
                "Responses 响应未完成或格式无效，缺少已完成的 assistant output_text"
            ) from exc

    @staticmethod
    def _parse_response(response: httpx.Response) -> Message:
        try:
            result: dict[str, Any] = response.json()
            choice = result["choices"][0]
            message = choice["message"]
            if message["role"] != "assistant" or not isinstance(message["content"], str):
                raise ValueError("Expected assistant text")
            return Message(role=message["role"], content=message["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelCommunicationError("模型响应格式无效，缺少 choices[0].message") from exc
