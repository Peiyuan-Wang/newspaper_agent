from __future__ import annotations

from typing import Protocol, Sequence

from .messages import Message, ToolCall, ToolDefinition


class ModelClient(Protocol):
    def complete(self, messages: Sequence[Message], *, tools: Sequence[ToolDefinition] = ()) -> Message: ...


class ToolExecutor(Protocol):
    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    def execute(self, call: ToolCall) -> str: ...


class Tool(Protocol):
    name: str
    definition: ToolDefinition

    def invoke(self, arguments: dict) -> object: ...


class MemoryRepository(Protocol):
    def save(self, key: str, value: str) -> None: ...

    def get(self, key: str) -> str | None: ...


class TextEncoder(Protocol):
    """Interest and article representations must share this exact coordinate system."""
    version: str
    dimension: int

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...
