"""Opt-in four-output live role-switch experiment using public/synthetic evidence."""
import argparse
import json
from pathlib import Path

from newspaper_agent.sources.contracts import EvidenceRecord
from .service import SynthesisService
from .brief_quality import ModelBriefReviewer, RUBRIC


def cases():
    paper = EvidenceRecord('eval:ssg', 'arxiv', 'api', 'arXiv',
        'Spin Space Groups: Full Classification and Applications', 'https://arxiv.org/abs/2307.10364',
        'paper', 'The authors use O(N) representations to classify spin space groups. Electronic states generally '
        'form projective representations of magnetic space groups. They identify SSGs for 1604 magnetic '
        'structures in MAGNDATA and discuss material examples of unusual electronic bands and spin textures. '
        'SOC applicability requires care.', '2024-05-18', is_fixture=False, content_level='abstract')
    news = EvidenceRecord('eval:synthetic-news', 'fixture', 'api', 'Synthetic evaluation',
        '虚构测试：Agent框架更新', 'https://example.org/synthetic-agent', 'news',
        '虚构测试资料，不是真实产品：框架新增可恢复的工具调用检查点和按账号隔离的记忆存储。'
        '批处理接口支持并发任务，但尚未公开吞吐量和成本测试。迁移需要修改存储适配器。',
        None, is_fixture=True, content_level='feed')
    return [(paper, {'interests': ['群论与投影表示'], 'knowledge_level': 'beginner',
                     'domain_reading_rules': ['凝聚态：expert，关注分类构造和表示论，不解释基础术语']},
             ('表示', '分类'), ('材料', '1604')),
            (paper, {'interests': ['磁性材料与能带'],
                     'domain_reading_rules': ['材料：expert，关注具体体系、电子结构和SOC适用条件']},
             ('材料', '磁结构', '能带', '电子结构'), ('O(N)', '表示论')),
            (news, {'interests': ['Agent架构可靠性'],
                    'domain_reading_rules': ['AI：入门，关注故障恢复和账号隔离，少讲商业背景']},
             ('恢复', '检查点', '隔离'), ('成本', '吞吐')),
            (news, {'interests': ['Agent部署成本和迁移'],
                    'domain_reading_rules': ['工程：熟悉基础，优先迁移代价和未验证的性能指标']},
             ('迁移', '成本', '吞吐'), ('检查点', '隔离'))]


def evaluate(model, *, review=False):
    outputs = []
    reviewer = ModelBriefReviewer(model) if review and model else None
    for evidence, profile, expected, other in cases():
        report = SynthesisService(model, max_repairs=1, brief_reviewer=reviewer).generate(
            '选择与读者兴趣有关的事实，用连贯短段落介绍主要变化和必要限制。', [evidence], profile=profile, news_brief=True)
        payload = report.knowledge_payload
        text = '\n'.join(t for c in payload['claims'] for t in
                         [c['statement'], *c['conditions'], c['uncertainty']] if t)
        outputs.append({'case': evidence.id, 'reader': profile['interests'][0], 'text': text,
                        'characters': len(text.replace('\n', '')), 'model_calls': report.metrics['model_calls'],
                        'contract_passed': report.metrics['validation_passed'] and bool(payload['claims']),
                        'focus': payload.get('reader_focus'),
                        'expected_terms_present': [t for t in expected if t.lower() in text.lower()],
                        'other_terms_present': [t for t in other if t.lower() in text.lower()],
                        'editorial_review': payload.get('writing_framework', {}).get('review'),
                        'issues': list(report.issues)})
    pairs = [{'case': outputs[i]['case'], 'different_text': outputs[i]['text'] != outputs[i+1]['text'],
              'different_focus': outputs[i]['focus'] != outputs[i+1]['focus']}
             for i in (0, 2)]
    return {'mode': 'live' if model else 'no_model', 'outputs': outputs, 'pairs': pairs,
            'logical_model_calls': sum(o['model_calls'] for o in outputs),
            'review_enabled': reviewer is not None, 'human_review_dimensions': list(RUBRIC),
            'semantic_accuracy': None, 'human_preference': None,
            'note': '关键词和文本差异只辅助人工检查，不证明事实正确或个性化有效。虚构新闻不能作为真实消息。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly spend up to eight logical model calls')
    parser.add_argument('--review', action='store_true', help='With --live, enable semantic review and repairs (up to sixteen total calls)')
    parser.add_argument('--provider')
    parser.add_argument('--connection-user', help='Use only this app account selected connection, never its profile')
    args = parser.parse_args()
    if args.review and not args.live:
        parser.error('--review需要显式--live；普通回归不调用评审模型')
    model = None
    if args.live:
        if args.connection_user:
            from newspaper_agent.database.research_repository import ResearchRepository
            from newspaper_agent.database.model_connections import ModelConnectionRepository
            from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings
            root = Path(__file__).resolve().parents[3]
            connections = ModelConnectionRepository(ResearchRepository(root / 'data/research.sqlite3'))
            selected = next((c for c in connections.list(args.connection_user) if c['selected']), None)
            if selected is None:
                parser.error('该账号没有已选择的连接')
            model = OpenAICompatibleClient(OpenAICompatibleSettings(
                provider='brief_evaluation', base_url=selected['base_url'], model=selected['model'],
                api_key_env='UNUSED_EVALUATION_KEY', wire_api=selected['wire_api'],
                max_tokens=1200, timeout=None, max_attempts=1),
                api_key=connections.key(args.connection_user, selected['id']))
        else:
            from newspaper_agent.model.factory import create_model_client
            model = create_model_client(args.provider)
    # ASCII JSON survives Windows console encodings; consumers can decode the Unicode escapes.
    result = evaluate(model, review=args.review)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if args.live and not all(row['contract_passed'] for row in result['outputs']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
