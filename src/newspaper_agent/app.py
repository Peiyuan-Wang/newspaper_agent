from __future__ import annotations

from newspaper_agent.agent_core.messages import Message


class EchoModel:
    def complete(self, messages: list[Message]) -> Message:
        return Message(role="assistant", content="已收到：" + messages[-1].content)


def main() -> None:
    print(EchoModel().complete([Message(role="user", content="newspaper_agent ready")]).content)
