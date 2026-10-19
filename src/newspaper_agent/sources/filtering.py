"""Transparent, bounded evidence selection. A relevance score is not a truth score."""
from dataclasses import replace
import re
import calendar
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .contracts import EvidenceRecord
from .query import ALIASES, matches, plan_query
from .time_windows import scope_from_query, in_explicit_window


def canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urlencode(query), ""))


def terms(text: str) -> tuple[str, ...]:
    # Latin words and Chinese bigrams; intentionally transparent, not semantic retrieval.
    words = re.findall(r"[a-z0-9][a-z0-9_-]{1,}|[\u4e00-\u9fff]+", text.lower())
    stop = {"the", "and", "for", "news", "latest", "research", "search", "please", "最新", "新闻", "搜索", "帮我", "一下"}
    result = []
    for word in words:
        if word in stop:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", word):
            result.extend(word[i:i + 2] for i in range(len(word) - 1) if word[i:i + 2] not in stop)
        else:
            result.append(word)
    return tuple(dict.fromkeys(result))[:40]


def select_report_evidence(records, question, profile, *, audit=None, now=None, limit=8):
    """Recheck saved multi-interest reports as a union, never as one AND query."""
    audit = audit or {}
    queries = audit.get('queries') if audit.get('mode') == 'subscriptions' else None
    if not isinstance(queries, list) or not 1 <= len(queries) <= 12 or any(
            not isinstance(q, str) or not 1 <= len(q.strip()) <= 400 for q in queries):
        queries = [audit.get('query') or question]
    days = audit.get('recent_days') or plan_query(question).recent_days
    batches, decisions = [], []
    for query in queries:
        chosen, rejected = select_evidence(records, query, profile, limit=limit, recent_days=days, now=now,
                                          time_scope=audit.get('time_scope'))
        batches.append(chosen)
        decisions.extend(rejected)
    chosen, seen = [], set()
    for index in range(limit):
        for batch in batches:
            if index < len(batch) and batch[index].url not in seen and len(chosen) < limit:
                chosen.append(batch[index])
                seen.add(batch[index].url)
    return tuple(chosen), tuple(decisions)


def select_evidence(records, query, profile, *, limit=5, recent_days=None, now=None, time_scope=None):
    """Return selected records and audit decisions; keep differing claims and versions."""
    plan = plan_query(query)
    query_terms = terms(plan.query)
    recent_days = recent_days if recent_days is not None else plan.recent_days
    time_scope = time_scope or scope_from_query(query)
    if time_scope not in (None, 'today', '24h'):
        raise ValueError('未知新闻时间范围')
    today = (now or datetime.now(timezone.utc)).date()
    interests = tuple(str(x).casefold() for x in profile.get("interests", ()))
    excluded = tuple(str(x).casefold() for x in profile.get("excluded", ()))
    seen, ranked, decisions = set(), [], []
    for record in records:
        if record.is_fixture:
            decisions.append({"id": record.id, "decision": "rejected_fixture"})
            continue
        key = canonical_url(record.url)
        # Exact content duplicates can indicate republication; retain the original URL lineage.
        if key in seen:
            decisions.append({"id": record.id, "decision": "duplicate_url", "url": key})
            continue
        seen.add(key)
        if time_scope and not in_explicit_window(record.published_at, time_scope, now=now):
            decisions.append({'id': record.id, 'decision': 'outside_time_window'})
            continue
        text = (record.title + " " + record.snippet).casefold()
        excluded_hits = [x for x in excluded if x and x in text]
        if excluded_hits:
            decisions.append({"id": record.id, "decision": "user_excluded", "matched": excluded_hits})
            continue
        hits = [x for x in query_terms if any(matches(v, text) for v in ALIASES.get(x, (x,)))]
        source_hits = []
        if 'ai' in query_terms and 'ai' not in hits and 'ai' in record.payload.get('source_topics', []):
            source_hits = ['ai']
            hits.append('ai')
        interest_hits = [x for x in interests if x and x in text]
        # APIs are fuzzy candidate generators, not a relevance verdict. Short Latin
        # tokens match whole words: AI must not accidentally match "said" or "chair".
        missing_topics = [x for x in plan.topics if x not in hits]
        other_terms = [x for x in query_terms if x not in plan.topics]
        missing_focus = bool(plan.topics and other_terms and not any(x in hits for x in other_terms))
        if query_terms and (not hits or missing_topics or missing_focus):
            decisions.append({"id": record.id, "decision": "query_mismatch", "missing_topics": missing_topics})
            continue
        if recent_days and not time_scope and record.published_at:
            try:
                date = record.published_at
                if len(date) == 4:
                    latest = datetime(int(date), 12, 31).date()
                    earliest = datetime(int(date), 1, 1).date()
                elif len(date) == 7:
                    year, month = map(int, date.split('-'))
                    latest = datetime(year, month, calendar.monthrange(year, month)[1]).date()
                    earliest = datetime(year, month, 1).date()
                else:
                    latest = earliest = datetime.fromisoformat(date.replace('Z', '+00:00')).date()
                if latest < today - timedelta(days=recent_days) or earliest > today:
                    decisions.append({'id': record.id, 'decision': 'outside_time_window'})
                    continue
            except (ValueError, TypeError):
                pass  # An unknown date is labelled, never fabricated.
        detail = {"query_matches": hits[:8], "interest_matches": interest_hits[:5],
                  "ranking": "query_overlap_then_explicit_interests", "truth_assessed": False,
                  "recent_days": recent_days}
        detail['source_topic_matches'] = source_hits
        record = replace(record, payload={**record.payload, "selection": detail})
        ranked.append((len(hits), len(interest_hits), record))
    ranked.sort(key=lambda item: (-item[0], -item[1]))
    chosen = [entry[2] for entry in ranked[:limit]]
    decisions.extend({"id": entry[2].id, "decision": "kept" if i < limit else "result_budget"}
                     for i, entry in enumerate(ranked))
    return tuple(chosen), tuple(decisions)
