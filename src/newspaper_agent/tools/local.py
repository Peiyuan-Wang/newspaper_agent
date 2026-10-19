"""安全、无副作用的本地教学工具。"""

import math

from newspaper_agent.agent_core.messages import ToolDefinition


class AddNumbersTool:
    name = "add_numbers"
    definition = ToolDefinition(
        name=name,
        description="将两个有限数字相加，返回精确执行结果。",
        parameters={
            "type": "object",
            "properties": {"left": {"type": "number"}, "right": {"type": "number"}},
            "required": ["left", "right"],
            "additionalProperties": False,
        },
    )

    def invoke(self, arguments: dict) -> dict:
        if set(arguments) != {"left", "right"}:
            raise ValueError("Expected left and right")
        for value in arguments.values():
            if type(value) not in (int, float) or abs(value) > 1e12 or not math.isfinite(value):
                raise ValueError("Expected finite numbers within 1e12")
        return {"sum": arguments["left"] + arguments["right"]}
