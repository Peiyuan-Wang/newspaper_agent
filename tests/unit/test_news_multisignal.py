import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import pytest

from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.news_feedback import NewsFeedback
from newspaper_agent.news_ranking import (DEFAULT_WEIGHTS, build_signals, rank_sections,
    recommendation_score, validate_weights, credibility, groups, article_key)
from newspaper_agent.research.importance_scoring import parse_importance, DIMENSIONS
from newspaper_agent.sources.contracts import EvidenceRecord, SourceDescriptor

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 18, 8, tzinfo=timezone.utc)


def source(identity='official', host='official.example', role='primary', domains=('news',)):
    return SourceDescriptor(identity, identity, domains, ('news',), ('*',), ('en',),
                            'rss', 'enabled', f'https://{host}/feed', role=role)


def article(identity='a', src='official', **changes):
    record = EvidenceRecord(identity, src, 'rss', src, 'New artificial intelligence agent model release',
        f'https://news.example/{identity}', 'news', 'The agent model is now publicly available.',
        NOW.isoformat(), published_at=NOW.isoformat(), content_level='feed', is_fixture=False)
    return replace(record, **changes)


@pytest.fixture
def store(tmp_path):
    repo = ResearchRepository(tmp_path / 'feedback.sqlite3')
    for name in ('alice', 'bob'):
        repo.register(name, 'password123')
    return NewsFeedback(repo)


def save(store, rows, user='alice'):
    return store.repo.save_report(user, 'test', {'knowledge_payload': {'evidence': [asdict(r) for r in rows]}})


def test_votes_replace_withdraw_isolate_and_use_stable_url(store):
    first = article()
    rid = save(store, [first])
    second = replace(first, id='other-snapshot', url=first.url + '?utm_source=test')
    other = save(store, [second])
    assert article_key(first) == article_key(second)
    store.vote('alice', rid, first.id, 1)
    assert store.state('alice', other)[second.id]['vote'] == 1
    store.vote('alice', other, second.id, -1)
    assert store.state('alice', rid)[first.id]['vote'] == -1
    for user, report, eid, value in [('bob', rid, first.id, 1), ('alice', rid, 'bad', 1),
                                     ('alice', rid, first.id, True), ('alice', rid, first.id, 2)]:
        with pytest.raises(ValueError):
            store.vote(user, report, eid, value)
    store.vote('alice', rid, first.id, 0)
    assert store.state('alice', other)[second.id]['vote'] == 0


def test_click_is_weak_and_repeat_never_amplifies_interest(store):
    a, b = article(), article('b')
    rid = save(store, [a, b])
    profile = {'interests': ['agent']}
    assert store.adjustments('alice', [a, b], profile)[b.id] == 0
    store.opened('alice', rid, a.id)
    weak = store.adjustments('alice', [b], profile)[b.id]
    assert 0 < weak < 1
    for _ in range(5):
        store.opened('alice', rid, a.id)
    assert store.adjustments('alice', [b], profile)[b.id] == weak
    assert store.state('alice', rid)[a.id]['clicks'] == 6
    store.vote('alice', rid, a.id, -1)
    assert store.adjustments('alice', [b], profile)[b.id] < 0
    store.vote('alice', rid, a.id, 1)
    assert store.adjustments('alice', [b], profile)[b.id] > weak
    assert store.adjustments('alice', [b], {'interests': ['unrelated']})[b.id] == 0
    store.configure('alice', DEFAULT_WEIGHTS, False)
    assert store.adjustments('alice', [b], profile) == {}
    store.clear('bob')
    assert store.state('alice', rid)[a.id]['vote'] == 1
    store.clear('alice')
    assert store.state('alice', rid)[a.id] == {'vote': 0, 'clicks': 0}


def test_impression_idempotent_and_account_scoped(store):
    a = article()
    rid = save(store, [a])
    for _ in range(2):
        store.impression('alice', rid, a.id, 'personal', 0, 'session')
    with store.repo._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM news_impressions').fetchone()[0] == 1
    with pytest.raises(ValueError):
        store.impression('bob', rid, a.id, 'personal', 0, 'session')


def test_importance_quote_and_bounded_rubric():
    a = article()
    row = {'id': 0, 'score': 12, 'importance': dict.fromkeys(DIMENSIONS, 80), 'quote': 'publicly available'}
    plain, assessed = parse_importance(json.dumps({'scores': [row]}), [a])
    assert json.loads(plain) == {'scores': [{'id': 0, 'score': 12}]}
    assert assessed[0]['score'] == 80
    for bad in [dict(row, quote='invented evidence'), dict(row, importance=dict.fromkeys(DIMENSIONS, True)),
                dict(row, importance=dict.fromkeys(DIMENSIONS, 101))]:
        with pytest.raises(ValueError):
            parse_importance(json.dumps({'scores': [bad]}), [a])
    for value in ['{"scores":[],"scores":[]}', '{"scores":[],"extra":0}', '[]']:
        with pytest.raises(ValueError):
            parse_importance(value, [a])
    _, unknown = parse_importance('{"scores":[{"id":0,"score":12}]}', [a])
    assert unknown[0]['score'] is None


def test_credibility_is_source_prior_not_vote_or_fact_verification():
    a = article()
    official = credibility(a, source())
    forum = credibility(a, source('forum', 'news.ycombinator.com', 'discovery'))
    assert official['score'] > forum['score']
    assert official['fact_verified'] is False and official['independent_verification'] is None


