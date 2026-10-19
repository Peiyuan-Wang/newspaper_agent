from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
import ipaddress
import re
from urllib.parse import urlsplit

DOMAINS = ("academic", "medical", "finance", "news", "general")
KINDS = ("paper", "trial", "filing", "news", "web")
STATUSES = ("discovered", "reviewed", "validated", "enabled", "suspended", "rejected", "archived")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise ValueError("无效标识符")
    return value


def public_url(value):
    """仅候选 URL 语法检查，不联网，不代替 DNS/重定向安全检查。"""
    try:
        if not isinstance(value, str) or len(value) > 1500 or any(c.isspace() for c in value):
            raise ValueError
        parts = urlsplit(value)
        host = parts.hostname
        if (parts.scheme != "https" or not host or parts.username or parts.password
                or parts.port not in (None, 443) or parts.query or parts.fragment):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if not address.is_global:
                raise ValueError
        else:
            if ("." not in host or host.endswith((".local", ".internal"))
                    or not re.fullmatch(r"[a-zA-Z0-9.-]+", host)
                    or re.fullmatch(r"[0-9.]+", host)):
                raise ValueError
            if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                   for label in host.split(".")):
                raise ValueError
    except (ValueError, TypeError):
        raise ValueError("来源 URL 需为无凭证、无查询参数的公共 HTTPS 地址") from None
    return value


@dataclass(frozen=True)
class SourceDescriptor:
    id: str
    name: str
    domains: tuple[str, ...]
    kinds: tuple[str, ...]
    regions: tuple[str, ...]
    languages: tuple[str, ...]
    adapter: str
    status: str
    homepage: str
    role: str = "discovery"
    capabilities: tuple[str, ...] = ("search", "fetch")
    filters: tuple[str, ...] = ()
    is_fixture: bool = False
    topics: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.id)
        if not isinstance(self.topics, tuple) or len(self.topics) > 20:
            raise ValueError('来源主题无效')
        for topic in self.topics:
            identifier(topic)
        public_url(self.homepage)
        if not isinstance(self.name, str) or not 1 <= len(self.name) <= 120:
            raise ValueError("无效来源名称")
        for values, allowed in ((self.domains, DOMAINS), (self.kinds, KINDS),
                                (self.capabilities, ("search", "fetch")),
                                (self.filters, ("region", "language", "kind"))):
            if not isinstance(values, tuple) or any(v not in allowed for v in values):
                raise ValueError("无效来源能力或分类")
        if not self.domains or not self.kinds or self.status not in STATUSES:
            raise ValueError("无效来源分类或状态")
        for values in (self.regions, self.languages):
            if (not isinstance(values, tuple) or not values
                    or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z*-]{1,16}", v) for v in values)):
                raise ValueError("无效地域或语言")
        if self.role not in ("primary", "discovery") or type(self.is_fixture) is not bool:
            raise ValueError("无效来源角色")
        if self.adapter not in ("fixture", "unimplemented", "arxiv", "pubmed", "crossref", "rss", "tavily"):
            raise ValueError("未知适配器；不允许动态导入代码")
        if self.status == "enabled" and self.adapter == "unimplemented":
            raise ValueError("未实现的适配器不可启用")
        if (self.adapter == "fixture") != self.is_fixture:
            raise ValueError("样例标识必须与适配器一致")


@dataclass(frozen=True)
class SearchRequest:
    query: str
    domains: tuple[str, ...]
    kind: str | None = None
    region: str | None = None
    language: str | None = None
    max_results: int = 3
    max_sources: int = 2
    recent_days: int | None = None

    def __post_init__(self):
        if not isinstance(self.query, str) or not 1 <= len(self.query.strip()) <= 400:
            raise ValueError("query 需为 1 到 400 字符")
        if not isinstance(self.domains, tuple) or not self.domains or any(v not in DOMAINS for v in self.domains):
            raise ValueError("无效任务领域")
        if self.kind is not None and self.kind not in KINDS:
            raise ValueError("无效资料类型")
        for value in (self.region, self.language):
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z-]{1,16}", value)):
                raise ValueError("无效地域或语言筛选")
        for value, upper in ((self.max_results, 5), (self.max_sources, 3)):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError("检索数量超限")
        if self.recent_days is not None and (type(self.recent_days) is not int or not 1 <= self.recent_days <= 3660):
            raise ValueError('时间窗口无效')


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    source_id: str
    provider_id: str
    publisher: str
    title: str
    url: str
    kind: str
    snippet: str
    retrieved_at: str
    published_at: str | None = None
    content_level: str = "snippet"
    is_fixture: bool = True
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SearchBatch:
    selected_sources: tuple[str, ...]
    results: tuple[EvidenceRecord, ...]
    warnings: tuple[str, ...]
    failures: tuple[dict, ...] = ()
    is_fixture: bool = True


class SourceAdapter(Protocol):
    def search(self, request: SearchRequest) -> tuple[EvidenceRecord, ...]: ...
    def fetch(self, evidence_id: str) -> EvidenceRecord: ...
