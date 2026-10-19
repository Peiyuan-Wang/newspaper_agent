"""Reproducible local bandit tests, no model, network, or production user data."""
from dataclasses import asdict
from pathlib import Path
import math
import os
import random

import pytest

from newspaper_agent.recommendation import (DEFAULTS, POLICY_VERSION, validate_settings, neighbour,
    probabilities, select_news, anchors)
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.recommendation_repository import RecommendationRepository
from newspaper_agent.sources.contracts import EvidenceRecord, SourceDescriptor
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.research.service import ResearchSources
from newspaper_agent.research.session import ResearchSession
from newspaper_agent.research.news_pipeline import generate_news

ROOT = Path(__file__).resolve().parents[2]
PROFILE = {'interests': [], 'excluded': [], 'domains': ['academic'], 'interest_units': [
    {'topic': '群论', 'domain': 'academic', 'question': '投影表示如何构造', 'purpose': '研究',
     'methods': [], 'knowledge_level': 'expert', 'priority': 5, 'expires_on': None}]}
SETTINGS = DEFAULTS | {'enabled': True}


def record(i, title='Group theory'):
    return EvidenceRecord(f'feed:{i}', 'feed', 'rss', 'Feed', title, f'https://example.org/{i}',
                          'news', title, '2026-10-17T01:00:00Z', is_fixture=False)


def dataset():
    records = [record(i) for i in range(8)] + [record(8, 'Projective representations'),
        record(9, 'Group cohomology'), record(10, 'Group cohomology applications'), record(11, 'LLM agents')]
    scores = {r.id: 95 - i for i, r in enumerate(records)}
    return records, scores


@pytest.fixture
def repo(tmp_path):
    result = ResearchRepository(tmp_path / 'bandit.sqlite3')
    result.register('alice', 'password123')
    result.register('bob', 'password123')
    return result


@pytest.mark.parametrize('changes', [
    {'temperature': 0}, {'temperature': float('nan')}, {'epsilon': .4}, {'radius': -1},
    {'learning_rate': 1}, {'enabled': 1}, {'epsilon': True}, {'unknown': 0},
])
def test_settings_are_bounded_and_finite(changes):
    with pytest.raises(ValueError):
        validate_settings(DEFAULTS | changes)


def test_default_disabled_and_explicit_query_keep_exact_top_ten():
    records, scores = dataset()
    for settings, query in ((DEFAULTS, 'latest news'), (SETTINGS, 'SSG'),
                            (SETTINGS | {'epsilon': 0}, 'latest news')):
        selected, decisions = select_news(records, scores, PROFILE, {'feed': ('academic',)}, settings, query=query)
        assert selected == records[:10] and not decisions


def test_temperature_flattens_without_unstable_exponentials():
    cold, hot = probabilities([.9, .3], .02), probabilities([.9, .3], 2)
    assert cold[0] > hot[0] > .5
    assert sum(cold) == pytest.approx(1)
    assert all(math.isfinite(p) and p > 0 for p in cold)


def test_exact_radius_domain_and_exclusion_gates():
    adjacent = record(0, 'Projective representations')
    assert neighbour(adjacent, PROFILE, {'feed': ('academic',)}, .6)
    assert not neighbour(adjacent, PROFILE, {'feed': ('academic',)}, 0)
    assert not neighbour(adjacent, PROFILE, {'feed': ('medical',)}, 1)
    assert not neighbour(record(1, 'LLM agent'), PROFILE, {'feed': ('academic',)}, 1)
    assert not neighbour(adjacent, PROFILE | {'excluded': ['projective']}, {'feed': ('academic',)}, 1)
    assert not anchors(PROFILE | {'interest_units': [PROFILE['interest_units'][0] | {'expires_on': '2000-01-01'}]})


def test_exploration_quota_probability_reproducibility_and_no_duplicates():
    records, scores = dataset()
    before = dict(scores)
    selected, decisions = select_news(records, scores, PROFILE, {'feed': ('academic',)}, SETTINGS, rng=random.Random(17))
    again, same = select_news(records, scores, PROFILE, {'feed': ('academic',)}, SETTINGS, rng=random.Random(17))
    assert selected == again and decisions == same and scores == before
    assert selected[:8] == records[:8] and len({r.id for r in selected}) == 10
    exploratory = [d for d in decisions.values() if d['role'] == 'explore']
    assert len(exploratory) == 2 and 'feed:11' not in {r.id for r in selected}
    assert [d['candidate_count'] for d in exploratory] == [3, 2]
    assert all(0 < d['probability'] <= 1 for d in exploratory)


