"""Offline acceptance for explicit dates, confirmed units, mention tags and coverage."""
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import json
import os

import pytest

from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.feed_repository import FeedRepository
from newspaper_agent.database.news_repository import NewsRepository
from newspaper_agent.preferences import validate_interest_units, active_interest_units, confirmed_topics
from newspaper_agent.sources.annotations import content_tags, routing_topics
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.contracts import EvidenceRecord, SourceDescriptor
from newspaper_agent.sources.filtering import select_evidence
from newspaper_agent.sources.query import plan_query
from newspaper_agent.sources.time_windows import in_explicit_window, scope_from_query
from newspaper_agent.research.subscriptions import route_feeds
from newspaper_agent.research.service import ResearchSources, infer_domains
from newspaper_agent.research.tools import ResearchTools
from newspaper_agent.research.catalog_smoke import EXPANDED_SOURCE_IDS
from newspaper_agent.research.session import ResearchSession
from newspaper_agent.research.news_pipeline import generate_news

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)  # HK 09:00


def unit(**changes):
    return dict(topic='自旋空间群', domain='academic', question='如何构造投影表示', purpose='博士研究',
                methods=['群论'], knowledge_level='expert', priority=5, expires_on=None, **changes)


def source(identity, *, topics=(), domains=('academic',)):
    return SourceDescriptor(identity, identity, domains, ('news',), ('*',), ('en',),
                            'rss', 'enabled', f'https://example.org/{identity}', topics=topics)


def record(identity='one', **changes):
    data = dict(id='feed:' + identity, source_id='feed', provider_id='rss', publisher='Feed',
                title='Spin space group classification', url='https://example.org/' + identity,
                kind='news', snippet='Group theory and projective representations',
                retrieved_at=NOW.isoformat(), published_at=NOW.isoformat(), is_fixture=False)
    return EvidenceRecord(**(data | changes))


@pytest.fixture
def repo(tmp_path):
    result = ResearchRepository(tmp_path / 'research.sqlite3')
    result.register('alice', 'password123')
    result.register('bob', 'password123')
    return result


@pytest.mark.parametrize('value,today,hours', [
    ('2026-10-16T17:00:00Z', True, True),  # HK 17th, UTC 16th
    ('2026-10-16T15:59:59Z', False, True),
    ('2026-10-16T01:00:00Z', False, True),
    ('2026-10-16T00:59:59Z', False, False),
    ('2026-10-17T01:00:01Z', False, False),
    ('2026-10-17', True, False),
    ('2026-10-16', False, False),
    ('2026-10-17T08:00:00', False, False),
    ('2026-10', False, False), ('2026', False, False), (None, False, False),
    ('bad', False, False),
])
def test_precise_date_boundaries(value, today, hours):
    assert in_explicit_window(value, 'today', now=NOW) is today
    assert in_explicit_window(value, '24h', now=NOW) is hours


def test_today_query_strips_time_phrase_but_keeps_focus():
    assert scope_from_query('今天的AI新闻') == 'today'
    assert scope_from_query('过去24小时 AI') == '24h'
    assert plan_query('today AI news').query == 'ai'
    assert plan_query('今天 ai 新闻').recent_days == 1
    with pytest.raises(ValueError):
        in_explicit_window(NOW.isoformat(), 'week', now=NOW)
    records = [record(), record('old', published_at='2026-10-15'), record('unknown', published_at=None)]
    chosen, decisions = select_evidence(records, 'latest news', {}, time_scope='today', now=NOW)
    assert [r.id for r in chosen] == ['feed:one']
    assert sum(d['decision'] == 'outside_time_window' for d in decisions) == 2


def test_units_confirm_before_use_account_isolation_and_undo(repo):
    proposal = repo.propose_profile('alice', {'interest_units': [unit()]}, '我研究群论', '细分研究视角')
    assert not active_interest_units(repo.profile('alice'))
    with pytest.raises(ValueError):
        repo.resolve_proposal('bob', proposal, accept=True)
    event = repo.resolve_proposal('alice', proposal, accept=True)
    assert confirmed_topics(repo.profile('alice')) == ['自旋空间群']
    assert not repo.profile('bob')['interest_units']
    repo.undo('alice', event)
    assert not repo.profile('alice')['interest_units']


