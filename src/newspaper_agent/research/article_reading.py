"""Read full short bodies or cover every block of a long body before compact interpretation."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import json
import re

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.agent_core.runtime import RunCancelled
from newspaper_agent.sources.article_reader import ArticleReader, READER_VERSION
from newspaper_agent.synthesis.reader_context import article_context
from newspaper_agent.synthesis.brief_contract import parse_brief_json


def _passages(content, blocks):
    if not isinstance(content, str) or len(content) > 6000:
        raise ValueError('invalid_passages')
    try:
        data = parse_brief_json(content)
        if set(data) != {'passages'} or not isinstance(data['passages'], list) or len(data['passages']) > 2:
            raise ValueError('invalid_schema')
        quotes = []
        for p in data['passages']:
            if set(p) != {'block', 'quote'} or type(p['block']) is not int or p['block'] not in blocks:
                raise ValueError('invalid_block_id')
            q = p['quote']
            if not isinstance(q, str) or not q.strip() or len(q) > 1000:
                raise ValueError('quote_length')
            if q not in blocks[p['block']]:
                # Only whitespace variation is tolerated; recover the exact original span for downstream anchors.
                match = re.search(r'\s+'.join(re.escape(t) for t in q.split()), blocks[p['block']])
                if not match or len(match.group()) > 1000:
                    raise ValueError('quote_not_found')
                q = match.group()
            quotes.append(q)
        return quotes
    except (ValueError, KeyError, TypeError) as exc:
        code = str(exc)
        raise ValueError(code if code in ('invalid_schema', 'invalid_block_id', 'quote_length', 'quote_not_found')
                         else 'invalid_passages') from None


def document_for(store, user, record, *, control=None, reader=None):
    if control:
        control.check()
    cached = store.document(user, record.id, READER_VERSION)
    if cached:
        return cached
    document = (reader or ArticleReader()).read(record)
    if control:
        control.check()
    store.save_document(user, record.id, READER_VERSION, document)
    return document


def prepare_reading(record, document, model, profile, *, control=None, checkpoint=None, save_checkpoint=None, question=None):
    text = document.get('text', '')
    info = {'status': 'abstract_only', 'fetch_status': document['status'],
            'reason': document.get('reason', ''), 'blocks_total': 0, 'blocks_read': 0,
            'body_hash': sha256(text.encode()).hexdigest(), 'model_calls': 0}
    if document['status'] != 'extracted' or not text:
        return record, info
    if len(text) <= 12000:
        info.update(status='body_ready', blocks_total=1)
        return replace(record, snippet=text, content_level='fulltext'), info
    blocks = {i: text[start:start + 3500] for i, start in enumerate(range(0, len(text), 3500))}
    info['blocks_total'] = len(blocks)
    # Keep the article's own framing alongside personalized passages; otherwise the final
    # writer sees ablations but never learns what the model/product actually is.
    prefix = text[:1800]
    boundary = max(prefix.rfind('\n'), prefix.rfind('. '), prefix.rfind('。'))
    if boundary >= 400:
        prefix = prefix[:boundary + 1]
    selected = [prefix]
    state = checkpoint or {'completed': {}, 'attempts': []}
    prompt = (Path(__file__).resolve().parents[3] / 'prompts/news_reading.md').read_text(encoding='utf-8')
    for start in range(0, len(blocks), 4):
        if control:
            control.emit('article_reading', count=start, total=len(blocks))
            control.check()
        batch = {i: blocks[i] for i in range(start, min(start + 4, len(blocks)))}
        if str(start) in state['completed']:
            selected.extend(state['completed'][str(start)])
            info['blocks_read'] += len(batch)
            continue
        material = {'title': record.title,
                    'writing_context': article_context(question or record.title,
                        (replace(record, content_level='fulltext'),), profile),
                    'blocks': [{'id': i, 'text': t} for i, t in batch.items()]}
        base = [Message('system', prompt), Message('user', json.dumps(material, ensure_ascii=False,separators=(',',':')))]
        messages = base
        for attempt in range(2):
            response = None
            try:
                info['model_calls'] += 1
                response = model.complete(messages, tools=())
                if response.tool_calls:
                    raise ValueError('invalid_passages')
                passages = _passages(response.content, batch)
                selected.extend(passages)
                state['completed'][str(start)] = passages
                if save_checkpoint:
                    save_checkpoint(state)
                info['blocks_read'] += len(batch)
                break
            except RunCancelled:
                raise
            except (ValueError, TypeError) as exc:
                code = str(exc) if str(exc) in ('invalid_schema', 'invalid_block_id', 'quote_length', 'quote_not_found') else 'invalid_passages'
                state['attempts'] = (state['attempts'] + [{'batch': start, 'error': code,
                    'draft': response.content[:6000] if response is not None else ''}])[-8:]
                if save_checkpoint:
                    save_checkpoint(state)
                if attempt == 0 and response is not None:
                    messages = base + [Message('user', json.dumps({'error': code,
                        'allowed_block_ids': list(batch), 'previous_draft': response.content[:6000],
                        'instruction': '保持本次提供的真实块编号，不要从0重新编号。只复制短的连续完整原句，最多400字符，'
                                       '原样保留空白、标点，不翻译，不省略，不拼接。返回完整JSON。'}, ensure_ascii=False))]
                    continue
                info.update(status='reading_incomplete', reason='passage_validation_failed')
                return None, info
            except Exception:
                info.update(status='reading_incomplete', reason='reading_request_failed')
                return None, info
    info['status'] = 'body_read'
    if not selected:
        info.update(status='reading_incomplete', reason='no_relevant_passages')
        return None, info
    # These are original text passages, not generated summaries masquerading as evidence.
    return replace(record, snippet='\n\n'.join(dict.fromkeys(selected)), content_level='fulltext'), info
