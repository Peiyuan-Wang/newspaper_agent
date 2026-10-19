from __future__ import annotations

from dataclasses import asdict
import json
import re
from pathlib import Path
from time import perf_counter
from typing import Sequence

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.agent_core.protocols import ModelClient
from newspaper_agent.agent_core.runtime import RunCancelled
from newspaper_agent.sources.contracts import EvidenceRecord

from .contracts import Report
from .rendering import STYLES, render
from .validation import source_conditions, validate_response
from .news_brief import validate_brief
from .reader_context import reader_profile as _reader_profile, article_context
from .writing_framework import compose_news_prompt, framework_revision
from .brief_quality import validate_review

METHODS = ("baseline", "prompt", "structured")
_STRATEGIES = {
    "baseline": "策略 baseline：直接概括给定资料对问题的回答，按共同 JSON 契约返回。",
    "prompt": "策略 prompt：结论前置，每条只解释一个观点，必要术语就地解释，去掉重复和泛泛铺垫，保留所有关键条件。",
    "structured": "策略 structured：先将问题拆成需要证据回答的要点；组织独立主张与互补/相反资料；逐条关联原文；检查条件、时间和不确定性；只返回最终知识对象，不输出思考过程。",
}


class SynthesisService:
    def __init__(self, model: ModelClient | None, *, prompt_path: str | Path | None = None,
                 max_repairs: int = 2, brief_reviewer=None):
        if type(max_repairs) is not int or not 0 <= max_repairs <= 2:
            raise ValueError('max_repairs必须为0到2的整数')
        self.model = model
        self.brief_reviewer = brief_reviewer
        self.max_repairs = max_repairs
        self.prompt_path = Path(prompt_path) if prompt_path else Path(__file__).resolve().parents[3] / "prompts" / "explanation.md"

    def generate(self, question: str, evidence: Sequence[EvidenceRecord], *,
                 profile: dict | None = None, method: str = "structured", style: str = "standard",
                 news_brief: bool = False, news_context: list | None = None) -> Report:
        started = perf_counter()
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
            raise ValueError("问题需为 1 到 2000 字符")
        if method not in METHODS or style not in STYLES:
            raise ValueError("未知综合方法或阅读层级")
        issues = []
        selected = []
        used = set()
        chars = 0
        for record in evidence:
            if record.id in used:
                issues.append("duplicate_evidence_id")
                continue
            if len(selected) >= 8 or len(record.snippet) > (16000 if news_brief else 4000) or chars + len(record.snippet) > 16000:
                issues.append("context_evidence_omitted")
                continue
            used.add(record.id)
            selected.append(record)
            chars += len(record.snippet)
        selected = tuple(selected)
        payload = {"version": 1, "question": question, "method": method, "style": style,
                   "claims": [], "follow_up": [], "evidence": [asdict(record) for record in selected],
                   "source_conditions": source_conditions(selected), "semantic_verification": "unverified"}
        payload['reader_preferences'] = _reader_profile(profile)
        calls, review_calls, input_chars, output_chars = 0, 0, 0, 0
        diagnostic = {}
        attempts = []
        if selected and self.model is not None:
            try:
                prompt_path = self.prompt_path.parent / 'news_brief.md' if news_brief else self.prompt_path
                instructions = (compose_news_prompt(selected, self.prompt_path.parent) if news_brief
                                else prompt_path.read_text(encoding="utf-8"))
                if not instructions.strip() or len(instructions) > 12000:
                    raise ValueError("invalid_explanation_prompt")
                # 不发送 payload 等任意元数据，只传本阶段必要资料。
                material = [{key: record[key] for key in (
                    "id", "title", "snippet", "published_at", "content_level", "is_fixture"
                )} for record in payload["evidence"]]
                user = json.dumps({"question": question,
                                   **({} if news_brief else {'reader': _reader_profile(profile)}),
                                   "style": style, "evidence": material,
                                   **({'related_events': (news_context or [])[:6],
                                       'writing_context': article_context(question, selected, profile)} if news_brief else {})}, ensure_ascii=False)
                # News has its own editorial workflow; research strategies must not
                # accidentally reintroduce headings or broad comprehensive summaries.
                system = instructions if news_brief else instructions + "\n" + _STRATEGIES[method]
                if news_brief:
                    payload['writing_framework'] = {'revision': framework_revision(self.prompt_path.parent),
                        'semantic_review': 'not_reviewed'}
                base_messages = [Message(role='system', content=system), Message(role='user', content=user)]
                messages = base_messages
                for attempt in range(self.max_repairs + 1):
                    control = getattr(self.model, 'control', None)
                    if control:
                        control.emit('synthesis_validate' if attempt == 0 else 'synthesis_repair',
                                     attempt=attempt, total=self.max_repairs)
                        control.check()
                    input_chars += sum(len(m.content) for m in messages)
                    calls += 1
                    response = self.model.complete(messages, tools=())
                    output_chars += len(response.content)
                    audit = {'attempt': attempt + 1, 'passed': False,
                             'draft': response.content[:20000], 'truncated': len(response.content) > 20000}
                    attempts.append(audit)
                    try:
                        if response.tool_calls:
                            raise ValueError('unexpected_tool_call')
                        checked = (validate_brief(response.content, selected, _reader_profile(profile), news_context or []) if news_brief
                                   else validate_response(response.content, selected))
                        if news_brief and self.brief_reviewer is not None and checked['claims']:
                            review_calls += 1
                            review = validate_review(self.brief_reviewer.review(checked, selected, _reader_profile(profile)))
                            payload['writing_framework'].update(semantic_review='model_reviewed', review=review)
                            audit['semantic_review'] = review
                            if not review['passed']:
                                raise ValueError('brief_semantic_revision')
                    except ValueError as exc:
                        allowed = {'invalid_json', 'response_too_large', 'invalid_knowledge_schema',
                                   'invalid_claim_status', 'missing_evidence_anchor', 'invalid_evidence_anchor',
                                   'unexpected_tool_call', 'brief_too_long', 'brief_focus_invalid', 'brief_style_invalid', 'brief_lead_invalid',
                                   'brief_semantic_revision', 'brief_review_invalid'}
                        code = str(exc) if str(exc) in allowed else 'invalid_knowledge_schema'
                        audit['error'] = code
                        if hasattr(exc, 'location'):
                            audit.update(location=exc.location, reason=exc.reason)
                        if attempt >= self.max_repairs:
                            issues.append('validation_repair_exhausted')
                            raise
                        repair_name = 'news_brief_repair.md' if news_brief else 'synthesis_repair.md'
                        repair = (self.prompt_path.parent / repair_name).read_text(encoding='utf-8')
                        feedback = json.dumps({'error': code, 'location': audit.get('location'),
                                              'semantic_review': audit.get('semantic_review'),
                                              'reason': audit.get('reason'), 'previous_draft': audit['draft'],
                                              'draft_truncated': audit['truncated'],
                                              'instruction': ('保持原文证据和关键条件；focus可为空表示通用视角，非空必须原样来自writing_context.reader_model.confirmed_interests；最多2条主张，'
                                                              '中文正文合计最多180字符、英文700字符，不输出追问；直接陈述具体变化，'
                                                              '不写本文介绍、值得注意的是、建议优先读、具有重要意义等模板。'
                                                              'lead_subject为新闻真正核心的具体对象，第一条statement必须以它开头，先讲核心变化再讲指标细节。'
                                                              if news_brief else '修正契约与引用。')}, ensure_ascii=False)
                        # Do not accumulate earlier attempts or native tool requests in the replay.
                        messages = [Message('system', system + '\n' + repair), base_messages[1],
                                    Message('user', feedback)]
                        continue
                    audit['passed'] = True
                    payload.update(checked)
                    break
                if not payload["claims"]:
                    issues.append("insufficient_evidence")
            except (ValueError, OSError) as exc:
                safe_codes = {"invalid_json", "response_too_large", "invalid_knowledge_schema", "invalid_claim_status",
                              "missing_evidence_anchor", "invalid_evidence_anchor", "unexpected_tool_call",
                              "invalid_explanation_prompt", 'brief_too_long', 'brief_focus_invalid', 'brief_style_invalid', 'brief_lead_invalid',
                              'brief_semantic_revision', 'brief_review_invalid'}
                issues.append(str(exc) if str(exc) in safe_codes else "synthesis_unavailable")
            except RunCancelled:
                raise
            except Exception as exc:
                # Transport failures do not start an additional synthesis repair loop.
                issues.append("synthesis_unavailable")
                raw = getattr(exc, 'diagnostics', {})
                if isinstance(raw, dict):
                    code = raw.get('code')
                    if isinstance(code, str) and (code in {'read_timeout', 'connect_timeout', 'write_timeout', 'pool_timeout',
                            'proxy_error', 'connect_error', 'remote_protocol', 'read_error', 'write_error',
                            'transport_error', 'output_limit', 'incomplete_response', 'context_budget', 'invalid_json',
                            'unexpected_stream', 'invalid_response', 'invalid_tool_response', 'response_too_large', 'response_deadline'}
                            or re.fullmatch(r'http_\d{3}', code)):
                        diagnostic['code'] = code
                    for key in ('request_characters', 'http_status', 'elapsed_seconds', 'context_limit', 'attempts'):
                        value = raw.get(key)
                        if type(value) in (int, float):
                            diagnostic[key] = value
        elif selected:
            issues.append("model_unavailable")
        else:
            issues.append("no_evidence")
        if attempts:
            # Private report data only: never render unvalidated drafts as conclusions.
            payload['validation_attempts'] = attempts
        markdown = render(payload, style=style)
        metrics = {"elapsed_seconds": round(perf_counter() - started, 6), "model_calls": calls + review_calls,
                   'generation_calls': calls, 'semantic_review_calls': review_calls,
                   'character_metrics_scope': 'generation_only',
                   "input_characters": input_chars, "model_output_characters": output_chars,
                   "output_characters": len(markdown), "evidence_count": len(selected),
                   "claim_count": len(payload["claims"]), "tokens": None,
                   "human_reading_seconds": None, "comprehension_accuracy": None}
        metrics['repair_calls'] = max(0, calls - 1)
        metrics['validation_passed'] = bool(attempts and attempts[-1]['passed'])
        if diagnostic:
            metrics['model_diagnostic'] = diagnostic
        return Report(markdown, payload, tuple(dict.fromkeys(issues)), metrics)

    def compare(self, question: str, evidence: Sequence[EvidenceRecord], *,
                profile: dict | None = None, style: str = "standard") -> dict[str, Report]:
        """显式三策略对照；每个策略最多3次，共最多9次模型调用。"""
        return {method: self.generate(question, evidence, profile=profile, method=method, style=style)
                for method in METHODS}
