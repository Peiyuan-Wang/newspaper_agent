"""Top-ten news view: headline, AI interest score, collapsed interpretation."""
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox
from newspaper_agent.synthesis.editorial import category


def reading_status_text(reading):
    status = reading.get('status')
    if status == 'body_read':
        return ('正文已读取' if reading.get('blocks_total') == 1 else
                f"正文分段读取 {reading['blocks_read']}/{reading['blocks_total']}")
    # Older reports incorrectly labelled short-body generation failures as reading failures.
    legacy_ready = (status == 'reading_incomplete' and reading.get('fetch_status') == 'extracted'
                    and reading.get('blocks_total') == 1 and reading.get('model_calls') == 0
                    and not reading.get('reason'))
    if status == 'body_ready' or legacy_ready:
        return '正文已获取 · AI解读尚未完成'
    if status == 'reading_incomplete':
        return '正文分段阅读未完成，未生成正式解读'
    return '仅依据摘要 · 正文未取得'


def model_failure_text(diagnostic):
    code = diagnostic.get('code')
    reasons = {'remote_protocol': '模型响应连接中断或协议异常，未收到完整回复',
               'read_error': '接收模型回复时连接中断',
               'output_limit': '模型输出额度耗尽', 'context_budget': '解读输入超出上下文预算',
               'http_502': '模型网关返回502', 'http_503': '模型服务暂不可用（503）',
               'http_504': '模型网关等待上游超时（504）'}
    text = reasons.get(code, '')
    attempts = diagnostic.get('attempts')
    if text and type(attempts) is int and attempts > 0:
        text += f'（已尝试{attempts}次）'
    return text


