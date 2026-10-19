"""Evidence-labelled four-signal ranking; no embeddings, networking or UI."""
from datetime import datetime, timezone
from hashlib import sha256
from difflib import SequenceMatcher
import math
import re
from urllib.parse import urlsplit

from newspaper_agent.sources.filtering import canonical_url
from newspaper_agent.sources.time_windows import NEWS_TIMEZONE, in_explicit_window
from newspaper_agent.sources.annotations import content_tags

RANK_VERSION = 'four-signals-v2-rolling24h'
SECTIONS = {'personal': '个人推荐', 'major': '大事件', 'hot': '24小时热榜'}
DEFAULT_WEIGHTS = {'personal': [75, 15, 7, 3], 'major': [5, 25, 65, 5], 'hot': [5, 20, 10, 65]}
PUBLIC_FEEDS = frozenset({'bbc_world', 'bbc_technology', 'bbc_science', 'fed_press', 'nist_news',
                         'nasa_news', 'ithome_news', 'solidot_news'})
FORUM_HOSTS = {'news.ycombinator.com', 'discuss.huggingface.co', 'www.reddit.com', 'reddit.com'}


def validate_weights(value):
    if not isinstance(value, dict) or set(value) != set(SECTIONS):
        raise ValueError('推荐权重栏目无效')
    for weights in value.values():
        if (not isinstance(weights, list) or len(weights) != 4 or
                any(type(w) is not int or not 0 <= w <= 100 for w in weights) or sum(weights) != 100):
            raise ValueError('每栏四项权重需为0—100整数且合计100')
    return {k: list(v) for k, v in value.items()}


def article_key(record):
    return sha256(canonical_url(record.url).encode()).hexdigest()[:32]


def tag_keys(record):
    return sorted({t['concept'] for t in content_tags(record) if t['facet'] != 'contribution_mention'})


def organization(source):
    host = (urlsplit(getattr(source, 'homepage', '')).hostname or '').lower().removeprefix('www.')
    for suffix in ('bbc.co.uk', 'bbc.com', 'google.com', 'deepmind.google', 'nasa.gov'):
        if host == suffix or host.endswith('.' + suffix):
            return 'google' if suffix in ('google.com', 'deepmind.google') else 'bbc' if suffix.startswith('bbc') else suffix
    return host


def credibility(record, source):
    """A conservative metadata reference, never a probability of truth."""
    if source is None:
        prior, kind = 45, 'unknown'
    elif (urlsplit(getattr(source, 'homepage', '')).hostname or '') in FORUM_HOSTS:
        prior, kind = 42, 'community'
    elif record.kind == 'paper':
        prior, kind = 60, 'research_not_peer_review_assumption'
    elif getattr(source, 'role', 'discovery') == 'primary' and not source.id.startswith('source_'):
        prior, kind = 78, 'curated_primary'
    else:
        prior, kind = 60 if not source.id.startswith('source_') else 45, 'publisher_or_personal_source'
    dated = bool(record.published_at)
    snippet = bool(record.snippet and not record.snippet.startswith('Metadata only:'))
    score = round(.6 * prior + .25 * (65 if snippet else 20) + .15 * (70 if dated else 25))
    return {'score': score, 'basis': 'source_prior_and_metadata_only', 'source_kind': kind,
            'fact_verified': False, 'independent_verification': None,
            'note': '官网身份只是先验；未做事实核验，赞踩不会改变此分。'}


def published(record):
    value = record.published_at
    if not value or len(value) < 10:
        return None
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.replace(tzinfo=NEWS_TIMEZONE) if len(value) == 10 else date if date.tzinfo else None
    except ValueError:
        return None


def groups(records, sources):
    """Conservative candidate story grouping, NOT independent fact corroboration."""
    by_url, clusters = {}, []
    for record in records:
        key = canonical_url(record.url)
        title = re.sub(r'[^\w\u4e00-\u9fff]+', ' ', record.title.casefold()).strip()
        when = published(record)
        group = by_url.get(key)
        if group is None and len(title) >= 35 and when:
            words = set(title.split())
            for candidate in clusters:
                if not candidate['time'] or abs((when - candidate['time']).total_seconds()) > 86400:
                    continue
                other = candidate['title']
                overlap = len(words & set(other.split())) / max(1, len(words | set(other.split())))
                # Different numbers or named title content must not silently become the same event.
                if (re.findall(r'\d+', title) == re.findall(r'\d+', other) and
                        (title == other or overlap >= .85 and SequenceMatcher(None, title, other).ratio() >= .92)):
                    group = candidate
                    break
        if group is None:
            group = {'key': article_key(record), 'title': title, 'time': when, 'records': [], 'orgs': set()}
            clusters.append(group)
        by_url[key] = group
        group['records'].append(record.id)
        source = sources.get(record.source_id)
        if source and not source.id.startswith('source_'):
            owner = organization(source)
            if owner:
                group['orgs'].add(owner)
    return {identity: group for group in clusters for identity in group['records']}


