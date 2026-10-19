import io
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from newspaper_agent.sources.contracts import SearchRequest
from newspaper_agent.sources.live_adapters import (
    ARXIV_ENDPOINT, PUBMED_ENDPOINT, TAVILY_ENDPOINT, build_adapter, discover_feed_links,
)
from newspaper_agent.sources.network import (
    HTTPResponse, SafeHTTPClient, SourceNetworkError, _PinnedHTTPSConnection,
    resolve_public_addresses, validate_https_url,
)


def source(adapter, *, kinds=("paper",)):
    return SimpleNamespace(id="test_source", adapter=adapter, name="Test source", kinds=kinds,
                           homepage="https://example.org/feed", is_fixture=False)


class FakeHTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url):
        self.calls.append(("GET", url))
        return HTTPResponse(self.responses.pop(0))

    def post_json(self, url, payload, headers=None):
        self.calls.append(("POST", url, payload, headers))
        return HTTPResponse(self.responses.pop(0))


ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2601.00001v2</id><title>Example AI paper</title>
<summary>Reliable abstract with a condition.</summary><published>2026-01-02T12:00:00Z</published>
<updated>2026-02-03T12:00:00Z</updated><author><name>A. Researcher</name></author>
</entry></feed>'''


def test_arxiv_cache_citations_and_no_arbitrary_fetch():
    http = FakeHTTP(ATOM)
    adapter = build_adapter(source("arxiv"), http=http)
    request = SearchRequest("AI paper", ("academic",))
    result = adapter.search(request)
    assert len(result) == 1 and not result[0].is_fixture
    assert result[0].url == "https://arxiv.org/abs/2601.00001v2"
    assert result[0].content_level == "abstract"
    assert result[0].payload["publication_status"].startswith("preprint")
    assert result[0].published_at.startswith("2026-01-02")
    params = parse_qs(urlsplit(http.calls[0][1]).query)
    assert params["search_query"] == ["all:AI AND all:paper"]
    assert adapter.search(request) == result and len(http.calls) == 1
    assert adapter.fetch(result[0].id).snippet == result[0].snippet
    with pytest.raises(ValueError):
        adapter.fetch("https://127.0.0.1/")
    adapter.clear()
    with pytest.raises(ValueError):
        adapter.fetch(result[0].id)


def test_arxiv_query_error_is_not_an_article():
    http = FakeHTTP(ATOM.replace(b"http://arxiv.org/abs/2601.00001v2", b"http://arxiv.org/api/errors#bad_query"))
    with pytest.raises(SourceNetworkError, match="arxiv_query_error"):
        build_adapter(source("arxiv"), http=http).search(SearchRequest("bad", ("academic",)))


def test_pubmed_two_step_abstract_and_partial_date():
    xml = b'''<?xml version="1.0"?><!DOCTYPE PubmedArticleSet SYSTEM "https://example.org/unused.dtd">
    <PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1234</PMID><Article>
    <ArticleTitle>Medical <i>evidence</i></ArticleTitle><Journal><Title>Medical Journal</Title>
    <JournalIssue><PubDate><Year>2025</Year><Month>Jan</Month></PubDate></JournalIssue></Journal>
    <Abstract><AbstractText Label="RESULTS">Works under condition X.</AbstractText></Abstract></Article>
    </MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/test</ArticleId>
    </ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>'''
    http = FakeHTTP(b'{"esearchresult":{"idlist":["1234","not-an-id"]}}', xml)
    result = build_adapter(source("pubmed"), http=http).search(SearchRequest("test", ("medical",)))
    assert len(http.calls) == 2 and len(result) == 1
    assert result[0].url == "https://pubmed.ncbi.nlm.nih.gov/1234/"
    assert result[0].published_at == "2025-01"
    assert result[0].payload["doi"] == "10.1/test"
    assert "condition X" in result[0].snippet and result[0].content_level == "abstract"


def test_pubmed_empty_no_extra_request():
    http = FakeHTTP(b'{"esearchresult":{"idlist":[]}}')
    assert build_adapter(source("pubmed"), http=http).search(SearchRequest("test", ("medical",))) == ()
    assert len(http.calls) == 1


@pytest.mark.parametrize("xml", [b'<rss><broken>', b'<!DOCTYPE rss [<!ENTITY x "hello">]><rss/>',
                                   b'<rss>\x00</rss>'])
def test_malformed_or_entity_xml_rejected(xml):
    with pytest.raises(SourceNetworkError):
        build_adapter(source("rss"), http=FakeHTTP(xml)).search(SearchRequest("test", ("news",)))


RSS = b'''<rss version="2.0"><channel><title>News</title>
<item><title>AI policy update</title><link>https://news.example.org/ai</link>
<description><![CDATA[<p>Research policy detail</p><script>steal()</script>]]></description>
<pubDate>Tue, 13 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Sports</title><link>https://news.example.org/sports</link><description>Match</description></item>
<item><title>Local target</title><link>https://127.0.0.1/</link><description>AI</description></item>
</channel></rss>'''


def test_rss_filters_and_does_not_label_feed_as_fulltext():
    http = FakeHTTP(RSS)
    adapter = build_adapter(source("rss", kinds=("news",)), http=http)
    results = adapter.search(SearchRequest("AI policy", ("news",)))
    assert len(results) == 1 and results[0].title == "AI policy update"
    assert "steal" not in results[0].snippet
    assert results[0].content_level == "feed"
    assert not results[0].payload["article_fulltext_fetched"]
    assert adapter.fetch(results[0].id).content_level == "feed"
    assert adapter.search(SearchRequest("unmatched", ("news",))) == ()
    assert len(http.calls) == 1


def test_atom_updated_is_not_publication_date():
    atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>AI</title>
    <link href="/article"/><summary>Example</summary><updated>2026-01-02T12:00:00Z</updated></entry></feed>'''
    result = build_adapter(source("rss"), http=FakeHTTP(atom)).search(SearchRequest("AI", ("academic",)))[0]
    assert result.url == "https://example.org/article" and result.published_at is None


