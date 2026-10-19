"""Compact, item-checkpointed scoring with bounded adaptive recovery and concurrency."""
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict
import json
import math
from pathlib import Path

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.agent_core.runtime import RunCancelled
from newspaper_agent.database.news_feature_cache import feature
from newspaper_agent.synthesis.brief_contract import parse_brief_json
from .importance_scoring import parse_importance, DIMENSIONS


def parse_items(content, records):
    """Bad importance never discards a valid relevance score or another item's work."""
    if not isinstance(content,str) or len(content) > 40000:
        raise ValueError('invalid_scores')
    data = parse_brief_json(content)
    if not isinstance(data,dict) or set(data) != {'scores'} or not isinstance(data['scores'],list):
        raise ValueError('invalid_scores')
    if len(data['scores']) > 2*len(records)+10:
        raise ValueError('invalid_scores')
    rows, ids, warnings = [], Counter(), Counter()
    for value in data['scores']:
        if isinstance(value,list) and len(value) in (2,4):
            row = {'id':value[0], 'score':value[1]}
            if len(value) == 4:
                rubric = value[2]
                if isinstance(rubric,list) and len(rubric)==5:
                    rubric = dict(zip(DIMENSIONS,rubric))
                row.update(importance=rubric,quote=value[3])
        elif isinstance(value,dict):
            row = value
        else:
            warnings['invalid_row_shape'] += 1
            continue
        idx = row.get('id')
        if type(idx) is int and 0 <= idx < len(records):
            ids[idx] += 1
            rows.append(row)
        else:
            warnings['invalid_item_id'] += 1
    valid = {}
    for row in rows:
        idx, score = row['id'], row.get('score')
        if ids[idx] != 1:
            warnings['duplicate_item_id'] += 1
            continue
        if type(score) is not int or not 0 <= score <= 100:
            warnings['invalid_interest_score'] += 1
            continue
        try:
            _, assessed = parse_importance(json.dumps({'scores':[{**row,'id':0}]}), [records[idx]])
            importance = assessed[0]
        except ValueError:
            importance = {'score':None,'mode':'invalid_importance_not_relevance'}
            warnings['invalid_importance'] += 1
        valid[idx] = score, importance
    return valid, warnings


