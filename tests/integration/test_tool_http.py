import json

import httpx
import pytest

from newspaper_agent.agent_core.loop import AgentLoop
from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings, ModelCommunicationError
from newspaper_agent.tools.local import AddNumbersTool
from newspaper_agent.tools.registry import ToolRegistry


@pytest.mark.parametrize("wire_api", ["chat", "responses"])
def test_native_tool_round_trip_and_second_turn(wire_api):
    calls = []
    reasoning = {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque-state"}
    function = {"type": "function_call", "id": "fc1", "call_id": "call1", "name": "add_numbers",
                "arguments": '{"left":12,"right":30}', "status": "completed"}

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["authorization"] == "Bearer offline-key"
        assert len(body["tools"]) == 1
        function_schema = body["tools"][0] if wire_api == "responses" else body["tools"][0]["function"]
        assert function_schema["name"] == "add_numbers"
        assert function_schema["parameters"]["additionalProperties"] is False
        if len(calls) == 1:
            if wire_api == "responses":
                assert body["include"] == ["reasoning.encrypted_content"]
                return httpx.Response(200, json={"status": "completed", "output": [reasoning, function]})
            return httpx.Response(200, json={"choices": [{"message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "call1", "type": "function", "function": {
                        "name": "add_numbers", "arguments": function["arguments"]
                    }
                }]
            }}]})
        if wire_api == "responses":
            history = body["input"]
            assert history[1:3] == [reasoning, function]
            output = history[3]
            assert output["type"] == "function_call_output"
            assert output["call_id"] == "call1"
            assert json.loads(output["output"])["result"]["sum"] == 42
            return httpx.Response(200, json={"status": "completed", "output": [{
                "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "答案是42"}],
            }]})
        history = body["messages"]
        assert history[1]["tool_calls"][0]["id"] == "call1"
        assert history[2]["tool_call_id"] == "call1"
        assert json.loads(history[2]["content"])["result"]["sum"] == 42
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "答案是42"}}]})

    tools = ToolRegistry()
    tools.register(AddNumbersTool())
    client = OpenAICompatibleClient(OpenAICompatibleSettings(
        provider="relay", base_url="https://relay.example/v1", model="test",
        api_key_env="TEST_KEY", wire_api=wire_api, temperature=None, max_tokens=None,
    ), api_key="offline-key", transport=httpx.MockTransport(handler))
    loop = AgentLoop(client, tools)
    result = loop.run("计算")
    assert result.tool_calls_executed == 1
    assert result.response.content == "答案是42"
    # 第二轮也携带合法的完整调用链，不重复执行旧工具。
    result2 = loop.run("刚才是多少", result.state)
    assert result2.tool_calls_executed == 0
    assert len(calls) == 3


@pytest.mark.parametrize("wire_api,body", [
    ("chat", {"choices": [{"message": {"role": "assistant", "content": None,
        "tool_calls": [{"id": "a", "type": "function", "function": {"name": "add_numbers", "arguments": {}}}]}}]}),
    ("responses", {"status": "incomplete", "output": []}),
    ("responses", {"status": "completed", "output": [{"type": "function_call", "call_id": "", "name": "add_numbers", "arguments": '{}'}]}),
])
def test_invalid_tool_response_is_sanitized(wire_api, body):
    client = OpenAICompatibleClient(OpenAICompatibleSettings(
        provider="test", base_url="https://relay.example/v1", model="test", api_key_env="TEST_KEY", wire_api=wire_api,
    ), api_key="offline-key", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))
    with pytest.raises(ModelCommunicationError, match="工具响应"):
        client.complete([], tools=[AddNumbersTool.definition])
