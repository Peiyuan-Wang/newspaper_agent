"""Evidence-located mention tags, not an automatic judgement of the main contribution."""
from dataclasses import replace
import re

TAG_VERSION = 1
CONCEPTS = (
    ('topic', 'ai', '人工智能', ('artificial intelligence', '人工智能', 'AI')),
    ('topic', 'agent', 'Agent', ('agentic', 'agents', 'agent', '智能体')),
    ('topic', 'llm', '大语言模型', ('large language model', 'LLM', '大语言模型')),
    ('topic', 'ssg', '自旋空间群', ('spin space groups', 'spin space group', 'spin-space groups', 'spin-space group', 'SSG', '自旋空间群')),
    ('topic', 'physics', '凝聚态物理', ('condensed matter', '凝聚态', '能带', 'band structure')),
    ('method', 'group_theory', '群论', ('group theory', 'group-theoretic', '群论')),
    ('method', 'projective_representation', '投影表示', ('projective representations', 'projective representation', '投影表示')),
    ('method', 'cohomology', '群上同调', ('group cohomology', '群上同调')),
    ('method', 'rl', '强化学习', ('reinforcement learning', '强化学习')),
    ('method', 'sft', '监督微调', ('supervised fine-tuning', 'supervised finetuning', 'SFT', '监督微调')),
    ('contribution_mention', 'classification', '分类', ('classification', '分类')),
    ('contribution_mention', 'release', '发布', ('released', 'launch', '发布')),
)


def content_tags(record):
    found = []
    for facet, concept, label, aliases in CONCEPTS:
        for field in ('title', 'snippet'):
            text = getattr(record, field)
            pattern = '|'.join((r'(?<![A-Za-z0-9_])' + re.escape(a) + r'(?![A-Za-z0-9_])'
                                if a.isascii() else re.escape(a)) for a in aliases)
            match = re.search(pattern, text, re.I)
            if match:
                found.append({'facet': facet, 'concept': concept, 'label': label,
                              'basis': 'mention_only', 'field': field, 'quote': match.group(),
                              'start': match.start(), 'end': match.end(), 'version': TAG_VERSION})
                break
    return found


def annotate_record(record):
    return replace(record, payload={**record.payload, 'content_tags': content_tags(record), 'tag_version': TAG_VERSION})


def routing_topics(queries):
    text = ' '.join(queries)
    class Text:
        title, snippet = text, ''
    concepts = {t['concept'] for t in content_tags(Text())}
    result = concepts & {'ai', 'agent', 'llm'}
    if concepts & {'ssg', 'physics', 'group_theory', 'projective_representation', 'cohomology'}:
        result.add('physics')
    if concepts & {'group_theory', 'projective_representation', 'cohomology'}:
        result.add('math')
    return result
