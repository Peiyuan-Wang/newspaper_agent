"""Explicit model construction and conservative per-turn request budgets."""
from dataclasses import asdict
import json
import os
import time
from threading import RLock

from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings, ModelCommunicationError
from newspaper_agent.model.settings import load_provider_settings


def create_research_model(provider=None, *, config_path=None, max_output_tokens=1800, request_timeout=45,
                          gateway_recovery=False):
    from pathlib import Path
    settings = load_provider_settings(Path(config_path or "model_config.yaml"), provider)
    return OpenAICompatibleClient(OpenAICompatibleSettings(
        provider=settings.name, base_url=settings.base_url, model=settings.model,
        api_key_env=settings.api_key_env, wire_api=settings.wire_api, temperature=settings.temperature,
        max_tokens=min(settings.max_tokens or max_output_tokens, max_output_tokens),
        timeout=None if request_timeout is None else min(settings.timeout, request_timeout),
        max_attempts=2 if gateway_recovery else 1,
        retry_statuses=(502, 503, 504), retry_transport_errors=False,
        retry_transport_codes=('remote_protocol', 'read_error') if gateway_recovery else (),
        max_retry_delay=2), api_key=os.getenv(settings.api_key_env))


class ContextBudgetExceeded(ModelCommunicationError):
    """Safe numeric diagnostics, without including any prompt content."""
    def __init__(self, used, limit):
        self.code = 'context_budget'
        self.diagnostics = {'code': self.code, 'request_characters': used, 'context_limit': limit}
        super().__init__(f'本轮上下文超出字符预算（{used} > {limit}）；请提高上限或缩小问题后重试。')


class BudgetModel:
    def __init__(self, client, *, max_calls=6, max_context_chars=30000, max_seconds=240):
        if type(max_context_chars) is not int or not 1 <= max_context_chars <= 480000:
            raise ValueError('上下文字符预算必须为1–480000的整数')
        self.client = client
        self.max_calls, self.max_context_chars, self.max_seconds = max_calls, max_context_chars, max_seconds
        self.control = None
        self.lock = RLock()
        self.reset()

    def reset(self):
        self.calls = 0
        self.input_characters = 0
        self.started = time.monotonic()
        self.usage = {}
        self.usage_calls = 0

    def complete(self, messages, *, tools=()):
        return self.complete_using(self.client, messages, tools=tools)

    def complete_using(self, client, messages, *, tools=()):
        if self.control:
            self.control.check()
        # Include native wire replay and tool definitions in the bound, not just visible text.
        measure = getattr(client, 'request_characters', None)
        size = measure(messages, tools) if callable(measure) else len(json.dumps({"messages": [asdict(m) for m in messages],
                               "tools": [asdict(t) for t in tools]}, ensure_ascii=False))
        if size > self.max_context_chars:
            raise ContextBudgetExceeded(size, self.max_context_chars)
        with self.lock:
            if self.calls >= self.max_calls or (self.max_seconds is not None and
                                              time.monotonic() - self.started > self.max_seconds):
                raise ModelCommunicationError("已达到本轮模型请求或时间预算")
            self.calls += 1
            self.input_characters += size
        if hasattr(client, 'control'):
            client.control = self.control
        if hasattr(client, 'last_usage'):
            client.last_usage = {}
        try:
            result = client.complete(messages, tools=tools)
        finally:
            usage = getattr(client, 'last_usage', {})
            if isinstance(usage,dict) and 'total_tokens' in usage:
                with self.lock:
                    self.usage_calls += 1
                    for key,value in usage.items():
                        if type(value) is int and value >= 0:
                            self.usage[key] = self.usage.get(key,0) + value
        if self.control:
            self.control.check()
        return result

    def metrics(self):
        return {"model_calls": self.calls, "input_characters": self.input_characters,
                'max_context_chars': self.max_context_chars,
                "elapsed_seconds": round(time.monotonic() - self.started, 3),
                "tokens": self.usage.get('total_tokens'), 'token_usage': dict(self.usage),
                'usage_calls': self.usage_calls, 'usage_complete': self.usage_calls == self.calls,
                "token_note": "仅统计服务端已返回usage的请求；缺失或HTTP重试的用量未知，字符数不是token数。"}
