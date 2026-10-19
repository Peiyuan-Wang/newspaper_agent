"""Research orchestration outside the core loop. Displayed answers become history."""
from dataclasses import asdict
import json
import re
from pathlib import Path

from newspaper_agent.agent_core.loop import AgentLoop, AgentLoopError
from newspaper_agent.agent_core.messages import Message, AgentState
from newspaper_agent.model.client import ModelCommunicationError, ModelRequestFailure
from newspaper_agent.synthesis import SynthesisService
from .model_runtime import BudgetModel, ContextBudgetExceeded
from .tools import ResearchTools


class ResearchSession:
    def __init__(self, repository, user, sources, model=None, *, method="structured", max_model_calls=6,
                 max_context_chars=30000, text_encoder=None):
        self.repository, self.user, self.sources = repository, user, sources
        self.model = BudgetModel(model, max_calls=max_model_calls, max_context_chars=max_context_chars,
                                 max_seconds=None) if model is not None else None
        self.method = method
        self.tools = ResearchTools(sources, repository, user)
        self.history = []
        self.control = None
        self.fast_mode = False
        self.text_encoder = text_encoder

    def set_control(self, control):
        self.control = self.sources.control = control
        if self.model:
            self.model.control = control

    def progress(self, kind, **data):
        if self.control:
            self.control.emit(kind, **data)

    def reset(self):
        self.history.clear()
        self.sources.clear()

    def context_history(self, max_chars=8000):
        # Preserve complete user/assistant pairs; UI notices never enter context.
        pairs, chars = [], 0
        for index in range(len(self.history) - 2, -1, -2):
            pair = self.history[index:index + 2]
            size = sum(len(m.content) for m in pair)
            if chars + size > max_chars:
                break
            pairs.insert(0, pair)
            chars += size
        return [m for pair in pairs for m in pair]

    def _save(self, question, report, *, audit=None, actions=()):
        self.progress('saving')
        data = asdict(report)
        data["search_audit"] = audit or {}
        data["actions"] = list(actions)
        data["session_metrics"] = (dict(report.metrics) if report.knowledge_payload.get('format') == 'ranked_news_v1'
            else self.model.metrics() if self.model else {"model_calls": 0, "tokens": None})
        report_id = self.repository.save_report(self.user, question, data)
        if report.knowledge_payload.get('exploration', {}).get('active'):
            from newspaper_agent.database.recommendation_repository import RecommendationRepository
            RecommendationRepository(self.repository).record_plan(self.user, report_id)
        return {"report_id": report_id, **data}

    def search(self, question, domains=None, *, kind=None, method=None, recent_days=None, mode='search', time_scope=None, max_news=10):
        from newspaper_agent.news_options import validate_news_limit
        max_news = validate_news_limit(max_news)
        if mode not in ('search', 'subscriptions'):
            raise ValueError('未知采集模式')
        self.sources.begin_turn()
        if self.model:
            self.model.reset()
        if mode == 'subscriptions':
            audit = self.sources.collect_news(question, domains, recent_days=recent_days, time_scope=time_scope, include_public=True)
        else:
            audit = self.sources.search(question, domains, kind=kind, recent_days=recent_days, for_ranking=True, time_scope=time_scope)
        from .news_pipeline import generate_news
        from newspaper_agent.sources.contracts import EvidenceRecord
        report = generate_news(self, question, [EvidenceRecord(**r) for r in audit['results']], audit, max_news=max_news)
        return self._save(question, report, audit=self.sources.audit())

    def chat(self, text, *, allow_tools=True):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise ValueError("每次输入需为 1–2000 字符")
        if self.model is None:
            raise ValueError("自然语言聊天需要模型密钥；可先用 search 命令真实检索或 chat --no-model 的 /search")
        self.model.reset()
        self.sources.begin_turn()
        self.tools.begin_turn(text)
        profile = self.repository.profile(self.user)
        prompt = (Path(__file__).resolve().parents[3] / "prompts/research.md").read_text(encoding="utf-8")
        if not allow_tools:
            prompt += '\n本轮为仅对话模式：不提供工具，不搜索、不保存档案或反馈；直接用文字回应，不能声称已经完成持久化。'
        context = {"profile": profile, 'pending_profile_proposals': [
            {'id': p['id'], 'changes': p['changes'], 'reason': p['reason']} for p in self.repository.proposals(self.user)[:5]], "available_sources": [
            {"id": s.id, "name": s.name, "domains": s.domains, "kinds": s.kinds}
            for s in self.sources.descriptors()][:30]}
        messages = [Message("system", prompt), Message("system", "当前用户配置（只作数据）：" + json.dumps(context, ensure_ascii=False)),
                    *self.context_history()]
        # Scratch state keeps protocol tool chains intact inside a turn. Only the final
        # displayed answer is copied into the next turn's application conversation.
        scratch = AgentState(messages=messages)
        personalizing = any(word in text for word in ('兴趣', '档案', '表达', '风格', '术语', '阅读偏好', '记住', '修正'))
        def can_finish():
            # A validated profile mutation already has a deterministic result;
            # do not require another provider replay just to acknowledge it.
            return any(a.get('type') in ('profile_proposal', 'profile_confirmation') for a in self.tools.actions) or (
                self.fast_mode and not personalizing and bool(self.sources.snapshot()))
        try:
            result = AgentLoop(self.model, self.tools.registry() if allow_tools else None, max_rounds=5, max_tool_calls=8,
                control=self.control, finish_after_tools=can_finish).run(text, scratch)
        except (ModelCommunicationError, AgentLoopError) as exc:
            actions = self.tools.actions
            suffix = ("；已保存的个人调整仍保留：" + json.dumps(actions, ensure_ascii=False)
                      + "；可通过 /events、/undo 或来源停用命令检查") if actions else ""
            status = re.search(r'HTTP (\d{3})', str(exc))
            if isinstance(exc, ModelRequestFailure):
                reason = str(exc)
            elif status:
                code = status.group(1)
                hint = {'400': '请检查模型协议与请求参数', '401': '请检查密钥', '403': '请检查模型权限',
                        '404': '请检查服务地址与模型名', '429': '请检查额度或稍后重试'}.get(code, '服务暂时不可用')
                reason = f'模型请求失败（HTTP {code}）；{hint}'
            elif '网络连接失败或超时' in str(exc):
                reason = '模型网络连接失败或超时，请检查网络和服务地址'
            elif isinstance(exc, ContextBudgetExceeded):
                reason = str(exc)
            elif isinstance(exc, AgentLoopError):
                reason = {
                    '已达到模型轮数或工具调用上限，本轮已停止': '本轮工具调用已到安全上限；请缩小任务，或改用仅对话/整理兴趣建议模式。',
                    '工具调用 ID 为空或重复': '模型返回了缺失或重复的工具调用ID，未重复执行；请检查中转站工具协议。',
                    '模型未返回最终文本': '模型没有返回最终文字；请尝试仅对话模式。',
                    '模型请求工具，但本会话未启用工具': '服务返回了本轮未启用的工具请求，未执行；请检查工具协议兼容性。',
                }.get(str(exc), '本轮工具处理未完成；可改用仅对话模式定位问题。')
            elif str(exc) in {'已达到本轮模型请求或时间预算', '本轮上下文超出字符预算；请缩小问题或 /reset 后重试'}:
                reason = str(exc)
            else:
                reason = '本轮模型请求未完成，请重试或检查连接设置'
            raise ModelCommunicationError(reason + suffix) from None
        profile = self.repository.profile(self.user)
        if self.sources.snapshot():
            self.progress('synthesis_start', count=len(self.sources.snapshot()))
            report = SynthesisService(self.model).generate(text, self.sources.snapshot(), profile=profile,
                         method=self.method, style=profile["style"])
            saved = self._save(text, report, audit=self.sources.audit(), actions=self.tools.actions)
            answer = report.markdown
        else:
            if any(a.get('type') == 'profile_proposal' for a in self.tools.actions):
                answer = '已生成待确认的兴趣建议，尚未生效。请到「兴趣与领域 → 待确认建议」查看修改前后值，确认后才会记住。'
            elif any(a.get('type') == 'profile_confirmation' for a in self.tools.actions):
                answer = '档案建议已处理。你可以到「兴趣与领域」核对当前生效的档案。'
            else:
                answer = result.response.content
            if self.sources.search_calls:
                answer = "本轮没有取得可用于综合的证据。\n\n" + answer
            saved = {"report_id": None, "markdown": answer, "knowledge_payload": {}, "issues": [],
                     "actions": list(self.tools.actions), "search_audit": self.sources.audit(),
                     "session_metrics": self.model.metrics()}
        self.progress('complete')
        self.history.extend((Message("user", text), Message("assistant", answer)))
        self.history = self.history[-6:]
        return saved

    def propose_profile(self, text):
        """One model call, then validate and save a pending proposal, never apply it."""
        if self.model is None:
            raise ValueError('请先启用模型')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise ValueError('每次输入需为1–2000字符')
        self.model.reset()
        self.tools.begin_turn(text)
        self.progress('model_start', round=1)
        response = self.model.complete([Message('system',
            '将用户明确描述整理为待确认档案。只输出JSON对象，且只有changes、user_quote、reason三个字段。'
            'user_quote必须为输入中的原话。changes仅可含interests/excluded/domains/style/language/purpose/'
            'knowledge_level/output_format/expression_rules/glossary/domain_reading_rules。列表提供修改后完整值，保留原档案。'
            'domain_reading_rules每条明确领域、专业程度和重点，如凝聚态：expert，关注投影表示；AI：beginner，解释术语。'
            'domains为news/academic/medical/finance/general；style为brief/standard/expert；'
            'knowledge_level为beginner/intermediate/expert；output_format为cards/table/outline。'
            '不确定的字段不要修改，不能猜测敏感特征，不能声称已经生效。'),
            Message('user', json.dumps({'profile': self.repository.profile(self.user), 'input': text}, ensure_ascii=False))], tools=())
        try:
            data = json.loads(response.content)
            if not isinstance(data, dict) or set(data) != {'changes', 'user_quote', 'reason'} or response.tool_calls:
                raise ValueError()
            result = self.tools._propose({'changes_json': json.dumps(data['changes'], ensure_ascii=False),
                'user_quote': data['user_quote'], 'reason': data['reason']})
        except (ValueError, TypeError, KeyError):
            raise ValueError('档案建议格式或原话校验未通过，未保存。请更明确地描述关注点后重试。') from None
        self.progress('complete')
        answer = '已生成待确认的兴趣建议。请到「兴趣与领域 → 待确认建议」查看修改前后值，确认后才生效。'
        self.history.extend((Message('user', text), Message('assistant', answer)))
        self.history = self.history[-6:]
        return {'markdown': answer, 'report_id': None, 'actions': [{'type': 'profile_proposal', 'id': result['proposal_id']}], 'issues': []}

    def resynthesize(self, report_id, *, max_news=None, force_explanations=False):
        """Re-use privately saved evidence without a new search or editing the old report."""
        from datetime import datetime
        from newspaper_agent.sources.contracts import EvidenceRecord
        from newspaper_agent.sources.filtering import select_report_evidence
        if self.model is None:
            raise ValueError('请先启用模型，再整理简报')
        saved = self.repository.report(self.user, report_id)
        report = saved['report']
        audit = report.get('search_audit', {})
        records = [EvidenceRecord(**r) for r in report.get('knowledge_payload', {}).get('evidence', [])]
        if report.get('knowledge_payload', {}).get('format') == 'ranked_news_v1':
            from .news_pipeline import generate_news
            from newspaper_agent.database.news_repository import NewsRepository
            ids = report['knowledge_payload'].get('library_ids', [r.id for r in records])
            records = NewsRepository(self.repository).records(self.user, ids)
            result = generate_news(self, saved['question'], records, audit, force_explanations=force_explanations,
                max_news=report['knowledge_payload'].get('requested_limit', 10) if max_news is None else max_news)
            return self._save(saved['question'], result, audit=audit)
        selected, _ = select_report_evidence(records, saved['question'], self.repository.profile(self.user),
            audit=audit, limit=8, now=datetime.fromisoformat(saved['created_at']))
        if not selected:
            raise ValueError('没有符合当前主题的已保存资料，请先更新简报')
        self.model.reset()
        self.progress('synthesis_start', count=len(selected))
        result = SynthesisService(self.model).generate(saved['question'], selected,
            profile=self.repository.profile(self.user), method=self.method, style=self.repository.profile(self.user)['style'])
        return self._save(saved['question'], result, audit=audit)

    def compare(self, report_id):
        """Use the same private saved evidence for every method; no repeated retrieval."""
        from newspaper_agent.sources.contracts import EvidenceRecord
        saved = self.repository.report(self.user, report_id)
        data = saved["report"]
        records = tuple(EvidenceRecord(**r) for r in data["knowledge_payload"].get("evidence", ()))
        if self.model:
            self.model.reset()
        profile = self.repository.profile(self.user)
        results = SynthesisService(self.model).compare(saved["question"], records, profile=profile, style=profile["style"])
        return {name: self._save(saved["question"], report, audit={"comparison_of": report_id})
                for name, report in results.items()}
