from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WebSearchTool:
    """Web search 后端的可替换适配边界。"""

    name: str = "websearch"

    def invoke(self, arguments: dict) -> object:
        raise NotImplementedError("尚未配置 websearch 后端")