def build_signals(records, scores, sources, importance, adjustments=None, *, now=None):
    adjustments = adjustments or {}
    now = now or datetime.now(timezone.utc)
    clustered = groups(records, sources)
    result = {}
    for record in records:
        group = clustered[record.id]
        source = sources.get(record.source_id)
        trust = credibility(record, source)
        count = len(group['orgs'])
        heat = round(min(95, 45 * math.log2(max(1, count))))
        assessment = importance.get(record.id, {})
        base = scores.get(record.id)
        delta = adjustments.get(record.id, 0.)
        relevance = None if base is None else round(max(0, min(100, base + delta)), 1)
        when = published(record)
        age = (now - when).total_seconds() if when else None
        result[record.id] = {
            'relevance': relevance, 'base_interest': base, 'feedback_adjustment': round(delta, 2),
            'credibility': trust['score'], 'credibility_detail': trust,
            'importance': assessment.get('score'), 'importance_detail': assessment,
            'heat': heat if count else None, 'heat_detail': {'kind': 'observed_reporting_coverage',
                'publishers': count, 'platform_metrics': None, 'growth': None,
                'grouping': 'conservative_headline_or_url_proxy_not_fact_verification'},
            'group': group['key'], 'public': bool(source and not source.id.startswith('source_')),
            'recent_major': age is not None and 0 <= age <= 7 * 86400,
            'today': in_explicit_window(record.published_at, 'today', now=now),
            'recent_24h': in_explicit_window(record.published_at, '24h', now=now),
            'version': RANK_VERSION}
    return result


def recommendation_score(signals, weights):
    values = [signals[k] for k in ('relevance', 'credibility', 'importance', 'heat')]
    # Unknown is not imputed as fact: only observed components enter, and their
    # weights stay fixed: this is a lower bound, not a renormalized estimate.
    available = [(v, w) for v, w in zip(values, weights) if v is not None and w]
    return round(sum(v*w for v, w in available) / 100, 2) if available else None


def rank_sections(records, signals, weights, limit):
    validate_weights(weights)
    pools = {key: [] for key in SECTIONS}
    scores = {}
    for record in records:
        s = signals[record.id]
        if s['relevance'] is None:
            continue  # No model must never become a fabricated personal ranking.
        eligible = ['personal']
        if s['public'] and s['credibility'] >= 55 and s['recent_major'] and (s['importance'] or 0) >= 65:
            eligible.append('major')
        if s['public'] and s['credibility'] >= 45 and s.get('recent_24h', False) and s['heat_detail']['publishers'] >= 2:
            eligible.append('hot')
        scores[record.id] = {key: recommendation_score(s, weights[key]) for key in eligible}
        for section in eligible:
            pools[section].append(record)
    for key, rows in pools.items():
        rows.sort(key=lambda r: (-scores[r.id][key], r.id))
        seen = set()
        pools[key] = [r for r in rows if not (signals[r.id]['group'] in seen or seen.add(signals[r.id]['group']))]
    # Reserve scarce non-personal coverage first; total distinct interpretations <= limit.
    major_n = limit // 5
    hot_n = limit // 5
    chosen, chosen_groups = {}, set()
    for key, amount in (('major', major_n), ('hot', hot_n), ('personal', limit)):
        for row in pools[key]:
            if key != 'personal' and amount <= 0 or len(chosen) >= limit:
                break
            if signals[row.id]['group'] not in chosen_groups:
                chosen[row.id] = row
                chosen_groups.add(signals[row.id]['group'])
                amount -= 1
    selected = list(chosen.values())
    selected.sort(key=lambda r: (-scores[r.id].get('personal', 0), r.id))
    sections = {key: sorted([r.id for r in selected if key in scores[r.id]],
                           key=lambda identity: (-scores[identity][key], identity)) for key in SECTIONS}
    return selected, sections, scores
