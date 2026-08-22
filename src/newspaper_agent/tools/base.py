from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class Tool(Protocol):
    name: str

    def invoke(self, arguments: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
