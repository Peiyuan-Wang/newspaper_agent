from __future__ import annotations

import hashlib
import json
import re

from newspaper_agent.sources.contracts import EvidenceRecord

MAX_RESPONSE_CHARS = 20000


class EvidenceValidationError(ValueError):
    """Safe machine feedback, with no model text in the exception message."""
    def __init__(self, location, reason):
        super().__init__('invalid_evidence_anchor')
        self.location, self.reason = location, reason


def _text(value, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError("invalid_knowledge_schema")
    return value.strip()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def validate_response(content: str, evidence: tuple[EvidenceRecord, ...]) -> dict:
    """仅校验形状与逐字锚点；不宣称验证了主张的语义或现实真实性。"""
    if not isinstance(content, str) or len(content) > MAX_RESPONSE_CHARS:
        raise ValueError("response_too_large")
    try:
        result = json.loads(content, object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise ValueError("invalid_json") from None
    if not isinstance(result, dict) or set(result) != {"claims", "follow_up"}:
        raise ValueError("invalid_knowledge_schema")
    claims, follow_up = result["claims"], result["follow_up"]
    if not isinstance(claims, list) or len(claims) > 8:
        raise ValueError("invalid_knowledge_schema")
    if not isinstance(follow_up, list) or len(follow_up) > 3:
        raise ValueError("invalid_knowledge_schema")
    checked = []
    records = {record.id: record for record in evidence}
    for index, claim in enumerate(claims, 1):
        if not isinstance(claim, dict) or set(claim) != {
            "statement", "conditions", "evidence_refs", "uncertainty", "status"
        }:
            raise ValueError("invalid_knowledge_schema")
        if claim["status"] not in ("unverified", "disputed"):
            raise ValueError("invalid_claim_status")
        conditions, refs = claim["conditions"], claim["evidence_refs"]
        if not isinstance(conditions, list) or len(conditions) > 8:
            raise ValueError("invalid_knowledge_schema")
        if not isinstance(refs, list) or not 1 <= len(refs) <= 4:
            raise ValueError("missing_evidence_anchor")
        anchors = []
        for ref_index, ref in enumerate(refs):
            location = f'claims[{index - 1}].evidence_refs[{ref_index}]'
            if not isinstance(ref, dict) or set(ref) != {"evidence_id", "quote"}:
                raise EvidenceValidationError(location, '引用必须且只能包含evidence_id和quote')
            evidence_id = _text(ref["evidence_id"], 500)
            quote = _text(ref["quote"], 1600)
            record = records.get(evidence_id)
            if record is None:
                raise EvidenceValidationError(location, '来源ID不在本次资料中')
            if quote not in record.snippet:
                raise EvidenceValidationError(location, '引文不是对应snippet中的连续逐字原文')
            start = record.snippet.index(quote)
            anchors.append({"evidence_id": evidence_id, "quote": quote,
                            "locator": {"field": "snippet", "start": start, "end": start + len(quote)},
                            "snapshot_id": hashlib.sha256(record.snippet.encode("utf-8")).hexdigest(),
                            "content_level": record.content_level, "is_fixture": record.is_fixture})
        checked.append({"id": f"claim-{index}", "statement": _text(claim["statement"], 1200),
                        "conditions": [_text(value, 800) for value in conditions],
                        "evidence_refs": anchors,
                        "uncertainty": _text(claim["uncertainty"], 800, empty=True),
                        "status": "disputed" if claim["status"] == "disputed" else "anchor_checked",
                        "semantic_verification": "unverified"})
    return {"claims": checked, "follow_up": [_text(value, 300) for value in follow_up]}


def source_conditions(evidence: tuple[EvidenceRecord, ...]) -> list[dict]:
    """保留显式条件及常见边界句；启发式兜底，不是完整语义条件抽取。"""
    result = []
    marker = re.compile(r"仅|只适用|不代表|不能|不得|未能|尚未|但是|不过|局限|除非|前提|"
                        r"\b(?:only|unless|however|not|limited|except|uncertain)\b", re.I)
    for record in evidence:
        explicit = record.payload.get("required_conditions", [])
        if not isinstance(explicit, list):
            explicit = []
        sentences = re.split(r"(?<=[。！？.!?])\s*|\n+", record.snippet)
        values = [value for value in explicit if isinstance(value, str) and value and value in record.snippet]
        values.extend(sentence.strip() for sentence in sentences if marker.search(sentence))
        for value in dict.fromkeys(values):
            if value:
                result.append({"evidence_id": record.id, "text": value})
    return result
