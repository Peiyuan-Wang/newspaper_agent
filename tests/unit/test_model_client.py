from newspaper_agent.agent_core.messages import Message
from newspaper_agent.model.client import ModelCommunicationError, OpenAICompatibleClient, OpenAICompatibleSettings

import httpx
import pytest


def make_client(handler, api_key="test-key"):
    transport = httpx.MockTransport(handler)
    settings = OpenAICompatibleSettings(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
    )
    return OpenAICompatibleClient(settings, api_key=api_key, transport=transport)


def test_deepseek_request_and_response():
    def handler(request):
        assert request.url == "https://api.deepseek.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = request.read().decode()
        assert '"model":"deepseek-chat"' in body
        assert '"content":"你好"' in body
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "你好，我是 DeepSeek。"}}]},
        )

    response = make_client(handler).complete([Message(role="user", content="你好")])
    assert response == Message(role="assistant", content="你好，我是 DeepSeek。")


def test_missing_api_key_is_rejected(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings = OpenAICompatibleSettings(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
    )
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        OpenAICompatibleClient(settings)


def test_http_error_does_not_expose_key():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "unauthorized"}})

    with pytest.raises(ModelCommunicationError, match="模型请求失败") as error:
        make_client(handler, api_key="secret-key").complete([])
    assert "secret-key" not in str(error.value)


def test_retries_temporary_server_error():
    responses = [
        httpx.Response(503),
        httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}),
    ]
    calls = []

    def handler(request):
        calls.append(request)
        return responses.pop(0)

    delays = []
    client = make_client(handler, api_key="test-key")
    client._sleep = delays.append
    response = client.complete([Message(role="user", content="retry")])

    assert response.content == "ok"
    assert len(calls) == 2
    assert delays == [1.0]


def test_retry_after_is_capped_and_used():
    responses = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}),
    ]

    def handler(request):
        return responses.pop(0)

    delays = []
    client = make_client(handler)
    client._sleep = delays.append
    assert client.complete([]).content == "ok"
    assert delays == [3.0]


def test_authentication_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401)

    delays = []
    client = make_client(handler)
    client._sleep = delays.append
    with pytest.raises(ModelCommunicationError):
        client.complete([])
    assert len(calls) == 1
    assert delays == []


def test_network_error_stops_after_max_attempts():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("temporary network failure")

    delays = []
    client = make_client(handler)
    client._sleep = delays.append
    with pytest.raises(ModelCommunicationError):
        client.complete([])
    assert len(calls) == 3
    assert delays == [1.0, 2.0]


def test_invalid_response_is_rejected():
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(ModelCommunicationError, match="响应格式无效"):
        make_client(handler).complete([])
