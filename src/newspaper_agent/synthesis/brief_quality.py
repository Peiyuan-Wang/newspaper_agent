"""Explicit opt-in semantic review; never confused with deterministic validation."""
import json
from pathlib import Path

from newspaper_agent.agent_core.messages import Message
from .brief_contract import parse_brief_json
from .reader_context import article_context

RUBRIC = ('core_first', 'reader_relevance', 'clarity', 'evidence_support')
REVIEW_ISSUES = ('core_unclear', 'reader_mismatch', 'wording_unclear', 'unsupported_claim', 'missing_qualifier')


def validate_review(content):
    try:
        data = parse_brief_json(content)
        if not isinstance(data, dict) or set(data) != {'ratings', 'issues'}:
            raise ValueError()
        if not isinstance(data['ratings'], dict) or set(data['ratings']) != set(RUBRIC):
            raise ValueError()
        if any(type(v) is not int or not 1 <= v <= 5 for v in data['ratings'].values()):
            raise ValueError()
        if (not isinstance(data['issues'], list) or len(data['issues']) > 5
                or any(not isinstance(v, str) or v not in REVIEW_ISSUES for v in data['issues'])):
            raise ValueError()
        return {**data, 'passed': all(v >= 4 for v in data['ratings'].values()) and not data['issues']}
    except (ValueError, TypeError, KeyError):
        raise ValueError('brief_review_invalid') from None


class ModelBriefReviewer:
    """One request per review, only instantiated by an explicitly opted-in caller."""
    def __init__(self, model):
        self.model, self.calls = model, 0

    def review(self, checked, evidence, profile):
        prompt = (Path(__file__).resolve().parents[3] / 'prompts/editorial_review.md').read_text(encoding='utf-8')
        material = {'writing_context': article_context('', evidence, profile),
                    'draft': {k: checked[k] for k in ('lead_subject', 'reader_focus', 'claims')},
                    'evidence': [{'id': r.id, 'title': r.title, 'snippet': r.snippet,
                                  'content_level': r.content_level} for r in evidence]}
        self.calls += 1
        answer = self.model.complete([Message('system', prompt),
            Message('user', json.dumps(material, ensure_ascii=False))], tools=())
        if answer.tool_calls:
            raise ValueError('brief_review_invalid')
        return answer.content
