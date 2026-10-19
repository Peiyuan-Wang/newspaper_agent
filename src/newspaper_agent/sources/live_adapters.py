"""Small official API/feed adapters; no arbitrary webpage crawling or model calls."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit, quote
import xml.etree.ElementTree as ET

from .contracts import EvidenceRecord, SearchRequest, SourceDescriptor
from .network import HTTPResponse, SafeHTTPClient, SourceNetworkError, validate_https_url
from .filtering import select_evidence

ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"
PUBMED_ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
TAVILY_ENDPOINT = "https://api.tavily.com/search"
CROSSREF_ENDPOINT = "https://api.crossref.org/works"
ATOM = "{http://www.w3.org/2005/Atom}"


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_text(value, limit=1800):
    parser = _TextParser()
    parser.feed(str(value or "")[:100_000])
    return " ".join(unescape(" ".join(parser.parts)).split())[:limit]


def _text(node, path):
    child = node.find(path)
    return "" if child is None else " ".join("".join(child.itertext()).split())


def _xml(response: HTTPResponse):
    body = response.body
    # PubMed includes an external DOCTYPE. ElementTree never fetches that DTD;
    # reject internal entity declarations and non-ASCII-compatible encodings.
    if len(body) > 2_000_000 or b"\x00" in body or b"<!ENTITY" in body.upper():
        raise SourceNetworkError("unsafe_xml")
    if re.search(br"<!DOCTYPE[^>]*\[", body, re.I):
        raise SourceNetworkError("unsafe_xml")
    try:
        return ET.fromstring(body)
    except (ET.ParseError, ValueError):
        raise SourceNetworkError("invalid_xml") from None


def _date(value):
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    # Preserve partial dates: do not invent January 1 for a year-only record.
    if re.fullmatch(r"\d{4}", value):
        return value
    if re.fullmatch(r"\d{4}-\d{2}", value) and 1 <= int(value[-2:]) <= 12:
        return value
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(value).isoformat()
        except (ValueError, TypeError, OverflowError):
            return None


def _citation_url(value, *, base=None):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if base:
        value = urljoin(base, value)
    # Normalize historical HTTP citations to HTTPS, never fetch HTTP URLs.
    if value.startswith("http://"):
        value = "https://" + value[7:]
    try:
        return validate_https_url(value)
    except SourceNetworkError:
        return None


class _CachedAdapter:
    def __init__(self, source, http=None):
        self.source = source
        self.http = http or SafeHTTPClient()
        self._records = {}
        self._searches = {}

    def _record(self, *, url, title, text, kind, published_at=None, level="snippet", payload=None, publisher=None):
        text = plain_text(text, 6000)
        record = EvidenceRecord(
            id=self.source.id + ":" + sha256((url + "\n" + text).encode()).hexdigest()[:24],
            source_id=self.source.id, provider_id=self.source.adapter,
            publisher=plain_text(publisher or self.source.name, 200), title=plain_text(title, 250),
            url=url, kind=kind, snippet=text, retrieved_at=datetime.now(timezone.utc).isoformat(),
            published_at=_date(published_at), content_level=level, is_fixture=False,
            payload={"schema_version": 1, "locator": url, "snapshot_id": sha256(text.encode()).hexdigest(), **(payload or {})},
        )
        self._records[record.id] = record
        while len(self._records) > 200:
            del self._records[next(iter(self._records))]
        return replace(record, snippet=text[:1800])

    def fetch(self, evidence_id):
        if evidence_id not in self._records:
            raise ValueError("只能读取本适配器已检索的证据")
        return self._records[evidence_id]

    def clear(self):
        self._records.clear()
        self._searches.clear()

    def _cached(self, request, ttl=300):
        cached = self._searches.get(request)
        if cached and time.monotonic() - cached[0] < ttl:
            for record in cached[1]:
                self._records.setdefault(record.id, record)
            return cached[1]
        return None

    def _remember(self, request, results):
        results = tuple(results[:request.max_results])
        self._searches[request] = (time.monotonic(), results)
        while len(self._searches) > 50:
            del self._searches[next(iter(self._searches))]
        return results


class ArxivAdapter(_CachedAdapter):
    def search(self, request: SearchRequest):
        cached = self._cached(request, 86_400)
        if cached is not None:
            return cached
        query = request.query.strip()
        if not re.search(r"\b(all|ti|au|abs|cat|id):", query):
            terms = re.findall(r"[\w.-]+", query)[:20]
            query = " AND ".join("all:" + term for term in terms)
        params = urlencode({"search_query": query, "start": 0, "max_results": request.max_results,
                            "sortBy": "submittedDate" if request.recent_days else "relevance", "sortOrder": "descending"})
        root = _xml(self.http.get(ARXIV_ENDPOINT + "?" + params))
        if root.tag != ATOM + "feed":
            raise SourceNetworkError("invalid_arxiv_response")
        results = []
        for item in root.findall(ATOM + "entry")[:request.max_results]:
            raw_id = _text(item, ATOM + "id")
            if "api/errors" in raw_id:
                raise SourceNetworkError("arxiv_query_error")
            url = _citation_url(raw_id)
            if not url or urlsplit(url).hostname != "arxiv.org" or not urlsplit(url).path.startswith("/abs/"):
                continue
            results.append(self._record(
                url=url, title=_text(item, ATOM + "title"), text=_text(item, ATOM + "summary"), kind="paper",
                published_at=_text(item, ATOM + "published"), level="abstract",
                payload={"arxiv_id": urlsplit(url).path.removeprefix("/abs/"),
                         "updated_at": _date(_text(item, ATOM + "updated")),
                         "authors": [_text(author, ATOM + "name")[:120] for author in item.findall(ATOM + "author")[:10]],
                         "publication_status": "preprint; peer_review_not_verified"},
            ))
        return self._remember(request, results)


class PubMedAdapter(_CachedAdapter):
    def search(self, request: SearchRequest):
        cached = self._cached(request)
        if cached is not None:
            return cached
        options = {"db": "pubmed", "term": request.query, "retmode": "json",
                   "retmax": request.max_results, "sort": "relevance", "tool": "newspaper_agent"}
        if request.recent_days:
            options.update(reldate=request.recent_days, datetype='pdat')
        params = urlencode(options)
        response = self.http.get(PUBMED_ENDPOINT + "esearch.fcgi?" + params).json()
        try:
            ids = response["esearchresult"]["idlist"]
            if not isinstance(ids, list):
                raise ValueError
            ids = [value for value in ids[:request.max_results] if isinstance(value, str) and re.fullmatch(r"\d{1,12}", value)]
        except (KeyError, TypeError, ValueError):
            raise SourceNetworkError("invalid_pubmed_response") from None
        if not ids:
            return self._remember(request, [])
        params = urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "tool": "newspaper_agent"})
        root = _xml(self.http.get(PUBMED_ENDPOINT + "efetch.fcgi?" + params))
        if root.tag != "PubmedArticleSet":
            raise SourceNetworkError("invalid_pubmed_response")
        results = []
        for item in root.findall("PubmedArticle")[:request.max_results]:
            pmid = _text(item, "MedlineCitation/PMID")
            if pmid not in ids:
                continue
            article = item.find("MedlineCitation/Article")
            if article is None:
                continue
            abstract = " ".join(((section.get("Label", "") + ": ") if section.get("Label") else "")
                                + "".join(section.itertext()) for section in article.findall("Abstract/AbstractText"))
            date = article.find("ArticleDate")
            if date is None:
                date = article.find("Journal/JournalIssue/PubDate")
            published = None
            if date is not None:
                year, month, day = _text(date, "Year"), _text(date, "Month"), _text(date, "Day")
                if re.fullmatch(r"\d{4}", year):
                    published = year
                    months = {name: f"{i:02}" for i, name in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
                    month = months.get(month[:3], month)
                    if month.isdigit() and 1 <= int(month) <= 12:
                        published += "-" + month.zfill(2)
                        if day.isdigit():
                            published += "-" + day.zfill(2)
            doi = next((node.text for node in item.findall("PubmedData/ArticleIdList/ArticleId")
                        if node.get("IdType") == "doi"), None)
            results.append(self._record(
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", title=_text(article, "ArticleTitle"),
                text=abstract or "No abstract supplied by PubMed.", kind="paper", published_at=published,
                level="abstract" if abstract else "metadata", publisher=_text(article, "Journal/Title") or "PubMed",
                payload={"pmid": pmid, "doi": doi[:200] if doi else None, "has_abstract": bool(abstract)},
            ))
        return self._remember(request, results)


class CrossrefAdapter(_CachedAdapter):
    """Public scholarly metadata; an absent abstract is never replaced by an invented one."""
    def search(self, request: SearchRequest):
        cached = self._cached(request)
        if cached is not None:
            return cached
        options = {"query": request.query, "rows": request.max_results}
        if request.recent_days:
            options['filter'] = 'from-pub-date:' + (datetime.now(timezone.utc) - timedelta(days=request.recent_days)).date().isoformat()
        response = self.http.get(CROSSREF_ENDPOINT + "?" + urlencode(options)).json()
        try:
            items = response["message"]["items"]
            if not isinstance(items, list):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise SourceNetworkError("invalid_crossref_response") from None
        results = []
        for item in items[:request.max_results]:
            if not isinstance(item, dict):
                continue
            doi, titles = item.get("DOI"), item.get("title")
            if not isinstance(doi, str) or not doi.startswith("10.") or not isinstance(titles, list) or not titles or not isinstance(titles[0], str):
                continue
            published = None
            date_parts = item.get("published", {}).get("date-parts", []) if isinstance(item.get("published"), dict) else []
            if date_parts and isinstance(date_parts[0], list) and 1 <= len(date_parts[0]) <= 3 and all(type(n) is int for n in date_parts[0]):
                published = "-".join(str(n) if i == 0 else str(n).zfill(2) for i, n in enumerate(date_parts[0]))
            abstract = item.get("abstract") if isinstance(item.get("abstract"), str) else None
            results.append(self._record(url="https://doi.org/" + quote(doi, safe="/"), title=titles[0],
                text=abstract or ("Metadata only: " + titles[0] + ". No abstract supplied by Crossref."),
                kind="paper", published_at=published, level="abstract" if abstract else "metadata",
                publisher=item.get("publisher") if isinstance(item.get("publisher"), str) else "Crossref",
                payload={"doi": doi[:200], "has_abstract": bool(abstract), "article_fulltext_fetched": False}))
        return self._remember(request, results)


class RSSAdapter(_CachedAdapter):
    def __init__(self, source, *, endpoint=None, http=None):
        super().__init__(source, http)
        self.endpoint = validate_https_url(endpoint or source.homepage, allow_query=False)
        self._feed = None

    def _load(self):
        if self._feed and time.monotonic() - self._feed[0] < 300:
            return self._feed[1]
        root = _xml(self.http.get(self.endpoint))
        if root.tag == ATOM + "feed":
            entries, atom = root.findall(ATOM + "entry"), True
        elif root.tag == "rss":
            entries, atom = root.findall("channel/item"), False
        elif root.tag == "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF":
            entries, atom = root.findall("{http://purl.org/rss/1.0/}item"), False
        else:
            raise SourceNetworkError("not_a_feed")
        results = []
        for item in entries[:200]:
            prefix = ATOM if atom else "{http://purl.org/rss/1.0/}" if item.tag.startswith("{") else ""
            if atom:
                raw_url = next((link.get("href", "") for link in item.findall(ATOM + "link")
                                if link.get("rel", "alternate") == "alternate"), "")
                summary = _text(item, ATOM + "summary") or _text(item, ATOM + "content")
                published = _text(item, ATOM + "published")  # updated is not publication time
            else:
                raw_url = _text(item, prefix + "link")
                summary = _text(item, prefix + "description") or _text(item, "{http://purl.org/rss/1.0/modules/content/}encoded")
                published = _text(item, "pubDate") or _text(item, "{http://purl.org/dc/elements/1.1/}date")
            url = _citation_url(raw_url, base=self.endpoint) if raw_url else None
            title = _text(item, prefix + "title")
            if not url or not title:
                continue
            results.append(self._record(
                url=url, title=title, text=summary or title, kind=self.source.kinds[0],
                published_at=published, level="feed", payload={"feed_url": self.endpoint,
                    "coverage": "current_feed_only", "article_fulltext_fetched": False,
                    "source_topics": list(getattr(self.source, 'topics', ()))},
            ))
        self._feed = (time.monotonic(), tuple(results))
        return tuple(results)

    def collect(self):
        """Collect the bounded feed before applying any user's topic filters."""
        return self._load()

    def search(self, request: SearchRequest):
        records = self.collect()
        # Relevance/time checks precede truncation: a vague AI article cannot use
        # up the feed budget and hide a more specific agent item farther down.
        chosen, _ = select_evidence(records, request.query, {}, limit=request.max_results,
                                    recent_days=request.recent_days)
        return chosen

    def clear(self):
        super().clear()
        self._feed = None


