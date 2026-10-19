from dataclasses import asdict, replace
import json

import pytest

from newspaper_agent.agent_core.messages import Message, ToolCall
from newspaper_agent.synthesis import SynthesisService
from newspaper_agent.synthesis.evaluation import case_evidence, load_cases, run_offline_evaluation
from newspaper_agent.synthesis.rendering import source_link


class CapturingModel:
    def __init__(self, content, tool_calls=()):
        self.content = content
        self.tool_calls = tool_calls
        self.calls = []

    def complete(self, messages, *, tools=()):
        self.calls.append((messages, tools))
        return Message(role="assistant", content=self.content, tool_calls=self.tool_calls)


def valid_content(record, *, conditions=None, status="unverified"):
    return json.dumps({"claims": [{"statement": "只涉及给定资料中的成人样本。", "conditions": conditions or [],
                                   "evidence_refs": [{"evidence_id": record.id, "quote": record.snippet}],
                                   "uncertainty": "儿童效果未知。", "status": status}], "follow_up": ["可否解释样本范围？"]})


@pytest.fixture
def record():
    return case_evidence(load_cases()[0])[0]


@pytest.mark.parametrize("method", ["baseline", "prompt", "structured"])
def test_one_call_provider_neutral_and_json_report(record, method):
    model = CapturingModel(valid_content(record))
    report = SynthesisService(model).generate("适合谁？", [record], method=method)
    assert len(model.calls) == 1 and model.calls[0][1] == ()
    claim = report.knowledge_payload["claims"][0]
    assert claim["status"] == "anchor_checked" and claim["semantic_verification"] == "unverified"
    anchor = claim["evidence_refs"][0]
    assert record.snippet[anchor["locator"]["start"]:anchor["locator"]["end"]] == anchor["quote"]
    assert len(anchor["snapshot_id"]) == 64
    assert report.metrics["model_calls"] == 1 and report.metrics["tokens"] is None
    assert report.metrics["human_reading_seconds"] is None
    json.dumps(asdict(report))


@pytest.mark.parametrize("content,issue", [
    ("并非 JSON", "invalid_json"),
    ('{"claims":[],"claims":[],"follow_up":[]}', "invalid_json"),
    ('{"claims":[],"follow_up":[],"secret":true}', "invalid_knowledge_schema"),
    ("x" * 20001, "response_too_large"),
])
def test_invalid_response_falls_back_without_raw_model_text(record, content, issue):
    report = SynthesisService(CapturingModel(content)).generate("回答", [record])
    assert issue in report.issues and not report.knowledge_payload["claims"]
    assert record.snippet in report.markdown
    assert "尚未形成可靠综合结论" in report.markdown


@pytest.mark.parametrize("mutation", ["id", "quote", "status", "empty_refs"])
def test_false_anchors_or_verified_status_are_rejected(record, mutation):
    obj = json.loads(valid_content(record))
    claim = obj["claims"][0]
    if mutation == "id":
        claim["evidence_refs"][0]["evidence_id"] = "not-provided"
    elif mutation == "quote":
        claim["evidence_refs"][0]["quote"] = "invented"
    elif mutation == "status":
        claim["status"] = "verified"
    else:
        claim["evidence_refs"] = []
    report = SynthesisService(CapturingModel(json.dumps(obj))).generate("回答", [record])
    assert not report.knowledge_payload["claims"] and report.issues


def test_no_evidence_or_model_does_not_spend_calls(record):
    model = CapturingModel("unused")
    assert SynthesisService(model).generate("回答", []).metrics["model_calls"] == 0
    report = SynthesisService(None).generate("回答", [record])
    assert "model_unavailable" in report.issues and report.metrics["model_calls"] == 0
    assert not model.calls and "虚构" in report.markdown


@pytest.mark.parametrize("style", ["brief", "standard", "expert"])
def test_conditions_survive_every_layer_even_when_model_omits_them(record, style):
    report = SynthesisService(CapturingModel(valid_content(record))).generate("适合谁？", [record], style=style)
    assert "仅适用于成人，不代表儿童也有效。" in report.markdown
    assert "儿童效果未知" in report.markdown
    assert "未验证主张" in report.markdown


def test_brief_keeps_conditions_of_hidden_claims(record):
    obj = json.loads(valid_content(record))
    obj["claims"] = [dict(obj["claims"][0]) for _ in range(4)]
    obj["claims"][3]["conditions"] = ["隐藏主张仍需保留的关键条件"]
    report = SynthesisService(CapturingModel(json.dumps(obj))).generate("回答", [record], style="brief")
    assert "隐藏主张仍需保留的关键条件" in report.markdown
    assert "另有 2 条主张" in report.markdown


def test_disputed_preserves_disagreement(record):
    report = SynthesisService(CapturingModel(valid_content(record, status="disputed"))).generate("回答", [record])
    assert report.knowledge_payload["claims"][0]["status"] == "disputed"
    assert "不是对真伪的裁定" in report.markdown


def test_tool_calls_are_never_executed(record):
    model = CapturingModel(valid_content(record), (ToolCall("x", "danger", "{}"),))
    report = SynthesisService(model).generate("回答", [record])
    assert "unexpected_tool_call" in report.issues and not report.knowledge_payload["claims"]


def test_failure_does_not_echo_provider_or_retry(record):
    class FailingModel:
        calls = 0
        def complete(self, messages, *, tools=()):
            self.calls += 1
            raise RuntimeError("secret-key-response")
    model = FailingModel()
    report = SynthesisService(model).generate("回答", [record])
    assert model.calls == 1 and "secret" not in str(asdict(report))
    assert report.issues == ("synthesis_unavailable",)


def test_profile_is_allowlisted_and_evidence_payload_not_sent(record):
    model = CapturingModel(valid_content(record))
    SynthesisService(model).generate("回答", [record], profile={"purpose": "学习", "private_token": "never-send"})
    sent = model.calls[0][0][1].content
    assert "学习" in sent and "never-send" not in sent and "required_conditions" not in sent


def test_context_budget_omits_whole_record_instead_of_slicing_condition(record):
    too_long = replace(record, id="too-long", snippet="x" * 4001)
    report = SynthesisService(None).generate("回答", [record, too_long])
    assert "context_evidence_omitted" in report.issues
    assert len(report.knowledge_payload["evidence"]) == 1
    assert report.knowledge_payload["evidence"][0]["snippet"] == record.snippet


def test_duplicate_id_is_flagged(record):
    report = SynthesisService(None).generate("回答", [record, record])
    assert "duplicate_evidence_id" in report.issues and report.metrics["evidence_count"] == 1


def test_markdown_does_not_render_external_html_or_javascript(record):
    dangerous = replace(record, title="<script>alert(1)</script>", url="javascript:alert(1)")
    report = SynthesisService(None).generate("回答", [dangerous])
    assert "<script>" not in report.markdown and "javascript:" not in report.markdown
    assert "链接不可用" in report.markdown


def test_compare_is_explicit_three_call_budget(record):
    model = CapturingModel(valid_content(record))
    reports = SynthesisService(model).compare("回答", [record])
    assert len(model.calls) == 3 and list(reports) == ["baseline", "prompt", "structured"]
    assert len({call[0][0].content for call in model.calls}) == 3


def test_fixed_synthetic_evaluation_covers_twelve_cases_offline():
    result = run_offline_evaluation()
    assert len(load_cases()) == 12
    assert result["total"] == result["passed"] == 36
    assert result["actual_model_requests"] == 0
    assert result["human_comprehension_measured"] is False
