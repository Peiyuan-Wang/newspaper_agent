from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
import json
from types import SimpleNamespace

from newspaper_agent.sources.query import plan_query
from newspaper_agent.sources.filtering import select_evidence
from newspaper_agent.sources.contracts import EvidenceRecord, SearchRequest
from newspaper_agent.sources.live_adapters import build_adapter
from newspaper_agent.sources.network import HTTPResponse
from newspaper_agent.synthesis import SynthesisService


def record(id, title, text='', date='2026-10-01', provider='crossref'):
    return EvidenceRecord(id, provider, provider, 'Test publisher', title,
        'https://example.org/' + id, 'paper', text, '2026-10-14',
        published_at=date, content_level='abstract', is_fixture=False)


def test_reported_query_filters_bird_news_word_and_old_agent_paper():
    query = '了解一下ai，agent的大新闻'
    plan = plan_query(query)
    assert plan.query == 'ai agent' and plan.recent_days == 90
    assert plan_query('我想了解人工智能和智能体的最新新闻').query == 'ai agent'
    records = [record('bird', '为了天空的小鸟', date='2012-05'),
        record('journalism', 'AI赋能下数据新闻的实践路径', 'AI improves journalism.'),
        record('substring', 'The chair said an agent arrived'),
        record('old', 'AI agents for planning', 'Artificial intelligence agents', '2012'),
        record('good', 'Agentic artificial intelligence', 'AI agents use tools.'),
        record('chinese', '人工智能与智能体', '智能体工具调用')]
    chosen, decisions = select_evidence(records, query, {}, now=datetime(2026, 10, 14, tzinfo=timezone.utc))
    assert {r.id for r in chosen} == {'good', 'chinese'}
    assert any(d['id'] == 'old' and d['decision'] == 'outside_time_window' for d in decisions)
    assert any(d['id'] == 'substring' and d['decision'] == 'query_mismatch' for d in decisions)


def test_relevance_gate_applies_to_all_search_providers_and_interests_do_not_override():
    for provider in ('crossref', 'arxiv', 'pubmed', 'rss', 'tavily'):
        chosen, _ = select_evidence([record('bad', 'Sports', 'chair said robotics', provider=provider)],
            'AI agent', {'interests': ['robotics']})
        assert not chosen
    good = record('good', 'robotics study')
    assert select_evidence([good], 'robotics', {})[0]
    assert select_evidence([replace(good, published_at='2012')], 'robotics', {})[0]  # Historical research is allowed.
    assert not select_evidence([record('generic', 'AI agents use tools')], 'AI agent safety', {})[0]


def test_recent_crossref_request_uses_clean_query_and_date_filter():
    class HTTP:
        def get(self, url):
            params = parse_qs(urlsplit(url).query)
            assert params['query'] == ['ai agent']
            assert params['filter'][0].startswith('from-pub-date:')
            return HTTPResponse(json.dumps({'message': {'items': []}}).encode())
    descriptor = SimpleNamespace(id='crossref', adapter='crossref', name='Test', is_fixture=False)
    assert not build_adapter(descriptor, http=HTTP()).search(SearchRequest('ai agent', ('academic',), recent_days=90))


def test_fallback_report_has_scanable_results_without_long_abstract_dump():
    evidence = record('good', 'AI agents use tools', 'AI agents call tools. ' + 'Long background text. ' * 100)
    report = SynthesisService(None).generate('AI agents', [evidence])
    assert '# 本次简报' in report.markdown
    assert '### 1.' in report.markdown and '摘要摘录' in report.markdown
    assert evidence.snippet not in report.markdown
    assert report.knowledge_payload['evidence'][0]['snippet'] == evidence.snippet
    assert report.metrics['model_calls'] == 0
