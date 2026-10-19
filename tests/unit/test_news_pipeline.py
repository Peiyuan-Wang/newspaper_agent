from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import pytest

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.agent_core.runtime import RunControl, RunCancelled
from newspaper_agent.database.news_repository import NewsRepository
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.research.news_pipeline import generate_news, scoring_response
from newspaper_agent.research.service import ResearchSources
from newspaper_agent.research.session import ResearchSession
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.contracts import EvidenceRecord, SourceDescriptor

ROOT = Path(__file__).resolve().parents[2]


class Model:
    def __init__(self, *, bad_once=False, fail=False):
        self.score_calls = self.explanation_calls = 0
        self.bad_once, self.fail = bad_once, fail
        self.readers = []
    def complete(self, messages, *, tools=()):
        assert tools == ()
        data = json.loads(messages[1].content)
        if 'articles' in data:
            self.score_calls += 1
            if self.fail:
                raise RuntimeError('secret-upstream')
            if self.bad_once and self.score_calls == 1:
                return Message('assistant', '{}')
            return Message('assistant', json.dumps({'scores': [{'id': r['id'],
                'score': int(r['title'].split()[-1]) % 101} for r in data['articles']]}))
        self.explanation_calls += 1
        reader = data['writing_context']['reader_model']
        self.readers.append({'interests': reader['confirmed_interests'], **reader['expression']})
        record = data['evidence'][0]
        return Message('assistant', json.dumps({'focus': next(iter(reader['confirmed_interests']), ''), 'lead_subject': '有依据',
            'claims': [{'statement': '有依据的测试解读', 'conditions': [],
            'evidence_refs': [{'evidence_id': record['id'], 'quote': record['snippet']}],
            'uncertainty': '仅依据摘要', 'status': 'unverified'}], 'follow_up': []}, ensure_ascii=False))


@pytest.fixture
def setup(tmp_path):
    repo = ResearchRepository(tmp_path / 'news.sqlite3')
    repo.register('alice', 'password123')
    repo.register('bob', 'password123')
    sources = ResearchSources(repo, 'alice', ROOT / 'config/sources/live.yaml')
    descriptor = SourceDescriptor('feed_one', 'Feed', ('general',), ('news',), ('*',), ('en',),
        'rss', 'enabled', 'https://example.org/feed')
    sources.base = SourceCatalog([descriptor])
    model = Model()
    return repo, sources, model


def records(count):
    return [EvidenceRecord(f'feed_one:{i}', 'feed_one', 'rss', 'Feed', f'Unrelated literal words {i}',
        f'https://example.org/news/{i}', 'news', f'Original excerpt {i}',
        datetime.now(timezone.utc).isoformat(), is_fixture=False, content_level='feed') for i in range(count)]


def test_all_candidates_scored_by_ai_top_ten_interpreted_and_cached(setup):
    repo, sources, model = setup
    session = ResearchSession(repo, 'alice', sources, model)
    data = records(45)
    report = generate_news(session, 'a topic absent from every title', data, {})
    assert model.score_calls == 3 and model.explanation_calls == 10
    assert report.metrics['model_calls'] == 13  # News work does not hit the six-call chat limit.
    assert [r['interest_score'] for r in report.knowledge_payload['ranked_items']] == list(range(44, 34, -1))
    assert len(NewsRepository(repo).records('alice', [r.id for r in data])) == 45
    assert not NewsRepository(repo).records('bob', [r.id for r in data])
    assert all(x['score_state'] == 'scored' for x in NewsRepository(repo).articles('alice', [r.id for r in data]).values())
    again = generate_news(session, 'a topic absent from every title', data, {})
    assert model.score_calls == 3 and model.explanation_calls == 10 and again.metrics['model_calls'] == 0


def test_style_change_reinterprets_but_interest_change_rescores(setup):
    repo, sources, model = setup
    session = ResearchSession(repo, 'alice', sources, model)
    data = records(2)
    generate_news(session, 'latest news', data, {})
    repo.update_profile('alice', {'expression_rules': ['术语先解释'], 'style': 'brief'}, 'explicit test')
    generate_news(session, 'latest news', data, {})
    assert model.score_calls == 1 and model.explanation_calls == 4
    assert model.readers[-1]['expression_rules'] == ['术语先解释']
    repo.update_profile('alice', {'interests': ['new interest']}, 'explicit test')
    generate_news(session, 'latest news', data, {})
    assert model.score_calls == 2
    assert model.readers[-1]['interests'] == ['new interest']