def batch_limit(client):
    settings = getattr(client,'settings',None)
    if settings is None:
        return 20
    tokens = getattr(settings,'max_tokens',None)
    # Reserve space for protocol/reasoning; no silent increase to the user's output limit.
    return min(40,max(1,(tokens-512)//80)) if type(tokens) is int else 32


def scoring_budget(count, client):
    batches = math.ceil(count/batch_limit(client)) if count else 0
    return batches + max(20,2*batches) if count else 0


def score_pending(model, records, preferences, question, save, *, completed=0, total=None, reserve_calls=None):
    """save(valid_records) runs on coordinator thread, before scheduling any repairs."""
    total = len(records)+completed if total is None else total
    prompt = (Path(__file__).resolve().parents[3]/'prompts/interest_scoring_fast.md').read_text(encoding='utf-8')
    client = model.client
    limit = batch_limit(client)
    max_requests = scoring_budget(len(records),client)
    workers = 2 if callable(getattr(client,'fork',None)) else 1
    queue = deque((list(records[i:i+limit]),0) for i in range(0,len(records),limit))
    counts = {'scoring_calls':0,'failed_scoring_batches':0,'scoring_workers':workers,
              'scoring_batch_limit':limit,'scoring_reused':completed,'scoring_repaired_items':0}
    errors, warnings, fatal, transport_streak = Counter(), Counter(), None, 0

    def messages_for(batch, attempt=0):
        material = {'profile':preferences,'query':question,
                    'articles':[{'id':i,**feature(r)} for i,r in enumerate(batch)]}
        repair = ('\n本次是缺项补评，只返回本批id，各一次。score必须为0–100整数，不能用字符串或布尔值。'
                  '不确定的重要性用null和空引文，不要遗漏新闻或输出说明。' if attempt else '')
        return [Message('system',prompt+repair),Message('user',json.dumps(material,ensure_ascii=False,separators=(',',':')))]

    def size(messages):
        measure = getattr(client,'request_characters',None)
        return measure(messages,()) if callable(measure) else len(json.dumps(
            {'messages':[asdict(m) for m in messages],'tools':[]},ensure_ascii=False))

    # Plan actual fitting batches before reserving recovery calls. Tiny configured
    # contexts must not retain a budget calculated for nonexistent 40-item batches.
    packed = deque()
    while queue:
        batch,attempt = queue.popleft()
        if size(messages_for(batch)) > model.max_context_chars and len(batch)>1:
            half=len(batch)//2
            queue.appendleft((batch[half:],attempt))
            queue.appendleft((batch[:half],attempt))
        else:
            packed.append((batch,attempt))
    queue=packed
    max_requests=len(queue)+max(20,2*len(queue))
    if reserve_calls is not None:
        model.max_calls=max(model.max_calls,model.calls+max_requests+reserve_calls)

    def run(batch, messages):
        task_client = client.fork() if workers > 1 else client
        answer = None
        try:
            answer = model.complete_using(task_client,messages,tools=())
            if answer.tool_calls:
                return {},Counter(),'invalid_scores'
            valid, issues = parse_items(answer.content,batch)
            return valid,issues,None if len(valid)==len(batch) else 'invalid_scores'
        except RunCancelled:
            raise
        except ValueError:
            return {},Counter(),'invalid_scores' if answer is not None else 'model_request_failed'
        except Exception as exc:
            code = getattr(exc,'code',None)
            allowed = {'output_limit','context_budget','invalid_json','invalid_response','incomplete_response',
                       'http_400','http_401','http_403','http_404','http_429','http_500','http_502','http_503',
                       'http_504','http_524','read_timeout','connect_timeout'}
            return {},Counter(),code if code in allowed else 'model_request_failed'

    # No fire-and-forget requests: at most two in flight, each has isolated client diagnostics.
    active = {}
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='news-score') as executor:
        while queue or active:
            if model.control:
                model.control.check()
            while queue and len(active)<workers and not fatal:
                if counts['scoring_calls'] >= max_requests:
                    fatal = 'scoring_recovery_budget'
                    break
                batch,attempt = queue.popleft()
                messages = messages_for(batch,attempt)
                if size(messages) > model.max_context_chars:
                    if len(batch)>1:
                        half=len(batch)//2
                        queue.appendleft((batch[half:],attempt))
                        queue.appendleft((batch[:half],attempt))
                        continue
                    fatal='context_budget'
                    break
                if model.control:
                    model.control.emit('interest_scoring',count=completed,total=total)
                counts['scoring_calls'] += 1
                active[executor.submit(run,batch,messages)] = batch,attempt
            if not active:
                break
            ready,_ = wait(active,timeout=.1,return_when=FIRST_COMPLETED)
            for future in ready:
                batch,attempt = active.pop(future)
                valid,issues,error = future.result()
                warnings.update(issues)
                if valid:
                    save({batch[i].id:value for i,value in valid.items()})
                    completed += len(valid)
                    if attempt:
                        counts['scoring_repaired_items'] += len(valid)
                    if model.control:
                        model.control.emit('interest_scoring',count=completed,total=total)
                missing = [r for i,r in enumerate(batch) if i not in valid]
                if not missing:
                    transport_streak=0
                    continue
                counts['failed_scoring_batches'] += 1
                errors[error] += 1
                if error in {'http_400','http_401','http_403','http_404','http_429','model_request_failed'}:
                    fatal = error
                    continue
                transport_streak = transport_streak+1 if error not in {'invalid_scores','output_limit','context_budget'} else 0
                if transport_streak >= 3:
                    fatal=error
                    continue
                if attempt < 2 and not fatal:
                    # Only missing items are sent again. Shrink overloaded/malformed batches.
                    half=max(1,len(missing)//2) if len(missing)>2 else len(missing)
                    for i in range(0,len(missing),half):
                        queue.append((missing[i:i+half],attempt+1))
            if fatal:
                queue.clear()  # Already in-flight successful work is still checkpointed.
    counts.update(scoring_validation=dict(warnings),scoring_failures=dict(errors))
    if completed < total:
        counts['scoring_error'] = fatal or 'score_items_unresolved'
    return counts
