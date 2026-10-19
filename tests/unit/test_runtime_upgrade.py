"""Regression cases for actual user-visible failures, with no paid API calls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import asdict
from pathlib import Path
from threading import Barrier, Event
from datetime import datetime, timezone
import json
import time
import pytest

from newspaper_agent.agent_core.messages import Message, ToolCall
from newspaper_agent.agent_core.runtime import RunControl, RunCancelled
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.research.service import ResearchSources
from newspaper_agent.research.session import ResearchSession
from newspaper_agent.sources.contracts import EvidenceRecord
from newspaper_agent.synthesis import SynthesisService
from newspaper_agent.sources.query import plan_query

ROOT = Path(__file__).resolve().parents[2]


def evidence(id, title='AI agents use tools'):
    return EvidenceRecord(id, id, 'rss', 'Test publisher', title, 'https://example.org/' + id,
        'news', 'AI agents use tools. Deployment is not yet established.',
        datetime.now(timezone.utc).isoformat(), published_at=datetime.now(timezone.utc).date().isoformat(),
        content_level='feed', is_fixture=False)


def test_topic_routing_parallel_completion_and_progress(tmp_path, monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    repo = ResearchRepository(tmp_path / 'parallel.sqlite3')
    repo.register('reader', 'password123')
    barrier = Barrier(3)
    class Adapter:
        def __init__(self, source):
            self.source = source
        def search(self, request):
            assert request.query == 'ai agent'
            barrier.wait(timeout=3)  # Fails if the three sources run sequentially.
            return (evidence(self.source.id),)
    sources = ResearchSources(repo, 'reader', ROOT / 'config/sources/live.yaml', factory=lambda s, **kw: Adapter(s))
    events = []
    sources.control = RunControl(events.append)
    batch = sources.search('搜索ai，agent相关的近期新闻', ['news'])
    assert len(batch['results']) == 3 and not batch['failures']
    selected = [s for s in sources.descriptors() if s.id in batch['selected_sources']]
    assert len(selected) == 3
    assert all(s.adapter == 'rss' and s.role == 'primary' and s.status == 'enabled'
               and {'ai', 'agent'} <= set(s.topics) for s in selected)
    assert sum(e['type'] == 'source_start' for e in events) == 3
    assert any(e['type'] == 'filter_end' for e in events)
    assert plan_query('搜索ai，agent相关的近期新闻').query == 'ai agent'


def test_fast_research_uses_one_planning_and_one_synthesis_call(tmp_path, monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    repo = ResearchRepository(tmp_path / 'fast.sqlite3')
    repo.register('reader', 'password123')
    item = evidence('one')
    class Adapter:
        def search(self, request):
            return (item,)
    sources = ResearchSources(repo, 'reader', ROOT / 'config/sources/live.yaml', factory=lambda s, **kw: Adapter())
    class Model:
        calls = 0
        def complete(self, messages, *, tools=()):
            self.calls += 1
            if tools:
                return Message('assistant', '', tool_calls=(ToolCall('s1', 'research_search',
                    json.dumps({'query': 'AI agent news', 'domains': ['news'], 'kind': 'news'})),))
            return Message('assistant', json.dumps({'claims': [{'statement': '智能体使用工具。',
                'conditions': [], 'uncertainty': '部署表现尚不明确。', 'status': 'unverified',
                'evidence_refs': [{'evidence_id': 'one', 'quote': 'AI agents use tools.'}]}], 'follow_up': []}))
    model = Model()
    session = ResearchSession(repo, 'reader', sources, model)
    session.fast_mode = True
    session.set_control(RunControl())
    result = session.chat('搜索 AI agent 的近期新闻')
    assert model.calls == 2
    assert '智能体使用工具' in result['markdown']
    assert session.history[-1].content == result['markdown']


def test_cancelled_turn_does_not_commit_response_or_report(tmp_path):
    repo = ResearchRepository(tmp_path / 'cancel.sqlite3')
    repo.register('reader', 'password123')
    control = RunControl()
    class Model:
        def complete(self, *args, **kwargs):
            control.cancel()
            return Message('assistant', 'Do not show this late response')
    session = ResearchSession(repo, 'reader', ResearchSources(repo, 'reader', ROOT / 'config/sources/live.yaml'), Model())
    session.set_control(control)
    with pytest.raises(RunCancelled):
        session.chat('你好')
    assert not session.history and not repo.reports('reader')


def test_chat_widgets_order_navigation_draft_preservation_and_cancel(tmp_path):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    app = QApplication.instance() or QApplication([])
    repo = ResearchRepository(tmp_path / 'ui.sqlite3')
    repo.register('reader', 'password123')
    window = Window(repo, 'reader')
    window.chat_view.clear()
    window.chat_view.add_message('assistant', '先前的回答', markdown=True)
    # Selecting an old message reproduces the cursor-position trigger from the screenshot.
    window.chat_view.messages[0]['body'].selectAll()
    window.session.model = object()
    started, release = Event(), Event()
    def slow_chat(text):
        started.set()
        release.wait(5)
        window.session.control.check()
        return {'markdown': '## 新回答\n\n- **重点**：已完成', 'report_id': None}
    window.session.chat = slow_chat
    # set_control only needs a control property on a model; use a simple fake object.
    from types import SimpleNamespace
    window.session.model = SimpleNamespace(control=None)
    window.chat_input.setPlainText('搜索 AI agent')
    window.send_chat()
    assert started.wait(2)
    window.chat_input.setPlainText('下一条还在写')
    assert [m['role'] for m in window.chat_view.messages] == ['assistant', 'user']
    assert window.chat_view.messages[-1]['text'] == '搜索 AI agent'
    assert window.central.isEnabled() and window.nav_buttons[0].isEnabled()
    window.navigate(0)
    assert window.stack.currentIndex() == 0
    window.stop_job()
    release.set()
    deadline = time.monotonic() + 5
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert not window.busy
    assert window.chat_input.toPlainText() == '下一条还在写'
    assert window.chat_view.messages[-1]['role'] == 'error'
    assert window.retry_button.isEnabled()
    window.close()


def test_editorial_grouping_preserves_all_links():
    a, b = evidence('source_a'), evidence('source_b')
    report = SynthesisService(None).generate('AI agents', [a, b])
    assert '1 件信息 · 2 条来源资料' in report.markdown
    assert '产品与智能体' in report.markdown
    assert a.url in report.markdown and b.url in report.markdown


def test_dialogue_restores_only_own_completed_turns(tmp_path):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.theme import STYLE
    from newspaper_agent.database.desktop_repository import DesktopRepository
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    repo = ResearchRepository(tmp_path / 'dialogue.sqlite3')
    repo.register('reader', 'password123')
    repo.register('other', 'password456')
    store = DesktopRepository(repo)
    messages = [{'role': 'user', 'content': '搜索 AI、agent 相关的近期新闻'},
        {'role': 'assistant', 'content': '## 界面验收样例\n\n**这不是实际新闻，只检查展示效果。**\n\n'
            '### 产品与智能体\n\n- **发生了什么**：这里显示带依据的具体变化。\n'
            '- **与你的关系**：围绕你关注的工具调用与部署。\n\n'
            '### 还缺什么\n\n来源暂时不可用时显示覆盖缺口，不补入无关新闻。'}]
    store.save_dialogue('reader', messages)
    assert store.dialogue('other') == []
    with pytest.raises(ValueError):
        store.save_dialogue('reader', [{'role': 'system', 'content': 'invalid'}])
    window = Window(repo, 'reader')
    assert len(window.session.history) == 2
    assert window.chat_view.messages[0]['role'] == 'user'
    assert '界面验收样例' in window.chat_view.toPlainText()
    window.navigate(1)
    window.model_badge.setText('界面验收 · 离线样例')
    window.show()
    for _ in range(5):
        app.processEvents()
    window.grab().save(str(tmp_path / 'chat-upgrade.png'))
    window.navigate(5)
    for _ in range(5):
        app.processEvents()
    window.grab().save(str(tmp_path / 'settings-upgrade.png'))
    window.reset_chat()
    assert store.dialogue('reader') == [] and not window.session.history
    window.close()


def test_user_time_window_survives_model_query_rewrite(tmp_path):
    from newspaper_agent.research.tools import ResearchTools
    from newspaper_agent.sources.query import recent_window
    assert recent_window('过去一个月 AI 的新闻') == 30
    assert plan_query('搜索AI agent近30天的新闻').query == 'ai agent'
    assert plan_query('过去一个月人工智能和智能体的新闻').query == 'ai agent'
    class Sources:
        def search(self, *args, **kwargs):
            assert kwargs['recent_days'] == 30
            return {'decisions': [], 'results': []}
    tools = ResearchTools(Sources(), None, 'reader')
    tools.begin_turn('过去一个月 AI 的新闻')
    tools._search({'query': 'artificial intelligence', 'domains': ['news'], 'kind': 'news'})
