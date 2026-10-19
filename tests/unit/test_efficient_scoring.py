from dataclasses import replace
from collections import Counter
from types import SimpleNamespace
import json
import threading
import time
import httpx
import pytest

from test_news_pipeline import setup, records, Model
from newspaper_agent.agent_core.messages import Message
from newspaper_agent.research.news_pipeline import generate_news
from newspaper_agent.research.session import ResearchSession
from newspaper_agent.research.efficient_scoring import parse_items, score_pending, batch_limit
from newspaper_agent.research.model_runtime import BudgetModel
from newspaper_agent.research.importance_scoring import parse_importance, DIMENSIONS
from newspaper_agent.database.news_feature_cache import NewsFeatureCache, feature_key
from newspaper_agent.database.news_repository import NewsRepository
from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings, ModelRequestFailure


def test_350_news_bad_importance_no_longer_discards_250_valid_scores(setup):
    repo,sources,_ = setup
    class Compact(Model):
        settings = SimpleNamespace(max_tokens=4096)
        def complete(self,messages,*,tools=()):
            data=json.loads(messages[1].content)
            if 'articles' not in data:
                return super().complete(messages,tools=tools)
            self.score_calls += 1
            rows=[]
            for r in data['articles']:
                index=int(r['title'].split()[-1])
                quote='incorrect quote' if index>=100 and index%20==0 else r['snippet']
                rows.append([r['id'],index%101,[60]*5,quote])
            return Message('assistant',json.dumps({'scores':rows},separators=(',',':')))
    items=records(350)
    # Reproduce exactly the old all-or-nothing validation outcome: five valid batches.
    old_count=0
    for start in range(0,350,20):
        batch=items[start:start+20]
        raw={'scores':[{'id':i,'score':50,'importance':dict.fromkeys(DIMENSIONS,60),
                        'quote':'incorrect quote' if start>=100 and i==0 else r.snippet}
                       for i,r in enumerate(batch)]}
        try:
            parse_importance(json.dumps(raw),batch)
            old_count += len(batch)
        except ValueError:
            pass
    assert old_count==100
    model=Compact()
    session=ResearchSession(repo,'alice',sources,model)
    report=generate_news(session,'latest news',items,{})
    assert report.knowledge_payload['scored_count']==350
    assert report.knowledge_payload['ranking_complete']
    assert model.score_calls==9 and model.explanation_calls==10
    assert report.metrics['scoring_validation']['invalid_importance']==13
    assert all(i['status']=='completed' for i in report.knowledge_payload['ranked_items'])
    rid=session._save('latest news',report)['report_id']
    again=session.resynthesize(rid)
    assert again['metrics']['model_calls']==0
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM news_feature_kv WHERE user_id=?',('alice',)).fetchone()[0]==350
    assert not NewsFeatureCache(repo).decisions('bob',[feature_key(items[0])],report.knowledge_payload['score_version'],'irrelevant')


def test_partial_rows_retry_only_missing_and_accept_fences():
    rows=records(4)
    class Partial:
        def __init__(self): self.seen=[]
        def complete(self,messages,*,tools=()):
            articles=json.loads(messages[1].content)['articles']
            self.seen.append([r['title'] for r in articles])
            scores=[[r['id'],75] for r in articles if len(self.seen)>1 or r['id']!=2]
            return Message('assistant','```json\n'+json.dumps({'scores':scores})+'\n```')
    client=Partial(); saved={}
    result=score_pending(BudgetModel(client,max_calls=10),rows,{},'latest news',saved.update)
    assert len(saved)==4 and len(client.seen)==2
    assert client.seen[1]==[rows[2].title]
    assert result['scoring_repaired_items']==1
    valid,_=parse_items('{"scores":[[0,50],[0,60],[1,true],[2,80]]}',rows)
    assert set(valid)=={2}


def test_output_limit_splits_without_repeating_successful_batches():
    class Limited:
        def __init__(self): self.sizes=[]
        def complete(self,messages,*,tools=()):
            articles=json.loads(messages[1].content)['articles']; self.sizes.append(len(articles))
            if len(articles)>5:
                raise ModelRequestFailure('output_limit','limited',{})
            return Message('assistant',json.dumps({'scores':[[r['id'],60] for r in articles]}))
    client=Limited(); saved={}
    result=score_pending(BudgetModel(client,max_calls=30),records(20),{},'latest news',saved.update)
    assert len(saved)==20 and client.sizes==[20,10,10,5,5,5,5]
    assert 'scoring_error' not in result


def test_parallel_requests_are_bounded_and_save_on_coordinator():
    state={'active':0,'peak':0,'calls':0}; lock=threading.Lock(); barrier=threading.Barrier(2)
    class Parallel:
        def fork(self): return Parallel()
        def complete(self,messages,*,tools=()):
            with lock:
                state['active']+=1; state['calls']+=1
                state['peak']=max(state['peak'],state['active']); number=state['calls']
            if number<=2: barrier.wait(timeout=3)
            articles=json.loads(messages[1].content)['articles']
            with lock: state['active']-=1
            return Message('assistant',json.dumps({'scores':[[r['id'],65] for r in articles]}))
    saved={}; caller=threading.get_ident()
    def save(values):
        assert threading.get_ident()==caller
        saved.update(values)
    budget=BudgetModel(Parallel(),max_calls=10)
    result=score_pending(budget,records(45),{},'latest news',save)
    assert state['peak']==2 and len(saved)==45 and budget.calls==3
    assert result['scoring_workers']==2


