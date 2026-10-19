import os
import pytest
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.model_connections import ModelConnectionRepository, WindowsSecretCodec, validate_connection


def values(**changes):
    return dict(name='离线测试连接', base_url='https://example.org/v1', model='test-model',
                homepage='https://example.org', wire_api='responses', **changes)


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_dpapi_roundtrip_and_scope():
    codec = WindowsSecretCodec()
    encrypted = codec.encrypt('dummy-test-only', 'alice:connection')
    assert b'dummy-test-only' not in encrypted
    assert codec.decrypt(encrypted, 'alice:connection') == 'dummy-test-only'
    with pytest.raises(ValueError):
        codec.decrypt(encrypted, 'bob:connection')


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_connections_persist_isolate_and_replace(tmp_path):
    repo = ResearchRepository(tmp_path / 'connections.sqlite3')
    repo.register('alice', 'password123')
    repo.register('bob', 'password123')
    store = ModelConnectionRepository(repo)
    identity = store.save('alice', values(), 'dummy-test-only')
    reopened = ModelConnectionRepository(ResearchRepository(repo.path))
    assert reopened.key('alice', identity) == 'dummy-test-only'
    assert 'secret' not in reopened.list('alice')[0]
    assert reopened.list('bob') == []
    for operation in (lambda: store.key('bob', identity), lambda: store.get('bob', identity),
                      lambda: store.delete('bob', identity), lambda: store.save('bob', values(), 'dummy', identity)):
        with pytest.raises(ValueError):
            operation()
    changed = {**values(), 'base_url': 'https://other.example/v1'}
    with pytest.raises(ValueError):
        store.save('alice', changed, identity=identity)
    store.save('alice', changed, 'dummy-replaced', identity)
    assert store.key('alice', identity) == 'dummy-replaced'
    store.save('alice', {**changed, 'model': 'second-model'}, identity=identity)
    assert store.get('alice', identity)['model'] == 'second-model'
    assert b'dummy-replaced' not in repo.path.read_bytes()
    store.delete('alice', identity)
    assert store.list('alice') == []


@pytest.mark.parametrize('url', ['http://example.org/v1', 'https://user:secret@example.org/v1',
    'https://example.org/v1?key=dummy', 'https://example.org/v1#x', 'file:///tmp/key', 'https://example.org:bad/v1'])
def test_invalid_connection_urls(url):
    with pytest.raises(ValueError):
        validate_connection({**values(), 'base_url': url})