def test_tavily_key_fixed_destination_and_no_paid_depth_escalation():
    with pytest.raises(SourceNetworkError, match="missing_search_key"):
        build_adapter(source("tavily"))
    with pytest.raises(ValueError, match="官方"):
        build_adapter(source("tavily"), api_key="test-key", endpoint="https://attacker.org")
    http = FakeHTTP(json.dumps({"results": [{"title": "News", "url": "https://news.example.org/a",
                                           "content": "Summary", "published_date": None}]}).encode())
    adapter = build_adapter(source("tavily"), api_key="unit-test-only", http=http)
    result = adapter.search(SearchRequest("news", ("news",), max_results=1))[0]
    method, endpoint, payload, headers = http.calls[0]
    assert endpoint == TAVILY_ENDPOINT and headers == {"Authorization": "Bearer unit-test-only"}
    assert payload["search_depth"] == "basic" and payload["max_results"] == 1
    assert payload["include_answer"] is False and payload["include_raw_content"] is False
    assert result.content_level == "snippet" and result.published_at is None
    assert "unit-test-only" not in repr(result)


def test_discover_only_returns_safe_explicit_feed_links():
    html = b'''<html><head><link rel="alternate" type="application/rss+xml" href="/rss.xml">
    <link rel="alternate" type="application/atom+xml" href="https://127.0.0.1/feed">
    <link rel="alternate" type="application/rss+xml" href="https://example.org/feed?token=secret">
    <link rel="stylesheet" href="/style.css"></head><script>fetch('/evil')</script></html>'''
    http = FakeHTTP(html)
    assert discover_feed_links("https://example.org/blog", http=http) == ("https://example.org/rss.xml",)
    assert len(http.calls) == 1


@pytest.mark.parametrize("url", ["http://example.org", "https://localhost/", "https://127.0.0.1/",
    "https://[::1]/", "https://[::ffff:127.0.0.1]/", "https://224.0.0.1/", "https://169.254.169.254/",
    "https://example.org:8443", "https://secret@example.org", "https://example.org/#x",
    "https://example.org\\@127.0.0.1/", "https://example.org/\nHeader:secret", "https://0x7f000001/"])
def test_network_rejects_unsafe_syntax(url):
    with pytest.raises(SourceNetworkError, match="unsafe_url"):
        validate_https_url(url)


def test_dns_mixed_public_private_answers_rejected(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443)),
                                                             (2, 1, 6, "", ("10.0.0.1", 443))])
    with pytest.raises(SourceNetworkError, match="unsafe_dns"):
        resolve_public_addresses("example.org")


def test_connection_pins_ip_but_tls_uses_hostname(monkeypatch):
    events = []
    class Sock:
        def getpeername(self): return ("93.184.216.34", 443)
        def close(self): events.append("closed")
    class Context:
        def wrap_socket(self, sock, *, server_hostname):
            events.append(("sni", server_hostname))
            return sock
    def connect(address, timeout):
        events.append(("connect", address))
        return Sock()
    monkeypatch.setattr("socket.create_connection", connect)
    connection = _PinnedHTTPSConnection("example.org", "93.184.216.34", 3)
    connection._context = Context()
    connection.connect()
    assert events == [("connect", ("93.184.216.34", 443)), ("sni", "example.org")]


def test_connection_rejects_changed_peer(monkeypatch):
    class Sock:
        def getpeername(self): return ("127.0.0.1", 443)
        def close(self): pass
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: Sock())
    with pytest.raises(SourceNetworkError, match="unsafe_peer"):
        _PinnedHTTPSConnection("example.org", "93.184.216.34", 3).connect()


@pytest.mark.parametrize("status,headers,body,code", [
    (302, {"Location": "https://127.0.0.1"}, b"", "redirect_blocked"),
    (429, {}, b"secret server text", "rate_limited"),
    (403, {}, b"secret server text", "access_denied"),
    (200, {"Content-Length": "99999999"}, b"", "response_too_large"),
    (200, {"Content-Encoding": "gzip"}, b"", "unsupported_encoding"),
    (200, {}, b"x" * 1025, "response_too_large"),
])
def test_http_redirect_size_and_error_redaction(monkeypatch, status, headers, body, code):
    class Response:
        def __init__(self):
            self.status, self.stream = status, io.BytesIO(body)
        def getheader(self, key, default=None): return headers.get(key, default)
        def read1(self, size): return self.stream.read(size)
    class Connection:
        sock = None
        def __init__(self, *a): pass
        def request(self, *a, **k): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr("newspaper_agent.sources.network.resolve_public_addresses", lambda host: ("93.184.216.34",))
    monkeypatch.setattr("newspaper_agent.sources.network._PinnedHTTPSConnection", Connection)
    with pytest.raises(SourceNetworkError) as error:
        SafeHTTPClient(max_bytes=1024).get("https://example.org/feed")
    assert str(error.value) == code and "secret" not in str(error.value)
