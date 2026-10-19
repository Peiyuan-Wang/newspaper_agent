"""Product-level regression using fake network/model responses, never paid APIs."""
from dataclasses import asdict
import json
from pathlib import Path
import httpx
import pytest

from newspaper_agent.agent_core.messages import Message, ToolCall
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings, ModelCommunicationError
from newspaper_agent.research.cli import main, interactive
from newspaper_agent.research.service import ResearchSources
from newspaper_agent.research.session import ResearchSession
from newspaper_agent.research.tools import ResearchTools
from newspaper_agent.sources.contracts import EvidenceRecord, SearchRequest
from newspaper_agent.sources.filtering import select_evidence
from newspaper_agent.sources.live_adapters import build_adapter
from newspaper_agent.sources.network import HTTPResponse, SourceNetworkError

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "config/sources/live.yaml"
RSS = b'''<rss version="2.0"><channel><title>Test feed</title><item>
<title>robotics study</title><link>https://example.org/robots</link>
<description>Robotics improved only in the laboratory. Field performance is unknown.</description>
<pubDate>Tue, 13 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'''


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url in {"https://example.org", "https://example.org/discover"}:
            return HTTPResponse(b'<link rel="alternate" type="application/rss+xml" href="/rss.xml">')
        return HTTPResponse(RSS)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    repo.register("alice", "test-password-alice")
    repo.register("bob", "test-password-bob")
    http = FakeHTTP()
    sources = ResearchSources(repo, "alice", CATALOG, http=http)
    return repo, sources, http


def feedback_args(**updates):
    return {"kind": "missed", "user_quote": "我关注 robotics，但没搜到", "interests_add": ["robotics"],
            "interests_remove": [], "excluded_add": [], "excluded_remove": [], "domains": [], "style": None, **updates}


def claim_json(evidence):
    return json.dumps({"claims": [{"statement": "Robotics improved in the laboratory.",
        "conditions": ["Only in the laboratory."], "uncertainty": "Field performance is unknown.",
        "status": "unverified", "evidence_refs": [{"evidence_id": evidence["id"], "quote": evidence["snippet"]}]}], "follow_up": []})


@pytest.mark.parametrize("wire", ["chat", "responses"])
def test_authenticated_search_synthesis_save_and_history_both_protocols(workspace, wire):
    repo, sources, http = workspace
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            args = json.dumps({"query": "robotics", "domains": ["news"], "kind": "news"})
            if wire == "responses":
                return httpx.Response(200, json={"status": "completed", "output": [{"type": "function_call", "call_id": "s1", "name": "research_search", "arguments": args}]})
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "s1", "type": "function", "function": {"name": "research_search", "arguments": args}}]}}]})
        if len(calls) == 2:
            text = "DRAFT must not become the next-turn answer"
        else:
            assert "tools" not in body  # One isolated synthesis call; no write tools.
            text = claim_json(asdict(sources.snapshot()[0]))
        if wire == "responses":
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text}]}]})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text}}]})
    client = OpenAICompatibleClient(OpenAICompatibleSettings("test", "https://example.org/v1", "test", "TEST_KEY", wire_api=wire),
                                   api_key="unit-test", transport=httpx.MockTransport(handler))
    session = ResearchSession(repo, "alice", sources, client)
    result = session.chat("请查 robotics 新闻")
    assert len(calls) == 3 and result["session_metrics"]["model_calls"] == 3
    assert "laboratory" in result["markdown"] and "unknown" in result["markdown"]
    assert session.history[-1].content == result["markdown"]
    assert not any("DRAFT" in m.content for m in session.history)
    assert result["knowledge_payload"]["claims"][0]["status"] == "anchor_checked"
    assert repo.report("alice", result["report_id"])["report"]["markdown"] == result["markdown"]
    with pytest.raises(ValueError):
        repo.report("bob", result["report_id"])
    assert 1 <= len(http.calls) <= 3  # Expanded catalog still obeys the three-source search budget.


def test_missed_feedback_discovery_private_activation_and_shared_review(workspace):
    repo, sources, http = workspace
    tools = ResearchTools(sources, repo, "alice")
    tools.begin_turn("我关注 robotics，但没搜到；请从 https://example.org 发现新的来源")
    feedback = tools.registry().get("record_feedback").invoke(feedback_args())
    assert repo.profile("alice")["interests"] == ["robotics"]
    assert repo.profile("bob")["interests"] == []
    assert "coverage_or_query_gap_needs_search" in feedback["diagnosis"]
    discovered = tools.registry().get("discover_sources").invoke({"query": "robotics", "domain": "news", "website": "https://example.org"})
    source = discovered["candidates"][0]
    assert source["scope"] == "private" and source["status"] == "enabled"
    assert not repo.list_sources("bob")
    sources.begin_turn()
    batch = sources.search("robotics", ["news"])
    assert source["id"] in batch["selected_sources"]
    shared = repo.share_source("alice", source["id"])
    assert not repo.list_sources("bob")
    sources.validate(shared["id"])
    with pytest.raises(ValueError):
        repo.publish_source("bob", shared["id"])
    repo.publish_source("alice", shared["id"])
    visible = repo.list_sources("bob")
    assert len(visible) == 1 and visible[0]["id"] == shared["id"]
    assert "interests" not in visible[0] and not repo.events("bob")
    repo.undo("alice", feedback["event_id"])
    assert repo.profile("alice")["interests"] == []


