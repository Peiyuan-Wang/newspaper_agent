from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import pytest

from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.feed_repository import FeedRepository
from newspaper_agent.research.service import ResearchSources
from newspaper_agent.research.subscriptions import route_feeds
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.contracts import SourceDescriptor, EvidenceRecord


ROOT = Path(__file__).resolve().parents[2]


def source(identity, domains=('finance',), *, topics=()):
    return SourceDescriptor(identity, identity, domains, ('news',), ('*',), ('en',),
        'rss', 'enabled', f'https://example.org/{identity}.xml', topics=topics)


def record(s, name='rates', *, title=None):
    return EvidenceRecord(s.id + ':' + name, s.id, 'rss', s.name, title or name,
        f'https://example.org/{s.id}/{name}', 'news', f'{title or name} updated',
        datetime.now(timezone.utc).isoformat(), published_at=datetime.now(timezone.utc).date().isoformat(),
        content_level='feed', is_fixture=False, payload={'feed_url': s.homepage})


@pytest.fixture
def workspace(tmp_path):
    repo = ResearchRepository(tmp_path / 'feeds.sqlite3')
    repo.register('alice', 'password123')
    repo.register('bob', 'password123')
    return repo


def service(repo, user='alice', *, fail=False, counts=None):
    counts = counts if counts is not None else []
    class Adapter:
        def __init__(self, s):
            self.source = s
        def collect(self):
            counts.append(self.source.id)
            if fail:
                raise RuntimeError('not public')
            return (record(self.source), record(self.source, 'robotics'))
    result = ResearchSources(repo, user, ROOT / 'config/sources/live.yaml', factory=lambda s, **kw: Adapter(s))
    result.base = SourceCatalog([source('finance_one'), source('general_one', ('general',)), source('academic_one', ('academic',))])
    return result


def test_general_sources_participate_in_every_domain_and_have_reserved_capacity():
    sources = [source('finance_' + str(i)) for i in range(20)] + [source('general_one', ('general',))]
    routed = route_feeds(sources, ('finance',), ['rates'])
    assert len(routed) == 12 and 'general_one' in {s.id for s in routed}
    assert route_feeds([source('general_one', ('general',))], ('medical',), ['cancer'])
    assert not route_feeds([source('ai_feed', ('general',), topics=('ai',))], ('medical',), ['agent'])


def test_subscription_pool_reuses_across_sessions_and_accounts_are_separate(workspace):
    counts = []
    first = service(workspace, counts=counts)
    first.begin_turn()
    batch = first.collect_news('rates', ['finance'])
    assert {r['source_id'] for r in batch['results']} == {'finance_one', 'general_one'}
    assert 'academic_one' not in batch['selected_sources']
    assert len(counts) == 2
    second = service(workspace, counts=counts)
    second.collect_news('rates', ['finance'])
    assert len(counts) == 2
    assert all(x['state'] == 'cached' for x in second.last_search['collection_status'])
    service(workspace, user='bob', counts=counts).collect_news('rates', ['finance'])
    assert len(counts) == 4
    evidence_id = next(iter(second.evidence))
    assert second.fetch(evidence_id)['content_level'] == 'feed'


def test_feed_pool_merges_deduplicates_and_bounds_and_endpoint_change_does_not_leak(workspace):
    store, s = FeedRepository(workspace), source('finance_one')
    old = record(s)
    store.save('alice', s, (old,), 0)
    new = replace(old, snippet='revised')
    kept = store.save('alice', s, (new, record(s, 'robotics')), 1)
    assert len(kept) == 2 and next(r for r in kept if r.id == old.id).snippet == 'revised'
    assert store.load('bob', s) == ((), None)
    assert store.load('alice', replace(s, homepage='https://other.example/feed')) == ((), None)
    for i in range(2):
        store.save('alice', s, tuple(record(s, f'item{i}_{j}') for j in range(200)), i + 2)
    assert len(store.load('alice', s)[0]) == 200


def test_failed_collection_uses_stale_pool_and_disabled_sources_stay_invisible(workspace):
    first = service(workspace)
    store = FeedRepository(workspace)
    s = first.base.sources[0]
    store.save('alice', s, (record(s),), 0)
    second = service(workspace, fail=True)
    batch = second.collect_news('rates', ['finance'])
    assert batch['results'] and batch['failures']
    assert any(f['cached_count'] == 1 for f in batch['failures'])
    second.base = SourceCatalog([replace(s, status='suspended')])
    second.begin_turn()
    assert not second.collect_news('rates', ['finance'])['results']
    with pytest.raises(ValueError, match='停用'):
        second.fetch(s.id + ':rates')


def test_all_explicit_interests_not_just_first_are_selected(workspace):
    # Use the existing profile mutation boundary; no raw database profile updates.
    workspace.update_profile('alice', {'interests': ['rates', 'robotics']}, 'Explicit test preferences')
    result = service(workspace).collect_news('latest news', ['finance'])
    assert {r['title'] for r in result['results']} == {'rates', 'robotics'}
    from newspaper_agent.sources.filtering import select_report_evidence
    selected, _ = select_report_evidence([EvidenceRecord(**r) for r in result['results']],
                                         'latest news', workspace.profile('alice'), audit=result)
    assert {r.title for r in selected} == {'rates', 'robotics'}


def test_catalog_new_sources_and_pending_sources_are_explicit():
    catalog = SourceCatalog.load(ROOT / 'config/sources/live.yaml')
    sources = {s.id: s for s in catalog.sources}
    assert len(sources) == 54
    assert sources['mit_physics'].domains == ('academic',)
    assert sources['bbc_technology'].domains == ('general', 'news')
    assert all(sources[s].status == 'suspended' for s in ('nih_nibib', 'ecb_press', 'nasa_jpl'))


def test_private_sources_never_enter_another_accounts_feed_pool(workspace):
    item = workspace.store_source('alice', 'private feed', 'https://example.org/private.xml', 'finance')
    workspace.set_source_status('alice', item['id'], 'validated')
    workspace.set_source_status('alice', item['id'], 'enabled')
    alice, bob = service(workspace), service(workspace, user='bob')
    assert item['id'] in alice.collect_news('rates', ['finance'])['selected_sources']
    assert item['id'] not in bob.collect_news('rates', ['finance'])['selected_sources']


def test_desktop_subscription_entry_filter_and_opt_in_timer(workspace):
    from PySide6.QtWidgets import QApplication
    from newspaper_agent.desktop.app import Window
    app = QApplication.instance() or QApplication([])
    window = Window(workspace, 'alice')
    assert window.news_mode.currentData() == 'subscriptions'
    assert not window.auto_collect.isChecked() and not window.subscription_timer.isActive()
    window.source_filter.setCurrentIndex(window.source_filter.findData('finance'))
    entries = '\n'.join(window.source_list.item(i).text() for i in range(window.source_list.count()))
    assert '联储' in entries and 'BBC' in entries and 'MIT' not in entries
    assert '暂停' in entries and '欧洲央行' in entries
    calls = []
    window.collect_feed_pool = lambda: calls.append('explicit-opt-in')
    window.auto_collect.setChecked(True)
    assert calls == ['explicit-opt-in'] and window.subscription_timer.isActive()
    window.auto_collect.setChecked(False)
    assert not window.subscription_timer.isActive()
    window.close()