def show_ranked_report(window, saved):
    from .app import label, box, button, browser, disclosure, open_link
    report, user = saved['report'], window.user
    payload = report['knowledge_payload']
    limit = payload.get('requested_limit', 10)
    section = window.news_section.currentData() if hasattr(window, 'news_section') else 'personal'
    ranking = payload.get('ranking', {})
    records = {r['id']: r for r in payload['evidence']}
    items = [item for item in payload['ranked_items'] if item['evidence_id'] in records]
    if ranking and (payload['ranking_complete'] or section != 'personal'):
        order = ranking['sections'].get(section, [])
        positions = {identity: i for i,identity in enumerate(order)}
        items = sorted([i for i in items if i['evidence_id'] in positions], key=lambda i: positions[i['evidence_id']])
    elif section != 'personal':
        items = []
    if window.edition_category.currentData():
        items = [i for i in items if category(records[i['evidence_id']]) == window.edition_category.currentData()]
    if window.edition_order.currentIndex() == 1:
        items.sort(key=lambda i: records[i['evidence_id']].get('published_at') or '', reverse=True)
    container, ratings = QWidget(), QWidget()
    layout, rating_layout = QVBoxLayout(container), QVBoxLayout(ratings)
    window.news_explanation_panels = []
    scores = window.ratings.ratings(user, window.report_id)
    from newspaper_agent.database.recommendation_repository import RecommendationRepository
    recommendations = RecommendationRepository(window.repo)
    from newspaper_agent.database.news_feedback import NewsFeedback
    from .news_interactions import interaction_row, ExposureTracker
    from newspaper_agent.news_ranking import SECTIONS
    feedback = NewsFeedback(window.repo)
    states = feedback.state(user, window.report_id)
    if hasattr(window, 'exposure_tracker'):
        window.exposure_tracker.stop()
    cards = []
    legacy_hot = section == 'hot' and ranking.get('version') == 'four-signals-v1'
    if legacy_hot:
        layout.addWidget(label('历史快照：按生成当日统计；更新或重新整理后使用滚动24小时。', 'subtitle'))
    if not items:
        message = ('所选范围内暂无新闻。可以扩大时间范围、查看检索详情中的信源状态，或稍后更新。'
                   if not payload['library_count'] else
                   '当前栏目暂无可展示新闻；可切换全部栏目。若评分未完成，请检查模型后重试，已采集内容保留。')
        if section == 'major':
            message = '暂无符合条件的大事件。需要已知近7天日期、足够来源参考及有材料依据的重要性估计，不用普通新闻凑数。'
        elif section == 'hot':
            message = ('这份历史报告没有当日多源热点。' if legacy_hot else
                '暂无近24小时多源热点。按生成时刻向前滚动24小时，需要明确发布时间和至少两个来源组织的覆盖；不是全网热搜。')
        layout.addWidget(label(message))
    for item in items:
        record = records[item['evidence_id']]
        card, body = box()
        body.addWidget(label(record['title'], 'cardTitle'))
        decision = item.get('recommendation', {})
        if decision.get('role') == 'explore':
            body.addWidget(label('兴趣拓展 · 关联：' + decision['anchor'], 'eyebrow'))
        signals = item.get('signals')
        score_text = '' if signals else (f"兴趣分 {item['interest_score']}/100  ·  "
                    if item['interest_score'] is not None else '待评分 · 不是AI排名  ·  ')
        body.addWidget(label(f"{score_text}{record['publisher']}  ·  {(record.get('published_at') or '日期未知')[:10]}", 'eyebrow'))
        if signals:
            fmt = lambda v: '未评估' if v is None else f'{v:g}'
            final_score = item.get('recommendation_scores', {}).get(section)
            signal_label = label(f"推荐 {fmt(final_score)} · 关联 {fmt(signals['relevance'])} · 可信参考 {fmt(signals['credibility'])} · "
                f"重要 {fmt(signals['importance'])} · 热度 {fmt(signals['heat'])}", 'subtitle')
            signal_label.setToolTip('可信度仅来源/元数据参考，不是真伪概率；重要性仅给定材料的模型估计。\n'
                f"热度=可见来源中{signals['heat_detail']['publishers']}个组织的报道覆盖，没有平台互动或增速。\n"
                '相似标题只是保守分组，不是独立核验。缺失指标不伪造，推荐分只计已观测贡献的下界。\n'
                + ('重要性依据：' + signals.get('importance_detail', {}).get('quote', '')
                   if signals.get('importance') is not None else '重要性未取得可用估计。'))
            body.addWidget(signal_label)
            semantic = signals.get('semantic_match')
            if semantic:
                match_label = label('兴趣匹配：' + semantic['category'] + ' / ' + '、'.join(semantic['topics']), 'subtitle')
                match_label.setToolTip(f"本次画像匹配 · 余弦相似度 {semantic['cosine']:.3f}；不是点击概率。\n"
                                      f"最近的已有标签：{semantic.get('nearest_tag') or '无'}；反馈在下次生成时重建画像。")
                body.addWidget(match_label)
        tags = record.get('payload', {}).get('content_tags', [])
        if tags:
            tag_label = label(' · '.join(t['label'] for t in tags[:4]), 'subtitle')
            tag_label.setToolTip('标题或摘要中的词汇提及；不代表论文的主要贡献或事实核验。')
            body.addWidget(tag_label)
        reading = item.get('reading', {})
        if reading:
            body.addWidget(label(reading_status_text(reading), 'subtitle'))
        toggle, explanation = disclosure(body, 'AI解读')
        text = browser()
        reasons = {'passage_validation_failed': '正文选段未匹配原文', 'reading_request_failed': '正文阅读请求失败',
                   'no_relevant_passages': '未取得有依据的相关选段', 'brief_too_long': '正文超出短版长度',
                   'brief_focus_invalid': '兴趣视角不在当前档案', 'brief_style_invalid': '表达仍含模板套话',
                   'brief_lead_invalid': '首句主体与核心对象不一致', 'invalid_json': '模型格式未修正完成',
                   'invalid_evidence_anchor': '引文未匹配原文', 'invalid_knowledge_schema': '输出字段不符合契约',
                   'synthesis_unavailable': '模型请求未完成', 'insufficient_evidence': '资料不足以支持解读',
                   'context_evidence_omitted': '正文超出本次输入容量'}
        diagnostic = '；'.join(dict.fromkeys(reasons[c] for c in item.get('diagnostics', []) if c in reasons))
        detail = model_failure_text(item.get('model_diagnostic', {}))
        if detail:
            diagnostic = diagnostic.replace('模型请求未完成', detail) if '模型请求未完成' in diagnostic else detail
        text.setMarkdown(item['explanation'] or (f'全库评分尚未完成，未开始前{limit}名解读；已完成分数保留，可重试。'
            if item['status'] == 'ranking_incomplete' else
            (diagnostic + '。' if diagnostic else '本条解读尚未完成。') + '新闻和兴趣分已保存，可重新整理。'))
        text.setMinimumHeight(95)
        text.setMaximumHeight(240)
        explanation.setContentsMargins(20, 6, 0, 6)
        explanation.addWidget(text)
        window.news_explanation_panels.append((toggle, text.parentWidget()))
        interaction_row(window, body, record, states[record['id']])
        layout.addWidget(card)
        cards.append((record['id'], card))
        rating_card, rating_body = box()
        rating_body.addWidget(label(record['title'], 'cardTitle'))
        row = QHBoxLayout()
        row.addWidget(label('你的评价（不同于AI兴趣分）'))
        choice = QComboBox()
        choice.addItem('未评分', None)
        for value in range(1, 6):
            choice.addItem(str(value), value)
        choice.setCurrentIndex(choice.findData(scores.get(record['id'])))
        choice.currentIndexChanged.connect(lambda idx, w=choice, eid=record['id'], rid=window.report_id:
            window.local(lambda: window.ratings.rate(user, rid, eid, w.currentData())) if w.currentData() else None)
        row.addWidget(choice)
        rating_body.addLayout(row)
        if decision.get('role') == 'explore':
            # Explicit topic feedback is separate from generic 1–5 ratings and writing quality.
            topic_feedback = QComboBox()
            for title, key in (('拓展反馈 · 不参与学习／撤回', 'clear'),
                               ('这个方向有用', 'useful'), ('这个方向不相关', 'irrelevant')):
                topic_feedback.addItem(title, key)
            topic_feedback.setCurrentIndex(topic_feedback.findData(
                recommendations.feedback_kind(user, window.report_id, record['id'])))
            topic_feedback.currentIndexChanged.connect(
                lambda idx, w=topic_feedback, eid=record['id'], rid=window.report_id:
                window.local(lambda: recommendations.feedback(user, rid, eid, w.currentData())))
            rating_body.addWidget(topic_feedback)
        rating_layout.addWidget(rating_card)
    layout.addStretch()
    rating_layout.addStretch()
    window.ranked_scroll.setWidget(container)
    window.article_scroll.setWidget(ratings)
    window.brief_stack.setCurrentIndex(1)
    window.exposure_tracker = ExposureTracker(window, cards, section)
    recommendations.mark_displayed(user, window.report_id, [i['evidence_id'] for i in items])
    window.summary.setMarkdown(report['markdown'])
    state = ('暂无新闻' if not payload['library_count'] else
             ('四指标推荐' if ranking else 'AI兴趣排序') if payload['ranking_complete'] else f'评分未完成 · 不是全库前{limit}名')
    if payload.get('exploration', {}).get('active'):
        state = f'核心推荐与兴趣拓展 · 非纯分数前{limit}'
    window.result_status.setText(f"{SECTIONS[section]} · 已评分 {payload['scored_count']}/{payload['library_count']} 条 · 展示 {len(items)} 条 · {state}")
    window.search_details.setPlainText(
        f"待评分 {payload['pending_count']} 条；无关键词兴趣硬筛选。\n"
        f"本报告模型调用 {report['metrics'].get('model_calls', 0)} 次；评分/解读缓存可复用。\n"
        + (f"复用评分 {report['metrics'].get('scoring_reused',0)} 条；仅补评修复 {report['metrics'].get('scoring_repaired_items',0)} 条。\n")
        + (f"重要性格式或引文未通过 {report['metrics']['scoring_validation']['invalid_importance']} 条，相关性评分仍已保留。\n"
           if report['metrics'].get('scoring_validation',{}).get('invalid_importance') else '')
        + (f"服务端已报告Token {report['metrics']['tokens']}；其中缓存输入 {report['metrics'].get('token_usage',{}).get('cached_input_tokens','未知')}，推理 {report['metrics'].get('token_usage',{}).get('reasoning_tokens','未知')}。未返回usage的请求不计入，不代表零消耗。\n"
           if report['metrics'].get('tokens') is not None else '服务商未提供完整Token用量；字符数不是Token数。\n') +
        '兴趣分不代表新闻真伪或重要性；引用检查不代表事实核验。\n' +
        ('四指标加权；缺失分项不计已获分。可信度非核验、热度仅报道代理；赞踩和打开仅本机记录。\n'
         if ranking else '') +
        ({'active': '本地多兴趣语义画像已参与关联性；未训练聊天模型。\n',
          'model_not_ready': '尚未准备语义模型，当前沿用标签反馈；可到兴趣与领域准备。\n',
          'semantic_unavailable': '语义计算未完成，本次沿用标签反馈，未伪造向量。\n',
          'no_confirmed_interests': '尚无已确认兴趣，未建立语义画像。\n',
          'disabled': '语义画像已关闭，本次沿用标签反馈。\n'}.get(payload.get('semantic_profile', {}).get('status'), '')) +
        ({'today': '时间：香港时间今天；未知日期不充当天新闻。\n',
          '24h': '时间：过去24小时；无精确时间戳的资料不计入。\n'}.get(report.get('search_audit', {}).get('time_scope'), '')) +
        coverage_text(report.get('search_audit', {}).get('coverage', {})) +
        (f"{sum(bool(i.get('archive_pending')) for i in items)}条实体/事件归档待校验，不影响已通过的解读。\n"
         if any(i.get('archive_pending') for i in items) else '') +
        ('部分评分或解读未完成，已完成结果保留，可重试。' if report.get('issues') else '') +
        ('\n评分诊断：' + report['metrics']['scoring_error'] if report['metrics'].get('scoring_error') else ''))
    window.search_details_toggle.setChecked(False)
    window.search_details.hide()


def coverage_text(coverage):
    if not coverage:
        return ''
    return (f"本轮选择 {coverage['selected_sources']}/{coverage['eligible_sources']} 个适用信源；"
            f"另 {coverage['deferred_sources']} 个待后续轮换。\n")
