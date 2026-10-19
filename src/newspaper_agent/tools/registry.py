from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
import json

from newspaper_agent.agent_core.messages import ToolCall, ToolDefinition
from .base import ToolExecutionError


class Tool(Protocol):
    name: str
    definition: ToolDefinition

    def invoke(self, arguments: dict[str, Any]) -> Any: ...


@dataclass
class ToolRegistry:
    _tools: dict[str, Tool]

    def __init__(self) -> None:
        self._tools = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError("工具名称重复")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self._tools.values())

    def execute(self, call: ToolCall) -> str:
        if call.name not in self._tools:
            return json.dumps({"ok": False, "error": "unknown_tool"})
        try:
            if len(call.arguments) > 8192:
                raise ValueError("Arguments too large")
            arguments = json.loads(call.arguments)
            if not isinstance(arguments, dict):
                raise ValueError("Arguments must be an object")
        except (TypeError, ValueError):
            return json.dumps({"ok": False, "error": "invalid_arguments"})
        try:
            result = self.get(call.name).invoke(arguments)
            output = json.dumps({"ok": True, "result": result}, ensure_ascii=False, allow_nan=False)
            if len(output) > 16384:
                return json.dumps({"ok": False, "error": "output_too_large"})
            return output
        except ToolExecutionError as exc:
            return json.dumps({"ok": False, "error": exc.code})
        except ValueError:
            return json.dumps({"ok": False, "error": "invalid_arguments"})
        except Exception:
            # 不将工具内部异常、路径或密钥回传给模型。
            return json.dumps({"ok": False, "error": "tool_execution_failed"})
