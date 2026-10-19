"""Small reader-conditioned output contract; no extra model calls or retrieval."""
import json
import re
from datetime import date

from .validation import validate_response
from .brief_contract import parse_brief_json, normalize_anchors
from newspaper_agent.preferences import confirmed_topics

BRIEF_VERSION = 4


def validate_brief(content, evidence, reader, related_events=()):
    if not isinstance(content, str) or len(content) > 20000:
        raise ValueError('response_too_large')
    raw = parse_brief_json(content)
    fields = {'focus', 'lead_subject', 'claims', 'follow_up'}
    if not isinstance(raw, dict) or set(raw) not in (fields, fields | {'news'}):
        raise ValueError('invalid_knowledge_schema')
    metadata_input = raw.pop('news', None)
    lead_subject = raw.pop('lead_subject')
    focus = raw.pop('focus')
    interests = confirmed_topics(reader)
    # Public-interest news can be useful without matching a confirmed personal topic.
    # Empty focus means a general view, never a newly inferred user preference.
    if not isinstance(focus, str) or (focus != '' and focus not in interests):
        raise ValueError('brief_focus_invalid')
    normalize_anchors(raw, evidence)
    checked = validate_response(json.dumps(raw, ensure_ascii=False), evidence)
    claims = checked['claims']
    if len(claims) > 2 or checked['follow_up'] or sum(len(c['conditions']) for c in claims) > 2:
        raise ValueError('brief_too_long')
    texts = [t for c in claims for t in [c['statement'], *c['conditions'], c['uncertainty']]]
    limit = 700 if reader.get('language') == 'en' else 180
    if sum(len(t) for t in texts) > limit:
        raise ValueError('brief_too_long')
    if not isinstance(lead_subject, str) or len(lead_subject) > 120:
        raise ValueError('brief_lead_invalid')
    if claims and (not lead_subject.strip() or not claims[0]['statement'].startswith(lead_subject.strip())):
        raise ValueError('brief_lead_invalid')
    prose = ' '.join(c['statement'] for c in claims)
    if any(p in prose for p in ('值得注意的是', '与你的兴趣相关', '建议优先读', '具有重要意义', '不容错过')):
        raise ValueError('brief_style_invalid')
    if any(c['statement'].startswith(('本文介绍', '本文讨论', '结论：', '要点：', '- ', '* ')) for c in claims):
        raise ValueError('brief_style_invalid')
    # A single paragraph is a mechanical contract, not a judgement of writing quality.
    if any('\n' in t or '\r' in t or re.match(r'^\s*(?:#{1,6}\s|\d+[.)、]\s*|[•●]\s*)', t)
           for t in texts):
        raise ValueError('brief_style_invalid')
    statements = [re.sub(r'\s+', '', c['statement']).rstrip('。.!！') for c in claims]
    if len(set(statements)) != len(statements):
        raise ValueError('brief_style_invalid')
    # Archiving must not discard an otherwise valid, evidence-anchored interpretation.
    metadata_issues = []
    try:
        metadata = validate_metadata(metadata_input, evidence, related_events)
    except (ValueError, TypeError, KeyError):
        metadata, metadata_issues = {}, ['metadata_pending']
    return {**checked, 'reader_focus': focus, 'lead_subject': lead_subject.strip(),
            'brief_version': BRIEF_VERSION, 'news_metadata': metadata, 'metadata_issues': metadata_issues}


def validate_metadata(value, evidence, related_events):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) != {'entities', 'event'}:
        raise ValueError('invalid_knowledge_schema')
    entities = value['entities']
    if not isinstance(entities, list) or len(entities) > 6:
        raise ValueError('invalid_knowledge_schema')
    body = '\n'.join(r.snippet for r in evidence)
    names = (body + '\n' + '\n'.join(r.title for r in evidence)).casefold()
    for e in entities:
        if not isinstance(e, dict) or set(e) != {'name', 'kind'} or e['kind'] not in ('company', 'model', 'topic'):
            raise ValueError('invalid_knowledge_schema')
        if not isinstance(e['name'], str) or not 1 <= len(e['name'].strip()) <= 80 or e['name'].casefold() not in names:
            raise ValueError('invalid_knowledge_schema')
    event = value['event']
    if event is not None:
        if not isinstance(event, dict) or set(event) != {'subject', 'action', 'object', 'occurred_at', 'quote', 'continues'}:
            raise ValueError('invalid_knowledge_schema')
        for k in ('subject', 'action', 'object', 'quote'):
            if not isinstance(event[k], str) or not 1 <= len(event[k].strip()) <= (1000 if k == 'quote' else 160):
                raise ValueError('invalid_knowledge_schema')
        if event['quote'] not in body:
            raise ValueError('invalid_evidence_anchor')
        at = event['occurred_at']
        if at is not None:
            try:
                if not isinstance(at, str) or len(at) != 10 or date.fromisoformat(at).isoformat() != at:
                    raise ValueError()
            except ValueError:
                raise ValueError('invalid_knowledge_schema') from None
        if event['continues'] is not None:
            if not isinstance(event['continues'], str) or event['continues'] not in {r['id'] for r in related_events}:
                raise ValueError('invalid_knowledge_schema')
    return value
