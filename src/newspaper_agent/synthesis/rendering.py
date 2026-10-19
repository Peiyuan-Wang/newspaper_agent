from __future__ import annotations

from urllib.parse import urlsplit
import re
from .editorial import category, groups, policy

STYLES = ("brief", "standard", "expert")


def excerpt(record, maximum=220):
    """A labelled extract, never a fabricated model summary."""
    if record.get('content_level') == 'metadata':
        return '仅有标题与出版信息，来源未提供摘要；无法据此判断具体发现。'
    text = ' '.join(record.get('snippet', '').split())
    sentences = re.split(r'(?<=[。！？])|(?<=[.!?])\s+', text)
    selected = ''.join(sentences[:2]) if re.search(r'[\u4e00-\u9fff]', text) else ' '.join(sentences[:2])
    return selected[:maximum] + ('…' if len(selected) > maximum or len(selected) < len(text) else '')


def safe_text(value: str) -> str:
    """将模型/来源当正文显示，禁用其 HTML 与自带 Markdown 链接。"""
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for mark in ("\\", "`", "*", "_", "[", "]", "#"):
        value = value.replace(mark, "\\" + mark)
    return value


def source_link(record: dict) -> str:
    url = record["url"]
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in ("http", "https") and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not any(char.isspace() or char in "<>\\\"'" for char in url))
    except ValueError:
        valid = False
    title = safe_text(record["title"])
    if valid:
        return f"[{title}]({url.replace('(', '%28').replace(')', '%29')})"
    return title + "（链接不可用）"


