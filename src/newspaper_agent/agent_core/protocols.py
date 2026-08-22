from __future__ import annotations

from typing import Protocol, Sequence

from .messages import Message


class ModelClient(Protocol):
    def complete(self, messages: Sequence[Message]) -> Message: ...


class Tool(Protocol):
    name: str

    def invoke(self, arguments: dict) -> object: ...


class MemoryRepository(Protocol):
    def save(self, key: str, value: str) -> None: ...

    def get(self, key: str) -> str | None: ...