def test_endpoint_normalization():
    assert validate_connection({**values(), 'base_url': 'https://example.org/v1/responses'})['base_url'] == 'https://example.org/v1'
    assert validate_connection({**values(), 'base_url': 'http://localhost:8080/v1'})['base_url'].startswith('http:')
    with pytest.raises(ValueError):
        validate_connection({**values(), 'base_url': 'https://example.org/v1/chat/completions'})


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_visual_connection_saved_without_request_and_restored(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QLineEdit
    from PySide6.QtGui import QFontDatabase
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.theme import STYLE
    import httpx
    def no_network(*args, **kwargs):
        raise AssertionError('No automatic model request permitted')
    monkeypatch.setattr(httpx.Client, 'post', no_network)
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setStyleSheet(STYLE)
    repo = ResearchRepository(tmp_path / 'ui.sqlite3')
    repo.register('reader', 'password123')
    window = Window(repo, 'reader')
    window.connection_name.setText('测试档案')
    window.connection_url.setText('https://example.org/v1')
    window.connection_model.setText('test-model')
    window.connection_protocol.setCurrentIndex(1)
    window.model_key.setText('dummy-ui-test-only')
    window.save_connection(False)
    identity = window.provider.currentData()
    assert identity and window.model_key.text() == ''
    assert window.model_key.echoMode() == QLineEdit.EchoMode.Password
    window.navigate(5)
    window.show()
    app.processEvents()
    window.grab().save(str(tmp_path / 'saved-connections-preview.png'))
    window.enable_model()
    assert window.session.model is not None
    assert window.session.model.client.settings.timeout is None
    assert window.session.model.max_seconds is None
    assert not hasattr(window, 'model_timeout')
    assert window.connection_client(gateway_recovery=False).settings.timeout is None
    assert os.getenv('NEWSPAPER_CONNECTION_' + identity.upper()) is None
    window.close()
    reopened = Window(repo, 'reader')
    assert reopened.provider.currentData() == identity
    assert reopened.connection_model.text() == 'test-model'
    assert reopened.model_key.text() == ''
    assert reopened.session.model is None  # Remembered, not silently making paid requests.
    reopened.enable_model()
    assert reopened.session.model is not None
    assert reopened.session.model.client.settings.timeout is None
    reopened.close()


def test_briefing_readable_preview(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from dataclasses import asdict
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.theme import STYLE
    from newspaper_agent.sources.contracts import EvidenceRecord
    from newspaper_agent.synthesis import SynthesisService
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setStyleSheet(STYLE)
    repo = ResearchRepository(tmp_path / 'preview.sqlite3')
    repo.register('reader', 'password123')
    record = EvidenceRecord('demo', 'offline', 'offline', '离线排版测试',
        '【离线排版测试】AI agents：工具调用与可靠性评估', 'https://example.org/paper', 'paper',
        '这份虚构测试资料用于检查 AI agents 简报排版。工具调用在实验环境中取得进展，但不能直接推断真实生产效果。',
        '2026-10-14', published_at='2026-10-14', content_level='abstract', is_fixture=False)
    report = asdict(SynthesisService(None).generate('AI agents', [record]))
    report['search_audit'] = {'query': 'AI agents', 'warnings': ['未配置 Tavily']}
    rid = repo.save_report('reader', 'AI agents', report)
    window = Window(repo, 'reader')
    window.refresh_reports(rid)
    window.show()
    app.processEvents()
    assert '来源速览' in window.result_status.text()
    assert '有效结果 1 条' in window.result_status.text()
    assert '未配置 Tavily' not in window.summary.toPlainText()
    window.grab().save(str(tmp_path / 'briefing-preview.png'))
    window.close()


def test_briefing_diagnostics_collapsed_not_in_body(tmp_path):
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    app = QApplication.instance() or QApplication([])
    repo = ResearchRepository(tmp_path / 'brief.sqlite3')
    repo.register('reader', 'password123')
    rid = repo.save_report('reader', 'ssg', {'knowledge_payload': {'evidence': [], 'claims': [],
        'source_conditions': [], 'follow_up': []}, 'search_audit': {'query': 'ssg', 'recent_days': 90,
        'warnings': ['未配置 Tavily', '当前选择学术研究'], 'failures': ['request_timeout']}})
    window = Window(repo, 'reader')
    window.refresh_reports(rid)
    assert '未配置 Tavily' not in window.summary.toPlainText()
    assert '主题检索词' not in window.summary.toPlainText()
    assert '未配置 Tavily' in window.search_details.toPlainText()
    assert window.search_details.isHidden()
    window.search_details_toggle.setChecked(True)
    assert not window.search_details.isHidden()
    window.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_new_api_button_opens_blank_form_and_keeps_existing_connection(tmp_path):
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.model_settings import NEW_CONNECTION
    app = QApplication.instance() or QApplication([])
    repo = ResearchRepository(tmp_path / 'new-api.sqlite3')
    repo.register('reader', 'password123')
    window = Window(repo, 'reader')
    old = window.connections.save('reader', values(), 'dummy-existing-test-only')
    window.reload_connections(old)
    window.model_key.setText('dummy-unsubmitted-test-only')
    window.new_connection_button.click()
    assert window.provider.currentData() == NEW_CONNECTION
    assert all(widget.text() == '' for widget in (window.connection_name, window.connection_url,
        window.connection_model, window.connection_homepage, window.model_key))
    assert not window.update_connection_button.isEnabled()
    assert not window.delete_connection_button.isEnabled()
    assert window.connection_protocol.currentData() == 'chat'
    window.connection_name.setText('全新服务')
    window.connection_url.setText('https://new.example.org/v1')
    window.connection_model.setText('new-test-model')
    window.model_key.setText('dummy-new-test-only')
    with pytest.raises(ValueError, match='保存连接'):
        window.connection_client()
    window.save_connection(False)
    new = window.provider.currentData()
    assert new not in (old, NEW_CONNECTION, None)
    assert len(window.connections.list('reader')) == 2
    assert window.connections.key('reader', old) == 'dummy-existing-test-only'
    assert window.connections.key('reader', new) == 'dummy-new-test-only'
    assert window.model_key.text() == ''
    window.close()