class TavilyAdapter(_CachedAdapter):
    def __init__(self, source, *, api_key=None, http=None):
        super().__init__(source, http)
        if not isinstance(api_key, str) or not api_key.strip():
            raise SourceNetworkError("missing_search_key")
        if any(ord(c) < 33 or ord(c) > 126 for c in api_key) or len(api_key) > 512:
            raise SourceNetworkError("invalid_search_key")
        self._api_key = api_key

    def search(self, request: SearchRequest):
        cached = self._cached(request)
        if cached is not None:
            return cached
        payload = {"query": request.query, "max_results": request.max_results,
                   "search_depth": "basic", "topic": "news" if "news" in request.domains else "general",
                   "include_answer": False, "include_raw_content": False, "auto_parameters": False}
        response = self.http.post_json(TAVILY_ENDPOINT, payload,
                                      headers={"Authorization": "Bearer " + self._api_key}).json()
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise SourceNetworkError("invalid_tavily_response")
        results = []
        for item in response["results"][:request.max_results]:
            if not isinstance(item, dict):
                continue
            url = _citation_url(item.get("url"))
            if not url or not isinstance(item.get("title"), str):
                continue
            results.append(self._record(
                url=url, title=item["title"], text=item.get("content", ""),
                kind="news" if payload["topic"] == "news" else "web", published_at=item.get("published_date"),
                publisher=urlsplit(url).hostname, payload={"search_depth": "basic", "article_fulltext_fetched": False},
            ))
        return self._remember(request, results)


