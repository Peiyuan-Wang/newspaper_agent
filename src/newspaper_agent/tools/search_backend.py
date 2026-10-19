"""搜索后端与统一结果结构，不依赖模型客户端。"""

from dataclasses import asdict, dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
import math
import os
from pathlib import Path
import re
import time
from typing import Callable, Protocol
from urllib.parse import urlsplit

import httpx
import yaml

from .base import ToolExecutionError


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published_at: str | None = None


class SearchBackend(Protocol):
    name: str
    is_fixture: bool

    def search(self, query: str, *, max_results: int, time_range: str | None) -> list[SearchResult]: ...


@dataclass(frozen=True)
class SearchSettings:
    provider: str = "tavily"
    api_key_env: str = "TAVILY_API_KEY"
    timeout: float = 20.0
    max_attempts: int = 1
    max_results: int = 3


def load_search_settings(path: Path) -> SearchSettings:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError()
        settings = SearchSettings(**raw)
    except (OSError, UnicodeError, yaml.YAMLError, TypeError, ValueError) as exc:
        raise ValueError("搜索配置无效，请检查 --search-config；密钥只能放在环境变量中") from exc
    if settings.provider != "tavily":
        raise ValueError("当前真实搜索仅支持 tavily")
    if not isinstance(settings.api_key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", settings.api_key_env):
        raise ValueError("搜索 api_key_env 必须是合法环境变量名")
    if type(settings.timeout) not in (int, float) or not math.isfinite(settings.timeout) or not 0 < settings.timeout <= 120:
        raise ValueError("搜索 timeout 必须大于 0 且不超过 120 秒")
    if type(settings.max_attempts) is not int or not 1 <= settings.max_attempts <= 3:
        raise ValueError("搜索 max_attempts 必须是 1 到 3 的整数")
    if type(settings.max_results) is not int or not 1 <= settings.max_results <= 5:
        raise ValueError("搜索 max_results 必须是 1 到 5 的整数")
    return settings


def normalize_date(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    # 只解析确定的日期格式，不把“昨天”等相对文本猜成时间。
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(value).isoformat()
        except (ValueError, TypeError, OverflowError):
            return None


class FixtureSearchBackend:
    name = "fixture"
    is_fixture = True

    def search(self, query, *, max_results, time_range):
        # 固定教学样例，不进行真实搜索或日期筛选，不是当天新闻。
        return [SearchResult(
            title="[虚构样例] 示例机构发布新闻资料整理方案",
            url="https://news.example.invalid/sample-1",
            snippet="固定教学数据：示例机构介绍资料整理方法。不是实际新闻，不可作为事实证据。",
            published_at=None,
        ), SearchResult(
            title="[虚构样例] 示例团队展示检索流程",
            url="https://news.example.invalid/sample-2",
            snippet="固定教学数据：用于演示标题、链接、摘要和未知发布时间的传递。",
            published_at=None,
        )][:max_results]


class TavilySearchBackend:
    name = "tavily"
    is_fixture = False

    def __init__(self, settings: SearchSettings, *, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._api_key = api_key or os.getenv(settings.api_key_env)
        if not self._api_key or not self._api_key.strip():
            raise ValueError(f"缺少搜索密钥，请设置环境变量 {settings.api_key_env}")
        self._transport = transport
        self._sleep = sleep

    def search(self, query, *, max_results, time_range):
        payload = {
            "query": query, "topic": "news", "search_depth": "basic",
            "max_results": max_results, "include_answer": False,
            "include_raw_content": False, "include_images": False,
            "auto_parameters": False, "include_published_date": True,
        }
        if time_range is not None:
            payload["time_range"] = time_range
        for attempt in range(self.settings.max_attempts):
            try:
                with httpx.Client(timeout=self.settings.timeout, transport=self._transport) as client:
                    response = client.post("https://api.tavily.com/search", json=payload,
                        headers={"Authorization": f"Bearer {self._api_key}"})
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in {408, 429, 500, 502, 503, 504} and attempt + 1 < self.settings.max_attempts:
                    self._sleep(min(2 ** attempt, 4))
                    continue
                code = {401: "search_auth_failed", 403: "search_forbidden", 429: "search_rate_limited",
                        432: "search_quota_exceeded", 433: "search_quota_exceeded"}.get(status, "search_http_error")
                raise ToolExecutionError(code) from exc
            except httpx.RequestError as exc:
                if attempt + 1 < self.settings.max_attempts:
                    self._sleep(min(2 ** attempt, 4))
                    continue
                code = "search_timeout" if isinstance(exc, httpx.TimeoutException) else "search_network_error"
                raise ToolExecutionError(code) from exc
            try:
                raw = response.json()
                if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
                    raise ValueError()
                results = []
                for item in raw["results"][:max_results]:
                    if not isinstance(item, dict):
                        raise ValueError()
                    title, url, content = item.get("title"), item.get("url"), item.get("content")
                    if not all(isinstance(value, str) for value in (title, url, content)):
                        raise ValueError()
                    parsed = urlsplit(url)
                    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                            or parsed.username or parsed.password or len(url) > 1500):
                        continue
                    if self._api_key in url:
                        continue
                    results.append(SearchResult(
                        title=title.replace(self._api_key, "[redacted]")[:200], url=url,
                        snippet=content.replace(self._api_key, "[redacted]")[:800],
                        published_at=normalize_date(item.get("published_date")),
                    ))
                return results
            except (ValueError, TypeError, KeyError) as exc:
                raise ToolExecutionError("search_invalid_response") from exc
        raise ToolExecutionError("search_network_error")