def test_grouping_organizations_not_feed_count_and_dates_are_strict():
    rows = [article('a', 'bbc_a'), article('b', 'bbc_b'), article('c', 'other')]
    sources = {'bbc_a': source('bbc_a', 'feeds.bbc.co.uk'), 'bbc_b': source('bbc_b', 'bbc.com'),
               'other': source('other', 'other.example')}
    grouped = groups(rows, sources)
    assert len(grouped['a']['orgs']) == 2
    signals = build_signals(rows, dict.fromkeys(['a', 'b', 'c'], 50), sources, {}, now=NOW)
    assert signals['a']['heat'] == 45
    _, sections, _ = rank_sections(rows, signals, DEFAULT_WEIGHTS, 10)
    assert len(sections['hot']) == 1  # One story, not three copies.
    stale = replace(rows[0], published_at='2026-09-01')
    assert not build_signals([stale], {'a': 90}, sources, {}, now=NOW)['a']['today']
    unknown = replace(stale, published_at=None)
    assert not build_signals([unknown], {'a': 90}, sources, {}, now=NOW)['a']['recent_major']
    distinct = replace(rows[2], title='New artificial intelligence agent model release 2')
    assert groups([rows[0], distinct], sources)['a']['key'] != groups([rows[0], distinct], sources)['c']['key']


def test_three_columns_reserve_public_budget_and_global_group_dedup():
    rows = [article(str(i), title=f'Distinct item {i}') for i in range(15)]
    signals = build_signals(rows, {r.id: 95-int(r.id)*5 for r in rows}, {'official': source()}, {}, now=NOW)
    for i in ('13', '14'):
        signals[i].update(importance=95, heat=90, recent_24h=True)
        signals[i]['heat_detail']['publishers'] = 3
    # Opposing winners from the same group must still use one interpretation.
    signals['14']['group'] = signals['13']['group']
    signals['14']['heat'] = 95
    selected, sections, scores = rank_sections(rows, signals, DEFAULT_WEIGHTS, 10)
    assert len(selected) == 10
    assert len({signals[r.id]['group'] for r in selected}) == 10
    assert sections['major'] and sections['hot']
    assert sections['personal'][0] == '0'
    assert scores[sections['major'][0]]['major'] > 75
    assert recommendation_score({'relevance': 80, 'credibility': 60, 'importance': None, 'heat': None}, [75,15,7,3]) == 69
    for bad in [dict(DEFAULT_WEIGHTS, personal=[100, 1, 0, 0]), dict(DEFAULT_WEIGHTS, hot=[True,20,10,69])]:
        with pytest.raises(ValueError):
            validate_weights(bad)


@pytest.mark.parametrize('age,eligible', [(0,True),(60,True),(86400,True),(86401,False),(-1,False)])
def test_hot_rolling_window_crosses_hong_kong_midnight(age, eligible):
    now = datetime(2026,10,18,16,11,tzinfo=timezone.utc)
    stamp = (now-timedelta(seconds=age)).isoformat()
    rows = [article('a','first',published_at=stamp),article('b','second',published_at=stamp)]
    sources = {'first':source('first','one.example'),'second':source('second','two.example')}
    signals = build_signals(rows, {'a':60,'b':60}, sources, {}, now=now)
    assert signals['a']['recent_24h'] is eligible
    _,sections,_ = rank_sections(rows,signals,DEFAULT_WEIGHTS,10)
    assert bool(sections['hot']) is eligible


@pytest.mark.parametrize('stamp',[None,'2026-10-19','2026-10-19T00:00:00','bad'])
def test_hot_never_guesses_missing_precise_timestamp(stamp):
    signals = build_signals([article(published_at=stamp)], {'a':60}, {'official':source()}, {}, now=NOW)
    assert not signals['a']['recent_24h']


def test_public_collection_lane_survives_specialized_domain(store):
    from newspaper_agent.research.service import ResearchSources
    from newspaper_agent.sources.catalog import SourceCatalog
    visited = []
    class Adapter:
        def __init__(self, s): self.s = s
        def collect(self):
            visited.append(self.s.id)
            return (article(self.s.id, self.s.id),)
    service = ResearchSources(store.repo, 'alice', ROOT / 'config/sources/live.yaml', factory=lambda s, **kw: Adapter(s))
    service.base = SourceCatalog([source('physics_'+str(i), domains=('academic',)) for i in range(15)] +
        [source('bbc_world'), source('fed_press'), source('nasa_news'), source('nist_news')])
    result = service.collect_news('physics', ['academic'], include_public=True, recent_days=90)
    assert len(visited) == 12 and len(result['public_source_ids']) == 4
    assert set(result['public_source_ids']) <= set(visited)


def test_card_votes_and_actual_outbound_open(store, monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QMainWindow
    from newspaper_agent.desktop.news_interactions import interaction_row
    import newspaper_agent.desktop.app as desktop
    app = QApplication.instance() or QApplication([])
    class Window(QMainWindow):
        busy = False
        def local(self, fn): return fn()
    window = Window()
    a = article()
    window.repo, window.user, window.report_id = store.repo, 'alice', save(store, [a])
    body = QWidget(window)
    layout = QVBoxLayout(body)
    accepted = [False]
    monkeypatch.setattr(desktop, 'open_link', lambda url: accepted[0])
    interaction_row(window, layout, asdict(a), {'vote': 0, 'clicks': 0})
    up, down, original = body.findChildren(QPushButton)
    up.click()
    assert up.isChecked() and not down.isChecked()
    down.click()
    assert down.isChecked() and not up.isChecked()
    down.click()
    assert store.state('alice', window.report_id)[a.id]['vote'] == 0
    original.click()
    assert store.state('alice', window.report_id)[a.id]['clicks'] == 0
    accepted[0] = True
    original.click()
    assert store.state('alice', window.report_id)[a.id]['clicks'] == 1
    window.close()
