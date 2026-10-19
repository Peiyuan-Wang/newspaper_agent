"""Opt-in public article smoke test, never uses a personal reading profile or writes a report."""
import argparse
import json
from pathlib import Path

from newspaper_agent.sources.article_reader import ArticleReader
from newspaper_agent.sources.contracts import EvidenceRecord
from newspaper_agent.research.article_reading import prepare_reading
from newspaper_agent.research.model_runtime import BudgetModel
from .service import SynthesisService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--connection-user', required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error('需显式--live；此操作读取公开网页并最多调用模型6次。')
    from newspaper_agent.database.research_repository import ResearchRepository
    from newspaper_agent.database.model_connections import ModelConnectionRepository
    from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings
    root = Path(__file__).resolve().parents[3]
    connections = ModelConnectionRepository(ResearchRepository(root / 'data/research.sqlite3'))
    selected = next((c for c in connections.list(args.connection_user) if c['selected']), None)
    if not selected:
        parser.error('没有选中的模型连接')
    client = OpenAICompatibleClient(OpenAICompatibleSettings(
        provider='public_reading_test', base_url=selected['base_url'], model=selected['model'],
        api_key_env='UNUSED_PUBLIC_READING_KEY', wire_api=selected['wire_api'],
        max_tokens=1600, timeout=None, max_attempts=1),
        api_key=connections.key(args.connection_user, selected['id']))
    model = BudgetModel(client, max_calls=6, max_seconds=None)
    record = EvidenceRecord('smoke:smollm3', 'hf', 'rss', 'Hugging Face',
        'SmolLM3: smol, multilingual, long-context reasoner', 'https://huggingface.co/blog/smollm3',
        'news', 'Public technical article', '2026-10-16', published_at='2025-07-08', is_fixture=False)
    document = ArticleReader().read(record)
    profile = {'interests': ['模型后训练方法'], 'domain_reading_rules': ['AI：熟悉基础，关注后训练与可复现方法']}
    state = {}
    evidence, reading = prepare_reading(record, document, model, profile,
        save_checkpoint=lambda value: state.update(value))
    output = {'fetch_status': document['status'], 'characters': document.get('characters', 0), 'reading': reading}
    if evidence:
        result = SynthesisService(model, max_repairs=1).generate(
            '概括与后训练有关的具体方法，不写泛泛推荐。', [evidence], profile=profile, news_brief=True)
        output.update(claims=result.knowledge_payload['claims'], issues=result.issues,
                      valid=result.metrics['validation_passed'], metadata=result.knowledge_payload.get('news_metadata'))
    output['model_calls'] = model.calls
    output['reading_errors'] = [r['error'] for r in state.get('attempts', [])]
    print(json.dumps(output, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
