"""Two explicit AI stages: library interest scoring, then top-ten interpretation."""
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import json
import math

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.agent_core.runtime import RunCancelled
from newspaper_agent.database.news_repository import NewsRepository
from newspaper_agent.synthesis import SynthesisService
from newspaper_agent.synthesis.contracts import Report
from newspaper_agent.synthesis.rendering import safe_text
from newspaper_agent.synthesis.news_brief import BRIEF_VERSION
from newspaper_agent.synthesis.writing_framework import framework_revision
from newspaper_agent.sources.annotations import annotate_record, TAG_VERSION
from newspaper_agent.preferences import active_interest_units
from newspaper_agent.recommendation import select_news
from newspaper_agent.database.recommendation_repository import RecommendationRepository
from .model_runtime import BudgetModel
from .article_reading import document_for, prepare_reading
from newspaper_agent.news_options import DEFAULT_NEWS_LIMIT, validate_news_limit
from newspaper_agent.database.news_feedback import NewsFeedback
from newspaper_agent.news_ranking import build_signals, rank_sections, RANK_VERSION
from .importance_scoring import parse_importance
from .efficient_scoring import score_pending, scoring_budget
from newspaper_agent.database.news_feature_cache import NewsFeatureCache
from newspaper_agent.synthesis.reader_context import reader_profile


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def scoring_response(content, count):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('invalid_scores')
            result[k] = v
        return result
    try:
        if not isinstance(content, str) or len(content) > 20000:
            raise ValueError()
        data = json.loads(content, object_pairs_hook=unique)
        if set(data) != {'scores'} or len(data['scores']) != count:
            raise ValueError()
        scores = {}
        for row in data['scores']:
            if set(row) != {'id', 'score'} or type(row['id']) is not int or not 0 <= row['id'] < count:
                raise ValueError()
            if row['id'] in scores or type(row['score']) is not int or not 0 <= row['score'] <= 100:
                raise ValueError()
            scores[row['id']] = row['score']
        return scores
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('invalid_scores') from None


def explain_text(report):
    lines = []
    for claim in report['knowledge_payload'].get('claims', []):
        lines.append(safe_text(claim['statement']))
        if claim['conditions']:
            lines.append('；'.join(safe_text(c) for c in claim['conditions']))
        if claim['uncertainty']:
            lines.append(safe_text(claim['uncertainty']))
    return ' '.join(t if t.endswith(('。', '！', '？', '.', '!', '?', '；', ';')) else t + '。'
                    for t in dict.fromkeys(lines) if t.strip())


