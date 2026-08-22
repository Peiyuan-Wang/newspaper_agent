from __future__ import annotations

from dataclasses import dataclass

from .client import MCPClient


@dataclass
class MCPRegistry:
    clients: dict[str, MCPClient]

    def __init__(self) -> None:
        self.clients = {}

    def register(self, client: MCPClient) -> None:
        self.clients[client.server_name] = client
