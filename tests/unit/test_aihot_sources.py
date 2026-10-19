from pathlib import Path

from newspaper_agent.research.catalog_smoke import AIHOT_SOURCE_IDS, main
from newspaper_agent.research.subscriptions import route_feeds
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.live_adapters import build_adapter
from newspaper_agent.sources.network import HTTPResponse


ROOT = Path(__file__).resolve().parents[2]


def test_aihot_public_catalog_is_unique_classified_and_has_no_paid_adapter():
    catalog = SourceCatalog.load(ROOT / 'config/sources/live.yaml')
    sources = [s for s in catalog.sources if s.id in AIHOT_SOURCE_IDS]
    assert len(sources) == 18
    assert len({s.homepage for s in sources}) == 18
    assert all(s.adapter == 'rss' and not s.is_fixture for s in sources)
    assert all(set(s.domains) == {'academic', 'news'} for s in sources)
    assert all(set(s.topics) == {'ai', 'agent', 'llm'} for s in sources)
    assert {s.id for s in sources if s.status == 'suspended'} == {'ai_deepmind', 'ai_github'}


def test_each_enabled_aihot_source_uses_existing_rss_collection_without_credentials():
    class HTTP:
        def get(self, url):
            assert url.startswith('https://')
            return HTTPResponse(b'<rss version="2.0"><channel><item><title>AI model release</title>'
                                b'<link>https://example.org/release</link>'
                                b'<description>Model details</description></item></channel></rss>')

    for source in SourceCatalog.load(ROOT / 'config/sources/live.yaml').sources:
        if source.id in AIHOT_SOURCE_IDS and source.status == 'enabled':
            items = build_adapter(source, http=HTTP()).collect()
            assert len(items) == 1
            assert items[0].source_id == source.id
            assert items[0].content_level == 'feed' and not items[0].is_fixture


def test_expansion_preserves_routing_budget_general_capacity_and_domain_boundary():
    sources = SourceCatalog.load(ROOT / 'config/sources/live.yaml').sources
    routed = route_feeds(sources, ('news',), ['ai agent'])
    assert len(routed) == 12
    assert len([s for s in routed if 'general' in s.domains]) == 2
    assert not {'ai_deepmind', 'ai_github'} & {s.id for s in routed}
    finance = route_feeds(sources, ('finance',), ['interest rates'])
    assert not AIHOT_SOURCE_IDS & {s.id for s in finance}


def test_aihot_probe_is_explicit_and_does_not_change_catalog(monkeypatch, capsys):
    import newspaper_agent.research.catalog_smoke as smoke
    seen = []

    class Adapter:
        def collect(self):
            return ()

    def factory(source):
        seen.append(source.id)
        return Adapter()

    monkeypatch.setattr(smoke, 'build_adapter', factory)
    main(['--aihot'])
    assert set(seen) == AIHOT_SOURCE_IDS
    assert len(capsys.readouterr().out.splitlines()) == 18
    sources = SourceCatalog.load(ROOT / 'config/sources/live.yaml').sources
    assert next(s for s in sources if s.id == 'ai_deepmind').status == 'suspended'
