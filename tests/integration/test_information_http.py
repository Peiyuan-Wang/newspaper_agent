import json
from pathlib import Path
import httpx
import pytest

from newspaper_agent.agent_core.loop import AgentLoop
from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.service import SourceService
from newspaper_agent.tools.information import SearchInformationTool, FetchEvidenceTool
from newspaper_agent.tools.registry import ToolRegistry


@pytest.mark.parametrize("wire", ["chat", "responses"])
def test_search_fetch_final_native_wire(wire):
    rounds = []
    def handler(request):
        body = json.loads(request.content)
        rounds.append(body)
        assert len(body["tools"]) == 2
        if len(rounds) == 1:
            name = "search_information"
            args = {"query": "AI", "domains": ["academic"], "kind": "paper", "region": None, "language": "en"}
        elif len(rounds) == 2:
            outputs = body["input"] if wire == "responses" else body["messages"]
            result = json.loads(outputs[-1]["output" if wire == "responses" else "content"])["result"]
            assert result["is_fixture"] and result["results"][0]["published_at"] is None
            name, args = "fetch_evidence", {"evidence_id": result["results"][0]["id"]}
        else:
            if wire == "responses":
                return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "虚构样例，不是真实论文"}]}]})
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "虚构样例，不是真实论文"}}]})
        call = {"name": name, "arguments": json.dumps(args)}
        call_id = f"call_{len(rounds)}"
        if wire == "responses":
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "function_call", "call_id": call_id, **call}]})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": call}]}}]})
    service = SourceService(SourceCatalog.load(Path(__file__).resolve().parents[2] / "config/sources/catalog.yaml"))
    registry = ToolRegistry()
    registry.register(SearchInformationTool(service))
    registry.register(FetchEvidenceTool(service))
    client = OpenAICompatibleClient(OpenAICompatibleSettings(provider="offline", model="test", base_url="https://relay.example/v1", api_key_env="TEST_KEY", wire_api=wire), api_key="offline-key", transport=httpx.MockTransport(handler))
    result = AgentLoop(client, registry).run("AI论文")
    assert result.tool_calls_executed == 2 and len(rounds) == 3
    assert "虚构" in result.response.content
