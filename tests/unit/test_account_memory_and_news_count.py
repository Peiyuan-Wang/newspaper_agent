"""Local security and configurable news count: no mail/model/production database."""
import base64
import json
import os
import random
from dataclasses import asdict
from pathlib import Path

import pytest

from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.account_security import AccountSecurity, digest
from newspaper_agent.database.desktop_repository import DesktopRepository
from newspaper_agent.desktop.account_access import RememberedLogin
from newspaper_agent.news_options import validate_news_limit
from newspaper_agent.recommendation import select_news, DEFAULTS


@pytest.fixture
def repo(tmp_path):
    repository = ResearchRepository(tmp_path / 'test.sqlite3')
    repository.register('alice', 'old-password')
    repository.register('bob', 'bob-password')
    return repository


class Codec:
    def encrypt(self, text, scope):
        return (scope + '\n' + text[::-1]).encode()
    def decrypt(self, value, scope):
        prefix, text = value.decode().rsplit('\n', 1)
        if prefix != scope:
            raise ValueError('bad scope')
        return text[::-1]


def test_remembered_device_and_name_only_do_not_store_password_or_token(repo):
    remembered = RememberedLogin(repo, Codec())
    remembered.save('alice', 'old-password', True)
    data = remembered.read()
    token = Codec().decrypt(base64.b64decode(data['token']), remembered.scope)
    assert remembered.restore() == 'alice'
    assert 'old-password' not in remembered.path.read_text() and token not in remembered.path.read_text()
    assert token.encode() not in repo.path.read_bytes()
    remembered.save('bob', 'bob-password', False)
    assert remembered.read() == {'user': 'bob'} and remembered.restore() is None
    assert AccountSecurity(repo).restore(token) is None
    remembered.forget()
    assert not remembered.path.exists()


def test_remembered_expiry_corruption_and_user_tamper_fail_closed(repo, monkeypatch):
    import newspaper_agent.database.account_security as security
    remembered = RememberedLogin(repo, Codec())
    remembered.save('alice', 'old-password', True)
    original = remembered.path.read_text()
    data = json.loads(original)
    data['user'] = 'bob'
    remembered.path.write_text(json.dumps(data))
    assert remembered.restore() is None
    remembered.path.write_text(original)
    now = security.time.time()
    monkeypatch.setattr(security.time, 'time', lambda: now + 31 * 86400)
    assert remembered.restore() is None
    remembered.path.write_text('not json')
    assert remembered.restore() is None
    remembered.path.write_text(json.dumps({'user': 'alice', 'token': '!!!'}))
    assert remembered.restore() is None


def test_codec_failure_revokes_new_token_without_plaintext_fallback(repo):
    class Broken(Codec):
        def encrypt(self, text, scope):
            raise ValueError('offline failure')
    remembered = RememberedLogin(repo, Broken())
    with pytest.raises(ValueError, match='不会保存明文'):
        remembered.save('alice', 'old-password', True)
    assert not remembered.path.exists()
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM remembered_devices').fetchone()[0] == 0


def test_recovery_is_single_use_replaces_old_code_revokes_devices_and_keeps_profile(repo):
    security = AccountSecurity(repo)
    repo.update_profile('alice', {'interests': ['群论']}, 'explicit')
    with pytest.raises(ValueError):
        security.recovery_code('alice', 'wrong-password')
    old = security.recovery_code('alice', 'old-password')
    code = security.recovery_code('alice', 'old-password')
    token = security.issue_device('alice', 'old-password')
    for user, key in (('alice', old), ('bob', code), ('unknown', code)):
        with pytest.raises(ValueError, match='账号或恢复码不正确'):
            security.reset_password(user, key, 'new-password')
    assert security.restore(token) == 'alice'
    assert code.replace('-', '').encode() not in repo.path.read_bytes()
    security.reset_password('alice', code.lower(), 'new-password')
    assert not repo.authenticate('alice', 'old-password') and repo.authenticate('alice', 'new-password')
    assert security.restore(token) is None
    assert repo.profile('alice')['interests'] == ['群论']
    assert repo.authenticate('bob', 'bob-password')
    with pytest.raises(ValueError):
        security.reset_password('alice', code, 'another-password')


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI only')
def test_real_windows_dpapi_remembered_login_in_temporary_database(repo):
    remembered = RememberedLogin(repo)
    remembered.save('alice', 'old-password', True)
    assert remembered.restore() == 'alice'
    remembered.forget()
    assert remembered.restore() is None