def test_unrequested_domain_and_style_are_not_persisted(workspace):
    repo, sources, _ = workspace
    tools = ResearchTools(sources, repo, "alice")
    tools.begin_turn("你好")
    for update in ({"domains": ["finance"]}, {"style": "expert"}):
        with pytest.raises(ValueError):
            tools.registry().get("record_feedback").invoke(feedback_args(user_quote="你好", interests_add=[], **update))
    assert repo.profile("alice")["version"] == 0


def test_feedback_survives_model_failure_with_action_id(workspace):
    repo, sources, _ = workspace
    class FailingModel:
        calls = 0
        def complete(self, messages, *, tools=()):
            self.calls += 1
            if self.calls == 1:
                return Message("assistant", "", tool_calls=(ToolCall("f1", "record_feedback", json.dumps(feedback_args())),))
            raise ModelCommunicationError("secret upstream detail")
    session = ResearchSession(repo, "alice", sources, FailingModel())
    with pytest.raises(ModelCommunicationError) as exc:
        session.chat("我关注 robotics，但没搜到")
    event = repo.events("alice")[0]
    assert event["id"] in str(exc.value) and "secret" not in str(exc.value)
    assert not session.history
    repo.undo("alice", event["id"])


def test_tavily_key_is_injected_only_into_tavily(workspace, monkeypatch):
    repo, _, _ = workspace
    monkeypatch.setenv("TAVILY_API_KEY", "unit-test-key")
    received = []
    def factory(source, **kwargs):
        received.append((source.adapter, kwargs))
        return object()
    sources = ResearchSources(repo, "alice", CATALOG, factory=factory)
    for source in sources.descriptors():
        sources._adapter(source)
    for adapter, kwargs in received:
        assert kwargs.get("api_key") == ("unit-test-key" if adapter == "tavily" else None)


def test_cli_authentication_and_no_model_search(workspace, monkeypatch, capsys):
    repo, _, http = workspace
    monkeypatch.setenv("LOCAL_TEST_PASSWORD", "wrong-password")
    common = ["--user", "alice", "--database", str(repo.path), "--password-env", "LOCAL_TEST_PASSWORD"]
    with pytest.raises(SystemExit) as exc:
        main([*common, "profile"])
    assert exc.value.code == 2
    monkeypatch.setenv("LOCAL_TEST_PASSWORD", "test-password-alice")
    monkeypatch.setattr("newspaper_agent.research.cli.ResearchSources", lambda repository, user, path:
                        ResearchSources(repository, user, path, http=http))
    main([*common, "search", "robotics", "--domain", "news"])
    output = capsys.readouterr().out
    assert "robotics study" in output and "本轮模型请求：0" in output
    assert len(repo.reports("alice")) == 1


def test_compare_reuses_private_snapshot_without_network(workspace):
    repo, sources, http = workspace
    session = ResearchSession(repo, "alice", sources)
    initial = session.search("robotics", ["news"])
    count = len(http.calls)
    results = session.compare(initial["report_id"])
    assert set(results) == {"baseline", "prompt", "structured"}
    assert len(http.calls) == count
    assert all(r["knowledge_payload"]["evidence"] == initial["knowledge_payload"]["evidence"] for r in results.values())
    with pytest.raises(ValueError):
        ResearchSession(repo, "bob", ResearchSources(repo, "bob", CATALOG, http=http)).compare(initial["report_id"])


def test_filter_preserves_conflict_and_document_query_parameters():
    def record(id, url, text):
        return EvidenceRecord(id, "rss", "rss", "Test", "robotics", url, "news", text, "2026-10-13", is_fixture=False)
    records = [record("a", "https://example.org/article?id=1&utm_source=test", "robotics works"),
               record("duplicate", "https://example.org/article?id=1", "robotics works"),
               record("counter", "https://example.org/article?id=2", "robotics does not work"),
               record("excluded", "https://example.org/article?id=3", "robotics sports")]
    chosen, decisions = select_evidence(records, "robotics", {"excluded": ["sports"]})
    assert {r.id for r in chosen} == {"a", "counter"}
    assert {d["decision"] for d in decisions} == {"kept", "duplicate_url", "user_excluded"}


def test_crossref_metadata_and_snapshot_change():
    from types import SimpleNamespace
    class HTTP:
        def get(self, url):
            return HTTPResponse(json.dumps({"message": {"items": [{"DOI": "10.1000/example", "title": ["robotics"],
                "published": {"date-parts": [[2025, 4]]}, "publisher": "Test"}]}}).encode())
    source = SimpleNamespace(id="crossref", adapter="crossref", name="Crossref", is_fixture=False)
    adapter = build_adapter(source, http=HTTP())
    result = adapter.search(SearchRequest("robotics", ("academic",)))[0]
    assert result.content_level == "metadata" and result.published_at == "2025-04"
    assert result.payload["has_abstract"] is False
    changed = adapter._record(url=result.url, title=result.title, text="Changed text", kind="paper")
    assert changed.id != result.id and changed.payload["snapshot_id"] != result.payload["snapshot_id"]


def test_empty_or_failed_source_is_not_a_negative_fact(workspace):
    repo, _, _ = workspace
    class Broken:
        def get(self, url):
            raise SourceNetworkError("rate_limited")
    sources = ResearchSources(repo, "alice", CATALOG, http=Broken())
    result = ResearchSession(repo, "alice", sources).search("robotics", ["news"])
    assert "no_evidence" in result["issues"]
    assert result["search_audit"]["failures"][0]["error"] == "rate_limited"
    assert any("不表示" in w for w in result["search_audit"]["warnings"])
