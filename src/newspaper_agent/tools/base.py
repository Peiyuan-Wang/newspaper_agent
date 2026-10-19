from __future__ import annotations

from typing import Any, Protocol

from newspaper_agent.agent_core.messages import ToolDefinition


class Tool(Protocol):
    name: str
    definition: ToolDefinition

    def invoke(self, arguments: dict[str, Any]) -> Any: ...


# ToolDefinition 在核心定义，本模块保留兼容导入。


class ToolExecutionError(RuntimeError):
    """预定义的安全工具错误码，不携带服务端响应正文。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
