from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    name: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    # 模型适配器自己的回放数据；核心循环不解释其协议结构。
    model_data: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass
class AgentState:
    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add(self, message: Message) -> None:
        self.messages.append(message)
