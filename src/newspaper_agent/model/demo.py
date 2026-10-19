"""确定性的工具教学演示，不是理解自然语言的模型。"""

import json

from newspaper_agent.agent_core.messages import Message, ToolCall


class DemoToolModel:
    def complete(self, messages, *, tools=()):
        if messages[-1].role == "tool":
            result = json.loads(messages[-1].content)
            if not result["ok"]:
                return Message("assistant", "离线演示：工具执行失败。")
            return Message("assistant", f"离线演示：工具算出 12 + 30 = {result['result']['sum']}。")
        call_id = f"demo_{len(messages)}"
        return Message("assistant", "", tool_calls=(
            ToolCall(call_id, "add_numbers", '{"left":12,"right":30}'),
        ))
