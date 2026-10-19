"""Source-first news collection; no model, search key, or background scheduler."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import time

from newspaper_agent.database.feed_repository import FeedRepository
from newspaper_agent.sources.filtering import select_evidence
from newspaper_agent.sources.query import plan_query
from newspaper_agent.agent_core.runtime import RunCancelled
from newspaper_agent.preferences import confirmed_topics, active_interest_units
from newspaper_agent.sources.annotations import routing_topics
from newspaper_agent.sources.time_windows import scope_from_query
from newspaper_agent.news_ranking import PUBLIC_FEEDS


def route_feeds(sources, domains, queries, *, limit=12, attempts=None):
    topics = {topic for query in queries for topic in plan_query(query[:400]).topics} | routing_topics(queries)
    eligible = [s for s in sources if s.adapter == 'rss' and s.status == 'enabled' and not s.is_fixture
                and (set(domains) & set(s.domains) or 'general' in s.domains)
                and (not s.topics or not topics or topics & set(s.topics))]
    specialized = [s for s in eligible if 'general' not in s.domains]
    general = [s for s in eligible if 'general' in s.domains]
    attempts = attempts or {}
    order = lambda s: (not s.id.startswith('source_'), attempts.get(s.id, -1), s.role != 'primary', s.id)
    specialized.sort(key=order)
    general.sort(key=order)
    # Reserve space for universal sources even when the specialized set is large.
    selected = []
    remaining = list(specialized)
    uncovered = set(topics)
    while remaining and len(selected) < limit - min(2, len(general)):
        # Explicit private additions remain first; then cover each requested topic
        # before filling the rest in least-recently-attempted order.
        source = next((s for s in remaining if s.id.startswith('source_')), None)
        source = source or next((s for s in remaining if set(s.topics) & uncovered), remaining[0])
        selected.append(source)
        remaining.remove(source)
        uncovered -= set(source.topics)
    selected += general[:limit - len(selected)]
    selected += [s for s in specialized if s not in selected][:limit - len(selected)]
    return tuple(selected)


def collect_news(service, query, domains=None, *, recent_days=None, time_scope=None, include_public=False):
    from .service import infer_domains
    if service.control:
        service.control.check()
    profile = service.repository.profile(service.user)
    broad = query.strip().casefold() in {'latest news', 'news', '最新新闻', '最近新闻', '最新消息', '看看最新消息'}
    units = active_interest_units(profile)
    interests = confirmed_topics(profile)
    queries = ([u['topic'] + ' ' + ' '.join(u['methods']) for u in sorted(units, key=lambda u: -u['priority'])]
               + interests) if broad and interests else [query]
    queries = list(dict.fromkeys(q[:400] for q in queries))[:12]
    plans = [plan_query(q) for q in queries]
    domains = tuple(domains or dict.fromkeys([*infer_domains(' '.join(queries), profile),
                                             *(u['domain'] for u in units if broad)]))
    time_scope = time_scope or scope_from_query(query)
    if time_scope not in (None, 'today', '24h'):
        raise ValueError('未知新闻时间范围')
    days = recent_days if recent_days is not None else 30
    store = FeedRepository(service.repository)
    available = service.descriptors()
    attempts = store.attempts(service.user, available)
    selected = route_feeds(available, domains, queries, attempts=attempts)
    public = []
    if include_public:
        public = sorted([s for s in available if s.id in PUBLIC_FEEDS and s.adapter == 'rss'],
                        key=lambda s: (attempts.get(s.id, -1), s.id))[:4]
        if public:
            personal = route_feeds(available, domains, queries, limit=8, attempts=attempts)
            selected = tuple(dict((s.id, s) for s in [*personal, *public]).values())
            if len(selected) < 12:
                selected += tuple(s for s in route_feeds(available, domains, queries, attempts=attempts)
                                  if s.id not in {x.id for x in selected})[:12-len(selected)]
    eligible = route_feeds(available, domains, queries, limit=max(12, len(available)), attempts=attempts)
    eligible = tuple(dict((s.id, s) for s in [*eligible, *public]).values())
    records, failures, statuses = [], [], []
    tasks = []
    now = time.time()
    for source in selected:
        saved, checked_at = store.load(service.user, source)
        if checked_at is not None and 0 <= now - checked_at < 900:
            store.record_attempt(service.user, source, now)
            records.extend(saved)
            statuses.append({'source_id': source.id, 'state': 'cached', 'count': len(saved)})
            continue
        tasks.append(source)
    def retrieve(source):
        if service.control:
            service.control.emit('source_start', name=source.name)
        return service._adapter(source).collect()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(retrieve, s): s for s in tasks}
        for future in as_completed(futures):
            source = futures[future]
            try:
                store.record_attempt(service.user, source, now)
                fresh = future.result()
                if service.control:
                    service.control.check()
                kept = store.save(service.user, source, tuple(fresh), now)
                records.extend(kept)
                statuses.append({'source_id': source.id, 'state': 'collected', 'count': len(kept)})
                service.health[source.id] = {'status': 'ok', 'checked_at': datetime.now(timezone.utc).isoformat(), 'count': len(kept)}
                if service.control:
                    service.control.emit('source_end', name=source.name, count=len(kept))
            except RunCancelled:
                raise
            except Exception:
                saved, _ = store.load(service.user, source)
                records.extend(saved)
                failures.append({'source_id': source.id, 'error': 'feed_collection_failed', 'cached_count': len(saved)})
                service.health[source.id] = {'status': 'failed', 'checked_at': datetime.now(timezone.utc).isoformat(), 'error': 'feed_collection_failed'}
    # Deterministic newest-first ties, independent of concurrent request completion order.
    records.sort(key=lambda r: (r.published_at or '', r.id), reverse=True)
    # Only collection scope/date/deduplication here. Interest screening belongs to AI.
    chosen, rejected = select_evidence(records, 'latest news', {}, limit=len(records), recent_days=days, time_scope=time_scope)
    for record in chosen:
        service.evidence[record.id] = service.turn_evidence[record.id] = record
    while len(service.evidence) > 100:
        del service.evidence[next(iter(service.evidence))]
    warnings = ['订阅采集只覆盖已配置来源；不代表全网搜索。']
    if not chosen:
        warnings.append('所选时间范围内暂无可确认日期的资料；未自动放宽时间或用旧闻凑数。' if time_scope
                        else '当前订阅池未找到相关资料，可切换主动检索或补充来源。')
    if len(interests) > 12 and broad:
        warnings.append('来源路由每轮使用最多12个兴趣查询；AI评分使用完整已确认档案。')
    service.last_search = {'mode': 'subscriptions', 'query': ' | '.join(p.query for p in plans),
        'public_source_ids': [s.id for s in public],
        'queries': [p.query for p in plans],
        'original_query': query, 'domains': list(domains), 'recent_days': days,
        'time_scope': time_scope,
        'coverage': {'eligible_sources': len(eligible), 'selected_sources': len(selected),
                     'deferred_sources': max(0, len(eligible) - len(selected)),
                     'outside_window': sum(d['decision'] == 'outside_time_window' for d in rejected)},
        'selected_sources': [s.id for s in selected], 'collection_status': statuses,
        'results': [asdict(r) for r in chosen], 'warnings': warnings, 'failures': failures,
        'candidate_count': len(chosen), 'is_fixture': False}
    service.search_history.append(service.last_search)
    if service.control:
        service.control.emit('filter_end', count=len(chosen), rejected=max(0, len(records) - len(chosen)))
    return service.last_search