def test_permanent_errors_stop_without_request_storm_and_pending_not_zero():
    class Unavailable:
        def __init__(self,code): self.calls=0; self.code=code
        def complete(self,*args,**kwargs):
            self.calls+=1
            raise ModelRequestFailure(self.code,'unavailable',{})
    for code,expected in [('http_401',1),('http_429',1),('http_502',3)]:
        client=Unavailable(code); saved={}
        result=score_pending(BudgetModel(client,max_calls=30),records(350),{},'latest news',saved.update)
        assert client.calls==expected and not saved and result['scoring_error']==code


def test_persistent_kv_reuses_content_across_snapshot_ids_and_invalidates_changes(setup):
    repo,sources,model=setup
    session=ResearchSession(repo,'alice',sources,model)
    row=records(1)[0]
    report=generate_news(session,'latest news',[row],{})
    alias=replace(row,id='new-snapshot',retrieved_at='2026-10-18T00:00:00+00:00')
    again=generate_news(session,'latest news',[alias],{})
    assert model.score_calls==1 and again.metrics['feature_cache_restored']==1
    changed=replace(alias,snippet='Updated evidence changes the assessment')
    generate_news(session,'latest news',[changed],{})
    assert model.score_calls==2
    assert NewsRepository(repo).articles('alice',[changed.id])[changed.id]['score_state']=='scored'


def test_usage_accounting_includes_failed_output_and_does_not_add_reasoning_twice():
    replies=[{'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},
              'usage':{'input_tokens':100,'output_tokens':200,'total_tokens':300,
                       'input_tokens_details':{'cached_tokens':80},'output_tokens_details':{'reasoning_tokens':190}}}]
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json=replies[0]))
    client=OpenAICompatibleClient(OpenAICompatibleSettings('test','https://example.org/v1','test','unused',
        wire_api='responses',max_attempts=1),api_key='test-only',transport=transport)
    budget=BudgetModel(client)
    with pytest.raises(ModelRequestFailure): budget.complete([Message('user','test')])
    assert budget.metrics()['tokens']==300 and budget.metrics()['token_usage']['reasoning_tokens']==190
    assert budget.metrics()['token_usage']['cached_input_tokens']==80
    clone=client.fork()
    assert clone is not client and clone.last_usage=={}
    assert OpenAICompatibleClient.usage_counts({'usage':{'prompt_tokens':12,'completion_tokens':3}})['total_tokens']==15
    assert OpenAICompatibleClient.usage_counts({'usage':{'total_tokens':True}})=={}


def test_context_packing_and_compact_format_reduce_duplicate_output():
    class Echo:
        def complete(self,messages,*,tools=()):
            articles=json.loads(messages[1].content)['articles']
            return Message('assistant',json.dumps({'scores':[[r['id'],60] for r in articles]}))
    saved={}
    result=score_pending(BudgetModel(Echo(),max_calls=30,max_context_chars=2400),records(20),{},'latest news',saved.update)
    assert len(saved)==20 and result['scoring_calls']>1
    old=json.dumps({'scores':[{'id':i,'score':80,'importance':dict.fromkeys(DIMENSIONS,60),'quote':'Evidence'} for i in range(40)]})
    new=json.dumps({'scores':[[i,80,[60]*5,'Evidence'] for i in range(40)]},separators=(',',':'))
    assert len(new)<.4*len(old)  # Character count, explicitly not an actual tokenizer benchmark.


def test_expression_change_reuses_long_reading_and_unchanged_profile_revision(setup):
    repo,sources,_=setup
    class ReaderModel(Model):
        block_calls=0
        def complete(self,messages,*,tools=()):
            data=json.loads(messages[1].content)
            if 'blocks' in data:
                self.block_calls+=1
                b=data['blocks'][0]
                return Message('assistant',json.dumps({'passages':[{'block':b['id'],'quote':b['text'][:40]}]}))
            response=super().complete(messages,tools=tools)
            if 'articles' not in data:
                body=json.loads(response.content)
                body['claims'][0]['evidence_refs'][0]['quote']=data['evidence'][0]['snippet'][:40]
                return Message('assistant',json.dumps(body))
            return response
    model=ReaderModel(); session=ResearchSession(repo,'alice',sources,model)
    session.article_reader=SimpleNamespace(read=lambda _: {'status':'extracted','text':'Original facts about the new method. '*500})
    first=generate_news(session,'latest news',records(1),{})
    assert first.metrics['reading_calls']==2 and model.block_calls==2
    repo.update_profile('alice',{'expression_rules':['先说明实际发现']},'test confirmed style')
    second=generate_news(session,'latest news',records(1),{})
    assert second.metrics['reading_calls']==0 and model.block_calls==2
    assert second.metrics['interpretation_calls']==1
    repo.update_profile('alice',{'expression_rules':['先说明实际发现']},'same content new revision')
    third=generate_news(session,'latest news',records(1),{})
    assert third.metrics['model_calls']==0


def test_context_repacking_reserves_enough_calls_for_whole_library():
    class Echo:
        settings=SimpleNamespace(max_tokens=4096)
        def complete(self,messages,*,tools=()):
            articles=json.loads(messages[1].content)['articles']
            return Message('assistant',json.dumps({'scores':[[r['id'],60] for r in articles]}))
    rows=[replace(r,snippet='x'*600) for r in records(80)]
    saved={}
    budget=BudgetModel(Echo(),max_calls=3,max_context_chars=1600)
    result=score_pending(budget,rows,{},'latest news',saved.update,reserve_calls=11)
    assert len(saved)==80 and result['scoring_calls']==80 and 'scoring_error' not in result