def test_editorial_revision_rewrites_interpretation_without_rescoring(setup, monkeypatch):
    import newspaper_agent.research.news_pipeline as pipeline
    repo, sources, model = setup
    session = ResearchSession(repo, 'alice', sources, model)
    data = records(1)
    monkeypatch.setattr(pipeline, 'framework_revision', lambda: 'editorial-a')
    generate_news(session, 'latest news', data, {})
    monkeypatch.setattr(pipeline, 'framework_revision', lambda: 'editorial-b')
    generate_news(session, 'latest news', data, {})
    assert model.score_calls == 1 and model.explanation_calls == 2


def test_bad_score_schema_repaired_without_fake_scores(setup):
    repo, sources, _ = setup
    model = Model(bad_once=True)
    report = generate_news(ResearchSession(repo, 'alice', sources, model), 'latest news', records(2), {})
    assert model.score_calls == 2 and report.knowledge_payload['scored_count'] == 2
    with pytest.raises(ValueError):
        scoring_response('{"scores":[{"id":0,"score":true}]}', 1)
    with pytest.raises(ValueError):
        scoring_response('{"scores":[{"id":0,"score":101}]}', 1)


def test_failed_or_missing_model_keeps_articles_pending_not_zero(setup):
    repo, sources, _ = setup
    model = Model(fail=True)
    report = generate_news(ResearchSession(repo, 'alice', sources, model), 'latest news', records(42), {})
    assert model.score_calls == 1 and not model.explanation_calls
    assert report.knowledge_payload['pending_count'] == 42
    assert all(i['interest_score'] is None for i in report.knowledge_payload['ranked_items'])
    assert 'secret-upstream' not in str(asdict(report))
    assert len(NewsRepository(repo).records('alice', [r.id for r in records(42)])) == 42


def test_large_library_report_does_not_embed_screening_records(setup):
    repo, sources, model = setup
    report = generate_news(ResearchSession(repo, 'alice', sources, model), 'latest news', records(642), {})
    assert model.score_calls == 33 and model.explanation_calls == 10
    sources.last_search = {'query': 'latest news', 'decisions': [{'id': 'x'}] * 5136,
                           'results': [asdict(r) for r in records(642)]}
    sources.search_history = [sources.last_search]
    audit = sources.audit()
    assert 'decisions' not in audit and 'results' not in audit
    assert 'decisions' not in audit['searches'][0]
    session = ResearchSession(repo, 'alice', sources, model)
    saved = session._save('latest news', report, audit=audit)
    assert saved['report_id'] and len(json.dumps(saved, ensure_ascii=False)) < 1000000
    assert len(saved['knowledge_payload']['evidence']) == 10


def test_cancel_does_not_begin_scoring(setup):
    repo, sources, model = setup
    session = ResearchSession(repo, 'alice', sources, model)
    session.set_control(RunControl(max_seconds=None))
    session.control.cancel()
    with pytest.raises(RunCancelled):
        generate_news(session, 'latest news', records(2), {})
    assert model.score_calls == 0


def test_partial_scoring_is_checkpointed_but_not_called_full_library_top_ten(setup):
    repo, sources, _ = setup
    class Partial(Model):
        def complete(self, messages, *, tools=()):
            if 'articles' in json.loads(messages[1].content) and self.score_calls:
                raise RuntimeError('offline failure after a completed batch')
            return super().complete(messages, tools=tools)
    model = Partial()
    report = generate_news(ResearchSession(repo, 'alice', sources, model), 'latest news', records(42), {})
    assert report.knowledge_payload['scored_count'] == 20
    assert not report.knowledge_payload['ranking_complete'] and model.explanation_calls == 0
    recovered = Model()
    report = generate_news(ResearchSession(repo, 'alice', sources, recovered), 'latest news', records(42), {})
    assert recovered.score_calls == 2  # Only the remaining 22, not the first completed 20.
    assert report.knowledge_payload['ranking_complete'] and recovered.explanation_calls == 10


def test_ranked_desktop_titles_scores_and_collapsed_interpretations(setup):
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    repo, sources, model = setup
    session = ResearchSession(repo, 'alice', sources, model)
    report = generate_news(session, 'latest news', records(12), {})
    result = session._save('latest news', report)
    app = QApplication.instance() or QApplication([])
    window = Window(repo, 'alice')
    window.refresh_reports(result['report_id'])
    assert window.brief_stack.currentIndex() == 1
    assert len(window.news_explanation_panels) == 10
    toggle, panel = window.news_explanation_panels[0]
    assert not toggle.isChecked() and panel.isHidden()
    toggle.setChecked(True)
    assert not panel.isHidden()
    window.close()


