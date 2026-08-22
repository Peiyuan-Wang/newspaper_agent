from __future__ import annotations

from dataclasses import dataclass

from .long_term import LongTermMemory
from .short_term import ShortTermMemory


@dataclass
class MemoryService:
    short_term: ShortTermMemory
    long_term: LongTermMemory
