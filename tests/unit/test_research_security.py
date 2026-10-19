"""Integration boundaries: a model cannot pick another account or exceed budgets."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from newspaper_agent.agent_core.messages import Message, ToolDefinition
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.model.client import ModelCommunicationError
from newspaper_agent.research import model_runtime
from newspaper_agent.research.model_runtime import BudgetModel
from newspaper_agent.research.service import ResearchSources
from newspaper_agent.research.tools import ResearchTools
from newspaper_agent.sources.contracts import EvidenceRecord


CATALOG = Path(__file__).resolve().parents[2] / "config" / "sources" / "live.yaml"


@pytest.fixture
def repo(tmp_path):
    result = ResearchRepository(tmp_path / "research.sqlite3")
    result.register("alice", "test-password-alice")
    result.register("bob", "test-password-bob")
    return result


class FakeAdapter:
    def __init__(self, source):
        self.source = source
        self.record = EvidenceRecord(id=source.id + ":robotics", source_id=source.id,
            provider_id=source.adapter, publisher="Test publisher", title="robotics research",
            url="https://example.org/" + source.id, kind=source.kinds[0], snippet="robotics abstract",
            retrieved_at="2026-10-13T00:00:00+00:00", is_fixture=False)

    def search(self, request):
        return (self.record,)

    def fetch(self, evidence_id):
        assert evidence_id == self.record.id
        return self.record


def fake_factory(source, *, http=None, **kwargs):
    return FakeAdapter(source)


def feedback_args(quote="robotics"):
    return {"kind": "missed", "user_quote": quote, "interests_add": ["robotics"],
        "interests_remove": [], "excluded_add": [], "excluded_remove": [], "domains": [], "style": None}


def test_retrieval_and_evidence_cache_do_not_cross_authenticated_users(repo, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    private = repo.store_source("bob", "personal feed", "https://example.org/personal.xml", "academic")
    repo.set_source_status("bob", private["id"], "validated")
    repo.set_source_status("bob", private["id"], "enabled")
    bob = ResearchSources(repo, "bob", CATALOG, factory=fake_factory)
    alice = ResearchSources(repo, "alice", CATALOG, factory=fake_factory)
    assert private["id"] in {s.id for s in bob.descriptors()}
    assert private["id"] not in {s.id for s in alice.descriptors()}
    batch = bob.search("robotics", ["academic"])
    personal = next(r for r in batch["results"] if r["source_id"] == private["id"])
    assert bob.fetch(personal["id"])["source_id"] == private["id"]
    with pytest.raises(ValueError, match="当前用户会话"):
        alice.fetch(personal["id"])
    # Visibility is checked again after retrieval, not only when caching a record.
    repo.set_source_status("bob", private["id"], "suspended")
    with pytest.raises(ValueError, match="停用或不可见"):
        bob.fetch(personal["id"])
    bob.clear()
    assert not bob.evidence and not bob.turn_evidence and not bob.adapters


def test_tool_schema_does_not_offer_account_or_shared_publication_controls(repo):
    sources = SimpleNamespace(user="bob", repository=repo)
    tools = ResearchTools(sources, repo, "bob")
    registry = tools.registry()
    assert set(registry.names()) == {"research_search", "read_evidence", "record_feedback", "discover_sources",
                                     'propose_profile', 'confirm_profile_proposal'}
    for definition in registry.definitions():
        assert not {"user", "user_id", "admin", "scope", "database"} & set(definition.parameters["properties"])
    tools.begin_turn("robotics 没有搜到")
    arguments = {**feedback_args(), "user": "alice"}
    with pytest.raises(ValueError):
        registry.get("record_feedback").invoke(arguments)
    assert repo.events("alice") == repo.events("bob") == []


def test_feedback_rejects_fabricated_user_quotes_and_unmentioned_terms(repo):
    tools = ResearchTools(SimpleNamespace(user="bob", repository=repo), repo, "bob")
    tools.begin_turn("请搜索 robotics")
    with pytest.raises(ValueError, match="用户原话"):
        tools.registry().get("record_feedback").invoke(feedback_args("伪造的反馈"))
    args = feedback_args()
    args["interests_add"] = ["private-company-secret"]
    with pytest.raises(ValueError, match="明确提到"):
        tools.registry().get("record_feedback").invoke(args)
    assert repo.events("bob") == []


def test_search_call_budget_resets_per_turn(repo, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    sources = ResearchSources(repo, "bob", CATALOG, factory=fake_factory)
    for _ in range(3):
        sources.search("robotics", ["academic"])
    with pytest.raises(ValueError, match="三次"):
        sources.search("robotics", ["academic"])
    sources.begin_turn()
    assert sources.search_calls == 0 and sources.discovery_calls == 0 and not sources.turn_evidence
    assert sources.search("robotics", ["academic"])["results"]


class FakeModel:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, tools=()):
        self.calls += 1
        return Message(role="assistant", content="answer")


def test_model_call_budget_prevents_an_extra_request_and_reports_no_fake_token_usage():
    client = FakeModel()
    budget = BudgetModel(client, max_calls=1)
    messages = [Message(role="user", content="hello")]
    budget.complete(messages)
    with pytest.raises(ModelCommunicationError, match="预算"):
        budget.complete(messages)
    assert client.calls == 1
    assert budget.metrics()["tokens"] is None
    budget.reset()
    budget.complete(messages)
    assert client.calls == 2


def test_context_and_time_budgets_stop_before_contacting_model(monkeypatch):
    client = FakeModel()
    budget = BudgetModel(client, max_context_chars=100)
    tool = ToolDefinition("large_tool", "x" * 200, {"type": "object", "properties": {}})
    with pytest.raises(ModelCommunicationError, match="字符预算"):
        budget.complete([], tools=[tool])
    assert client.calls == budget.calls == 0
    monkeypatch.setattr(model_runtime.time, "monotonic", lambda: budget.started + 241)
    with pytest.raises(ModelCommunicationError, match="预算"):
        budget.complete([])
    assert client.calls == 0


def test_research_model_creation_caps_output_retries_and_timeout(monkeypatch):
    monkeypatch.setenv("RESEARCH_TEST_KEY", "test-only-placeholder")
    settings = SimpleNamespace(name="test", base_url="https://example.org/v1", model="test-model",
        api_key_env="RESEARCH_TEST_KEY", wire_api="responses", temperature=None, max_tokens=9000, timeout=200)
    monkeypatch.setattr(model_runtime, "load_provider_settings", lambda *args: settings)
    model = model_runtime.create_research_model(max_output_tokens=700)
    assert model.settings.max_tokens == 700
    assert model.settings.timeout == 45
    assert model.settings.max_attempts == 1
