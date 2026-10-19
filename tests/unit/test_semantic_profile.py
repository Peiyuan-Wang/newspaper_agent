from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import math
import os
from pathlib import Path
from types import SimpleNamespace
import pytest

from newspaper_agent.semantic_profile import (seed_specs, make_slots, evolve, match, route, unit,
    dot, identity, constrain, MAX_ANGLE)
from newspaper_agent.preferences import validate_interest_units
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.news_repository import NewsRepository
from newspaper_agent.database.news_feedback import NewsFeedback
from newspaper_agent.database.semantic_repository import SemanticRepository
from newspaper_agent.research.semantic_personalization import SemanticPersonalizer, semantic_adjustments
from newspaper_agent.sources.contracts import EvidenceRecord, SourceDescriptor

NOW = datetime(2026,10,18,12,tzinfo=timezone.utc)


def interest(topic, category, **changes):
    return dict({'topic': topic, 'category': category, 'domain': 'academic', 'question': '', 'purpose': '',
                 'methods': [], 'knowledge_level': 'expert', 'priority': 3, 'expires_on': None}, **changes)


class Encoder:
    version, dimension = 'offline-test-space', 3
    def __init__(self): self.seen = []
    def encode(self, texts):
        self.seen.extend(texts)
        return [unit([1., .4 if 'application' in t else 0., 0.]) if 'alpha' in t else
                unit([0., 0., 1.]) for t in texts]


@pytest.fixture
def repo(tmp_path):
    repository = ResearchRepository(tmp_path/'semantic.sqlite3')
    repository.register('alice','password123')
    repository.register('bob','password123')
    return repository


def records():
    return [EvidenceRecord('one','official','rss','Official','alpha application',
        'https://example.org/one','news','alpha application',NOW.isoformat(),
        published_at=NOW.isoformat(),is_fixture=False)]


def source_map():
    s = SourceDescriptor('official','Official',('academic',),('news',),('*',),('en',),'rss','enabled','https://example.org/feed')
    return {s.id:s}


def setup_profile(repo):
    repo.update_profile('alice', {'interest_units': [interest('alpha','用户固定大类'), interest('beta','另一个大类')]}, 'explicit test')
    rows = records()
    NewsRepository(repo).put_articles('alice', rows)
    rid = repo.save_report('alice','test', {'knowledge_payload': {'evidence':[asdict(r) for r in rows]}})
    return rows, rid, repo.profile('alice')


def test_hierarchy_contract_legacy_and_expiration():
    row = interest('alpha','Research')
    assert validate_interest_units([row])[0]['category'] == 'Research'
    old = dict(row)
    del old['category']
    assert 'category' not in validate_interest_units([old])[0]
    with pytest.raises(ValueError):
        validate_interest_units([dict(row, category='a'*61)])
    specs = seed_specs({'interest_units':[dict(row,expires_on='2000-01-01')], 'interests':['alpha','other']})
    assert [s['topic'] for s in specs] == ['other']


def test_categories_never_encoded_or_merged_and_similar_children_share_slot():
    profile = {'interest_units': [interest('a','Category A'),interest('b','Category A'),interest('c','Category B')]}
    specs = seed_specs(profile)
    assert all('Category' not in s['text'] for s in specs)
    slots = make_slots(specs,{s['id']:[1.,0.] for s in specs})
    assert len(slots) == 2
    assert sorted(len(s['topics']) for s in slots) == [1,2]
    assert all('vector' not in s for s in specs)  # Category is metadata; vectors belong to child slots.


def test_complete_link_does_not_merge_distant_endpoints_by_chain():
    specs = seed_specs({'interest_units':[interest(str(i),'one') for i in range(3)]})
    vectors = {s['id']:[math.cos(math.radians(int(s['topic'])*40)),math.sin(math.radians(int(s['topic'])*40))] for s in specs}
    assert len(make_slots(specs,vectors)) == 2