def test_units_expire_without_deleting_history():
    expired = unit() | {'expires_on': '2026-10-16'}
    profile = {'interest_units': [expired], 'interests': ['AI', 'AI']}
    assert not active_interest_units(profile, today=date(2026, 10, 17))
    assert active_interest_units(profile, today=date(2026, 10, 16)) == [expired]
    assert profile['interest_units'] == [expired]
    assert confirmed_topics({'interests': ['AI', 'AI']}) == ['AI']


@pytest.mark.parametrize('changes', [
    {'priority': True}, {'priority': 6}, {'expires_on': '2026-2-3'}, {'knowledge_level': 'all'},
    {'methods': ['x'] * 7}, {'domain': 'secret'}, {'unapproved': 'value'}, {'topic': ''},
])
def test_units_reject_invalid_fields(changes):
    with pytest.raises(ValueError):
        validate_interest_units([unit() | changes])


def test_tags_have_original_spans_are_mentions_only_and_private(repo):
    article = record()
    tags = content_tags(article)
    assert {'ssg', 'classification', 'group_theory', 'projective_representation'} <= {t['concept'] for t in tags}
    for tag in tags:
        assert getattr(article, tag['field'])[tag['start']:tag['end']] == tag['quote']
        assert tag['basis'] == 'mention_only'
    assert not content_tags(record(title='a chair said hello', snippet='unrelated'))
    assert {'physics', 'math'} <= routing_topics(['SSG 群论 投影表示'])
    store = NewsRepository(repo)
    store.put_articles('alice', [article])
    assert store.records('alice', [article.id])[0].payload['content_tags'] == tags
    assert not store.records('bob', [article.id])
    assert article.payload == {}  # Annotating persistence does not mutate caller records.


def test_feed_rotation_covers_multiple_topics_and_reserves_general():
    feeds = [source('ai_' + str(i), topics=('ai',)) for i in range(14)]
    feeds += [source('zz_physics', topics=('physics',)), source('general', domains=('general',))]
    selected = route_feeds(feeds, ('academic',), ['AI', 'SSG'])
    assert len(selected) == 12
    assert {'zz_physics', 'general'} <= {s.id for s in selected}
    rotated = route_feeds(feeds, ('academic',), ['AI', 'SSG'], attempts={s.id: 100 for s in selected})
    assert {'ai_8', 'ai_9'} <= {s.id for s in rotated}
    private = source('source_custom')
    assert route_feeds(feeds + [private], ('academic',), ['AI'])[0] == private


@pytest.mark.parametrize('query', ['SSG', '群论', 'projective representations', '凝聚态物理', 'physics'])
def test_physics_topics_infer_academic_instead_of_only_generic_news(query):
    assert 'academic' in infer_domains(query, {'domains': ['news']})


def test_attempts_isolated_from_feed_freshness_and_endpoint(repo):
    store, feed = FeedRepository(repo), source('feed')
    store.save('alice', feed, (record(),), 10)
    store.record_attempt('alice', feed, 20)
    assert store.attempts('alice', [feed]) == {'feed': 20}
    assert store.load('alice', feed)[1] == 10
    assert not store.attempts('bob', [feed])
    assert not store.attempts('alice', [replace(feed, homepage='https://other.example/feed')])


def test_feedback_reason_does_not_rewrite_long_term_profile(repo):
    tools = ResearchTools(None, repo, 'alice')
    tools.begin_turn('今天暂时不想看群论')
    args = {'kind': 'not_now', 'user_quote': '今天暂时不想看群论', 'interests_add': [],
            'interests_remove': [], 'excluded_add': [], 'excluded_remove': [], 'domains': [], 'style': None}
    before = repo.profile('alice')
    result = tools._feedback(args)
    assert result['diagnosis'] == ['session_intent_only']
    assert repo.profile('alice') == before
    with pytest.raises(ValueError, match='不修改长期兴趣'):
        tools._feedback(args | {'excluded_add': ['群论']})
    with pytest.raises(ValueError, match='不修改长期档案'):
        repo.feedback('alice', 'not_now', '今天不看', changes={'excluded': ['群论']})


def test_catalog_expansion_preserves_suspended_boundaries():
    catalog = SourceCatalog.load(ROOT / 'config/sources/live.yaml')
    assert len(catalog.sources) == 54
    expanded = [s for s in catalog.sources if s.id in EXPANDED_SOURCE_IDS]
    assert len(expanded) == 21 and all(s.adapter == 'rss' for s in expanded)
    assert len([s for s in expanded if s.status == 'enabled']) == 16
    assert {s.id for s in expanded if s.status == 'suspended'} == {
        'aps_physics', 'aps_prl', 'kr36_news', 'infoq_news', 'who_news'}


