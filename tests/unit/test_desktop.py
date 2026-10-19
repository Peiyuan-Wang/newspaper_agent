import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.desktop_repository import DesktopRepository


def test_key_input_session_only_and_connection_test(tmp_path, monkeypatch):
    pytest.importorskip('PySide6')
    import time
    from PySide6.QtWidgets import QApplication, QLineEdit
    import newspaper_agent.desktop.app as desktop
    from newspaper_agent.model.settings import load_provider_settings
    app = QApplication.instance() or QApplication([])
    repo = ResearchRepository(tmp_path / 'keys.sqlite3')
    repo.register('reader', 'password123')
    window = desktop.Window(repo, 'reader')
    provider = window.provider.currentText()
    settings = load_provider_settings(desktop.ROOT / 'model_config.yaml', provider)
    monkeypatch.setenv(settings.api_key_env, 'original-test-value')
    calls = []
    class FakeClient:
        def complete(self, messages, *, tools):
            calls.append((messages, tools))
    def create(name, **options):
        assert name == provider
        assert os.getenv(settings.api_key_env) == 'session-test-value'
        return FakeClient()
    monkeypatch.setattr(desktop, 'create_research_model', create)
    assert window.model_key.echoMode() == QLineEdit.EchoMode.Password
    window.model_key.setText('session-test-value')
    window.show_key.setChecked(True)
    assert window.model_key.echoMode() == QLineEdit.EchoMode.Normal
    window.enable_model()
    assert window.session.model is not None
    assert not calls  # Enabling does not send a request.
    assert window.model_key.text() == ''
    assert window.model_key.echoMode() == QLineEdit.EchoMode.Password
    window.test_model_connection()
    deadline = time.monotonic() + 5
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert not window.busy and len(calls) == 1
    assert calls[0][0][0].content == 'Reply with OK only.'
    assert '连接成功' in window.connection_state.text()
    window.close()
    assert os.getenv(settings.api_key_env) == 'original-test-value'
    assert 'session-test-value' not in repo.path.read_bytes().decode('latin1')


def test_ratings_ownership_and_validation(tmp_path):
    repo = ResearchRepository(tmp_path / 'test.sqlite3')
    repo.register('alice', 'password123')
    repo.register('bob', 'password456')
    rid = repo.save_report('alice', 'question', {'knowledge_payload': {'evidence': [{'id': 'e1'}]}})
    store = DesktopRepository(repo)
    store.rate('alice', rid, 'e1', 4)
    store.rate('alice', rid, 'e1', 5)
    assert store.ratings('alice', rid) == {'e1': 5}
    for args in [('bob', rid, 'e1', 3), ('alice', rid, 'missing', 3), ('alice', rid, 'e1', True), ('alice', rid, 'e1', 6)]:
        with pytest.raises(ValueError):
            store.rate(*args)
    with pytest.raises(ValueError):
        store.ratings('bob', rid)


def test_window_navigation_profile_report(tmp_path):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.theme import STYLE
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    repo = ResearchRepository(tmp_path / 'ui.sqlite3')
    repo.register('reader', 'password123')
    window = Window(repo, 'reader')
    assert window.stack.count() == 7
    assert window.provider.count() > 0
    window.interests.setPlainText('robotics\nAI safety')
    window.save_profile()
    assert repo.profile('reader')['interests'] == ['robotics', 'AI safety']
    window.feedback_text.setPlainText('缺少机器人落地案例')
    window.save_feedback()
    assert len(repo.events('reader')) == 2
    rid = repo.save_report('reader', 'robotics', {'markdown': '# 测试简报', 'knowledge_payload': {'evidence': []}})
    window.refresh_reports(rid)
    assert window.report_id == rid
    for i in range(6):
        window.navigate(i)
        assert window.stack.currentIndex() == i
    window.show()
    app.processEvents()
    window.close()


def test_history_hides_irrelevant_evidence_without_deleting_saved_report(tmp_path):
    pytest.importorskip('PySide6')
    from dataclasses import asdict
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.theme import STYLE
    from newspaper_agent.sources.contracts import EvidenceRecord
    from newspaper_agent.synthesis import SynthesisService
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    repo = ResearchRepository(tmp_path / 'history.sqlite3')
    repo.register('reader', 'password123')
    def evidence(id, title, snippet, date):
        return EvidenceRecord(id, 'crossref', 'crossref', '测试资料 · 离线回归', title,
            'https://example.org/' + id, 'paper', snippet, '2026-10-14',
            published_at=date, content_level='abstract', is_fixture=False)
    records = [evidence('bird', '为了天空的小鸟', '无关条目', '2012-05'),
        evidence('good', '人工智能智能体的工具调用研究', '这篇测试资料讨论 AI agents 的工具调用。结果仅来自实验环境，尚不能推断生产效果。' * 20, '2026-10-01')]
    data = asdict(SynthesisService(None).generate('了解一下ai，agent的大新闻', records))
    data['search_audit'] = {'domains': ['academic'], 'query': '了解一下ai，agent的大新闻'}
    rid = repo.save_report('reader', '了解一下ai，agent的大新闻', data)
    window = Window(repo, 'reader')
    window.refresh_reports(rid)
    assert '为了天空的小鸟' not in window.summary.toPlainText()
    assert '有效结果 1 条' in window.result_status.text()
    assert len(repo.report('reader', rid)['report']['knowledge_payload']['evidence']) == 2
    window.reader.setCurrentIndex(1)
    window.show()
    app.processEvents()
    window.grab().save(str(tmp_path / 'quality-preview.png'))
    window.close()


def test_background_search_and_rating_ui(tmp_path):
    pytest.importorskip('PySide6')
    import time
    from PySide6.QtWidgets import QApplication, QComboBox
    from newspaper_agent.desktop.app import Window
    app = QApplication.instance() or QApplication([])
    repo = ResearchRepository(tmp_path / 'background.sqlite3')
    repo.register('reader', 'password123')
    window = Window(repo, 'reader')
    def fake_search(query, domains, **options):
        rid = repo.save_report('reader', query, {'markdown': '可追溯简报',
            'knowledge_payload': {'evidence': [{'id': 'e1', 'title': '测试新闻',
                'url': 'https://example.org/news', 'snippet': '仅用于离线测试',
                'source_id': 'test', 'provider_id': 'rss', 'publisher': 'Test',
                'kind': 'news', 'retrieved_at': '2026-10-14', 'is_fixture': False}]}})
        return {'report_id': rid}
    window.session.search = fake_search
    window.search()
    assert window.busy
    deadline = time.monotonic() + 5
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert not window.busy
    score = window.article_scroll.widget().findChild(QComboBox)
    score.setCurrentIndex(4)
    assert window.ratings.ratings('reader', window.report_id) == {'e1': 4}
    window.close()
