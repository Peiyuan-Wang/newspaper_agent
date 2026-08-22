from __future__ import annotations

from collections.abc import Iterable

from newspaper_agent.agent_core.messages import Message


class ShortTermMemory:
    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages
        self._messages: list[Message] = []

    def append(self, message: Message) -> None:
        self._messages.append(message)
        self._messages = self._messages[-self.max_messages :]

    def extend(self, messages: Iterable[Message]) -> None:
        for message in messages:
            self.append(message)

    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)