def test_feedback_moves_only_matching_category_and_is_replayable():
    specs = seed_specs({'interest_units':[interest('alpha','A'),interest('beta','B')]})
    slots = make_slots(specs,{s['id']:([1.,0.,0.] if s['topic']=='alpha' else [0.,0.,1.]) for s in specs})
    x = unit([1.,.5,0.])
    row = {'vector':x,'domains':['academic'],'kind':'up','at':NOW.isoformat()}
    evolved, skipped = evolve(slots,[row],now=NOW)
    a = next(s for s in evolved if s['category']=='A')
    b = next(s for s in evolved if s['category']=='B')
    assert dot(a['vector'],x) > dot(a['anchor'],x)
    assert b['vector'] == b['anchor'] and b['observations'] == 0 and skipped == 0
    assert evolve(slots,[row],now=NOW)[0] == evolved
    restored,_ = evolve(slots,[],now=NOW)
    assert all(s['vector'] == s['anchor'] and s['affinity_bias']==0 for s in restored)
    negative,_ = evolve(slots,[dict(row,kind='down')],now=NOW)
    assert dot(next(s for s in negative if s['category']=='A')['vector'],x) < dot(a['anchor'],x)


def test_collinear_negative_lowers_affinity_without_fabricating_rotation():
    specs = seed_specs({'interest_units':[interest('alpha','A')]})
    slots = make_slots(specs,{specs[0]['id']:[1.,0.]})
    document = {'vector':[1.,0.],'domains':['academic']}
    negative,_ = evolve(slots,[dict(document,kind='down',at=NOW.isoformat())],now=NOW)
    assert negative[0]['vector'] == [1.,0.]
    assert match(negative,document)['score'] < match(slots,document)['score']


def test_ambiguous_category_and_unrelated_feedback_do_not_move_slots():
    specs = seed_specs({'interest_units':[interest('a','A'),interest('b','B')]})
    slots = make_slots(specs,{s['id']:[1.,0.] for s in specs})
    assert not route(slots,[1.,0.],['academic'])
    assert not route(slots,[0.,1.],['academic'])
    assert not route(slots,[1.,0.],['medical'])
    evolved, skipped = evolve(slots,[{'vector':[1.,0.],'domains':['academic'],'kind':'up','at':NOW.isoformat()}],now=NOW)
    assert skipped == 1 and all(s['observations'] == 0 for s in evolved)


def test_angle_cap_weak_click_and_time_decay():
    anchor = [1.,0.]
    constrained = constrain(anchor,[0.,1.])
    assert math.degrees(math.acos(dot(anchor,constrained))) == pytest.approx(MAX_ANGLE)
    specs = seed_specs({'interest_units':[interest('a','A')]})
    slots = make_slots(specs,{specs[0]['id']:anchor})
    row = {'vector':unit([1.,1.]),'domains':['academic'],'at':NOW.isoformat(),'kind':'up'}
    liked,_ = evolve(slots,[row],now=NOW)
    clicked,_ = evolve(slots,[dict(row,kind='open')],now=NOW)
    old,_ = evolve(slots,[dict(row,at='2020-01-01T00:00:00+00:00')],now=NOW)
    assert liked[0]['angle'] > clicked[0]['angle'] > old[0]['angle']
    for bad in ([0.,0.],[float('nan'),1.],[float('inf'),0.]):
        with pytest.raises(ValueError): unit(bad)


