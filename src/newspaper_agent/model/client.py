from __future__ import annotations

from typing import Sequence

from newspaper_agent.agent_core.messages import Message


class ModelClient:
    """Provider-neutral 模型客户端边界。"""

    def complete(self, messages: Sequence[Message]) -> Message:
        raise NotImplementedError
