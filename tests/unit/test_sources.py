from dataclasses import asdict, replace
from pathlib import Path
import json
import yaml
import pytest

from newspaper_agent.sources.contracts import SearchRequest, public_url
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.service import SourceService
from newspaper_agent.tools.information import SearchInformationTool, FetchEvidenceTool
from newspaper_agent.tools.registry import ToolRegistry
from newspaper_agent.agent_core.messages import ToolCall, Message
from newspaper_agent.agent_core.loop import AgentLoop
from newspaper_agent.database.source_repository import SourceRepository
from newspaper_agent.sources.__main__ import main
from newspaper_agent.app import run_interactive

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def catalog():
    return SourceCatalog.load(ROOT / "config/sources/catalog.yaml")


@pytest.mark.parametrize("domains,kind,expected", [
    (("academic",), "paper", "fixture_academic"),
    (("finance",), "filing", "fixture_finance"),
    (("medical",), "trial", "fixture_medical"),
    (("news",), "news", "fixture_news"),
    (("general",), "web", "fixture_general"),
])
def test_specialist_routing(catalog, domains, kind, expected):
    selected, warnings = catalog.route(SearchRequest("query", domains, kind=kind))
    assert selected[0].id == expected
    assert all(s.status == "enabled" and s.is_fixture for s in selected)


def test_cross_domain_coverage_and_hard_filters(catalog):
    selected, _ = catalog.route(SearchRequest("AI医疗", ("academic", "medical"), kind="paper"))
    assert {s.id for s in selected} == {"fixture_academic", "fixture_medical"}
    selected, warnings = catalog.route(SearchRequest("query", ("finance",), region="DE"))
    assert selected[0].id == "fixture_general"
    assert any("unsupported_filter:region" in w for w in warnings)
    assert any("general_fallback" in w for w in warnings)
    selected, warnings = catalog.route(SearchRequest("query", ("academic",), language="ja"))
    assert not selected and "no_eligible_source" in warnings


def test_preferences_cannot_override_domain_or_capability(catalog):
    preferences = {"fixture_general": 100, "fixture_academic_secondary": 2}
    selected, _ = catalog.route(SearchRequest("query", ("academic",)), preferences)
    assert selected[0].id == "fixture_academic_secondary"
    assert all("academic" in s.domains for s in selected)
    with pytest.raises(ValueError):
        replace(catalog.sources[-1], status="enabled")


@pytest.mark.parametrize("url", ["http://example.com", "https://localhost", "https://127.0.0.1", "https://10.0.0.1",
                                    "https://[::1]", "https://169.254.169.254", "https://host.local",
                                    "https://key@example.com", "https://example.com?api_key=secret", "https://example.com:8000",
                                    "https://example.com/#secret", "https://bad_.com", "https://127.1"])
def test_candidate_url_rejected(url):
    with pytest.raises(ValueError):
        public_url(url)


@pytest.mark.parametrize("values", [{"query": " "}, {"max_results": True}, {"max_sources": 4},
                                    {"domains": ("unknown",)}, {"kind": "quote"}, {"region": "*"}])
def test_invalid_request(values):
    with pytest.raises(ValueError):
        SearchRequest(**({"query": "q", "domains": ("general",)} | values))


@pytest.mark.parametrize("change", ["duplicate", "adapter", "secret", "version"])
def test_invalid_catalog_sanitized(tmp_path, catalog, change):
    entry = asdict(catalog.sources[0])
    for key in ("domains", "kinds", "regions", "languages", "filters", "capabilities"):
        entry[key] = list(entry[key])
    raw = {"version": 1, "sources": [entry]}
    if change == "duplicate":
        raw["sources"].append(entry)
    elif change == "adapter":
        entry["adapter"] = "malicious.module:run"
    elif change == "secret":
        entry["api_key"] = "never-echo-this"
    else:
        raw["version"] = True
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        SourceCatalog.load(path)
    assert "never-echo-this" not in str(exc.value)


def test_search_fetch_bounded_and_reset(catalog):
    service = SourceService(catalog)
    batch = service.search(SearchRequest("q", ("medical",), kind="trial", max_results=1))
    assert len(batch.results) == 1 and batch.is_fixture
    record = batch.results[0]
    assert record.published_at is None and record.payload["registration_id"] is None
    fetched = service.fetch(record.id)
    assert fetched.kind == "trial" and fetched.payload == record.payload and fetched.content_level == "fulltext"
    with pytest.raises(ValueError):
        service.fetch("https://example.com")
    service.clear()
    with pytest.raises(ValueError):
        service.fetch(record.id)


