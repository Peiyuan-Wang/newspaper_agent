"""Reproducible offline feedback experiment on synthetic, held-out article families.

Uses the production encoder, document features, route/evolve/match implementation.
Never reads production profiles or calls an LLM. Outputs are experiment artifacts.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import csv
import json
import math
import random
import statistics
import time

from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.model.embeddings import local_encoder, model_ready
from newspaper_agent.research.semantic_personalization import SemanticPersonalizer
from newspaper_agent.semantic_profile import evolve, match, dot, POLICY
from newspaper_agent.sources.contracts import EvidenceRecord, SourceDescriptor

NOW = datetime(2026, 10, 19, 0, tzinfo=timezone.utc)
CHECKPOINTS = (0, 4, 8, 16, 32, 48, 64)
CONDITIONS = ('clean', 'noise20', 'opens', 'shift')
LABELS = {'clean':'正常赞踩', 'noise20':'20%误点赞踩', 'opens':'只有打开', 'shift':'中途偏好反转'}
# Fixed before evaluation: first four facets provide feedback, last four are held out.
# These are fictional research themes, NOT claims about actual published news.
TOPICS = {
    'theory': ('physics', True, 'Spin space group theory and projective representations',
        'A synthetic mathematical study of spin symmetry develops group theoretic constructions.',
        ['group extensions and cocycles', 'irreducible projective representations',
         'reciprocal space nonsymmorphic symmetry', 'cohomology of spin space groups',
         'classification by factor systems', 'representation induction and restriction',
         'algebraic constraints on symmetry degeneracy', 'topological invariants of symmetry representations']),
    'materials': ('physics', False, 'Magnetic material applications of spin space group symmetry',
        'A synthetic materials study evaluates experimental compounds and electronic band structures.',
        ['crystal growth and magnetic measurements', 'density functional calculations of candidate compounds',
         'neutron scattering of ordered magnetic crystals', 'experimental band degeneracy in alloys',
         'chemical substitution and sample preparation', 'spectroscopy of magnetic thin films',
         'materials screening for spin transport devices', 'pressure control of magnetic phases']),
    'reliability': ('ai', True, 'Reliable tool use in large language model agents',
        'A synthetic technical study evaluates agent failure modes and reliable execution.',
        ['tool call schema validation', 'recovery from interrupted tool execution',
         'grounded responses and evidence checking', 'agent memory consistency tests',
         'sandbox isolation of untrusted tools', 'idempotent execution and retries',
         'long horizon planning failure benchmarks', 'evaluation of permission boundaries']),
    'consumer': ('ai', False, 'Consumer applications of large language model agents',
        'A fictional product announcement discusses consumer features and subscription plans.',
        ['chat assistant pricing and discounts', 'animated assistant avatars',
         'mobile voice companion interface', 'social sharing of generated pictures',
         'new themes for chat windows', 'consumer subscription bundles',
         'celebrity voice packs for assistants', 'marketing campaigns for AI companions']),
    'cooking': ('other', False, 'Home cooking and bread baking',
        'A fictional cooking guide compares recipes and household techniques.',
        ['sourdough starter temperature', 'measuring flour and water', 'baking crust in a home oven',
         'mixing yeast with flour', 'storing freshly baked bread', 'kneading dough by hand',
         'seasoning vegetable soup', 'preparing homemade pasta']),
}


def dataset():
    rows, meta = [], {}
    for topic, (category, relevant, title, premise, facets) in TOPICS.items():
        for facet_index, facet in enumerate(facets):
            split = 'train' if facet_index < 4 else 'test'
            for variant in range(4 if split == 'train' else 2):
                eid = f'{topic}-{facet_index}-{variant}'
                heading = [f'{title}: {facet}', f'{facet.capitalize()} in {title.lower()}',
                           f'A controlled study of {facet}', f'Comparing approaches to {facet}'][variant]
                snippet = f'{premise} The focus is {facet}. '
                snippet += ['The comparison discusses assumptions and limitations.',
                            'The example examines methods and their applicable conditions.',
                            'The evaluation records outcomes under a controlled setting.',
                            'The discussion separates observations from interpretation.'][variant]
                rows.append(EvidenceRecord(eid,'synthetic','fixture','Synthetic experiment',heading,
                    'https://example.org/experiment/'+eid,'news',snippet,NOW.isoformat(),
                    published_at=NOW.isoformat(),is_fixture=True))
                meta[eid] = {'topic':topic,'category':category,'relevant':relevant,'split':split,'facet':facet_index}
    return rows, meta


def profile():
    pairs = [('Condensed matter','spin space group symmetry and electronic band structures'),
             ('Condensed matter','group cohomology and projective representations'),
             ('AI systems','large language model agents and applications')]
    return {'interest_units':[{'category':cat,'topic':topic,'domain':'academic','methods':[],
        'question':'','purpose':'','knowledge_level':'expert','priority':3,'expires_on':None}
        for cat,topic in pairs]}


def relevant(meta, condition, step):
    return (not meta['relevant'] if condition == 'shift' and step >= 32 and meta['category']=='physics'
            else meta['relevant'])


def ranking_metrics(ranking, labels, topics, k=10):
    selected = ranking[:k]
    dcg = sum(float(labels[eid])/math.log2(i+2) for i,eid in enumerate(selected))
    ideal = sum(1/math.log2(i+2) for i in range(min(k,sum(labels.values()))))
    counts = [sum(topics[eid]==topic for eid in selected) for topic in set(topics.values())]
    entropy = -sum((n/len(selected))*math.log2(n/len(selected)) for n in counts if n)
    return {'precision':sum(labels[eid] for eid in selected)/len(selected),
            'ndcg':dcg/ideal if ideal else 0., 'topic_entropy':entropy}


def evaluate(slots, docs, meta, test_ids, base_scores, condition, step):
    matches = {eid:match(slots,docs[eid]) for eid in test_ids}
    scores = {eid:(m['score'] if m else 0.) for eid,m in matches.items()}
    # Exact deployed fusion without direct same-article vote: all evaluation IDs are unseen.
    hybrid = {eid:base_scores[eid]+max(-20.,min(20.,.25*(scores[eid]-base_scores[eid])))
              if matches[eid] else base_scores[eid] for eid in test_ids}
    labels = {eid:relevant(meta[eid],condition,step) for eid in test_ids}
    topics = {eid:meta[eid]['topic'] for eid in test_ids}
    ranked = sorted(test_ids,key=lambda eid:(-scores[eid],eid))
    fused = sorted(test_ids,key=lambda eid:(-hybrid[eid],eid))
    metrics = ranking_metrics(ranked,labels,topics)
    metrics.update({'hybrid_'+k:v for k,v in ranking_metrics(fused,labels,topics).items()})
    metrics['angle'] = max(s['angle'] for s in slots)
    metrics['matched_fraction'] = sum(m is not None for m in matches.values())/len(test_ids)
    for topic in TOPICS:
        metrics['score_'+topic] = statistics.mean(scores[eid] for eid in test_ids if meta[eid]['topic']==topic)
    return metrics


def simulate(slots, docs, meta, *, seeds=20):
    train_ids = [eid for eid,m in meta.items() if m['split']=='train' and m['category']!='other']
    test_ids = [eid for eid,m in meta.items() if m['split']=='test']
    output, snapshots = [], {}
    for seed in range(seeds):
        rng = random.Random(17000+seed)
        order = list(train_ids); rng.shuffle(order)
        # Fixed synthetic A scores, independent of target labels; not actual LLM scores.
        base = {eid:(55+rng.uniform(-10,10) if meta[eid]['category']!='other' else 10) for eid in test_ids}
        flips = {eid:rng.random()<.20 for eid in train_ids}
        for condition in CONDITIONS:
            observations = []
            for step in range(65):
                if step in CHECKPOINTS:
                    evolved, skipped = evolve(slots,observations,now=NOW)
                    measured = evaluate(evolved,docs,meta,test_ids,base,condition,step)
                    static = evaluate(slots,docs,meta,test_ids,base,condition,step)
                    output.append({'seed':seed,'condition':condition,'step':step,'observations':len(observations),
                        'skipped':skipped,**measured,**{'baseline_'+k:v for k,v in static.items()}})
                    if seed==0:
                        snapshots[f'{condition}:{step}'] = evolved
                if step==64: break
                eid = order[step]
                liked = relevant(meta[eid],condition,step)
                if condition=='opens':
                    if not liked: continue  # no click is not negative feedback
                    kind='open'
                else:
                    if condition=='noise20' and flips[eid]: liked=not liked
                    kind='up' if liked else 'down'
                observations.append({**docs[eid],'kind':kind,'at':NOW.isoformat(),'evidence_id':eid})
    return output,snapshots


def aggregate(rows):
    numeric = [k for k in rows[0] if k not in ('seed','condition','step')]
    output=[]
    for condition in CONDITIONS:
        for step in CHECKPOINTS:
            group=[r for r in rows if r['condition']==condition and r['step']==step]
            item={'condition':condition,'step':step}
            for key in numeric:
                values=[r[key] for r in group]
                mean=statistics.mean(values)
                ci=1.96*statistics.stdev(values)/math.sqrt(len(values)) if len(values)>1 else 0.
                item[key]={'mean':mean,'ci95':ci}
            output.append(item)
    return output


def controls(slots, docs, meta):
    eid=next(e for e,m in meta.items() if m['topic']=='theory' and m['split']=='train')
    obs={**docs[eid],'kind':'up','at':NOW.isoformat()}
    learned,_=evolve(slots,[obs],now=NOW)
    replay,_=evolve(learned,[obs],now=NOW)
    undo,_=evolve(learned,[],now=NOW)
    old,_=evolve(slots,[obs],now=NOW+timedelta(days=180))
    return {'replay_max_delta':max(abs(a-b) for x,y in zip(learned,replay) for a,b in zip(x['vector'],y['vector'])),
        'undo_max_delta':max(abs(a-b) for x,y in zip(slots,undo) for a,b in zip(x['anchor'],y['vector'])),
        'other_category_max_delta':max(abs(a-b) for x,y in zip(slots,learned) if x['category']=='AI systems'
                                     for a,b in zip(x['vector'],y['vector'])),
        'positive_mass_now':sum(s['positive_mass'] for s in learned),
        'positive_mass_after_180_days':sum(s['positive_mass'] for s in old)}


def run(output, *, seeds=20):
    if not 2<=seeds<=100: raise ValueError('seeds must be 2..100')
    root=Path(__file__).resolve().parents[3]
    if not model_ready(root/'data'): raise ValueError('本地语义模型未准备；本实验不会自动下载')
    started=time.perf_counter()
    encoder=local_encoder(str(root/'data'))
    records,meta=dataset()
    with TemporaryDirectory(prefix='newspaper-feedback-experiment-') as tmp:
        repo=ResearchRepository(Path(tmp)/'experiment.sqlite3')
        repo.register('experiment','synthetic-experiment-password')
        engine=SemanticPersonalizer(repo,'experiment',encoder)
        source=SourceDescriptor('synthetic','Synthetic',('academic',),('news',),('*',),('en',),
                                'rss','enabled','https://example.org/feed')
        state,_,_=engine.build(profile(),records,{'synthetic':source},now=NOW)
        docs=engine.store.documents('experiment',engine.version,[r.id for r in records])
    encoded_seconds=time.perf_counter()-started
    slots=state['slots']; sim_started=time.perf_counter()
    rows,snapshots=simulate(slots,docs,meta,seeds=seeds)
    result={'policy':POLICY,'encoder':encoder.version,'feature_version':engine.version,
        'dimension':encoder.dimension,'seeds':seeds,'synthetic':True,'llm_calls':0,
        'articles':len(records),'training_articles':64,'held_out_articles':40,
        'unused_out_of_domain_training_articles':16,'slots':len(slots),
        'encoding_seconds':round(encoded_seconds,3),'simulation_seconds':round(time.perf_counter()-sim_started,3),
        'controls':controls(slots,docs,meta),'aggregate':aggregate(rows)}
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    def save(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    save('results.json',result);save('profile_trajectories.json',snapshots)
    # K and V are the same immutable document feature in this implementation, not model KV tensors.
    save('news_KV.json',{'encoder':encoder.version,'feature_version':engine.version,
        'V_equals_K':True,'documents':{r.id:{'K':docs[r.id]['vector'],'V_ref':'K',**meta[r.id],
            'title':r.title,'snippet':r.snippet} for r in records}})
    with (output/'runs.csv').open('w',encoding='utf-8',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    from .feedback_experiment_report import write_report
    write_report(output,result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('outputs/feedback-experiment-2026-10-19'))
    parser.add_argument('--seeds',type=int,default=20)
    args=parser.parse_args()
    result=run(args.output,seeds=args.seeds)
    print(json.dumps({k:v for k,v in result.items() if k!='aggregate'},ensure_ascii=False,indent=2))
