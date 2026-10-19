from dataclasses import asdict
from newspaper_agent.agent_core.messages import ToolDefinition
from newspaper_agent.sources.contracts import DOMAINS, KINDS, SearchRequest
from newspaper_agent.sources.service import SourceService


class SearchInformationTool:
    name = "search_information"
    definition = ToolDefinition(name, "按任务领域选择已启用来源。当前只有虚构离线样例，不得作为真实证据。", {
        "type": "object", "properties": {
            "query": {"type": "string"},
            "domains": {"type": "array", "items": {"type": "string", "enum": list(DOMAINS)}},
            "kind": {"type": ["string", "null"], "enum": [*KINDS, None]},
            "region": {"type": ["string", "null"]}, "language": {"type": ["string", "null"]},
        }, "required": ["query", "domains", "kind", "region", "language"], "additionalProperties": False,
    })

    def __init__(self, service: SourceService):
        self.service = service

    def invoke(self, arguments):
        if set(arguments) != {"query", "domains", "kind", "region", "language"} or not isinstance(arguments["domains"], list):
            raise ValueError("无效检索参数")
        values = dict(arguments)
        values["domains"] = tuple(values["domains"])
        return asdict(self.service.search(SearchRequest(**values)))


class FetchEvidenceTool:
    name = "fetch_evidence"
    definition = ToolDefinition(name, "读取本会话检索返回的证据 id；不接受 URL。当前仅有虚构正文。", {
        "type": "object", "properties": {"evidence_id": {"type": "string"}},
        "required": ["evidence_id"], "additionalProperties": False,
    })

    def __init__(self, service: SourceService):
        self.service = service

    def invoke(self, arguments):
        if set(arguments) != {"evidence_id"} or not isinstance(arguments["evidence_id"], str):
            raise ValueError("无效证据参数")
        return asdict(self.service.fetch(arguments["evidence_id"]))
