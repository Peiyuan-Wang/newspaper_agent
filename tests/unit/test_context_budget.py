from pathlib import Path
import pytest
from newspaper_agent.agent_core.messages import Message, ToolDefinition
from newspaper_agent.research.model_runtime import BudgetModel, ContextBudgetExceeded
from newspaper_agent.research.session import ResearchSession
from newspaper_agent.model.client import ModelCommunicationError
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.desktop_repository import DesktopRepository, CONTEXT_BUDGETS


class FakeModel:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, tools=()):
        self.calls += 1
        return Message('assistant', '离线测试回复')


def test_larger_budget_allows_same_input_without_truncation():
    client = FakeModel()
    messages = [Message('user', '测' * 35000)]
    small = BudgetModel(client)
    with pytest.raises(ContextBudgetExceeded, match=r'> 30000') as caught:
        small.complete(messages)
    assert '测' not in str(caught.value) and client.calls == 0
    large = BudgetModel(client, max_context_chars=60000)
    assert large.complete(messages).content == '离线测试回复'
    assert client.calls == 1 and len(messages[0].content) == 35000
    assert large.metrics()['max_context_chars'] == 60000


def test_tool_definition_and_tool_replay_are_included():
    client = FakeModel()
    model = BudgetModel(client, max_context_chars=60000)
    tool = ToolDefinition('example', 'D' * 25000)
    message = Message('tool', 'R' * 25000, model_data={'wire': 'W' * 25000})
    with pytest.raises(ContextBudgetExceeded):
        model.complete([message], tools=[tool])
    assert client.calls == 0
    BudgetModel(client, max_context_chars=120000).complete([message], tools=[tool])
    assert client.calls == 1


@pytest.mark.parametrize('value', [0, -1, 480001, True, '120000', 1.5])
def test_invalid_runtime_budgets_rejected(value):
    with pytest.raises(ValueError):
        BudgetModel(FakeModel(), max_context_chars=value)


def test_budget_preference_persists_per_account(tmp_path):
    repo = ResearchRepository(tmp_path / 'budget.sqlite3')
    repo.register('alice', 'password123')
    repo.register('bob', 'password123')
    store = DesktopRepository(repo)
    assert store.context_budget('alice') == 30000
    store.set_context_budget('alice', 120000)
    restored = DesktopRepository(ResearchRepository(repo.path))
    assert restored.context_budget('alice') == 120000
    assert restored.context_budget('bob') == 30000
    for value in (True, '120000', 999999, 100):
        with pytest.raises(ValueError):
            store.set_context_budget('alice', value)
    with pytest.raises(ValueError):
        store.set_context_budget('missing', 120000)


def test_session_passes_budget_and_preserves_safe_error(tmp_path):
    from newspaper_agent.research.service import ResearchSources
    repo = ResearchRepository(tmp_path / 'session.sqlite3')
    repo.register('reader', 'password123')
    root = Path(__file__).resolve().parents[2]
    sources = ResearchSources(repo, 'reader', catalog_path=root / 'config/sources/live.yaml')
    client = FakeModel()
    session = ResearchSession(repo, 'reader', sources, model=client, max_context_chars=100)
    assert session.model.max_context_chars == 100
    with pytest.raises(ModelCommunicationError, match=r'上下文超出字符预算（\d+ > 100）'):
        session.chat('hello')
    assert client.calls == 0 and session.history == []


def test_ui_choice_applies_and_restores_without_api_call(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from newspaper_agent.desktop import app as desktop
    from newspaper_agent.desktop.theme import STYLE
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setStyleSheet(STYLE)
    repo = ResearchRepository(tmp_path / 'ui.sqlite3')
    repo.register('reader', 'password123')
    client = FakeModel()
    monkeypatch.setattr(desktop, 'create_research_model', lambda *args, **kwargs: client)
    window = desktop.Window(repo, 'reader')
    assert tuple(window.context_budget.itemData(i) for i in range(window.context_budget.count())) == CONTEXT_BUDGETS
    assert window.context_budget.currentData() == 30000
    window.context_budget.setCurrentIndex(window.context_budget.findData(120000))
    window.model_key.setText('dummy-context-test-only')
    window.session.history = [Message('user', 'test'), Message('assistant', 'answer')]
    window.enable_model()
    assert window.session.model.max_context_chars == 120000
    assert len(window.session.history) == 2 and client.calls == 0
    assert window.ratings.context_budget('reader') == 120000
    window.navigate(5)
    window.request_options_toggle.setChecked(True)
    window.show()
    app.processEvents()
    window.grab().save(str(tmp_path / 'context-budget-preview.png'))
    window.close()
    reopened = desktop.Window(repo, 'reader')
    assert reopened.context_budget.currentData() == 120000
    assert reopened.session.model is None
    reopened.close()