def test_persisted_learning_undo_accounts_and_embedding_cache(repo):
    rows,rid,profile = setup_profile(repo)
    encoder = Encoder()
    engine = SemanticPersonalizer(repo,'alice',encoder)
    baseline, matches,_ = engine.build(profile,rows,source_map(),now=NOW)
    calls = len(encoder.seen)
    feedback = NewsFeedback(repo)
    feedback.vote('alice',rid,'one',1)
    learned,changed,_ = engine.build(profile,rows,source_map(),now=NOW)
    assert len(encoder.seen) == calls
    assert changed['one']['score'] > matches['one']['score']
    assert SemanticRepository(repo).state('alice')['slots'] == learned['slots']
    assert SemanticRepository(repo).state('bob') is None
    assert not SemanticRepository(repo).documents('bob',engine.version,['one'])
    assert all('用户固定大类' not in t for t in encoder.seen)
    feedback.vote('alice',rid,'one',0)
    restored,_,_ = engine.build(profile,rows,source_map(),now=NOW)
    assert restored['slots'] == baseline['slots']
    feedback.opened('alice',rid,'one')
    clicked,_,_ = engine.build(profile,rows,source_map(),now=NOW)
    feedback.opened('alice',rid,'one')
    repeated,_,_ = engine.build(profile,rows,source_map(),now=NOW)
    assert clicked['slots'] == repeated['slots']
    feedback.clear('alice')
    assert SemanticRepository(repo).state('alice') is None


def test_switching_encoder_space_rebuilds_instead_of_mixing_vectors(repo):
    rows,rid,profile = setup_profile(repo)
    first = Encoder()
    SemanticPersonalizer(repo,'alice',first).build(profile,rows,source_map(),now=NOW)
    second = Encoder()
    second.version = 'different-model-space'
    state,_,_ = SemanticPersonalizer(repo,'alice',second).build(profile,rows,source_map(),now=NOW)
    assert second.seen and state['encoder'].startswith('different-model-space')


def test_actual_ranking_uses_vectors_without_changing_other_signals(repo):
    rows,rid,profile = setup_profile(repo)
    session = SimpleNamespace(repository=repo,user='alice',text_encoder=Encoder(),control=None)
    adjustments,status,matches = semantic_adjustments(session,rows,profile,source_map(),{'one':50})
    assert status['status'] == 'active' and matches['one']['category']=='用户固定大类'
    assert -20 <= adjustments['one'] <= 20
    from newspaper_agent.news_ranking import build_signals
    before = build_signals(rows,{'one':50},source_map(),{},now=NOW)['one']
    after = build_signals(rows,{'one':50},source_map(),{},adjustments,now=NOW)['one']
    assert before['relevance'] != after['relevance']
    assert all(before[k] == after[k] for k in ('credibility','importance','heat'))
    SemanticRepository(repo).configure('alice',False)
    assert semantic_adjustments(session,rows,profile,source_map(),{'one':50})[1]['status']=='disabled'


def test_missing_model_and_bad_vectors_report_fallback(repo):
    rows,rid,profile = setup_profile(repo)
    session = SimpleNamespace(repository=repo,user='alice',control=None)
    assert semantic_adjustments(session,rows,profile,source_map(),{'one':50})[1]['status']=='model_not_ready'
    class Bad(Encoder):
        def encode(self,texts): return [[float('nan')]*3 for _ in texts]
    session.text_encoder = Bad()
    adjustments,status,_ = semantic_adjustments(session,rows,profile,source_map(),{'one':50})
    assert adjustments is None and status['status']=='semantic_unavailable'
    assert SemanticRepository(repo).state('alice') is None


def test_editor_saves_category_and_view_does_not_start_network(repo):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.interest_editor import InterestEditor
    from newspaper_agent.desktop.semantic_profile import SemanticProfileDialog
    app = QApplication.instance() or QApplication([])
    window = Window(repo,'alice')
    editor = InterestEditor(repo,'alice',window)
    editor.fields['topic'].setText('投影表示')
    editor.category.setCurrentText('凝聚态理论')
    editor.save()
    assert repo.profile('alice')['interest_units'][0]['category']=='凝聚态理论'
    dialog = SemanticProfileDialog(window)
    assert dialog.tree.topLevelItem(0).text(0)=='凝聚态理论'
    assert not window.busy and not dialog.rebuild.isEnabled()
    dialog.enabled.setChecked(False)
    assert not SemanticRepository(repo).enabled('alice')
    assert SemanticRepository(repo).enabled('bob')
    dialog.close()
    window.close()