@pytest.mark.parametrize('value', [0, -1, 101, True, 10., '20', None])
def test_invalid_news_count_is_rejected(value):
    with pytest.raises(ValueError):
        validate_news_limit(value)


def test_news_count_is_independent_per_user(repo):
    store = DesktopRepository(repo)
    assert store.news_limit('alice') == 10
    store.set_news_limit('alice', 35)
    assert store.news_limit('alice') == 35 and store.news_limit('bob') == 10
    with pytest.raises(ValueError):
        store.set_news_limit('unknown', 35)


def test_news_pipeline_number_and_budget_expand_beyond_ten(tmp_path):
    from newspaper_agent.agent_core.messages import Message
    from newspaper_agent.research.news_pipeline import generate_news
    from newspaper_agent.research.session import ResearchSession
    from newspaper_agent.research.service import ResearchSources
    from newspaper_agent.sources.catalog import SourceCatalog
    from newspaper_agent.sources.contracts import SourceDescriptor, EvidenceRecord
    repository = ResearchRepository(tmp_path / 'news.sqlite3')
    repository.register('alice', 'old-password')
    class Model:
        def __init__(self):
            self.scored = 0
        def complete(self, messages, *, tools=()):
            data = json.loads(messages[1].content)
            if 'articles' in data:
                self.scored += 1
                return Message('assistant', json.dumps({'scores': [{'id': r['id'], 'score': int(r['title'].split()[-1])} for r in data['articles']]}))
            row = data['evidence'][0]
            return Message('assistant', json.dumps({'focus': '', 'lead_subject': '测试对象', 'claims': [{
                'statement': '测试对象发布了新版本', 'conditions': [], 'uncertainty': '', 'status': 'unverified',
                'evidence_refs': [{'evidence_id': row['id'], 'quote': row['snippet']}]}], 'follow_up': []}))
    sources = ResearchSources(repository, 'alice', Path(__file__).resolve().parents[2] / 'config/sources/live.yaml')
    sources.base = SourceCatalog([SourceDescriptor('feed', 'Feed', ('general',), ('news',), ('*',), ('en',),
        'rss', 'enabled', 'https://example.org/feed')])
    model = Model()
    session = ResearchSession(repository, 'alice', sources, model)
    records = [EvidenceRecord(f'feed:{i}', 'feed', 'rss', 'Feed', f'News {i}', f'https://example.org/{i}',
        'news', '测试对象发布新版本。', '2026-10-18T01:00:00Z', is_fixture=False) for i in range(35)]
    for count, expected in ((1, 1), (25, 25), (100, 35)):
        report = generate_news(session, 'latest news', records, {}, max_news=count)
        assert len(report.knowledge_payload['ranked_items']) == expected
        assert report.knowledge_payload['requested_limit'] == count
        assert [r['interest_score'] for r in report.knowledge_payload['ranked_items']] == list(range(34, 34-expected, -1))
        assert report.knowledge_payload['scored_count'] == 35
    assert model.scored == 2  # Changing the output limit does not rescore the whole library.


def test_exploration_quota_uses_requested_number_not_ten():
    from newspaper_agent.sources.contracts import EvidenceRecord
    records = [EvidenceRecord(f'feed:{i}', 'feed', 'rss', 'Feed', 'Group theory', f'https://example.org/{i}',
        'news', '群论', '2026-10-18T01:00:00Z', is_fixture=False) for i in range(40)]
    selected, decisions = select_news(records, {r.id: 90 for r in records},
        {'interests': ['群论'], 'domains': ['academic']}, {'feed': ('academic',)},
        DEFAULTS | {'enabled': True}, max_news=25, rng=random.Random(2))
    assert len(selected) == 25 and sum(d['role'] == 'explore' for d in decisions.values()) == 5


