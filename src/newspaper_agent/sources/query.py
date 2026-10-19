"""Small explicit query normalization; no model call or guessed translation."""
import re
from dataclasses import dataclass


ALIASES = {
    'ai': ('ai', 'artificial intelligence', '人工智能'),
    'agent': ('agent', 'agents', 'agentic', '智能体'),
    'llm': ('llm', 'llms', 'large language model', '大语言模型'),
}


def matches(term, text):
    if re.fullmatch(r'[a-z0-9_-]+', term):
        return re.search(r'(?<![a-z0-9_])' + re.escape(term) + r'(?![a-z0-9_])', text.casefold()) is not None
    return term.casefold() in text.casefold()


@dataclass(frozen=True)
class QueryPlan:
    query: str
    topics: tuple[str, ...]
    recent_days: int | None


def recent_window(text):
    value = text.casefold()
    from .time_windows import scope_from_query
    if scope_from_query(value):
        return 1
    for pattern, days in ((r'近7天|最近一周|过去一周|\blast week\b', 7),
                          (r'近30天|最近一个月|过去一个月|\blast month\b', 30),
                          (r'近90天|最近三个月|过去三个月', 90)):
        if re.search(pattern, value):
            return days
    return 90 if re.search(r'新闻|最新|最近|近期|进展|\b(news|latest|recent)\b', value) else None


def plan_query(text):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 400:
        raise ValueError('query 需为 1 到 400 字符')
    value = text.casefold().strip()
    recent = recent_window(value)
    topics = tuple(key for key, variants in ALIASES.items() if any(matches(v, value) for v in variants))
    # Preserve advanced fielded academic queries instead of rewriting their syntax.
    if re.search(r'\b(all|ti|au|abs|cat|id):', value):
        return QueryPlan(text.strip(), topics, recent)
    cleaned = value
    cleaned = re.sub(r'过去\s*24\s*小时|近\s*24\s*小时|今天|当天|今日|\btoday\b|\blast\s+24\s+hours\b', ' ', cleaned)
    cleaned = re.sub(r'近(?:7|30|90)天|最近一周|过去一周|最近一个月|过去一个月|最近三个月|过去三个月|\blast (?:week|month)\b', ' ', cleaned)
    for variants in ALIASES.values():
        for word in sorted(variants, key=len, reverse=True):
            if re.fullmatch(r'[a-z0-9_-]+', word):
                cleaned = re.sub(r'(?<![a-z0-9_])' + re.escape(word) + r'(?![a-z0-9_])', ' ', cleaned)
            else:
                cleaned = cleaned.replace(word, ' ')
    cleaned = re.sub(r'了解一下|了解|我想|我希望|给我|告诉我|想要|介绍一下|介绍|帮我|请问|请|看看|一下|搜一下|搜索|相关|关于|有哪些|有什么|大新闻|最新消息|最新新闻|最近新闻|新闻|最新|最近|近期|进展|的', ' ', cleaned)
    cleaned = re.sub(r'\b(latest|recent|news|please|search|find|about|and|the)\b', ' ', cleaned)
    cleaned = re.sub(r'(?<!\S)(和|与|以及|及)(?!\S)', ' ', cleaned)
    remaining = re.findall(r'[a-z0-9][a-z0-9_.-]+|[\u4e00-\u9fff]+', cleaned)
    # Keep AI as a compact query term; explicit aliases are used by the local gate.
    query = ' '.join((*topics, *remaining)) or text.strip()
    return QueryPlan(query[:400], topics, recent)
