"""Small real-encoder control experiment; no production account or chat API."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from newspaper_agent.model.embeddings import local_encoder, model_ready
from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.sources.contracts import EvidenceRecord, SourceDescriptor
from newspaper_agent.research.semantic_personalization import SemanticPersonalizer
from newspaper_agent.semantic_profile import evolve, dot, match


def run():
    root = Path(__file__).resolve().parents[3]
    if not model_ready(root/'data'):
        raise ValueError('请先准备本地语义模型')
    encoder = local_encoder(str(root/'data'))
    cases = json.loads((root/'evals/semantic_profile_cases.json').read_text(encoding='utf-8'))
    profile = {'interest_units': [{**s,'domain':'academic','methods':[],'question':'','purpose':'',
        'knowledge_level':'expert','priority':3,'expires_on':None} for s in cases['interests']]}
    now = datetime.now(timezone.utc)
    rows = [EvidenceRecord(r['id'],'control','rss','Synthetic evaluation',r['title'],
        'https://example.org/control/'+r['id'],'paper',r['snippet'],now.isoformat(),is_fixture=False) for r in cases['articles']]
    source = SourceDescriptor('control','Synthetic evaluation',('academic',),('paper',),('*',),('en',),
                              'rss','enabled','https://example.org/feed')
    with TemporaryDirectory(prefix='newspaper-semantic-eval-') as temporary:
        repo = ResearchRepository(Path(temporary)/'evaluation.sqlite3')
        repo.register('evaluation','evaluation-only-password')
        engine = SemanticPersonalizer(repo,'evaluation',encoder)
        state,matches,_ = engine.build(profile,rows,{'control':source},now=now)
        documents = engine.store.documents('evaluation',engine.version,[r.id for r in rows])
        slots = state['slots']
        target = documents['math']
        liked,_ = evolve(slots,[{**target,'kind':'up','at':now.isoformat()}],now=now)
        disliked,_ = evolve(slots,[{**target,'kind':'down','at':now.isoformat()}],now=now)
        before = match(slots,target)
        positive,negative = match(liked,target),match(disliked,target)
        checks = {
            'related_categories_match': all((matches[r['id']] or {}).get('category') == r['expected_category']
                                           for r in cases['articles']),
            'positive_feedback_increases_similarity': positive['cosine'] > before['cosine'],
            'negative_feedback_decreases_similarity': negative['cosine'] < before['cosine'],
            'unrelated_category_unchanged': all(s['vector'] == original['vector'] for s,original in zip(liked,slots)
                                               if s['category'] != before['category']),
            'undo_restores_seed': all(s['vector'] == s['anchor'] for s in evolve(slots,[],now=now)[0]),
            'similar_interests_share_slot': len(slots) < len(cases['interests'])}
        return {'experiment': cases['description'], 'model':encoder.version,'dimension':encoder.dimension,
                'slots':[{'category':s['category'],'topics':s['topics']} for s in slots],
                'matches':{k:{key:v[key] for key in ('category','cosine','score')} if v else None for k,v in matches.items()},
                'feedback_similarity':{'before':before['cosine'],'like':positive['cosine'],'dislike':negative['cosine']},
                'checks':checks,'passed':all(checks.values())}


if __name__ == '__main__':
    result = run()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)
