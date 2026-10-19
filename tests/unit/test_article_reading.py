from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.agent_core.runtime import RunControl, RunCancelled
from newspaper_agent.sources.article_reader import ArticleReader, article_url, extract_body
from newspaper_agent.sources.article_reader import READER_VERSION
from newspaper_agent.sources.network import HTTPResponse, SafeHTTPClient, SourceNetworkError
from newspaper_agent.sources.contracts import EvidenceRecord
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.news_repository import NewsRepository
from newspaper_agent.research.article_reading import prepare_reading, document_for
from newspaper_agent.research.news_pipeline import generate_news, explain_text
from newspaper_agent.synthesis.news_brief import validate_metadata


@pytest.fixture
def record():
    return EvidenceRecord('rss:1', 'rss', 'rss', 'Official', 'OpenAI GPT-5 update',
        'https://openai.com/news/test', 'news', 'Short feed excerpt.', '2026-10-16T00:00:00Z',
        published_at='2026-10-15T00:00:00Z', is_fixture=False, content_level='feed')


@pytest.fixture
def store(tmp_path):
    repo = ResearchRepository(tmp_path / 'reading.sqlite3')
    repo.register('alice', 'password123')
    repo.register('bob', 'password123')
    return NewsRepository(repo)


def metadata(record, *, day='2026-10-15', object_='GPT-5', continues=None):
    return {'entities': [{'name': 'OpenAI', 'kind': 'company'}, {'name': 'GPT-5', 'kind': 'model'}],
            'event': {'subject': 'OpenAI', 'action': 'updates', 'object': object_, 'occurred_at': day,
                      'quote': record.snippet, 'continues': continues}}


def test_article_extraction_ignores_chrome_scripts_and_supports_jsonld():
    article = 'Important result and its limitations. ' * 15
    html = '<nav>NO NAV</nav><article><h1>Title</h1><p>' + article + '</p><script>NO SCRIPT</script></article><footer>NO FOOTER</footer>'
    assert article.strip() in extract_body(html)
    assert 'NO ' not in extract_body(html)
    assert extract_body('<html><div>Just navigation without article container</div></html>') == ''
    structured = '<script type="application/ld+json">' + json.dumps({'@graph': [{'articleBody': article}]}) + '</script>'
    assert extract_body(structured) == article.strip()
    nested = '<main><div class="blog-content"><p>' + article + '</p></div><article class="comment">Unrelated comment</article></main>'
    assert 'Unrelated comment' not in extract_body(nested)


def test_reader_gets_entire_body_including_end_conditions_and_rewrites_arxiv(record):
    tail = 'Only enterprise plans support export. Exports consume credits.'
    text = 'OpenAI introduces a version update. ' * 15 + tail
    http = SimpleNamespace(get_page=lambda url: HTTPResponse(('<article><p>' + text + '</p></article>').encode(),
                                                            content_type='text/html; charset=utf-8'))
    result = ArticleReader(http).read(record)
    assert result['status'] == 'extracted' and result['text'].endswith(tail)
    arxiv = replace(record, url='https://arxiv.org/abs/2307.10364v3')
    assert article_url(arxiv) == 'https://arxiv.org/html/2307.10364v3'


@pytest.mark.parametrize('body,ctype,status', [(b'<article>short</article>', 'text/html', 'unavailable'),
    (b'%PDF content', 'application/pdf', 'unavailable'),
    (('<article>' + 'x' * 48001 + '</article>').encode(), 'text/html', 'oversized')], ids=['short', 'pdf', 'oversized'])
def test_unconfirmed_and_oversized_body_never_claim_full_read(record, body, ctype, status):
    result = ArticleReader(SimpleNamespace(get_page=lambda _: HTTPResponse(body, content_type=ctype))).read(record)
    assert result['status'] == status and result['text'] == ''


def test_cache_is_private_and_failures_are_retried(store, record):
    store.put_articles('alice', [record])
    class Reader:
        calls = 0
        def read(self, record):
            self.calls += 1
            return {'status': 'unavailable', 'text': '', 'reason': 'access_denied'}
    reader = Reader()
    document_for(store, 'alice', record, reader=reader)
    document_for(store, 'alice', record, reader=reader)
    assert reader.calls == 1 and store.document('bob', record.id, READER_VERSION) is None
    with store.repo._connect(write=True) as db:
        db.execute("UPDATE news_documents SET fetched_at='2020-01-01T00:00:00+00:00'")
    document_for(store, 'alice', record, reader=reader)
    assert reader.calls == 2


