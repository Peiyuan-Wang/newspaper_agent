from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MCPClient:
    server_name: str
    connected: bool = False

    def connect(self) -> None:
        self.connected = True

    def list_tools(self) -> list[dict[str, Any]]:
        return []
