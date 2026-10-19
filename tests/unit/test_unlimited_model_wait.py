"""Unlimited model waiting without removing other safety boundaries."""
from types import SimpleNamespace
import httpx
import pytest
from newspaper_agent.agent_core.messages import Message
from newspaper_agent.agent_core.runtime import RunControl, RunCancelled
from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings, ModelRequestFailure
from newspaper_agent.research import model_runtime


def test_unlimited_http_timeout_and_response_deadline(monkeypatch):
    from newspaper_agent.model import diagnostics
    clock = [0]
    monkeypatch.setattr(diagnostics.time, 'monotonic', lambda: clock[0])
    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            clock[0] += 10000
            yield b'{"choices":[{"message":{"role":"assistant","content":"OK"}}]}'
    def handler(request):
        assert request.extensions['timeout'] == dict(connect=None, read=None, write=None, pool=None)
        return httpx.Response(200, stream=Slow())
    client = OpenAICompatibleClient(OpenAICompatibleSettings(
        provider='offline', base_url='https://example.org/v1', model='offline',
        api_key_env='UNUSED', timeout=None, max_attempts=1),
        api_key='dummy-key', transport=httpx.MockTransport(handler))
    client.control = RunControl(max_seconds=None)
    assert client.complete([]).content == 'OK'


def test_unlimited_control_and_budget_still_respect_cancellation_and_call_limit(monkeypatch):
    control = RunControl(max_seconds=None)
    control.started -= 10000
    control.check()
    budget = model_runtime.BudgetModel(SimpleNamespace(complete=lambda *args, **kwargs: Message('assistant', 'OK')),
                                     max_seconds=None, max_calls=1)
    monkeypatch.setattr(model_runtime.time, 'monotonic', lambda: budget.started + 10000)
    assert budget.complete([]).content == 'OK'
    with pytest.raises(model_runtime.ModelCommunicationError, match='预算'):
        budget.complete([])
    control.cancel()
    with pytest.raises(RunCancelled):
        control.check()


def test_preset_factory_explicit_unlimited_overrides_yaml(monkeypatch):
    monkeypatch.setenv('OFFLINE_KEY', 'dummy-key')
    monkeypatch.setattr(model_runtime, 'load_provider_settings', lambda *args: SimpleNamespace(
        name='test', base_url='https://example.org/v1', model='offline', api_key_env='OFFLINE_KEY',
        wire_api='responses', temperature=None, max_tokens=1800, timeout=45))
    assert model_runtime.create_research_model(request_timeout=None).settings.timeout is None
    assert model_runtime.create_research_model(request_timeout=30).settings.timeout == 30


def test_unlimited_wait_keeps_response_size_limit():
    client = OpenAICompatibleClient(OpenAICompatibleSettings(
        provider='offline', base_url='https://example.org/v1', model='offline',
        api_key_env='UNUSED', timeout=None, max_attempts=1), api_key='dummy-key',
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'x' * 4_000_001)))
    with pytest.raises(ModelRequestFailure) as caught:
        client.complete([])
    assert caught.value.code == 'response_too_large'