def test_all_long_body_blocks_are_read_and_quotes_remain_original(record):
    text = 'a' * 14000 + 'Terminal condition: not free.'
    calls = []
    class Model:
        def complete(self, messages, *, tools=()):
            data = json.loads(messages[1].content)
            calls.extend(b['id'] for b in data['blocks'])
            return Message('assistant', json.dumps({'passages': [{'block': b['id'], 'quote': b['text'][-500:]}
                for b in data['blocks'][-2:]]}))
    selected, info = prepare_reading(record, {'status': 'extracted', 'text': text}, Model(), {})
    assert calls == list(range(5)) and info['blocks_read'] == info['blocks_total'] == 5
    assert info['status'] == 'body_read' and selected.snippet.endswith('not free.')
    assert all(p in text for p in selected.snippet.split('\n\n'))


def test_long_read_retains_original_framing_even_when_passages_focus_on_details(record):
    opening = 'CodeModel is an open code-generation model with a reproducible training recipe.\n'
    text = opening + 'Details. ' * 1600
    class Model:
        def complete(self, messages, *, tools=()):
            blocks = json.loads(messages[1].content)['blocks']
            b = blocks[-1]
            return Message('assistant', json.dumps({'passages': [{'block': b['id'], 'quote': b['text'][-100:]}]}))
    selected, info = prepare_reading(record, {'status': 'extracted', 'text': text}, Model(), {})
    assert info['status'] == 'body_read' and selected.snippet.startswith(opening)


def test_long_read_failure_does_not_silently_summarize_first_part(record):
    class Model:
        calls = 0
        def complete(self, messages, *, tools=()):
            self.calls += 1
            return Message('assistant', '{"passages":[{"block":0,"quote":"invented"}]}')
    model = Model()
    selected, info = prepare_reading(record, {'status': 'extracted', 'text': 'a' * 15000}, model, {})
    assert selected is None and info['status'] == 'reading_incomplete' and model.calls == 2


def test_reading_checkpoint_resumes_only_failed_blocks(record):
    text = 'a' * 14000 + 'Tail condition.'
    state, seen = {}, []
    class Partial:
        def complete(self, messages, *, tools=()):
            blocks = json.loads(messages[1].content)['blocks']
            seen.append([b['id'] for b in blocks])
            if blocks[0]['id'] == 4:
                return Message('assistant', '{}')
            return Message('assistant', json.dumps({'passages': [{'block': blocks[0]['id'], 'quote': 'a' * 100}]}))
    _, info = prepare_reading(record, {'status': 'extracted', 'text': text}, Partial(), {},
                              save_checkpoint=lambda value: state.update(value))
    assert info['blocks_read'] == 4 and '0' in state['completed'] and state['attempts']
    class Finish:
        def complete(self, messages, *, tools=()):
            blocks = json.loads(messages[1].content)['blocks']
            assert [b['id'] for b in blocks] == [4]
            return Message('assistant', '{"passages":[{"block":4,"quote":"Tail condition."}]}')
    selected, info = prepare_reading(record, {'status': 'extracted', 'text': text}, Finish(), {}, checkpoint=state)
    assert info['blocks_read'] == 5 and info['model_calls'] == 1 and selected.snippet.endswith('Tail condition.')


def test_cancel_before_document_fetch(store, record):
    control = RunControl(max_seconds=None)
    control.cancel()
    with pytest.raises(RunCancelled):
        document_for(store, 'alice', record, control=control)


def test_article_redirects_recheck_public_url_and_strip_ambient_credentials(monkeypatch):
    from newspaper_agent.sources import network
    requests = []
    class Response:
        def __init__(self, status, headers, content=b'body'):
            self.status, self.headers, self.stream = status, headers, io.BytesIO(content)
        def getheader(self, name, default=None):
            return self.headers.get(name, default)
        def read1(self, n):
            return self.stream.read(n)
    responses = iter([Response(302, {'Location': 'https://second.org/article'}),
                      Response(200, {'Content-Type': 'text/html'})])
    class Connection:
        sock = None
        def __init__(self, host, address, timeout):
            self.host = host
        def request(self, method, target, body=None, headers=None):
            requests.append((self.host, headers))
        def getresponse(self):
            return next(responses)
        def close(self):
            pass
    monkeypatch.setattr(network, 'resolve_public_addresses', lambda _: ('93.184.216.34',))
    monkeypatch.setattr(network, '_PinnedHTTPSConnection', Connection)
    result = SafeHTTPClient().get_page('https://first.org/article')
    assert result.final_url == 'https://second.org/article'
    assert len(requests) == 2 and all('Authorization' not in h and 'Cookie' not in h for _, h in requests)
    responses = iter([Response(302, {'Location': 'https://127.0.0.1/private'})])
    with pytest.raises(SourceNetworkError, match='unsafe_url'):
        SafeHTTPClient().get_page('https://first.org/article')


