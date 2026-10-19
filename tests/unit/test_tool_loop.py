import json

import pytest

from newspaper_agent.agent_core.loop import AgentLoop, AgentLoopError
from newspaper_agent.agent_core.messages import AgentState, Message, ToolCall
from newspaper_agent.app import main
from newspaper_agent.model.demo import DemoToolModel
from newspaper_agent.model.client import ModelCommunicationError
from newspaper_agent.tools.local import AddNumbersTool
from newspaper_agent.tools.registry import ToolRegistry


def registry():
    tools = ToolRegistry()
    tools.register(AddNumbersTool())
    return tools


def test_demo_loop_retains_matched_tool_history():
    state = AgentState()
    loop = AgentLoop(DemoToolModel(), registry())
    result = loop.run("演示", state)
    assert result.tool_calls_executed == 1
    assert [m.role for m in state.messages] == ["user", "assistant", "tool", "assistant"]
    assert state.messages[1].tool_calls[0].id == state.messages[2].tool_call_id
    assert json.loads(state.messages[2].content) == {"ok": True, "result": {"sum": 42}}
    assert loop.run("再演示", state).tool_calls_executed == 1
    assert len(state.messages) == 8


@pytest.mark.parametrize("arguments", ["not json", "[]", '{}', '{"left":true,"right":2}',
    '{"left":1,"right":2,"extra":3}', '{"left":NaN,"right":2}',
    '{"left":1e100,"right":2}', '{"left":"1","right":2}', 'x' * 8193])
def test_invalid_arguments_are_rejected(arguments):
    output = registry().execute(ToolCall("a", "add_numbers", arguments))
    assert json.loads(output) == {"ok": False, "error": "invalid_arguments"}


def test_unknown_and_duplicate_tools():
    tools = registry()
    assert json.loads(tools.execute(ToolCall("a", "shell", '{}')))["error"] == "unknown_tool"
    with pytest.raises(ValueError, match="重复"):
        tools.register(AddNumbersTool())


def test_execution_failure_does_not_expose_internal_data():
    class Broken(AddNumbersTool):
        def invoke(self, arguments):
            raise RuntimeError("secret-key /private/path")
    tools = ToolRegistry()
    tools.register(Broken())
    result = tools.execute(ToolCall("a", "add_numbers", '{}'))
    assert json.loads(result)["error"] == "tool_execution_failed"
    assert "secret" not in result


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.histories = []

    def complete(self, messages, *, tools=()):
        self.histories.append(list(messages))
        result = next(self.replies)
        if isinstance(result, Exception):
            raise result
        return result


def calling(*calls):
    return Message("assistant", "", tool_calls=tuple(calls))


def test_multiple_calls_and_unknown_tool_recovery():
    model = ScriptedModel([
        calling(ToolCall("a", "unknown", '{}'), ToolCall("b", "add_numbers", '{"left":2,"right":3}')),
        Message("assistant", "已处理"),
    ])
    result = AgentLoop(model, registry()).run("test")
    assert result.tool_calls_executed == 2
    assert [m.tool_call_id for m in model.histories[1] if m.role == "tool"] == ["a", "b"]
    assert json.loads(model.histories[1][-2].content)["error"] == "unknown_tool"


def test_failure_after_tool_does_not_pollute_state():
    state = AgentState([Message("user", "old"), Message("assistant", "old answer")])
    before = list(state.messages)
    model = ScriptedModel([
        calling(ToolCall("a", "add_numbers", '{"left":1,"right":2}')),
        ModelCommunicationError("HTTP 500"),
    ])
    with pytest.raises(ModelCommunicationError):
        AgentLoop(model, registry()).run("test", state)
    assert state.messages == before


@pytest.mark.parametrize("options,replies", [
    ({"max_rounds": 1}, [calling(ToolCall("a", "add_numbers", '{}'))]),
    ({"max_tool_calls": 1}, [calling(ToolCall("a", "add_numbers", '{}'), ToolCall("b", "add_numbers", '{}'))]),
    ({}, [calling(ToolCall("a", "add_numbers", '{}'), ToolCall("a", "add_numbers", '{}'))]),
    ({}, [calling(ToolCall("", "add_numbers", '{}'))]),
])
def test_limits_and_bad_ids_leave_history_unchanged(options, replies):
    state = AgentState()
    with pytest.raises(AgentLoopError):
        AgentLoop(ScriptedModel(replies), registry(), **options).run("test", state)
    assert state.messages == []


def test_tool_calls_disabled_by_default():
    with pytest.raises(AgentLoopError, match="未启用"):
        AgentLoop(ScriptedModel([calling(ToolCall("a", "add_numbers", '{}'))])).run("test")


def test_offline_demo_cli_and_echo_guard(capsys):
    main(["--mode", "demo"])
    out = capsys.readouterr().out
    assert "12 + 30 = 42" in out
    assert "工具 1 次" in out
    with pytest.raises(SystemExit):
        main(["--tools"])


def test_invalid_arguments_can_be_corrected_by_model():
    model = ScriptedModel([
        calling(ToolCall("a", "add_numbers", '{}')),
        calling(ToolCall("b", "add_numbers", '{"left":2,"right":3}')),
        Message("assistant", "5"),
    ])
    result = AgentLoop(model, registry()).run("test")
    assert result.tool_calls_executed == 2
    assert json.loads(model.histories[1][-1].content)["error"] == "invalid_arguments"
    assert json.loads(model.histories[2][-1].content)["result"]["sum"] == 5


def test_repeated_calls_stop_before_last_round_execution():
    model = ScriptedModel([
        calling(ToolCall(str(i), "add_numbers", '{"left":1,"right":2}')) for i in range(4)
    ])
    state = AgentState()
    with pytest.raises(AgentLoopError, match="上限"):
        AgentLoop(model, registry()).run("test", state)
    assert len(model.histories) == 4
    assert sum(m.role == "tool" for m in model.histories[-1]) == 3
    assert state.messages == []


@pytest.mark.parametrize("options", [{"max_rounds": 0}, {"max_rounds": True}, {"max_tool_calls": -1}])
def test_invalid_loop_budget_rejected(options):
    with pytest.raises(ValueError):
        AgentLoop(DemoToolModel(), registry(), **options)
