"""Per-user source discovery, retrieval and provenance. No model dependency."""
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit
import os
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.contracts import SourceDescriptor, SearchRequest, EvidenceRecord, DOMAINS, public_url
from newspaper_agent.sources.filtering import select_evidence
from newspaper_agent.sources.query import plan_query
from newspaper_agent.sources.live_adapters import build_adapter, discover_feed_links


def infer_domains(query, profile=None):
    mapping = {
        "academic": ("论文", "学术", "arxiv", "paper", "research", "机器学习", "深度学习", "物理", "数学", "physics", "mathematics"),
        "medical": ("医学", "医疗", "临床", "药物", "medical", "clinical", "pubmed"),
        "finance": ("金融", "利率", "股票", "银行", "财报", "finance", "interest rate", "federal reserve"),
        "news": ("新闻", "时事", "news"),
    }
    from newspaper_agent.sources.annotations import routing_topics
    academic_topic = bool(routing_topics([query]) & {'physics', 'math'})
    found = tuple(domain for domain, words in mapping.items()
                  if (domain == 'academic' and academic_topic) or any(w in query.casefold() for w in words))
    return found or tuple((profile or {}).get("domains", ())) or ("general",)


class ResearchSources:
    def __init__(self, repository, user, catalog_path=Path("config/sources/live.yaml"), *, http=None, factory=build_adapter):
        self.repository, self.user = repository, user
        self.base = SourceCatalog.load(Path(catalog_path))
        self.http, self.factory = http, factory
        self.adapters = {}
        self.evidence: dict[str, EvidenceRecord] = {}
        self.turn_evidence: dict[str, EvidenceRecord] = {}
        self.last_search = {}
        self.search_history = []
        self.search_calls = 0
        self.discovery_calls = 0
        self.cooldowns = {}
        self.health = {}
        self.control = None

    def begin_turn(self):
        self.turn_evidence.clear()
        self.search_calls = self.discovery_calls = 0
        self.last_search = {}
        self.search_history = []

    def clear(self):
        self.begin_turn()
        self.evidence.clear()
        self.adapters.clear()
        self.cooldowns.clear()

    def descriptors(self):
        sources = [s for s in self.base.sources if not s.is_fixture and s.status == "enabled"
                   and (s.adapter != "tavily" or os.getenv("TAVILY_API_KEY"))]
        for item in self.repository.list_sources(self.user):
            if item["status"] != "enabled":
                continue
            sources.append(self._descriptor(item))
        return tuple(sources)

    @staticmethod
    def _descriptor(item):
        return SourceDescriptor(id=item["id"], name=item["name"], domains=(item["domain"],),
            kinds=("news",), regions=("*",), languages=("*",), adapter="rss",
            status="enabled", homepage=item["url"], filters=("kind",))

    def _adapter(self, source):
        if source.id not in self.adapters:
            options = {"http": self.http}
            if source.adapter == "tavily":
                options["api_key"] = os.getenv("TAVILY_API_KEY")
            self.adapters[source.id] = self.factory(source, **options)
        return self.adapters[source.id]

    def search(self, query, domains=None, *, kind=None, max_results=5, recent_days=None, for_ranking=False, time_scope=None):
        from newspaper_agent.sources.time_windows import scope_from_query
        time_scope = time_scope or scope_from_query(query)
        if time_scope not in (None, 'today', '24h'):
            raise ValueError('未知新闻时间范围')
        if self.control:
            self.control.check()
        if self.search_calls >= 3:
            raise ValueError("单轮最多三次检索")
        self.search_calls += 1
        profile = self.repository.profile(self.user)
        original_query = query
        if isinstance(query, str) and query.strip().casefold() in {"latest news", "news", "最新新闻", "最近新闻", "最新消息", "看看最新消息"} and profile["interests"]:
            query = profile["interests"][0]
        plan = plan_query(query)
        request = SearchRequest(plan.query, tuple(domains or infer_domains(query, profile)), kind=kind,
                                max_results=max_results, max_sources=3,
                                recent_days=1 if time_scope else recent_days if recent_days is not None else plan.recent_days)
        available = tuple(s for s in self.descriptors() if not s.topics or set(s.topics) & set(plan.topics))
        if plan.topics and set(request.domains) & {'news', 'general'}:
            specialized = [s for s in available if set(s.topics) & set(plan.topics)]
            if specialized:
                # Topic-specific sources replace broad world-news feeds, preserving
                # user-enabled additions and general search for coverage gaps.
                available = tuple(s for s in available if s in specialized or s.adapter == 'tavily'
                                  or s.id.startswith('source_') or 'news' not in s.domains and 'general' not in s.domains)
        catalog = SourceCatalog(available)
        # Explicitly enabled personal/shared additions get a small routing preference,
        # otherwise a fixed result budget can permanently starve newly learned sources.
        built_in = {s.id for s in self.base.sources}
        sources, warnings = catalog.route(request, {s.id: 2 for s in catalog.sources if s.id not in built_in})
        records, failures = [], []
        tasks = []
        for source in sources:
            if self.cooldowns.get(source.id, 0) > time.monotonic():
                failures.append({"source_id": source.id, "error": "temporary_cooldown"})
                continue
            tasks.append((source, self._adapter(source)))
        def retrieve(source, adapter):
            if self.control:
                self.control.emit('source_start', name=source.name)
            return adapter.search(request)
        # Each adapter owns its cache; only one operation per adapter is dispatched.
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(retrieve, s, a): s for s, a in tasks}
            found = {}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    found[source.id] = future.result()
                    self.health[source.id] = {'status': 'ok', 'checked_at': datetime.now(timezone.utc).isoformat(),
                                              'count': len(found[source.id])}
                    if self.control:
                        self.control.emit('source_end', name=source.name, count=len(found[source.id]))
                except Exception as exc:
                    if self.control:
                        self.control.check()
                    code = getattr(exc, 'code', 'source_unavailable')
                    failures.append({'source_id': source.id, 'error': code if isinstance(code, str) and len(code) < 80 else 'source_unavailable'})
                    self.health[source.id] = {'status': 'failed', 'checked_at': datetime.now(timezone.utc).isoformat(),
                                              'error': failures[-1]['error']}
                    self.cooldowns[source.id] = time.monotonic() + 60
                    if self.control:
                        self.control.emit('source_failed', name=source.name)
            for source in sources:
                records.extend(found.get(source.id, ()))
        chosen, decisions = select_evidence(records, 'latest news' if for_ranking else plan.query,
            {} if for_ranking else profile, limit=len(records) if for_ranking else request.max_results,
            recent_days=request.recent_days, time_scope=time_scope)
        if self.control:
            self.control.emit('filter_end', count=len(chosen), rejected=len(records) - len(chosen))
        for record in chosen:
            self.evidence[record.id] = record
            self.turn_evidence[record.id] = record
        warnings = list(warnings)
        if query != plan.query:
            warnings.append('主题检索词：' + plan.query)
        if request.recent_days:
            warnings.append(('按香港时间今天筛选；未知/不精确日期不计入当天。' if time_scope == 'today' else
                             '按过去24小时筛选；缺少带时区时间戳的资料不计入。' if time_scope == '24h' else
                             f'按近{request.recent_days}天筛选；发布日期未知的资料单独标注。'))
            if 'academic' in request.domains:
                warnings.append('当前选择学术研究：返回论文线索，不能代表完整的行业大新闻或产品发布。')
        if original_query != query:
            warnings.append("泛化查询已使用你的首个明确兴趣词：" + query)
        if not os.getenv("TAVILY_API_KEY"):
            warnings.append("未配置 Tavily：当前仅搜索已接入的专业数据库与 RSS，不是全网覆盖。")
        if not chosen:
            warnings.append("未找到匹配资料；这不表示相关事件不存在。可调整关键词、领域或补充来源。")
        if decisions and any(x["decision"] == "user_excluded" for x in decisions):
            warnings.append("有资料被个人排除词过滤；可通过反馈修正。")
        self.last_search = {"query": plan.query, "original_query": original_query, "recent_days": request.recent_days,
                            'time_scope': time_scope, "domains": list(request.domains), "selected_sources": [s.id for s in sources],
                            "results": [asdict(r) for r in chosen], "warnings": warnings, "failures": failures,
                            "decisions": list(decisions), "is_fixture": False}
        self.search_history.append(self.last_search)
        while len(self.evidence) > 100:
            del self.evidence[next(iter(self.evidence))]
        # Stored privately in a report; only compact audit details enter the model context.
        return self.last_search

    def collect_news(self, query, domains=None, *, recent_days=None, time_scope=None, include_public=False):
        from .subscriptions import collect_news
        return collect_news(self, query, domains, recent_days=recent_days, time_scope=time_scope, include_public=include_public)

    def fetch(self, evidence_id):
        if evidence_id not in self.evidence:
            raise ValueError("只能读取当前用户会话已有证据")
        record = self.evidence[evidence_id]
        descriptors = self.descriptors()
        visible = {s.id for s in descriptors}
        if record.source_id not in visible:
            raise ValueError("来源已停用或不可见")
        source = next(s for s in descriptors if s.id == record.source_id)
        if source.adapter == 'rss' and record.payload.get('feed_url') == source.homepage:
            # Persistent feed snapshots remain excerpts, not newly fetched article full text.
            return asdict(record)
        result = self.adapters[record.source_id].fetch(evidence_id)
        if result.id != evidence_id or result.source_id != record.source_id or result.is_fixture:
            raise ValueError("证据身份不一致")
        self.evidence[evidence_id] = self.turn_evidence[evidence_id] = result
        return asdict(result)

    def discover(self, *, query, domain, website=None):
        if domain not in DOMAINS or self.discovery_calls >= 2:
            raise ValueError("发现参数无效或超出单轮两次上限")
        self.discovery_calls += 1
        pages = []
        if website:
            public_url(website)
            pages = [website]
        else:
            batch = self.search(query, (domain,), max_results=3)
            for record in batch["results"]:
                parsed = urlsplit(record["url"])
                if parsed.scheme == "https":
                    pages.append(f"https://{parsed.netloc}")
            pages = list(dict.fromkeys(pages))[:2]
        candidates, errors = [], []
        existing = {s["url"] for s in self.repository.list_sources(self.user, include_pending=True)}
        existing.update(s.homepage for s in self.base.sources)
        for page in pages:
            try:
                links = discover_feed_links(page, http=self.http)
                # A URL supplied by the user may already be an RSS endpoint.
                if not links and website:
                    links = (page,)
                for feed_url in links[:3]:
                    if feed_url in existing:
                        continue
                    public_url(feed_url)
                    item = self.repository.store_source(self.user, urlsplit(feed_url).hostname or "RSS", feed_url, domain)
                    candidates.append(item)
                    existing.add(feed_url)
            except Exception:
                errors.append({"website": page, "error": "feed_discovery_failed"})
        return {"candidates": candidates[:6], "errors": errors,
                "next_step": "候选保持个人私密；validate 验证后 enable 启用。共享需 share/publish。",
                "coverage": "仅检查所给站点或当前搜索找到的最多两个站点；不是全网穷尽发现。"}

    def validate(self, source_id):
        item = self.repository.source(self.user, source_id)
        descriptor = self._descriptor(item)
        adapter = self.factory(descriptor, http=self.http)
        records = adapter.search(SearchRequest("latest news", (item["domain"],), max_results=1))
        # Valid XML with zero matches is still a valid feed; the adapter checks XML root.
        self.repository.set_source_status(self.user, source_id, "validated")
        return {"id": source_id, "status": "validated", "sample_count": len(records), "enabled": False}

    def snapshot(self):
        return tuple(self.turn_evidence.values())[:10]

    def audit(self):
        # Evidence lives in the news library/report, never duplicated in search audit.
        keys = {'mode', 'query', 'queries', 'original_query', 'recent_days', 'time_scope', 'coverage', 'domains', 'public_source_ids',
                'selected_sources', 'collection_status', 'warnings', 'failures', 'is_fixture', 'candidate_count'}
        compact = lambda batch: {k: v for k, v in batch.items() if k in keys}
        return {**compact(self.last_search), 'searches': [compact(batch) for batch in self.search_history]}