def test_entity_library_event_groups_and_distinct_versions_are_private(store, record):
    second = replace(record, id='rss:2', url='https://openai.com/news/another')
    third = replace(record, id='rss:3', title='OpenAI GPT-6 release')
    store.put_articles('alice', [record, second, third])
    store.index_metadata('alice', record, metadata(record))
    store.index_metadata('alice', second, metadata(second))
    store.index_metadata('alice', third, metadata(third, object_='GPT-6'))
    result = store.library('alice', entity='OpenAI', kind='company', visible_sources={'rss'})
    assert result['total'] == 3
    ids = {r['record']['id']: r['event_id'] for r in result['items']}
    assert ids[record.id] == ids[second.id] != ids[third.id]
    assert store.library('bob')['total'] == 0
    assert store.library('alice', visible_sources=set())['total'] == 0
    assert store.entity_counts('bob', {'rss'}) == []
    assert store.library('alice', query='GPT-6')['total'] == 1
    assert len(store.library('alice', limit=1, offset=1)['items']) == 1


def test_explicit_continuation_timeline_not_company_lifecycle(store, record):
    store.put_articles('alice', [record])
    store.index_metadata('alice', record, metadata(record))
    first = store.library('alice')['items'][0]['event_id']
    followup = replace(record, id='rss:2', title='OpenAI GPT-5 evaluation', published_at='2026-10-16')
    unrelated = replace(record, id='rss:3', title='OpenAI GPT-6 release')
    store.put_articles('alice', [followup, unrelated])
    next_meta = metadata(followup, day='2026-10-16', continues=first)
    next_meta['event']['action'] = 'evaluates'
    store.index_metadata('alice', followup, next_meta)
    store.index_metadata('alice', unrelated, metadata(unrelated, object_='GPT-6'))
    rows = store.timeline('alice', first, {'rss'})
    assert [r['record']['id'] for r in rows] == [record.id, followup.id]
    assert not store.timeline('bob', first, {'rss'})
    assert not store.timeline('alice', first, set())


@pytest.mark.parametrize('change', ['date', 'quote', 'parent', 'entity'])
def test_archive_metadata_rejects_invalid_values(record, change):
    data = metadata(record)
    if change == 'date':
        data['event']['occurred_at'] = '2026-02-30'
    elif change == 'quote':
        data['event']['quote'] = 'not present'
    elif change == 'parent':
        data['event']['continues'] = {'fake': 'cross-account'}
    else:
        data['entities'][0]['name'] = 'Invented Corp'
    with pytest.raises(ValueError):
        validate_metadata(data, [record], [])


def test_pipeline_uses_body_not_feed_and_then_reuses_body_and_interpretation(store, record):
    full = 'OpenAI provides a concrete change. ' * 10 + 'Export is enterprise only.'
    class Reader:
        calls = 0
        def read(self, record):
            self.calls += 1
            return {'status': 'extracted', 'text': full, 'reason': ''}
    class Model:
        calls = 0
        def complete(self, messages, *, tools=()):
            self.calls += 1
            data = json.loads(messages[1].content)
            if 'articles' in data:
                return Message('assistant', '{"scores":[{"id":0,"score":90}]}')
            assert data['evidence'][0]['snippet'] == full
            return Message('assistant', json.dumps({'focus': '', 'lead_subject': 'OpenAI', 'claims': [{'statement': 'OpenAI调整导出功能，仅企业套餐可用。',
                'conditions': [], 'uncertainty': '', 'status': 'unverified',
                'evidence_refs': [{'evidence_id': record.id, 'quote': 'Export is enterprise only.'}]}], 'follow_up': []}))
    from newspaper_agent.research.model_runtime import BudgetModel
    reader, model = Reader(), Model()
    session = SimpleNamespace(repository=store.repo, user='alice',
        sources=SimpleNamespace(descriptors=lambda: [SimpleNamespace(id='rss')]),
        model=BudgetModel(model), control=None, article_reader=reader)
    first = generate_news(session, 'latest', [record], {})
    assert first.knowledge_payload['ranked_items'][0]['reading']['status'] == 'body_read'
    assert first.knowledge_payload['evidence'][0]['snippet'] == full
    second = generate_news(session, 'latest', [record], {})
    assert model.calls == 2 and reader.calls == 1 and second.metrics['model_calls'] == 0
    text = first.knowledge_payload['ranked_items'][0]['explanation']
    assert not text.startswith('- ') and '适用条件：' not in text and '\n' not in text