def render(payload: dict, *, style: str) -> str:
    if style not in STYLES:
        raise ValueError("未知阅读层级")
    evidence = payload["evidence"]
    if not evidence:
        return "# 暂未发现匹配信息\n\n本次没有可用资料，暂时无法形成有依据的回答。\n\n**下一步**：调整关键词、扩大时间范围，或补充专业来源后重试。检索失败与覆盖范围见「检索详情」。"
    records = {record["id"]: record for record in evidence}
    editions = groups(evidence)
    lines = ['## 本次简报', f'**{len(editions)} 件信息 · {len(evidence)} 条来源资料** · ' + ('AI 解读 · 重点与依据' if payload['claims'] else '来源速览 · 原文摘录')]
    if any(record["is_fixture"] for record in evidence):
        lines.append("【含虚构教学样例】样例仅用于验证流程，不能据此得出现实结论。")
    claims = payload["claims"]
    limit = {"brief": 2, "standard": 4, "expert": 8}[style]
    if not claims:
        lines.append("尚未形成可靠综合结论；以下为来源摘要摘录，不是 AI 解读。")
    else:
        lines.append('## 先看重点')
    table = payload.get('reader_preferences', {}).get('output_format') == 'table'
    outline = payload.get('reader_preferences', {}).get('output_format') == 'outline'
    table_at, table_rows = len(lines), []
    if claims and table:
        table_rows = ['| 重点 | 适用条件 | 尚不能确认 | 依据 |', '|---|---|---|---|']
    for index, claim in enumerate(claims[:limit]):
        statement = safe_text(claim["statement"])
        links = [source_link(records[ref["evidence_id"]]) for ref in claim["evidence_refs"]]
        if table:
            cells = [statement, '；'.join(safe_text(v) for v in claim['conditions']) or '未提供',
                safe_text(claim['uncertainty']) or '未提供', '、'.join(dict.fromkeys(links))]
            if claim['status'] == 'disputed':
                cells[2] += '；资料存在待核对分歧，此标记不是对真伪的裁定。'
            table_rows.append('| ' + ' | '.join(c.replace('|', '\\|').replace('\n', ' ') for c in cells) + ' |')
        else:
            lines.append(f'{index + 1}. **{statement}**' if outline else f'### {index + 1}. {statement}')
        if claim["conditions"] and not table:
            lines.append("适用条件：" + "；".join(safe_text(value) for value in claim["conditions"]))
        if claim["uncertainty"] and not table:
            lines.append("尚不能确认：" + safe_text(claim["uncertainty"]))
        if claim["status"] == "disputed" and not table:
            lines.append("资料存在待核对分歧；此标记不是对真伪的裁定。")
        if not table:
            lines.append("依据：" + "、".join(dict.fromkeys(links)))
        if style == "expert":
            for ref in claim["evidence_refs"]:
                lines.append("原文锚点：" + safe_text(ref["quote"]) + f"（{safe_text(ref['content_level'])}）")
    if table_rows:
        lines.insert(table_at, '\n'.join(table_rows))
    # 关键边界不随短版主张数量截断；全部原句均来自所用资料。
    boundaries = list(dict.fromkeys(item["text"] for item in payload["source_conditions"]))
    # 隐去主张的条件/不确定性仍保留，避免短版删除改变结论的限定。
    for claim in claims[limit:]:
        boundaries.extend(claim["conditions"])
        if claim["uncertainty"]:
            boundaries.append(claim["uncertainty"])
        if claim["status"] == "disputed":
            boundaries.append("其余资料含待核对分歧，可展开查看。")
    if not claims or style == "expert":
        if claims:
            lines.append('## 相关资料')
        current_section = None
        section_order = {c['id']: i for i, c in enumerate(policy()['categories'])}
        labels = {c['id']: c['label'] for c in policy()['categories']}
        editions.sort(key=lambda e: section_order.get(category(e['primary']), 99))
        fallback_rows = ['| 主题与原文 | 来源摘录 | 日期 | 栏目 |', '|---|---|---|---|'] if not claims and table else []
        for index, edition in enumerate(editions, 1):
            record = edition['primary']
            section = category(record)
            if fallback_rows:
                link = source_link(record)
                if len(edition['sources']) > 1:
                    link += '；' + '、'.join(source_link(r) for r in edition['sources'][1:])
                cells = [link, safe_text(excerpt(record)), safe_text((record['published_at'] or '未知')[:10]), labels[section]]
                fallback_rows.append('| ' + ' | '.join(c.replace('|', '\\|').replace('\n', ' ') for c in cells) + ' |')
                continue
            if current_section != section:
                lines.append('## ' + labels[section])
                current_section = section
            fixture = "【虚构样例】" if record["is_fixture"] else ""
            levels = {'abstract': '论文摘要', 'metadata': '仅出版信息', 'feed': '订阅内容', 'snippet': '网页片段', 'fulltext': '全文'}
            lines.append(f"### {index}. {fixture}{source_link(record)}\n\n"
                         f"{safe_text(record.get('publisher') or '原始来源')} · {safe_text((record['published_at'] or '日期未知')[:10])} · {levels.get(record['content_level'], '来源片段')}\n\n"
                         f"**摘要摘录：** {safe_text(record['snippet'] if style == 'expert' else excerpt(record))}")
            if len(edition['sources']) > 1:
                lines.append('同题来源：' + '、'.join(source_link(r) for r in edition['sources'][1:]))
            lines.append('---')
        if fallback_rows:
            lines.append('\n'.join(fallback_rows))
    if boundaries:
        lines.append("## 适用条件与待确认\n\n必须保留的边界：\n" + "\n".join("- " + safe_text(value) for value in dict.fromkeys(boundaries)))
    lines.append('## 阅读边界')
    lines.append("引用检查只确认 ID 和引文位置匹配，未验证主张是否被语义支持或现实事实是否正确。")
    if any(record["content_level"] != "fulltext" for record in evidence):
        lines.append("本次包含摘要或片段，不能视为已阅读全部原文。")
    if len(claims) > limit:
        lines.append(f"另有 {len(claims) - limit} 条主张，可请求展开。")
    if payload["follow_up"]:
        lines.append("可继续追问：" + "；".join(safe_text(value) for value in payload["follow_up"]))
    return "\n\n".join(lines)