def test_no_eligible_neighbours_falls_back_without_fake_exploration():
    records, scores = dataset()
    selected, decisions = select_news(records, scores, PROFILE, {'feed': ('medical',)}, SETTINGS)
    assert selected == records[:10] and not any(d['role'] == 'explore' for d in decisions.values())
    selected, decisions = select_news(records, scores, PROFILE, {'feed': ('academic',)}, SETTINGS | {'radius': 0})
    assert selected == records[:10]
    assert all(d['role'] == 'core' for d in decisions.values())


def test_low_scores_unknown_tags_and_small_lists_cannot_force_exploration():
    records, scores = dataset()
    scores.update({r.id: 39 for r in records[8:]})
    selected, decisions = select_news(records, scores, PROFILE, {'feed': ('academic',)}, SETTINGS)
    assert selected == records[:10] and all(d['role'] == 'core' for d in decisions.values())
    unknown = PROFILE | {'interest_units': [PROFILE['interest_units'][0] | {'topic': '未知细分主题'}]}
    _, decisions = select_news(records, scores, unknown, {'feed': ('academic',)}, SETTINGS)
    assert not any(d['role'] == 'explore' for d in decisions.values())
    for size in (0, 1, 4):
        selected, decisions = select_news(records[:size], scores, PROFILE, {'feed': ('academic',)}, SETTINGS)
        assert selected == records[:size] and not decisions


def test_explicit_feedback_changes_conditional_probability():
    records, scores = dataset()
    class First:
        def random(self):
            return 0
    _, decisions = select_news(records, scores, PROFILE, {'feed': ('academic',)}, SETTINGS, rng=First())
    action = decisions['feed:8']
    learned = {(action['context'], action['tag']): {'sum': 30., 'count': 30}}
    _, positive = select_news(records, scores, PROFILE, {'feed': ('academic',)}, SETTINGS, learned, rng=First())
    learned[(action['context'], action['tag'])]['sum'] = 0.
    _, negative = select_news(records, scores, PROFILE, {'feed': ('academic',)}, SETTINGS, learned, rng=First())
    assert positive['feed:8']['probability'] > decisions['feed:8']['probability'] > negative['feed:8']['probability']


def test_context_keys_change_with_task_not_unconfirmed_guesses():
    original = anchors(PROFILE)[0]['key']
    assert anchors(PROFILE | {'purpose': '更改研究用途'})[0]['key'] != original
    assert anchors(PROFILE | {'knowledge_level': 'beginner'})[0]['key'] != original
    assert anchors(PROFILE | {'pending_proposal': 'AI'})[0]['key'] == original


def saved_plan(repo, user='alice'):
    records, scores = dataset()
    selected, decisions = select_news(records, scores, PROFILE, {'feed': ('academic',)}, SETTINGS, rng=random.Random(1))
    payload = {'evidence': [asdict(r) for r in selected], 'exploration': {'enabled': True, 'active': True},
               'ranked_items': [{'evidence_id': r.id, 'recommendation': decisions[r.id]} for r in selected]}
    identity = repo.save_report(user, 'latest news', {'knowledge_payload': payload})
    store = RecommendationRepository(repo)
    store.record_plan(user, identity)
    target = next(r.id for r in selected if decisions[r.id]['role'] == 'explore')
    return identity, target, decisions[target]


def test_feedback_is_explicit_isolated_replaceable_and_withdrawable(repo):
    store = RecommendationRepository(repo)
    assert store.settings('alice') == DEFAULTS and not store.learned('alice')
    store.configure('alice', SETTINGS)
    assert store.settings('bob') == DEFAULTS
    rid, eid, decision = saved_plan(repo)
    with pytest.raises(ValueError, match='已展示'):
        store.feedback('alice', rid, eid, 'useful')
    with pytest.raises(ValueError):
        store.mark_displayed('bob', rid, [eid])
    store.mark_displayed('alice', rid, [eid])
    key = decision['context'], decision['tag']
    for _ in range(3):
        store.record_plan('alice', rid)  # Reopening never resets an existing display or duplicates learning.
        store.feedback('alice', rid, eid, 'useful')
    assert store.learned('alice')[key] == {'sum': 1., 'count': 1}
    assert not store.learned('bob')
    store.feedback('alice', rid, eid, 'irrelevant')
    assert store.learned('alice')[key] == {'sum': 0., 'count': 1}
    store.feedback('alice', rid, eid, 'clear')
    assert not store.learned('alice')
    store.feedback('alice', rid, eid, 'useful')
    store.reset('alice')
    assert not store.learned('alice') and store.feedback_kind('alice', rid, eid) == 'clear'
    store.configure('alice', DEFAULTS)
    with pytest.raises(ValueError, match='关闭时不学习'):
        store.feedback('alice', rid, eid, 'useful')


