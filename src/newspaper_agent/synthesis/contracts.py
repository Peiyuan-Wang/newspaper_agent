from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Report:
    """asdict 后可直接 JSON 序列化；用量未知时明确为 None。"""

    markdown: str
    knowledge_payload: dict
    issues: tuple[str, ...]
    metrics: dict
