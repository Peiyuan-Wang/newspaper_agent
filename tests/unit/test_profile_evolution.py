import json
from pathlib import Path
import pytest
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.research.tools import ResearchTools
from newspaper_agent.synthesis import SynthesisService
from newspaper_agent.agent_core.messages import Message
from newspaper_agent.sources.contracts import EvidenceRecord


@pytest.fixture
def repo(tmp_path):
    repo = ResearchRepository(tmp_path / 'personal.sqlite3')
    repo.register('alice', 'password123')
    repo.register('bob', 'password456')
    return repo


def test_pending_confirmation_is_scoped_versioned_and_reversible(repo):
    id = repo.propose_profile('alice', {'interests': ['Agent工具调用'], 'purpose': '学习架构',
        'output_format': 'table', 'expression_rules': ['先讲具体变化']}, '我关注Agent工具调用', '归纳用户明确方向')
    assert repo.profile('alice')['interests'] == []
    assert repo.proposals('bob') == []
    with pytest.raises(ValueError):
        repo.resolve_proposal('bob', id, accept=True)
    event = repo.resolve_proposal('alice', id, accept=True)
    assert repo.profile('alice')['output_format'] == 'table'
    assert not repo.proposals('alice')
    repo.undo('alice', event)
    assert repo.profile('alice')['interests'] == [] and repo.profile('alice')['output_format'] == 'cards'


def test_stale_proposal_cannot_overwrite_newer_feedback(repo):
    id = repo.propose_profile('alice', {'interests': ['old']}, 'old', '旧建议')
    repo.update_profile('alice', {'interests': ['new']}, '用户新偏好')
    with pytest.raises(ValueError, match='档案已改变'):
        repo.resolve_proposal('alice', id, accept=True)
    assert repo.profile('alice')['interests'] == ['new']
    repo.resolve_proposal('alice', id, accept=False)
    assert not repo.proposals('alice')


def test_chat_proposal_and_explicit_confirmation(repo):
    tools = ResearchTools(None, repo, 'alice')
    tools.begin_turn('我希望用表格解释Agent机制')
    suggestion = tools.registry().get('propose_profile').invoke({'changes_json': json.dumps({'output_format': 'table'}),
        'user_quote': '我希望用表格解释Agent机制', 'reason': '用户选择表格结构'})
    assert repo.profile('alice')['output_format'] == 'cards'
    tools.begin_turn('确认AI是不是会调用工具？')
    with pytest.raises(ValueError):
        tools._confirm({'proposal_id': suggestion['proposal_id'], 'decision': 'accept', 'user_quote': tools.user_text})
    tools.begin_turn('确认档案建议')
    result = tools._confirm({'proposal_id': suggestion['proposal_id'], 'decision': 'accept', 'user_quote': tools.user_text})
    assert result['status'] == 'accepted' and repo.profile('alice')['output_format'] == 'table'


def test_multiple_proposals_need_specific_selection(repo):
    ids = [repo.propose_profile('alice', {'purpose': word}, word, '用途候选') for word in ('学习', '决策')]
    tools = ResearchTools(None, repo, 'alice')
    tools.begin_turn('确认档案建议')
    with pytest.raises(ValueError, match='有多项'):
        tools._confirm({'proposal_id': ids[0], 'decision': 'accept', 'user_quote': tools.user_text})
    assert repo.profile('alice')['purpose'] == ''


def test_profile_defaults_and_new_fields_are_not_shared(repo):
    profile = repo.profile('alice')
    profile['glossary'].append('not-shared')
    assert repo.profile('bob')['glossary'] == []
    repo.update_profile('alice', {'glossary': ['Agent = 能使用工具的系统'], 'knowledge_level': 'beginner'}, '用户确认术语约定')
    assert repo.profile('bob')['knowledge_level'] == 'intermediate'
    with pytest.raises(ValueError):
        repo.propose_profile('alice', {'api_key': 'forbidden'}, '原话', '无效')


def test_reader_preferences_reach_model_and_table_preserves_conditions(tmp_path):
    record = EvidenceRecord('e1', 'test', 'rss', 'Test', 'Agent study', 'https://example.org/a',
        'paper', 'Agents use tools only in a test environment.', '2026-10-14', is_fixture=False)
    class Model:
        def complete(self, messages, *, tools=()):
            reader = json.loads(messages[1].content)['reader']
            assert reader['glossary'] == ['Agent = 可使用工具的系统']
            assert reader['knowledge_level'] == 'beginner'
            assert 'private_token' not in reader
            return Message('assistant', json.dumps({'claims': [{'statement': '智能体可使用工具。',
                'conditions': ['仅测试环境'], 'uncertainty': '生产效果未知', 'status': 'unverified',
                'evidence_refs': [{'evidence_id': 'e1', 'quote': 'Agents use tools'}]}], 'follow_up': []}))
    profile = {'glossary': ['Agent = 可使用工具的系统'], 'knowledge_level': 'beginner',
        'output_format': 'table', 'private_token': 'never-send'}
    report = SynthesisService(Model()).generate('有什么结果', [record], profile=profile)
    assert '| 重点 |' in report.markdown and '仅测试环境' in report.markdown and '生产效果未知' in report.markdown
    assert '|---|---|---|---|\n| ' in report.markdown  # Table rows are contiguous.
    offline = SynthesisService(None).generate('Agent', [record], profile=profile)
    assert '| 主题与原文 |' in offline.markdown
    pytest.importorskip('PySide6')
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QTextTable, QFontDatabase
    from newspaper_agent.desktop.theme import STYLE
    from newspaper_agent.desktop.rich_text import RichBrowser
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    view = RichBrowser()
    view.setMarkdown(report.markdown)
    tables = [f for f in view.document().rootFrame().childFrames() if isinstance(f, QTextTable)]
    assert len(tables) == 1 and tables[0].columns() == 4 and tables[0].rows() == 2
    view.resize(1100, 700)
    view.show()
    app.processEvents()
    view.grab().save(str(tmp_path / 'personalized-table.png'))
    view.close()


def test_desktop_interest_interview_and_confirmation(repo):
    pytest.importorskip('PySide6')
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    app = QApplication.instance() or QApplication([])
    repo.propose_profile('alice', {'purpose': '研究Agent工具调用', 'output_format': 'table'}, '我研究Agent', '研究用途')
    window = Window(repo, 'alice')
    assert window.proposal_list.count() == 1
    window.resolve_profile_proposal(True)
    assert window.proposal_list.count() == 0
    assert window.purpose.text() == '研究Agent工具调用'
    window.start_profile_interview()
    assert window.stack.currentIndex() == 1
    assert '最多3个' in window.chat_input.toPlainText()
    window.close()
