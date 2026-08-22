from __future__ import annotations

from dataclasses import dataclass

from .messages import AgentState, Message


@dataclass(frozen=True)
class LoopResult:
    response: Message
    state: AgentState


class AgentLoop:
    def __init__(self, model, tools=None):
        self.model = model
        self.tools = tools

    def run(self, user_input: str, state: AgentState | None = None) -> LoopResult:
        current = state or AgentState()
        current.add(Message(role="user", content=user_input))
        response = self.model.complete(current.messages)
        current.add(response)
        return LoopResult(response=response, state=current)