def generate_news(session, question, records, audit, *, force_explanations=False, max_news=DEFAULT_NEWS_LIMIT):
    max_news = validate_news_limit(max_news)
    descriptors = session.sources.descriptors()
    visible = {s.id for s in descriptors}
    records = tuple(annotate_record(r) for r in records if r.source_id in visible)[:2400]
    store = NewsRepository(session.repository)
    store.put_articles(session.user, records)
    profile = session.repository.profile(session.user)
    preferences = {k: profile.get(k) for k in ('interests', 'excluded', 'domains', 'purpose', 'knowledge_level')}
    preferences.update(interest_units=active_interest_units(profile),
                       domain_reading_rules=profile.get('domain_reading_rules', []))
    client = session.model.client if session.model else None
    settings = getattr(client, 'settings', None)
    model_identity = {k: getattr(settings, k, None) for k in ('base_url', 'model', 'wire_api')}
    score_prompt = (Path(__file__).resolve().parents[3] / 'prompts/interest_scoring.md').read_text(encoding='utf-8')
    version = fingerprint({'schema': 3, 'tags': TAG_VERSION, 'profile': preferences, 'query': question,
                           'prompt': fingerprint(score_prompt), 'model': model_identity})
    feedback = NewsFeedback(session.repository)
    importance_version = fingerprint({'schema': 1, 'prompt': fingerprint(score_prompt), 'model': model_identity})
    importance = feedback.importance(session.user, [r.id for r in records], importance_version)
    # Separate news budget from the six-call conversational loop: bounded by actual batch work.
    model = BudgetModel(client, max_calls=scoring_budget(len(records),client) + 11 * min(max_news, len(records)),
                        max_context_chars=session.model.max_context_chars, max_seconds=None) if client else None
    if model:
        model.control = session.control
    known = store.articles(session.user, [r.id for r in records])
    cache = NewsFeatureCache(session.repository)
    keys = cache.prepare(session.user, records)
    cache.save(session.user, {keys[r.id]:(known[r.id]['score'],importance[r.id]) for r in records
        if known[r.id]['score_version']==version and known[r.id]['score_state']=='scored' and r.id in importance},
        version,importance_version)
    cached = cache.decisions(session.user, keys.values(),version,importance_version)
    restored = {r.id:cached[keys[r.id]] for r in records if keys[r.id] in cached and
                (known[r.id]['score_version']!=version or known[r.id]['score_state']!='scored' or r.id not in importance)}
    if restored:
        store.score(session.user,{eid:v[0] for eid,v in restored.items()},version)
        feedback.save_importance(session.user,importance_version,{eid:v[1] for eid,v in restored.items()})
        importance.update({eid:v[1] for eid,v in restored.items()})
        known = store.articles(session.user,[r.id for r in records])
    pending = [r for r in records if known[r.id]['score_version'] != version or known[r.id]['score_state'] != 'scored'
               or r.id not in importance]
    scoring_metrics = {'scoring_calls':0,'failed_scoring_batches':0,'scoring_reused':len(records)-len(pending),
                       'feature_cache_restored':len(restored)}
    if model and pending:
        def save(values):
            assessed = {eid:value[1] for eid,value in values.items()}
            # Preserve independent valid importance already obtained for this snapshot.
            assessed = {eid:importance.get(eid,value) for eid,value in assessed.items()}
            feedback.save_importance(session.user,importance_version,assessed)
            importance.update(assessed)
            store.score(session.user,{eid:v[0] for eid,v in values.items()},version)
            cache.save(session.user,{keys[eid]:(v[0],assessed[eid]) for eid,v in values.items()},version,importance_version)
        scoring_metrics.update(score_pending(model,pending,preferences,question,save,
            completed=len(records)-len(pending),total=len(records),reserve_calls=11*min(max_news,len(records))))
    known = store.articles(session.user, [r.id for r in records])
    ranked = [r for r in records if known[r.id]['score_version'] == version and known[r.id]['score_state'] == 'scored']
    ranked.sort(key=lambda r: (-known[r.id]['score'], r.id))
    complete = len(ranked) == len(records) and bool(records)
    source_map = {s.id: s for s in descriptors}
    adjustments = feedback.adjustments(session.user, records, profile)
    base_scores = {r.id: known[r.id]['score'] if r in ranked else None for r in records}
    from .semantic_personalization import semantic_adjustments
    vector_adjustments, semantic_status, semantic_matches = semantic_adjustments(session, records, profile, source_map, base_scores)
    if vector_adjustments is not None:
        adjustments = vector_adjustments
    signals = build_signals(records, base_scores,
                            source_map, importance, adjustments)
    for identity, matched in semantic_matches.items():
        if matched is not None:
            signals[identity]['semantic_match'] = matched
    ranking_preferences = feedback.preferences(session.user)
    top, sections, column_scores = rank_sections(ranked, signals, ranking_preferences['weights'], max_news) if complete else ([], {}, {})
    recommendations = RecommendationRepository(session.repository)
    exploration = recommendations.settings(session.user)
    decisions = {}
    if complete and exploration['enabled'] and semantic_status['status'] != 'active':
        personal_ranked = sorted(ranked, key=lambda r: (-column_scores[r.id]['personal'], r.id))
        sampled, decisions = select_news(personal_ranked, {r.id: known[r.id]['score'] for r in ranked}, profile,
            {s.id: s.domains for s in descriptors}, exploration, recommendations.learned(session.user), query=question, max_news=max_news)
        if decisions:
            # Preserve the existing exploration path only when no public-column allocations exist.
            # Public news values must not be randomized by a personal bandit.
            if not sections.get('major') and not sections.get('hot'):
                top, seen_groups = [], set()
                for candidate in [*sampled, *personal_ranked]:
                    group = signals[candidate.id]['group']
                    if group not in seen_groups and len(top) < max_news:
                        top.append(candidate)
                        seen_groups.add(group)
                decisions = {identity: d for identity,d in decisions.items() if identity in {r.id for r in top}}
                sections['personal'] = [r.id for r in top]
            else:
                decisions = {}
    items, claims, issues, interpretation_calls, reading_calls = [], [], [], 0, 0
    interpreted_evidence = {}
    if not model:
        issues.append('model_unavailable')
    if not records:
        issues.append('no_evidence')
    if len(ranked) < len(records):
        issues.append('interest_scoring_incomplete')
    style_version = fingerprint({'schema': 1, 'brief_version': BRIEF_VERSION,
                                 'framework_revision': framework_revision(),
                                 'reader': reader_profile(profile), 'model': model_identity, 'query': question})
    # Passage selection depends on information needs, not the output wording style.
    reading_profile = {k:v for k,v in reader_profile(profile).items()
                       if k not in {'style','output_format','expression_rules','glossary','language'}}
    reading_prompt_revision = fingerprint((Path(__file__).resolve().parents[3]/'prompts/news_reading.md').read_text(encoding='utf-8'))
    reading_version = fingerprint({'reader':reading_profile,'query':question,'model':model_identity,
                                   'prompt':reading_prompt_revision})
    for index, record in enumerate(top):
        if session.control:
            session.control.emit('news_interpreting', count=index + 1, total=len(top))
        if session.control:
            session.control.emit('article_fetch', count=index + 1, total=len(top))
        document = document_for(store, session.user, record, control=session.control,
                                reader=getattr(session, 'article_reader', None))
        article_version = fingerprint({'style': style_version, 'article': keys[record.id],
            'body': {k:document.get(k) for k in ('text','status','reason')}})
        cached = None if force_explanations else store.interpretation(session.user, record.id, article_version)
        saved_id, result = cached if cached else (None, None)
        if result is None or not result['knowledge_payload'].get('claims'):
            passage_version = fingerprint({'reader':reading_version,'article':keys[record.id],
                                          'body':document.get('text',''),'status':document.get('status')})
            evidence, reading = prepare_reading(record, document, model, reading_profile, control=session.control, question=question,
                checkpoint=store.reading_state(session.user, record.id, passage_version),
                save_checkpoint=lambda state, rid=record.id, ver=passage_version:
                    store.save_reading_state(session.user, rid, ver, state))
            reading_calls += reading['model_calls']
            if evidence is None:
                result = {'knowledge_payload': {'claims': [], 'reading': reading}, 'metrics': {'model_calls': 0}}
            else:
                result = asdict(SynthesisService(model).generate(
                    '本次关注：' + question + '。用连贯短段落说明与读者细分兴趣有关的具体发现。',
                    [evidence], profile=profile, style=profile['style'], news_brief=True,
                    news_context=store.event_candidates(session.user, record, visible)))
                if reading['status'] == 'body_ready' and result['metrics']['validation_passed']:
                    reading.update(status='body_read', blocks_read=1)
                # body_ready means acquisition succeeded; generation/validation failure
                # must not overwrite it with a passage-reading failure.
                result['knowledge_payload']['reading'] = reading
            interpretation_calls += result['metrics']['model_calls']
            saved_id = store.save_interpretation(session.user, record.id, article_version, result)
        metadata = result['knowledge_payload'].get('news_metadata', {})
        store.index_metadata(session.user, record, metadata)
        evidence_rows = result['knowledge_payload'].get('evidence', [])
        interpreted_evidence[record.id] = evidence_rows[0] if evidence_rows else asdict(record)
        text = explain_text(result)
        if not text:
            issues.append('interpretation_incomplete')
        for claim in result['knowledge_payload'].get('claims', []):
            claims.append({**claim, 'id': f'news-{index + 1}-{claim["id"]}'})
        items.append({'evidence_id': record.id, 'interest_score': known[record.id]['score'],
                      'interpretation_id': saved_id, 'explanation': text,
                      'reader_focus': result['knowledge_payload'].get('reader_focus', ''),
                      'lead_subject': result['knowledge_payload'].get('lead_subject', ''),
                      'archive_pending': bool(result['knowledge_payload'].get('metadata_issues')),
                      'model_diagnostic': result.get('metrics', {}).get('model_diagnostic', {}),
                      'diagnostics': [code for code in [
                          result['knowledge_payload'].get('reading', {}).get('reason'),
                          *result.get('issues', [])] if code in {
                              'passage_validation_failed', 'reading_request_failed', 'no_relevant_passages',
                              'brief_too_long', 'brief_focus_invalid', 'brief_style_invalid', 'brief_lead_invalid',
                              'invalid_json', 'invalid_evidence_anchor', 'invalid_knowledge_schema',
                              'synthesis_unavailable', 'insufficient_evidence', 'context_evidence_omitted'}],
                      'reading': result['knowledge_payload'].get('reading', {}),
                      'status': 'completed' if text else 'pending'})
        if record.id in decisions:
            items[-1]['recommendation'] = decisions[record.id]
        items[-1]['signals'] = signals[record.id]
        items[-1]['recommendation_scores'] = column_scores.get(record.id, {})
    if not top:
        # A pending list is not an AI ranking; do not invent scores without a model.
        top = list(ranked[:max_news] or records[:max_news])
        items = [{'evidence_id': r.id,
                  'interest_score': known[r.id]['score'] if r in ranked else None,
                  'interpretation_id': None, 'explanation': '', 'status': 'ranking_incomplete'} for r in top]
    payload = {'format': 'ranked_news_v1', 'version': 1, 'question': question, 'claims': claims,
               'follow_up': [], 'source_conditions': [], 'semantic_verification': 'unverified',
               'evidence': [interpreted_evidence.get(r.id, asdict(r)) for r in top], 'ranked_items': items,
               'library_ids': [r.id for r in records], 'score_version': version,
               'ranking_complete': complete,
               'requested_limit': max_news,
               'library_count': len(records), 'scored_count': len(ranked), 'pending_count': len(records) - len(ranked)}
    payload['exploration'] = {**exploration, 'active': bool(decisions),
        'selected': sum(d['role'] == 'explore' for d in decisions.values()),
        'neighbourhood': 'finite_concept_graph_not_embeddings'}
    payload['ranking'] = {'version': RANK_VERSION, 'sections': sections, 'weights': ranking_preferences['weights'],
        'signal_scope': 'importance_from_given_abstract_credibility_prior_heat_reporting_proxy',
        'public_collection': audit.get('public_source_ids', [])}
    payload['semantic_profile'] = semantic_status
    # Full validated evidence already lives in each referenced interpretation. Avoid
    # copying up to 100 x 16k body excerpts into a size-limited presentation report.
    if len(json.dumps(payload, ensure_ascii=False)) > 650000:
        from dataclasses import replace
        payload['evidence'] = [asdict(replace(r, snippet=r.snippet[:600], payload={
            'report_snapshot': 'compact_original', 'full_evidence': 'news_interpretations',
            'content_tags': r.payload.get('content_tags', [])[:4]})) for r in top]
        payload['evidence_storage'] = 'compact_original_with_interpretation_refs'
        if len(json.dumps(payload, ensure_ascii=False)) > 650000:
            # These are duplicate aggregate copies, not the source-of-truth claims.
            # Full claims and quote anchors remain in the referenced interpretations.
            payload['claims'] = []
            payload['claim_storage'] = 'news_interpretations'
    lines = ['# 今日简报', f'新闻库 {len(records)} 条 · 已评分 {len(ranked)} 条 · ' +
             ((f'核心推荐与兴趣拓展共 {len(top)} 条' if decisions else f'三栏共 {len(top)} 条（去重）')
              if complete else f'评分未完成，尚未确定全库前{max_news}名')]
    for index, (record, item) in enumerate(zip(top, items), 1):
        score_text = f"{item['interest_score']}/100" if item['interest_score'] is not None else '待评分（不是AI排名）'
        lines.append(f'## {index}. {safe_text(record.title)} · 兴趣分 {score_text}')
        if item.get('signals'):
            from newspaper_agent.news_ranking import SECTIONS
            names = [name for key, name in SECTIONS.items() if record.id in sections.get(key, [])]
            s = item['signals']
            fmt = lambda value: '未评估' if value is None else f'{value:g}'
            lines.append(' / '.join(names) + f" · 推荐 {fmt(item['recommendation_scores'].get('personal'))} · "
                         f"关联 {fmt(s['relevance'])} · 可信参考 {fmt(s['credibility'])} · "
                         f"重要 {fmt(s['importance'])} · 报道热度 {fmt(s['heat'])}")
        if item.get('recommendation', {}).get('role') == 'explore':
            lines.append('兴趣拓展 · 关联：' + safe_text(item['recommendation']['anchor']))
        lines.append(item['explanation'] or 'AI解读未完成；新闻已保存，未取得的兴趣分不以0分代替。')
    if not records:
        lines.append('所选范围内暂无新闻；可扩大时间范围、检查信源状态或稍后更新，不用启用模型来补旧闻。')
    elif not ranked:
        lines.append('尚无可排名的AI兴趣分；请启用模型或检查评分请求后重试。新闻已保存，不用重新采集。')
    elif not complete:
        lines.append(f'仅展示已评分部分预览，不代表全库前{max_news}；完整评分后才开始解读。')
    metrics = {'model_calls': model.calls if model else 0, **scoring_metrics,
               'interpretation_calls': interpretation_calls, 'reading_calls': reading_calls,
               'scored_count': len(ranked), 'tokens': None}
    if model:
        metrics.update(model.metrics())
    return Report('\n\n'.join(lines), payload, tuple(dict.fromkeys(issues)), metrics)
