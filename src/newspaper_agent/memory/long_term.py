from __future__ import annotations

from dataclasses import dataclass

from newspaper_agent.agent_core.protocols import MemoryRepository


@dataclass
class LongTermMemory:
    repository: MemoryRepository

    def remember(self, key: str, value: str) -> None:
        self.repository.save(key, value)

    def recall(self, key: str) -> str | None:
        return self.repository.get(key)