def build_adapter(source: SourceDescriptor, *, endpoint: str | None = None,
                  api_key: str | None = None, http=None):
    """Only RSS endpoints are configurable. Credential destinations are fixed."""
    if source.is_fixture:
        raise ValueError("真实适配器不能用于虚构来源")
    if source.adapter == "rss":
        return RSSAdapter(source, endpoint=endpoint, http=http)
    fixed = {"arxiv": ARXIV_ENDPOINT, "pubmed": PUBMED_ENDPOINT, "crossref": CROSSREF_ENDPOINT, "tavily": TAVILY_ENDPOINT}
    if source.adapter not in fixed:
        raise ValueError("未实现的真实来源适配器")
    if endpoint is not None and endpoint != fixed[source.adapter]:
        raise ValueError("官方 API 端点不能覆盖")
    if source.adapter == "arxiv":
        return ArxivAdapter(source, http)
    if source.adapter == "pubmed":
        return PubMedAdapter(source, http)
    if source.adapter == "crossref":
        return CrossrefAdapter(source, http)
    return TavilyAdapter(source, api_key=api_key, http=http)


class _FeedLinks(HTMLParser):
    def __init__(self, page_url):
        super().__init__(convert_charrefs=True)
        self.page_url, self.links = page_url, []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if (tag == "link" and "alternate" in (values.get("rel") or "").lower().split()
                and (values.get("type") or "").lower() in ("application/rss+xml", "application/atom+xml")
                and values.get("href") and len(self.links) < 8):
            url = urljoin(self.page_url, values["href"])
            try:
                validate_https_url(url, allow_query=False)
            except SourceNetworkError:
                return
            if url not in self.links:
                self.links.append(url)


def discover_feed_links(page_url: str, *, http=None) -> tuple[str, ...]:
    """One bounded HTML read. Returns candidates; does not activate or fetch them."""
    validate_https_url(page_url, allow_query=False)
    response = (http or SafeHTTPClient()).get(page_url)
    if len(response.body) > 1_000_000:
        raise SourceNetworkError("response_too_large")
    parser = _FeedLinks(page_url)
    parser.feed(response.body.decode("utf-8", errors="replace"))
    return tuple(parser.links)
