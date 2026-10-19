from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.sources.contracts import EvidenceRecord

from .service import METHODS, SynthesisService


def load_cases(path: str | Path | None = None) -> list[dict]:
    target = Path(path) if path else Path(__file__).resolve().parents[3] / "evals" / "synthesis-cases.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("synthetic") is not True or not isinstance(data.get("cases"), list):
        raise ValueError("需要明确标记 synthetic 的 v1 固定资料包")
    return data["cases"]


def case_evidence(case: dict) -> tuple[EvidenceRecord, ...]:
    return tuple(EvidenceRecord(
        id=f"synthetic:{case['id']}:{index}", source_id="synthetic_eval", provider_id="offline",
        publisher="虚构评测材料", title=f"虚构资料 {case['id']} {index}",
        url=f"https://example.invalid/evals/{case['id']}/{index}", kind="web", snippet=snippet,
        retrieved_at="2026-10-13T00:00:00+00:00", is_fixture=True,
        payload={"required_conditions": [value for value in case["conditions"] if value in snippet]},
    ) for index, snippet in enumerate(case["snippets"], 1))


class ReplayModel:
    """离线回放固定响应，不执行模型推理或网络请求。"""
    def __init__(self, case: dict):
        self.case = case

    def complete(self, messages, *, tools=()):
        if tools:
            raise AssertionError("综合阶段不得启用工具")
        evidence = case_evidence(self.case)
        refs = [{"evidence_id": record.id, "quote": record.snippet} for record in evidence]
        if self.case.get("invalid_quote"):
            refs[0]["quote"] = "模型编造的引文不在资料里"
        claim = {"statement": self.case["statement"], "conditions": self.case["conditions"],
                 "evidence_refs": refs, "uncertainty": self.case["uncertainty"], "status": self.case["status"]}
        return Message(role="assistant", content=json.dumps({"claims": [claim] if evidence else [],
                                                            "follow_up": []}, ensure_ascii=False))


def run_offline_evaluation(path: str | Path | None = None) -> dict:
    rows = []
    for case in load_cases(path):
        for method in METHODS:
            report = SynthesisService(ReplayModel(case)).generate(case["question"], case_evidence(case),
                                                                method=method, style="brief")
            preserved = all(value in report.markdown for value in case["conditions"])
            rejected_bad_quote = not case.get("invalid_quote") or (
                "invalid_evidence_anchor" in report.issues and not report.knowledge_payload["claims"])
            fixtures_marked = not case["snippets"] or "虚构" in report.markdown
            passed = preserved and rejected_bad_quote and fixtures_marked
            rows.append({"case_id": case["id"], "method": method, "passed": passed,
                         "conditions_preserved": preserved, "bad_quote_rejected": rejected_bad_quote,
                         "fixture_marked": fixtures_marked, "metrics": report.metrics,
                         "issues": list(report.issues)})
    return {"scope": "synthetic_contract_regression", "actual_model_requests": 0,
            "total": len(rows), "passed": sum(row["passed"] for row in rows),
            "human_comprehension_measured": False, "rows": rows}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="虚构固定资料回归；不调用真实模型，不测量真实人类理解")
    parser.add_argument("--cases", type=Path)
    args = parser.parse_args(argv)
    result = run_offline_evaluation(args.cases)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
