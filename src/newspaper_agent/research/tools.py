"""Small tool surface bound to an authenticated user by the application."""
import json
from newspaper_agent.agent_core.messages import ToolDefinition
from newspaper_agent.sources.contracts import DOMAINS, KINDS
from newspaper_agent.tools.registry import ToolRegistry
from .service import infer_domains
from newspaper_agent.sources.query import recent_window


def schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


class BoundTool:
    def __init__(self, name, description, properties, handler):
        self.name, self.handler = name, handler
        self.definition = ToolDefinition(name, description, schema(properties))

    def invoke(self, args):
        if set(args) != set(self.definition.parameters["properties"]):
            raise ValueError("工具字段无效")
        return self.handler(args)


class ResearchTools:
    def __init__(self, sources, repository, user):
        self.sources, self.repository, self.user = sources, repository, user
        self.user_text = ""
        self.actions = []

    def begin_turn(self, text):
        self.user_text, self.actions = text, []

    def registry(self):
        registry = ToolRegistry()
        string = {"type": "string"}
        nullable = {"type": ["string", "null"]}
        strings = {"type": "array", "items": string}
        registry.register(BoundTool("research_search", "真实检索；按领域选择来源，包含个性化筛选原因。英文数据库请用英文关键词。", {
            "query": string, "domains": {"type": "array", "items": {"type": "string", "enum": list(DOMAINS)}},
            "kind": {"type": ["string", "null"], "enum": [*KINDS, None]},
        }, self._search))
        registry.register(BoundTool("read_evidence", "读取当前用户会话中已返回的证据；可能只有摘要，不能称为全文。", {
            "evidence_id": string}, lambda a: self.sources.fetch(a["evidence_id"])))
        registry.register(BoundTool('propose_profile',
            '提出待确认档案，不立即生效。changes_json可含interests、excluded、domains、style、language、purpose、knowledge_level、output_format、expression_rules、glossary、domain_reading_rules；或interest_units列表（最多12项），每项必须含topic、domain、question、purpose、methods字符串列表、knowledge_level(beginner/intermediate/expert)、priority(1到5)、expires_on(YYYY-MM-DD或null)，可选category为用户确认的兴趣大类（最多60字符，同类同名）。大类只分组，细分主题才编码向量。列表提供完整修改后列表，保留已有条目。',
            {'changes_json': string, 'user_quote': string, 'reason': string}, self._propose))
        registry.register(BoundTool('confirm_profile_proposal', '处理当前用户待确认档案；用户必须明确说确认档案建议/拒绝档案建议，多项建议需原话包含具体建议ID。',
            {'proposal_id': string, 'decision': {'type': 'string', 'enum': ['accept', 'reject']}, 'user_quote': string}, self._confirm))
        registry.register(BoundTool("record_feedback", "记录用户明确反馈并修正个人偏好。增删的关键词必须出现在当前用户原话中；仅个人数据，可撤销。", {
            "kind": {"type": "string", "enum": ["missed", "irrelevant", "useful", "fact_error", "too_long", "unclear",
                                                      'not_new', 'too_shallow', 'too_hard', 'source_quality', 'not_now']},
            "user_quote": string, "interests_add": strings, "interests_remove": strings,
            "excluded_add": strings, "excluded_remove": strings,
            "domains": {"type": "array", "items": {"type": "string", "enum": list(DOMAINS)}},
            "style": {"type": ["string", "null"], "enum": ["brief", "standard", "expert", None]},
        }, self._feedback))
        registry.register(BoundTool("discover_sources", "根据问题或用户给的网址寻找RSS/Atom来源，登记到个人候选库。默认不会共享。网站不提供feed时会明确失败。", {
            "query": string, "domain": {"type": "string", "enum": list(DOMAINS)}, "website": nullable,
        }, self._discover))
        return registry

    def _propose(self, args):
        quote = args['user_quote']
        if not isinstance(quote, str) or not quote.strip() or quote not in self.user_text:
            raise ValueError('建议必须关联当前用户原话')
        try:
            changes = json.loads(args['changes_json'])
        except (TypeError, ValueError):
            raise ValueError('档案建议需要有效JSON') from None
        proposal = self.repository.propose_profile(self.user, changes, quote, args['reason'])
        self.actions.append({'type': 'profile_proposal', 'id': proposal})
        return {'proposal_id': proposal, 'status': 'pending',
                'next_step': '请用户到兴趣与领域确认，或明确说“确认档案建议”。未确认前不能称为已记住。'}

    def _confirm(self, args):
        quote = args['user_quote']
        decision = args['decision']
        cue = '确认档案建议' if decision == 'accept' else '拒绝档案建议'
        if decision not in ('accept', 'reject') or not isinstance(quote, str) or not quote.strip() or quote not in self.user_text or cue not in self.user_text:
            raise ValueError('需要用户明确确认或拒绝档案建议')
        pending = self.repository.proposals(self.user)
        if len(pending) != 1 and args['proposal_id'] not in self.user_text:
            raise ValueError('有多项建议，请在界面选定或明确提供建议ID')
        event = self.repository.resolve_proposal(self.user, args['proposal_id'], accept=decision == 'accept')
        self.actions.append({'type': 'profile_confirmation', 'event_id': event})
        return {'status': 'accepted' if decision == 'accept' else 'rejected', 'event_id': event}

    def _search(self, args):
        from newspaper_agent.sources.time_windows import scope_from_query
        if not isinstance(args["domains"], list):
            raise ValueError("无效领域")
        batch = dict(self.sources.search(args["query"], args["domains"], kind=args["kind"],
                                        recent_days=recent_window(self.user_text), time_scope=scope_from_query(self.user_text)))
        batch["decisions"] = batch["decisions"][:12]
        # Keep search outputs comfortably within ToolRegistry's existing 16k character bound.
        batch["results"] = [{**r, "snippet": r["snippet"][:1000],
                             "payload": {k: v for k, v in r["payload"].items() if k in {"selection", "doi", "version", "snapshot_id", "locator"}}}
                            for r in batch["results"]]
        return batch

    def _feedback(self, args):
        if args['kind'] in {'not_new', 'too_shallow', 'too_hard', 'source_quality', 'not_now'} and (
                any(args[k] for k in ('interests_add', 'interests_remove', 'excluded_add', 'excluded_remove', 'domains'))
                or args['style'] is not None):
            raise ValueError('此类反馈只记录原因，不修改长期兴趣；请另提待确认档案建议')
        quote = args["user_quote"]
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 500 or quote not in self.user_text:
            raise ValueError("反馈需要当前用户原话")
        profile = self.repository.profile(self.user)
        changes, diagnosis = {}, []
        intent = self.user_text.casefold()
        cues = {
            "interests_add": ("关注", "感兴趣", "兴趣", "关心", "研究方向", "领域", "漏掉", "没搜到", "没有搜到", "missed", "interested", "focus"),
            "interests_remove": ("不再关注", "不感兴趣", "不再感兴趣", "移除", "删除", "remove"),
            "excluded_add": ("排除", "过滤", "不要", "不看", "不感兴趣", "exclude", "ignore", "irrelevant"),
            "excluded_remove": ("不要过滤", "不过滤", "取消排除", "不该排除", "不应该排除", "移除排除", "remove exclusion", "unexclude"),
        }
        for field in ("interests", "excluded"):
            add, remove = args[field + "_add"], args[field + "_remove"]
            if not isinstance(add, list) or not isinstance(remove, list) or len(add) + len(remove) > 12:
                raise ValueError("关键词过多")
            for value in [*add, *remove]:
                if not isinstance(value, str) or not value.strip() or value.casefold() not in self.user_text.casefold():
                    raise ValueError("只能从用户明确提到的词修正偏好")
            for operation, values in ((field + "_add", add), (field + "_remove", remove)):
                if values and not any(cue in intent for cue in cues[operation]):
                    raise ValueError("需要用户明确表达偏好修改意图")
            if add and field == "interests" and any(cue in intent for cue in ("不感兴趣", "不再感兴趣", "not interested")):
                raise ValueError("偏好方向不明确，请明确要关注的主题")
            if add and field == "excluded" and any(cue in intent for cue in cues["excluded_remove"]):
                raise ValueError("用户要求取消排除，不能新增排除")
            values = [v for v in profile[field] if v.casefold() not in {r.casefold() for r in remove}]
            for value in add:
                if value.casefold() not in {v.casefold() for v in values}:
                    values.append(value)
            if values != profile[field]:
                changes[field] = values
                diagnosis.append("interest_gap" if field == "interests" else "filter_gap")
        if args["domains"]:
            if not isinstance(args["domains"], list) or not set(args["domains"]) <= set(infer_domains(self.user_text)):
                raise ValueError("领域变更需要用户原话支持")
            changes["domains"] = args["domains"]
        if args["style"] is not None:
            style_cues = {"brief": ("太长", "简短", "简洁", "精简", "brief", "shorter"),
                         "expert": ("详细", "专业", "展开", "expert", "detail"),
                         "standard": ("标准", "standard")}
            if args["style"] not in style_cues or not any(c in intent for c in style_cues[args["style"]]):
                raise ValueError("风格变更需要用户明确选择")
            changes["style"] = args["style"]
        if args["kind"] == "missed":
            diagnosis.append("coverage_or_query_gap_needs_search")
        if args["kind"] == "fact_error":
            diagnosis.append("claim_needs_source_check_not_automatic_truth_update")
        reasons = {'not_new': 'novelty_or_duplicate_gap', 'too_shallow': 'reading_depth_gap',
                   'too_hard': 'expression_or_knowledge_gap', 'source_quality': 'source_quality_gap',
                   'not_now': 'session_intent_only'}
        if args['kind'] in reasons:
            diagnosis.append(reasons[args['kind']])
        event = self.repository.feedback(self.user, args["kind"], quote, changes=changes or None)
        self.actions.append({"type": "feedback", "event_id": event, "changes": changes})
        return {"event_id": event, "profile": self.repository.profile(self.user), "diagnosis": diagnosis,
                "next_step": "missed反馈请用修正后的主题重新检索；来源不足时调用discover_sources。/undo可撤销。"}

    def _discover(self, args):
        result = self.sources.discover(query=args["query"], domain=args["domain"], website=args["website"])
        # Automatic personal evolution is bounded to validating discovered feeds. No shared publication.
        for item in result["candidates"]:
            try:
                self.sources.validate(item["id"])
                self.repository.set_source_status(self.user, item["id"], "enabled")
                item["status"] = "enabled"
            except Exception:
                item["status"] = "discovered"
            self.actions.append({"type": "private_source", "id": item["id"], "status": item["status"]})
        result["next_step"] = "验证通过的来源已在个人范围启用；/sources查看，source suspend可停用；共享需要显式share/publish。"
        return result
