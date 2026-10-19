"""可配置 Responses 协议测试，不访问真实服务。"""

import json

import httpx
import pytest

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.model.client import (
    ModelCommunicationError,
    OpenAICompatibleClient,
    OpenAICompatibleSettings,
)


def completed_response(text="测试成功"):
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
    }


def make_probe(handler, **overrides):
    options = dict(wire_api="responses", temperature=None, max_tokens=None)
    options.update(overrides)
    return OpenAICompatibleClient(
        OpenAICompatibleSettings(
            provider="arbitrary-provider",
            base_url="https://relay.example/v1/",
            model="gpt-6-sol",
            api_key_env="NEWSPAPER_AGENT_CUSTOM_API_KEY",
            **options,
        ),
        api_key="offline-test-key",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


def test_responses_request_preserves_model_and_history():
    messages = [
        Message(role="system", content="请简短回答"),
        Message(role="user", content="你好"),
        Message(role="assistant", content="你好！"),
        Message(role="user", content="再测试一次"),
    ]

    def handler(request):
        assert str(request.url) == "https://relay.example/v1/responses"
        assert request.headers["authorization"] == "Bearer offline-test-key"
        assert json.loads(request.content) == {
            "model": "gpt-6-sol",
            "input": [{"role": m.role, "content": m.content} for m in messages],
            "store": False,
            "stream": False,
        }
        return httpx.Response(200, json=completed_response())

    assert make_probe(handler).complete(messages) == Message(
        role="assistant", content="测试成功"
    )


def test_responses_joins_text_blocks():
    result = completed_response("第一段")
    result["output"][1]["content"].append({"type": "output_text", "text": "第二段"})
    assert make_probe(lambda _: httpx.Response(200, json=result)).complete([]).content == "第一段第二段"


@pytest.mark.parametrize("result", [
    {"status": "incomplete", "output": []},
    {"status": "failed", "output": []},
    {"status": "completed", "output": []},
    {"status": "completed", "output": "not an array"},
    {"status": "completed", "output": [None]},
    {"choices": [{"message": {"role": "assistant", "content": "wrong protocol"}}]},
    completed_response(None),
    completed_response(""),
])
def test_invalid_responses_are_rejected(result):
    with pytest.raises(ModelCommunicationError, match="Responses"):
        make_probe(lambda _: httpx.Response(200, json=result)).complete([])


def test_responses_does_not_retry_bad_request_or_expose_key():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "offline-test-key"}})

    with pytest.raises(ModelCommunicationError, match="HTTP 400") as error:
        make_probe(handler).complete([Message(role="user", content="你好")])
    assert len(calls) == 1
    assert "offline-test-key" not in str(error.value)


@pytest.mark.parametrize("wire_api,route,message_field,token_field", [
    ("chat", "chat/completions", "messages", "max_tokens"),
    ("responses", "responses", "input", "max_output_tokens"),
])
@pytest.mark.parametrize("enabled", [True, False])
def test_protocol_routing_and_optional_parameters(wire_api, route, message_field, token_field, enabled):
    def handler(request):
        assert str(request.url) == f"https://relay.example/v1/{route}"
        body = json.loads(request.content)
        assert body[message_field] == [{"role": "user", "content": "你好"}]
        assert ("input" in body) == (wire_api == "responses")
        assert ("messages" in body) == (wire_api == "chat")
        if enabled:
            assert body["temperature"] == 0.0
            assert body[token_field] == 100
        else:
            assert "temperature" not in body
            assert "max_tokens" not in body
            assert "max_output_tokens" not in body
        if wire_api == "responses":
            assert "max_tokens" not in body
            return httpx.Response(200, json=completed_response())
        assert "max_output_tokens" not in body
        return httpx.Response(200, json={"choices": [{
            "message": {"role": "assistant", "content": "测试成功"}
        }]})

    client = make_probe(
        handler, wire_api=wire_api,
        temperature=0.0 if enabled else None, max_tokens=100 if enabled else None,
    )
    assert client.complete([Message(role="user", content="你好")]).content == "测试成功"


def test_sol_provider_name_does_not_override_chat_configuration():
    settings = OpenAICompatibleSettings(
        provider="custom_6sol", base_url="https://relay.example/v1", model="test",
        api_key_env="TEST_KEY",
    )

    def handler(request):
        assert str(request.url).endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["temperature"] == 0.2
        assert body["max_tokens"] == 2048
        return httpx.Response(200, json={"choices": [{
            "message": {"role": "assistant", "content": "ok"}
        }]})

    client = OpenAICompatibleClient(settings, api_key="test-key", transport=httpx.MockTransport(handler))
    assert client.complete([]).content == "ok"


@pytest.mark.parametrize("wire_api", ["invalid", None, ["responses"]])
def test_direct_client_rejects_invalid_wire_api(wire_api):
    with pytest.raises(ValueError, match="wire_api"):
        make_probe(lambda _: httpx.Response(200), wire_api=wire_api)
