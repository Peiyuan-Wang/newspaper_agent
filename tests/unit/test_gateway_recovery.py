"""Deterministic gateway recovery checks; never contact a real service."""
import json
from types import SimpleNamespace

import httpx
import pytest

from newspaper_agent.agent_core.loop import AgentLoop
from newspaper_agent.agent_core.messages import ToolDefinition
from newspaper_agent.agent_core.runtime import RunControl, RunCancelled
from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings, ModelRequestFailure
from newspaper_agent.research import model_runtime


def make_client(handler):
    model = OpenAICompatibleClient(OpenAICompatibleSettings(
        provider='saved_connection', base_url='https://example.org/v1', model='offline',
        api_key_env='UNUSED', max_attempts=2, retry_statuses=(502, 503, 504),
        retry_transport_errors=False, retry_transport_codes=('remote_protocol', 'read_error'), max_retry_delay=2),
        api_key='dummy-key', transport=httpx.MockTransport(handler))
    model._sleep = lambda _: None
    return model


def reply(text='OK'):
    return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': text}}]})


@pytest.mark.parametrize('status', [502, 503, 504])
def test_gateway_recovers_same_payload(status):
    requests, events = [], []
    def handler(request):
        requests.append(request.content)
        return httpx.Response(status) if len(requests) == 1 else reply()
    model = make_client(handler)
    model.control = RunControl(events.append)
    assert model.complete([]).content == 'OK'
    assert len(requests) == 2 and requests[0] == requests[1]
    assert model.last_diagnostic['attempts'] == 2
    assert model.last_diagnostic['http_status'] == 200
    assert any(e['type'] == 'model_retry' and e['attempt'] == 2 for e in events)


def test_persistent_gateway_stops_and_does_not_expose_response():
    calls, delays = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(502, text='private-upstream-error dummy-key', headers={'Retry-After': '999'})
    model = make_client(handler)
    model._sleep = delays.append
    with pytest.raises(ModelRequestFailure) as caught:
        model.complete([])
    assert len(calls) == 2 and delays == [2]
    assert caught.value.code == 'http_502'
    assert caught.value.diagnostics['attempts'] == 2
    assert '已尝试2次' in str(caught.value)
    assert 'private-upstream-error' not in str(caught.value)
    assert 'dummy-key' not in str(caught.value)


@pytest.mark.parametrize('status', [400, 401, 403, 404, 408, 429, 500])
def test_desktop_policy_does_not_retry_other_statuses(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status)
    with pytest.raises(ModelRequestFailure):
        make_client(handler).complete([])
    assert len(calls) == 1


def test_read_timeout_is_not_replayed():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('unknown generation outcome', request=request)
    with pytest.raises(ModelRequestFailure) as caught:
        make_client(handler).complete([])
    assert caught.value.code == 'read_timeout' and len(calls) == 1


def test_cancel_during_backoff_prevents_second_request():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(502)
    model = make_client(handler)
    model.control = RunControl()
    model._sleep = lambda _: model.control.cancel()
    with pytest.raises(RunCancelled):
        model.complete([])
    assert len(calls) == 1


def test_retry_after_tool_result_does_not_execute_tool_twice():
    requests, executions = [], []
    def handler(request):
        requests.append(request.content)
        if len(requests) == 1:
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'echo', 'arguments': '{}'}}]}}]})
        if len(requests) == 2:
            return httpx.Response(502)
        assert json.loads(request.content)['messages'][-1]['role'] == 'tool'
        return reply('done')
    class Tools:
        def definitions(self):
            return (ToolDefinition('echo', 'Offline echo'),)
        def execute(self, call):
            executions.append(call.id)
            return 'OK'
    result = AgentLoop(make_client(handler), Tools()).run('offline test')
    assert result.response.content == 'done'
    assert executions == ['call1'] and result.tool_calls_executed == 1
    assert len(requests) == 3 and requests[1] == requests[2]


def test_factory_recovery_is_explicit_and_bounded(monkeypatch):
    monkeypatch.setenv('OFFLINE_KEY', 'dummy-key')
    monkeypatch.setattr(model_runtime, 'load_provider_settings', lambda *args: SimpleNamespace(
        name='test', base_url='https://example.org/v1', model='offline', api_key_env='OFFLINE_KEY',
        wire_api='responses', temperature=None, max_tokens=1800, timeout=45))
    model = model_runtime.create_research_model(gateway_recovery=True)
    assert model.settings.max_attempts == 2
    assert model.settings.retry_statuses == (502, 503, 504)
    assert not model.settings.retry_transport_errors
    assert model.settings.retry_transport_codes == ('remote_protocol', 'read_error')
    assert model_runtime.create_research_model().settings.max_attempts == 1
    assert not model_runtime.create_research_model().settings.retry_transport_codes


@pytest.mark.parametrize('error', [httpx.RemoteProtocolError, httpx.ReadError])
def test_interrupted_response_retries_same_request_once(error):
    requests, events = [], []
    def handler(request):
        requests.append(request.content)
        if len(requests) == 1:
            raise error('private detail', request=request)
        return reply()
    model = make_client(handler)
    model.control = RunControl(events.append)
    assert model.complete([]).content == 'OK'
    assert len(requests) == 2 and requests[0] == requests[1]
    assert model.last_diagnostic['attempts'] == 2
    assert any(e['type'] == 'model_retry' and e['attempt'] == 2 for e in events)


def test_partial_response_disconnect_is_retried_without_using_partial_text():
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"choices":['
            raise httpx.RemoteProtocolError('incomplete chunked response')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=BrokenStream()) if len(calls) == 1 else reply()
    assert make_client(handler).complete([]).content == 'OK'
    assert len(calls) == 2


def test_gateway_and_disconnect_share_two_attempt_budget():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(502)
        raise httpx.RemoteProtocolError('private detail', request=request)
    with pytest.raises(ModelRequestFailure) as caught:
        make_client(handler).complete([])
    assert len(calls) == 2 and caught.value.diagnostics['attempts'] == 2
    assert caught.value.code == 'remote_protocol' and 'private detail' not in str(caught.value)


def test_cancel_during_disconnect_backoff_prevents_retry():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.RemoteProtocolError('disconnected', request=request)
    model = make_client(handler)
    model.control = RunControl()
    model._sleep = lambda _: model.control.cancel()
    with pytest.raises(RunCancelled):
        model.complete([])
    assert len(calls) == 1