def test_empty_today_does_not_blame_model(repo):
    sources = ResearchSources(repo, 'alice', ROOT / 'config/sources/live.yaml')
    session = ResearchSession(repo, 'alice', sources)
    result = generate_news(session, 'today AI', [], {'time_scope': 'today'})
    assert '所选范围内暂无新闻' in result.markdown
    assert result.metrics['model_calls'] == 0
    assert '请启用模型' not in result.markdown


def test_scoring_material_uses_units_domain_level_and_mention_tags(repo):
    repo.update_profile('alice', {'interest_units': [unit()],
                                'domain_reading_rules': ['物理：专业；AI：入门']}, '明确设置')
    seen = []
    class Model:
        def complete(self, messages, *, tools=()):
            seen.append(json.loads(messages[1].content))
            raise ValueError('offline test stop')  # Do not run interpretation or contact any network.
    sources = ResearchSources(repo, 'alice', ROOT / 'config/sources/live.yaml')
    sources.base = SourceCatalog([source('feed')])
    result = generate_news(ResearchSession(repo, 'alice', sources, Model()), 'SSG', [record()], {})
    assert len(seen) == 1 and result.metrics['scoring_calls'] == 1
    assert seen[0]['profile']['interest_units'] == [unit()]
    assert seen[0]['profile']['domain_reading_rules'] == ['物理：专业；AI：入门']
    assert '自旋空间群' in seen[0]['articles'][0]['mentioned_tags']
    assert 'quote' not in seen[0]['articles'][0]  # Span metadata is not duplicated into score output.


def test_today_subscription_uses_publication_not_fetch_date_and_compact_audit(repo):
    current = datetime.now(timezone.utc).isoformat()
    class Adapter:
        def collect(self):
            return (record(published_at=current), record('old', published_at='2020-01-01'),
                    record('unknown', published_at=None))
    sources = ResearchSources(repo, 'alice', ROOT / 'config/sources/live.yaml', factory=lambda *a, **kw: Adapter())
    sources.base = SourceCatalog([source('feed')])
    result = sources.collect_news('SSG', ['academic'], time_scope='today')
    assert [r['id'] for r in result['results']] == ['feed:one']
    assert result['coverage'] == {'selected_sources': 1, 'eligible_sources': 1,
                                  'deferred_sources': 0, 'outside_window': 2}
    audit = sources.audit()
    assert audit['time_scope'] == 'today' and 'results' not in audit and 'decisions' not in audit
    assert all('results' not in s for s in audit['searches'])


def test_failed_feed_attempt_rotates_without_refreshing_old_data(repo):
    feed = source('feed')
    store = FeedRepository(repo)
    store.save('alice', feed, (record(),), 0)
    class Failing:
        def collect(self):
            raise RuntimeError('offline failure')
    sources = ResearchSources(repo, 'alice', ROOT / 'config/sources/live.yaml', factory=lambda *a, **kw: Failing())
    sources.base = SourceCatalog([feed])
    result = sources.collect_news('SSG', ['academic'])
    assert result['failures'][0]['cached_count'] == 1
    assert store.load('alice', feed)[1] == 0
    assert store.attempts('alice', [feed])['feed'] > 0


def test_visual_date_controls_forward_exact_scope_and_interest_editor_saves(repo, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.interest_editor import InterestEditor
    app = QApplication.instance() or QApplication([])
    window = Window(repo, 'alice')
    calls = []
    monkeypatch.setattr(window.session, 'search', lambda *args, **kw: calls.append((args, kw)))
    monkeypatch.setattr(window, 'run_job', lambda job, done: job())
    for scope in ('today', '24h'):
        index = window.period.findData(scope)
        assert index >= 0
        window.period.setCurrentIndex(index)
        window.search()
        assert calls[-1][1]['time_scope'] == scope
        assert calls[-1][1]['recent_days'] is None
    editor = InterestEditor(repo, 'alice', window)
    editor.fields['topic'].setText('群论')
    editor.fields['question'].setText('投影表示如何构造')
    editor.fields['methods'].setText('群论；上同调')
    assert not repo.profile('alice')['interest_units']
    editor.save()
    assert repo.profile('alice')['interest_units'][0]['methods'] == ['群论', '上同调']
    assert not repo.profile('bob')['interest_units']
    editor.close()
    window.close()
    app.processEvents()