def test_adapter_failure_is_safe_partial_result(catalog):
    service = SourceService(catalog)
    class Broken:
        def search(self, request):
            raise RuntimeError("secret-key")
    service.adapters["fixture_academic"] = Broken()
    batch = service.search(SearchRequest("q", ("academic",)))
    assert len(batch.results) == 1 and len(batch.failures) == 1
    assert "secret-key" not in str(asdict(batch))


def test_registry_validates_tools(catalog):
    service = SourceService(catalog)
    registry = ToolRegistry()
    registry.register(SearchInformationTool(service))
    registry.register(FetchEvidenceTool(service))
    bad = registry.execute(ToolCall("a", "search_information", '{"query":"q"}'))
    assert json.loads(bad)["error"] == "invalid_arguments"
    args = {"query": "q", "domains": ["academic"], "kind": "paper", "region": None, "language": None}
    output = json.loads(registry.execute(ToolCall("a", "search_information", json.dumps(args))))
    assert output["ok"] and output["result"]["is_fixture"]


def test_candidate_and_feedback_isolation_and_undo(tmp_path, catalog):
    repo = SourceRepository(tmp_path / "private" / "sources.sqlite3")
    repo.add_candidate("alice", "personal", "个人来源", "academic", "https://example.org/feed")
    assert repo.candidates("bob") == []
    assert repo.candidates("alice")[0]["status"] == "discovered"
    with pytest.raises(ValueError):
        repo.add_candidate("alice", "personal", "个人来源", "academic", "https://example.org/feed")
    with pytest.raises(ValueError):
        repo.add_candidate("../alice", "another", "name", "academic", "https://example.org")
    event = repo.feedback("alice", "fixture_academic_secondary", "useful")
    repo.feedback("alice", "fixture_academic", "fact_error")
    assert repo.preferences("bob") == {}
    assert repo.preferences("alice")["fixture_academic"] == 0
    assert catalog.route(SearchRequest("q", ("academic",)), repo.preferences("alice"))[0][0].id == "fixture_academic_secondary"
    with pytest.raises(ValueError):
        repo.undo_feedback("bob", event)
    repo.undo_feedback("alice", event)
    assert repo.preferences("alice").get("fixture_academic_secondary", 0) == 0
    for _ in range(5):
        repo.feedback("alice", "fixture_academic", "useful")
    assert repo.preferences("alice")["fixture_academic"] == 2


def test_offline_cli_and_candidate_guard(capsys, tmp_path):
    common = ["--catalog", str(ROOT / "config/sources/catalog.yaml"), "--database", str(tmp_path / "private.sqlite3")]
    main([*common, "demo", "--domain", "academic", "--query", "AI论文"])
    text = capsys.readouterr().out
    assert "虚构" in text and "工具 2 次" in text and "fixture_academic" in text
    assert not (tmp_path / "private.sqlite3").exists()
    main([*common, "candidate-add", "--id", "personal", "--name", "个人来源", "--domain", "academic", "--url", "https://example.org/feed"])
    assert '"enabled": false' in capsys.readouterr().out
    main([*common, "route", "--domain", "academic"])
    assert "personal" not in capsys.readouterr().out


def test_reset_retains_system_prompt_and_clears_evidence(catalog):
    service = SourceService(catalog)
    record = service.search(SearchRequest("q", ("academic",))).results[0]
    class Model:
        def complete(self, messages):
            assert messages[0].role == "system"
            return Message("assistant", "ok")
    inputs = iter(["hello", "/reset", "/exit"])
    state = run_interactive(AgentLoop(Model()), read=lambda _: next(inputs), write=lambda _: None,
                            initial_messages=(Message("system", "fixture rules"),), on_reset=service.clear)
    assert len(state.messages) == 1
    with pytest.raises(ValueError):
        service.fetch(record.id)


def test_app_information_wiring(monkeypatch, capsys):
    import newspaper_agent.app as app
    monkeypatch.chdir(ROOT)
    class Model:
        def complete(self, messages, *, tools=()):
            assert messages[0].role == "system"
            assert {tool.name for tool in tools} == {"add_numbers", "search_information", "fetch_evidence"}
            return Message("assistant", "offline wiring verified")
    monkeypatch.setattr(app, "create_model_client", lambda *args, **kwargs: Model())
    app.main(["--mode", "live", "--information", "--message", "test"])
    assert "offline wiring verified" in capsys.readouterr().out