def test_login_prefills_name_and_number_button_saves_without_editing_config(repo, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    from newspaper_agent.desktop.app import Login, Window
    app = QApplication.instance() or QApplication([])
    RememberedLogin(repo, Codec()).save('alice', 'old-password', False)
    login = Login(repo)
    assert login.account.text() == 'alice' and not login.keep_login.isChecked()
    window = Window(repo, 'alice')
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *a, **kw: (25, True))
    window.news_limit_button.click()
    assert window.news_limit == 25 and DesktopRepository(repo).news_limit('alice') == 25
    calls = []
    monkeypatch.setattr(window.session, 'search', lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(window, 'run_job', lambda action, done: action())
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No)
    window.search()
    assert not calls
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    window.search()
    assert calls[0]['max_news'] == 25
    login.close()
    window.close()
    app.processEvents()


def test_one_hundred_body_snapshots_compact_report_but_keep_full_interpretations(repo, monkeypatch):
    from dataclasses import replace
    from newspaper_agent.agent_core.messages import Message
    from newspaper_agent.synthesis.contracts import Report
    from newspaper_agent.sources.contracts import EvidenceRecord, SourceDescriptor
    from newspaper_agent.sources.catalog import SourceCatalog
    from newspaper_agent.research.service import ResearchSources
    from newspaper_agent.research.session import ResearchSession
    import newspaper_agent.research.news_pipeline as pipeline
    source = SourceDescriptor('feed', 'Feed', ('general',), ('news',), ('*',), ('en',),
        'rss', 'enabled', 'https://example.org/feed')
    sources = ResearchSources(repo, 'alice', Path(__file__).resolve().parents[2] / 'config/sources/live.yaml')
    sources.base = SourceCatalog([source])
    class Model:
        def complete(self, messages, *, tools=()):
            data = json.loads(messages[1].content)
            return Message('assistant', json.dumps({'scores': [{'id': r['id'], 'score': 90} for r in data['articles']]}))
    monkeypatch.setattr(pipeline, 'document_for', lambda *a, **kw: {})
    monkeypatch.setattr(pipeline, 'prepare_reading', lambda record, *a, **kw:
        (replace(record, snippet='A' * 16000), {'status': 'body_read', 'model_calls': 0}))
    class Synthesis:
        def __init__(self, *a):
            pass
        def generate(self, question, records, **kw):
            return Report('', {'evidence': [asdict(records[0])], 'claims': [
                {'id': f'c{i}', 'statement': f'测试主体的新进展{i}', 'conditions': [], 'uncertainty': '',
                 'evidence_refs': [{'evidence_id': records[0].id, 'quote': 'A' * 1600} for _ in range(4)]}
                for i in range(2)]}, (), {'model_calls': 0, 'validation_passed': True})
    monkeypatch.setattr(pipeline, 'SynthesisService', Synthesis)
    session = ResearchSession(repo, 'alice', sources, Model())
    records = [EvidenceRecord(f'feed:{i}', 'feed', 'rss', 'Feed', f'News {i}', f'https://example.org/{i}',
        'news', 'original summary', '2026-10-18T01:00:00Z', is_fixture=False) for i in range(100)]
    report = pipeline.generate_news(session, 'latest news', records, {}, max_news=100)
    assert report.knowledge_payload['evidence_storage'] == 'compact_original_with_interpretation_refs'
    assert report.knowledge_payload['claim_storage'] == 'news_interpretations'
    assert len(report.knowledge_payload['ranked_items']) == 100
    saved = session._save('latest news', report)
    assert len(json.dumps(saved, ensure_ascii=False)) < 1000000
    iid = report.knowledge_payload['ranked_items'][0]['interpretation_id']
    with repo._connect() as db:
        row = db.execute('SELECT content FROM news_interpretations WHERE user_id=? AND id=?', ('alice', iid)).fetchone()
    assert json.loads(row['content'])['knowledge_payload']['evidence'][0]['snippet'] == 'A' * 16000
    assert len(json.loads(row['content'])['knowledge_payload']['claims'][0]['evidence_refs']) == 4
