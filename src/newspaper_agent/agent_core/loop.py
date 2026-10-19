from __future__ import annotations

from dataclasses import dataclass

from .messages import AgentState, Message
from .protocols import ModelClient, ToolExecutor


class AgentLoopError(RuntimeError):
    """工具循环达到安全边界或返回无效控制消息。"""


@dataclass(frozen=True)
class LoopResult:
    response: Message
    state: AgentState
    tool_calls_executed: int = 0


class AgentLoop:
    def __init__(self, model: ModelClient, tools: ToolExecutor | None = None, *, max_rounds: int = 4, max_tool_calls: int = 8, control=None, finish_after_tools=None):
        if type(max_rounds) is not int or max_rounds < 1 or type(max_tool_calls) is not int or max_tool_calls < 1:
            raise ValueError("循环轮数和工具调用上限必须是正整数")
        self.model = model
        self.tools = tools
        self.max_rounds = max_rounds
        self.max_tool_calls = max_tool_calls
        self.control, self.finish_after_tools = control, finish_after_tools

    def run(self, user_input: str, state: AgentState | None = None) -> LoopResult:
        current = state or AgentState()
        user_message = Message(role="user", content=user_input)
        pending = [*current.messages, user_message]
        definitions = self.tools.definitions() if self.tools is not None else ()
        executed = 0
        seen_ids = {call.id for message in current.messages for call in message.tool_calls}
        for round_index in range(self.max_rounds):
            if self.control:
                self.control.emit('model_start', round=round_index + 1)
            response = (self.model.complete(pending, tools=definitions) if definitions
                        else self.model.complete(pending))
            if self.control:
                self.control.check()
            if response.role != "assistant":
                raise AgentLoopError("模型必须返回 assistant 消息")
            pending.append(response)
            if not response.tool_calls:
                if not response.content.strip():
                    raise AgentLoopError("模型未返回最终文本")
                current.messages.extend(pending[len(current.messages):])
                return LoopResult(response, current, executed)
            if not definitions:
                raise AgentLoopError("模型请求工具，但本会话未启用工具")
            if round_index + 1 >= self.max_rounds or executed + len(response.tool_calls) > self.max_tool_calls:
                raise AgentLoopError("已达到模型轮数或工具调用上限，本轮已停止")
            ids = [call.id for call in response.tool_calls]
            if any(not call_id or call_id in seen_ids for call_id in ids) or len(ids) != len(set(ids)):
                raise AgentLoopError("工具调用 ID 为空或重复")
            seen_ids.update(ids)
            for call in response.tool_calls:
                if self.control:
                    self.control.emit('tool_start', name=call.name)
                output = self.tools.execute(call)
                if self.control:
                    self.control.emit('tool_end', name=call.name)
                pending.append(Message(role="tool", content=output, name=call.name, tool_call_id=call.id))
                executed += 1
            if self.finish_after_tools and self.finish_after_tools():
                current.messages.extend(pending[len(current.messages):])
                return LoopResult(response, current, executed)
        raise AgentLoopError("工具循环未返回最终答案")