def test_same_article_feedback_across_reports_does_not_multiply_and_disabled_can_withdraw(repo):
    store = RecommendationRepository(repo)
    store.configure('alice', SETTINGS)
    first, eid, decision = saved_plan(repo)
    second, same, _ = saved_plan(repo)
    assert eid == same
    for rid in (first, second):
        store.mark_displayed('alice', rid, [eid])
        store.feedback('alice', rid, eid, 'useful')
    key = decision['context'], decision['tag']
    assert store.learned('alice')[key] == {'sum': 1., 'count': 1}
    store.configure('alice', DEFAULTS)
    store.feedback('alice', first, eid, 'clear')
    store.feedback('alice', second, eid, 'clear')
    assert not store.learned('alice')


def test_missing_feedback_generic_rating_and_core_feedback_do_not_train(repo):
    from newspaper_agent.database.desktop_repository import DesktopRepository
    store = RecommendationRepository(repo)
    store.configure('alice', SETTINGS)
    rid, eid, _ = saved_plan(repo)
    store.mark_displayed('alice', rid, [eid])
    DesktopRepository(repo).rate('alice', rid, eid, 5)
    assert not store.learned('alice')
    store.mark_displayed('alice', rid, ['feed:0'])
    with pytest.raises(ValueError):
        store.feedback('alice', rid, 'feed:0', 'irrelevant')
    assert not store.learned('alice')


def test_pipeline_saves_probability_then_only_interprets_selected_ten(repo):
    from newspaper_agent.agent_core.messages import Message
    import json
    class Model:
        def __init__(self):
            self.calls = []
        def complete(self, messages, *, tools=()):
            data = json.loads(messages[1].content)
            self.calls.append(data)
            if 'articles' in data:
                return Message('assistant', json.dumps({'scores': [{'id': r['id'], 'score': 90-r['id']} for r in data['articles']]}))
            raise ValueError('offline interpretation stop')
    repo.update_profile('alice', PROFILE, 'explicit test profile')
    store = RecommendationRepository(repo)
    store.configure('alice', SETTINGS)
    source = SourceDescriptor('feed', 'Feed', ('academic',), ('news',), ('*',), ('en',),
        'rss', 'enabled', 'https://example.org/feed')
    sources = ResearchSources(repo, 'alice', ROOT / 'config/sources/live.yaml')
    sources.base = SourceCatalog([source])
    session = ResearchSession(repo, 'alice', sources, Model())
    session.article_reader = type('Reader', (), {'read': lambda self, record: {'status': 'unavailable'}})()
    records, _ = dataset()
    result = generate_news(session, 'latest news', records, {})
    payload = result.knowledge_payload
    assert len(payload['ranked_items']) == 10 and payload['exploration']['selected'] == 2
    assert result.metrics['scoring_calls'] == 1 and result.metrics['interpretation_calls'] <= 10
    assert all(item['interest_score'] == 90-int(item['evidence_id'].split(':')[1]) for item in payload['ranked_items'])
    saved = session._save('latest news', result)
    assert not store.learned('alice')  # Generated is not displayed or positively received.
    item = next(i for i in payload['ranked_items'] if i['recommendation']['role'] == 'explore')
    store.mark_displayed('alice', saved['report_id'], [item['evidence_id']])
    store.feedback('alice', saved['report_id'], item['evidence_id'], 'useful')
    assert sum(s['count'] for s in store.learned('alice').values()) == 1
    store.feedback('alice', saved['report_id'], item['evidence_id'], 'clear')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QComboBox
    from newspaper_agent.desktop.app import Window
    app = QApplication.instance() or QApplication([])
    window = Window(repo, 'alice')
    window.refresh_reports(saved['report_id'])
    choice = next(w for w in window.findChildren(QComboBox) if w.findData('clear') >= 0 and w.findData('useful') >= 0)
    choice.setCurrentIndex(choice.findData('useful'))
    assert sum(s['count'] for s in store.learned('alice').values()) == 1
    window.close()
    app.processEvents()


def test_visual_settings_are_opt_in_and_persist_per_account(repo):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    app = QApplication.instance() or QApplication([])
    window = Window(repo, 'alice')
    assert not window.exploration_enabled.isChecked()
    window.exploration_enabled.setChecked(True)
    window.exploration_fields['temperature'].setValue(.35)
    from PySide6.QtWidgets import QPushButton
    next(b for b in window.findChildren(QPushButton) if b.text() == '保存拓展设置').click()
    store = RecommendationRepository(repo)
    assert store.settings('alice')['temperature'] == .35 and store.settings('alice')['enabled']
    assert not store.settings('bob')['enabled']
    window.close()
    app.processEvents()
