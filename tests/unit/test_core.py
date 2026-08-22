from newspaper_agent.agent_core.messages import AgentState, Message
from newspaper_agent.agent_core.loop import AgentLoop
from newspaper_agent.memory.short_term import ShortTermMemory
from newspaper_agent.tools.registry import ToolRegistry


class FakeModel:
    def complete(self, messages):
        return Message(role="assistant", content="ok")


def test_agent_loop_adds_user_and_assistant_messages():
    result = AgentLoop(FakeModel()).run("hello")
    assert [message.role for message in result.state.messages] == ["user", "assistant"]
    assert result.response.content == "ok"


def test_short_term_memory_keeps_latest_messages():
    memory = ShortTermMemory(max_messages=2)
    memory.extend(Message(role="user", content=str(index)) for index in range(3))
    assert [message.content for message in memory.messages()] == ["1", "2"]


def test_tool_registry_registers_by_name():
    class Tool:
        name = "search"

    registry = ToolRegistry()
    registry.register(Tool())
    assert registry.names() == ("search",)
