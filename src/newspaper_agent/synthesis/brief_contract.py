"""Presentation-tolerant JSON and exact source-span recovery, without semantic quote repair."""
import json
import re

from .validation import _unique_object


def parse_brief_json(content):
    if not isinstance(content, str) or len(content) > 20000:
        raise ValueError('response_too_large')
    text = content.strip()
    fence = re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n```', text, re.I)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text, object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise ValueError('invalid_json') from None


def restore_quote(quote, original):
    if not isinstance(quote, str) or not quote.strip():
        return quote
    if quote in original:
        return quote
    match = re.search(r'\s+'.join(re.escape(t) for t in quote.split()), original)
    return match.group() if match else quote


def normalize_anchors(raw, evidence):
    records = {r.id: r.snippet for r in evidence}
    if not isinstance(raw.get('claims'), list):
        return
    for claim in raw['claims']:
        if not isinstance(claim, dict) or not isinstance(claim.get('evidence_refs'), list):
            continue
        for ref in claim['evidence_refs']:
            if isinstance(ref, dict) and isinstance(ref.get('evidence_id'), str):
                original = records.get(ref['evidence_id'])
                if original is not None:
                    ref['quote'] = restore_quote(ref.get('quote'), original)