def test_generation_failure_keeps_body_ready_and_retry_reuses_score_and_body(store, record):
    from newspaper_agent.model.client import ModelRequestFailure
    from newspaper_agent.research.model_runtime import BudgetModel
    full = 'OpenAI provides a concrete change. ' * 10
    class Reader:
        calls = 0
        def read(self, record):
            self.calls += 1
            return {'status': 'extracted', 'text': full, 'reason': ''}
    class Model:
        scores = 0
        explanations = 0
        def complete(self, messages, *, tools=()):
            data = json.loads(messages[1].content)
            if 'articles' in data:
                self.scores += 1
                return Message('assistant', '{"scores":[{"id":0,"score":90}]}')
            self.explanations += 1
            if self.explanations == 1:
                raise ModelRequestFailure('remote_protocol', 'disconnected',
                                          {'code': 'remote_protocol', 'attempts': 2})
            return Message('assistant', json.dumps({'focus':'', 'lead_subject':'OpenAI', 'claims':[{
                'statement':'OpenAI provides a concrete change.', 'conditions':[], 'uncertainty':'',
                'status':'unverified','evidence_refs':[{'evidence_id':record.id,
                                                      'quote':'OpenAI provides a concrete change.'}]}], 'follow_up':[]}))
    reader, model = Reader(), Model()
    session = SimpleNamespace(repository=store.repo, user='alice',
        sources=SimpleNamespace(descriptors=lambda: [SimpleNamespace(id='rss')]),
        model=BudgetModel(model), control=None, article_reader=reader)
    first = generate_news(session, 'latest', [record], {})
    item = first.knowledge_payload['ranked_items'][0]
    assert item['reading']['status'] == 'body_ready' and item['status'] == 'pending'
    assert item['model_diagnostic'] == {'code':'remote_protocol', 'attempts':2}
    second = generate_news(session, 'latest', [record], {})
    assert second.knowledge_payload['ranked_items'][0]['status'] == 'completed'
    assert second.knowledge_payload['ranked_items'][0]['reading']['status'] == 'body_read'
    assert model.scores == 1 and model.explanations == 2 and reader.calls == 1
    generate_news(session, 'latest', [record], {})
    assert model.explanations == 2


def test_reading_and_generation_failure_labels_are_distinct():
    pytest.importorskip('PySide6')
    from newspaper_agent.desktop.news_view import reading_status_text, model_failure_text
    legacy = {'status':'reading_incomplete', 'fetch_status':'extracted',
              'blocks_total':1,'model_calls':0,'reason':''}
    assert '正文已获取' in reading_status_text(legacy)
    assert '正文已获取' in reading_status_text({'status':'body_ready'})
    assert '分段阅读未完成' in reading_status_text({**legacy,'model_calls':2,'reason':'reading_request_failed'})
    assert '正文未取得' in reading_status_text({'status':'abstract_only'})
    assert '已尝试2次' in model_failure_text({'code':'remote_protocol','attempts':2})
    assert model_failure_text({'code':'private raw error'}) == ''


def test_library_ui_facets_and_timeline_without_model_or_network(store, record, monkeypatch):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.theme import STYLE
    from newspaper_agent.sources.catalog import SourceCatalog
    from newspaper_agent.sources.contracts import SourceDescriptor
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setStyleSheet(STYLE)
    store.put_articles('alice', [record])
    store.index_metadata('alice', record, metadata(record))
    window = Window(store.repo, 'alice')
    window.session.sources.base = SourceCatalog([SourceDescriptor('rss', 'Test', ('news',), ('news',),
        ('*',), ('en',), 'rss', 'enabled', 'https://openai.com/feed')])
    window.navigate(6)
    assert window.stack.currentIndex() == 6 and window.library_view.entities.count() >= 3
    assert window.library_view.displayed_groups == 1
    event_id = store.library('alice')['items'][0]['event_id']
    window.library_view.show_timeline(event_id)
    assert window.library_view.timeline_dialog.isVisible()
    window.library_view.timeline_dialog.close()
    window.resize(1280, 850)
    window.show()
    app.processEvents()
    assert window.grab().save(str(store.repo.path.parent / 'news-library-preview.png'))
    window.close()