def test_four_signal_pipeline_caches_importance_and_switches_columns_without_models(setup):
    from PySide6.QtWidgets import QApplication, QPushButton
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.research.importance_scoring import DIMENSIONS
    from newspaper_agent.database.news_feedback import NewsFeedback
    from dataclasses import replace
    repo, sources, _ = setup
    descriptors = [SourceDescriptor('feed_one', 'Primary', ('news',), ('news',), ('*',), ('en',),
        'rss', 'enabled', 'https://primary.example/feed', role='primary'),
        SourceDescriptor('feed_two', 'Other', ('news',), ('news',), ('*',), ('en',),
        'rss', 'enabled', 'https://other.example/feed')]
    sources.base = SourceCatalog(descriptors)
    class SignalsModel(Model):
        def complete(self, messages, *, tools=()):
            data = json.loads(messages[1].content)
            if 'articles' not in data:
                return super().complete(messages, tools=tools)
            self.score_calls += 1
            return Message('assistant', json.dumps({'scores': [
                {'id': r['id'], 'score': 80-r['id'], 'importance': dict.fromkeys(DIMENSIONS, 90),
                 'quote': r['snippet']} for r in data['articles']]}))
    model = SignalsModel()
    data = [replace(r, published_at=datetime.now(timezone.utc).isoformat()) for r in records(12)]
    data[-1] = replace(data[0], id='feed_two:copy', source_id='feed_two', url='https://other.example/story')
    data[0] = replace(data[0], title='This very specific event received broad reporting on a new research result')
    data[-1] = replace(data[-1], title=data[0].title)
    session = ResearchSession(repo, 'alice', sources, model)
    report = generate_news(session, 'latest news', data, {})
    payload = report.knowledge_payload
    assert all(payload['ranking']['sections'][section] for section in ('personal', 'major', 'hot'))
    assert len(payload['ranked_items']) == 10
    assert model.score_calls == 1 and model.explanation_calls == 10
    rid = session._save('latest news', report)['report_id']
    feedback = NewsFeedback(repo)
    eid = payload['ranked_items'][0]['evidence_id']
    feedback.vote('alice', rid, eid, -1)
    second = generate_news(session, 'latest news', data, {})
    assert model.score_calls == 1  # Feedback reorders locally without another AI scoring batch.
    assert second.knowledge_payload['ranked_items'][0]['signals']['importance'] == 90
    app = QApplication.instance() or QApplication([])
    window = Window(repo, 'alice')
    window.refresh_reports(rid)
    before = model.score_calls, model.explanation_calls
    for section in ('major', 'hot', 'personal'):
        window.news_section.setCurrentIndex(window.news_section.findData(section))
        assert window.news_explanation_panels
    assert (model.score_calls, model.explanation_calls) == before
    assert any(b.text() == '👍 有帮助' for b in window.findChildren(QPushButton))
    window.resize(1440, 980)
    window.show()
    app.processEvents()
    preview = repo.path.parent / 'four-signals-preview.png'
    assert window.grab().save(str(preview))
    print('Ranking preview:', preview)
    window.close()


def test_pending_news_still_visible_in_personal_column(setup):
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    repo, sources, _ = setup
    session = ResearchSession(repo, 'alice', sources, None)
    report = generate_news(session, 'latest news', records(3), {})
    rid = session._save('latest news', report)['report_id']
    app = QApplication.instance() or QApplication([])
    window = Window(repo, 'alice')
    window.refresh_reports(rid)
    assert len(window.news_explanation_panels) == 3
    window.news_section.setCurrentIndex(window.news_section.findData('hot'))
    assert not window.news_explanation_panels
    window.close()


def test_semantic_vectors_run_before_selection_without_extra_chat_requests(setup):
    from dataclasses import replace
    from newspaper_agent.database.semantic_repository import SemanticRepository
    repo, sources, model = setup
    repo.update_profile('alice', {'interests': ['alpha']}, 'explicit test')
    class Encoder:
        version,dimension = 'pipeline-test-space', 2
        def encode(self,texts): return [[1.,0.] for _ in texts]
    session = ResearchSession(repo,'alice',sources,model,text_encoder=Encoder())
    data = [replace(r,title='alpha '+str(i)) for i,r in enumerate(records(12))]
    report = generate_news(session,'latest news',data,{})
    payload = report.knowledge_payload
    assert payload['semantic_profile']['status']=='active'
    assert payload['semantic_profile']['dimension']==2
    assert payload['ranked_items'][0]['signals']['semantic_match']['category']=='未分类 · alpha'
    assert model.score_calls==1 and model.explanation_calls==10
    store = SemanticRepository(repo)
    state = store.state('alice')
    assert len(store.documents('alice',state['encoder'],[r.id for r in data]))==12
    assert all(i['signals']['importance'] is None for i in payload['ranked_items'])
