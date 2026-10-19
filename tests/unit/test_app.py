from newspaper_agent.agent_core.loop import AgentLoop
from newspaper_agent.agent_core.messages import Message
from newspaper_agent.app import main, run_interactive
from newspaper_agent.model.client import ModelCommunicationError
import pytest


class RecordingModel:
    def __init__(self):
        self.histories = []

    def complete(self, messages):
        self.histories.append(list(messages))
        return Message(role="assistant", content="ok")


def session(model, lines):
    inputs = iter(lines)
    outputs = []
    state = run_interactive(AgentLoop(model), read=lambda _: next(inputs), write=outputs.append)
    return state, outputs


def test_interactive_retains_context_and_skips_blank_input():
    model = RecordingModel()
    state, _ = session(model, ["  first  ", " ", "second", "/exit"])
    assert [m.content for m in model.histories[1]] == ["first", "ok", "second"]
    assert len(state.messages) == 4


def test_reset_starts_a_new_context():
    model = RecordingModel()
    state, outputs = session(model, ["first", "/reset", "second", "/exit"])
    assert [m.content for m in model.histories[1]] == ["second"]
    assert len(state.messages) == 2
    assert "上下文已清空。" in outputs


def test_eof_and_interrupt_end_session():
    for exception in (EOFError, KeyboardInterrupt):
        def read(_):
            raise exception
        state = run_interactive(AgentLoop(RecordingModel()), read=read, write=lambda _: None)
        assert state.messages == []


def test_single_message_cli(capsys):
    main(["--message", "新闻研究"])
    assert capsys.readouterr().out.strip() == "已收到：新闻研究"


def test_failure_does_not_pollute_history_and_session_can_continue():
    class FailingOnceModel(RecordingModel):
        def complete(self, messages):
            if not self.histories:
                self.histories.append(list(messages))
                raise ModelCommunicationError("HTTP 401")
            return super().complete(messages)
    model = FailingOnceModel()
    state, outputs = session(model, ["failed", "success", "/exit"])
    assert [m.content for m in model.histories[1]] == ["success"]
    assert [m.content for m in state.messages] == ["success", "ok"]
    assert any("HTTP 401" in text for text in outputs)


def test_live_missing_key_has_clean_error(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main(["--mode", "live", "--provider", "deepseek"])
    assert error.value.code == 2
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err


def test_echo_does_not_load_config_and_rejects_ignored_provider(capsys):
    main(["--config", "does-not-exist.yaml"])
    assert "已收到" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        main(["--provider", "deepseek"])
    assert error.value.code == 2
