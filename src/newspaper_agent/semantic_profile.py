"""Pure hierarchical multi-interest representation and reversible anchored feedback."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from newspaper_agent.preferences import active_interest_units, validate_interest_units

POLICY = 'anchored-multi-interest-v1'
MERGE_COSINE = .72
MATCH_COSINE = .30
CATEGORY_MARGIN = .04
ATTENTION_TEMPERATURE = .12
PRIOR = 3.
MAX_ANGLE = 25.


def identity(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def unit(vector):
    if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 4096:
        raise ValueError('向量维数无效')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
        raise ValueError('向量包含无效数值')
    length = math.sqrt(sum(v*v for v in vector))
    if length < 1e-9:
        raise ValueError('零向量不能表示语义')
    return [v/length for v in vector]


def dot(a, b):
    if len(a) != len(b):
        raise ValueError('不同坐标系维数不可比较')
    return max(-1., min(1., sum(x*y for x,y in zip(a,b))))


def mean(vectors, weights=None):
    if not vectors or len({len(v) for v in vectors}) != 1:
        raise ValueError('向量集合无效')
    weights = weights or [1.] * len(vectors)
    return unit([sum(w*v[i] for v,w in zip(vectors, weights)) for i in range(len(vectors[0]))])


def seed_specs(profile):
    """Category names never enter encoded text. Unassigned legacy topics stay isolated."""
    result = []
    try:
        used = {u['topic'].casefold() for u in validate_interest_units(profile.get('interest_units', []))}
    except ValueError:
        used = set()
    for row in active_interest_units(profile):
        category = row.get('category') or '未分类 · ' + row['topic']
        text = '。'.join([row['topic'], *row['methods'], row['question']]).strip('。')
        spec = {'category': category, 'topic': row['topic'], 'text': text,
                'domains': [row['domain']], 'priority': row['priority']}
        spec['id'] = identity(spec)
        result.append(spec)
        used.add(row['topic'].casefold())
    for topic in profile.get('interests', []):
        if topic.casefold() in used:
            continue
        spec = {'category': '未分类 · ' + topic, 'topic': topic, 'text': topic,
                'domains': list(profile.get('domains') or ['general']), 'priority': 3}
        spec['id'] = identity(spec)
        result.append(spec)
    return sorted(result, key=lambda x: x['id'])


def make_slots(seeds, vectors):
    clusters = [[s] for s in seeds]
    # Complete linkage prevents a chain of weak neighbours from merging a whole category.
    while True:
        candidate = None
        for i, a in enumerate(clusters):
            for j in range(i+1, len(clusters)):
                b = clusters[j]
                if a[0]['category'] != b[0]['category']:
                    continue
                similarity = min(dot(vectors[x['id']], vectors[y['id']]) for x in a for y in b)
                if similarity >= MERGE_COSINE and (candidate is None or similarity > candidate[0]):
                    candidate = similarity, i, j
        if candidate is None:
            break
        _, i, j = candidate
        clusters[i] += clusters.pop(j)
    slots = []
    for members in clusters:
        anchor = mean([vectors[s['id']] for s in members], [s['priority'] for s in members])
        slots.append({'id': identity([s['id'] for s in sorted(members, key=lambda s:s['id'])]),
            'category': members[0]['category'], 'topics': [s['topic'] for s in members],
            'domains': sorted({d for s in members for d in s['domains']}),
            'anchor': anchor, 'vector': list(anchor), 'positive_mass': 0., 'negative_mass': 0.,
            'observations': 0, 'angle': 0., 'affinity_bias': 0.})
    return slots


def domain_matches(slot, domains):
    return not domains or 'general' in domains or 'general' in slot['domains'] or bool(set(domains) & set(slot['domains']))


def route(slots, vector, domains, *, strict=True):
    candidates = [(slot, dot(slot['anchor'], vector)) for slot in slots if domain_matches(slot, domains)]
    candidates = [(s,c) for s,c in candidates if c >= MATCH_COSINE]
    categories = {}
    for slot, cosine in candidates:
        categories[slot['category']] = max(categories.get(slot['category'], -1), cosine)
    ordered = sorted(categories, key=lambda c: (-categories[c], c))
    if not ordered or (strict and len(ordered) > 1 and categories[ordered[0]]-categories[ordered[1]] < CATEGORY_MARGIN):
        return []
    best = categories[ordered[0]]
    candidates = [(s,c) for s,c in candidates if s['category'] == ordered[0] and c >= best-.12]
    weights = [math.exp((c-best)/ATTENTION_TEMPERATURE) for s,c in candidates]
    total = sum(weights)
    return [(s, w/total) for (s,c),w in zip(candidates,weights)]


def constrain(anchor, proposal):
    cosine = dot(anchor, proposal)
    limit = math.radians(MAX_ANGLE)
    if cosine >= math.cos(limit):
        return proposal
    tangent = [p-cosine*a for p,a in zip(proposal,anchor)]
    if sum(v*v for v in tangent) < 1e-12:
        return list(anchor)
    tangent = unit(tangent)
    return unit([math.cos(limit)*a + math.sin(limit)*t for a,t in zip(anchor,tangent)])


def evolve(slots, feedback, *, now=None):
    """Rebuild from current feedback, not repeated SGD: replacement/undo is exact."""
    now = now or datetime.now(timezone.utc)
    totals = {s['id']: {'positive': [0.]*len(s['anchor']), 'negative': [0.]*len(s['anchor']),
                        'positive_mass': 0., 'negative_mass': 0., 'observations': 0} for s in slots}
    skipped = 0
    for row in feedback:
        routing = route(slots, row['vector'], row['domains'])
        if not routing:
            skipped += 1
            continue
        try:
            when = datetime.fromisoformat(row['at'])
            if when.tzinfo is None:
                continue
            age = max(0, (now-when).total_seconds()/86400)
        except (ValueError, TypeError):
            continue
        weight = (0.1 if row['kind'] == 'open' else 1.) * 2**(-age/90.)
        sign = 'negative' if row['kind'] == 'down' else 'positive'
        for slot, attention in routing:
            acc = totals[slot['id']]
            w = weight * attention
            acc[sign] = [a+w*x for a,x in zip(acc[sign],row['vector'])]
            acc[sign+'_mass'] += w
            acc['observations'] += 1
    result = []
    for slot in slots:
        acc = totals[slot['id']]
        pos = [v/(PRIOR+acc['positive_mass']) for v in acc['positive']]
        neg = [v/(PRIOR+acc['negative_mass']) for v in acc['negative']]
        proposal = (unit([a+.75*p-.35*n for a,p,n in zip(slot['anchor'],pos,neg)])
                    if acc['observations'] else list(slot['anchor']))
        vector = constrain(slot['anchor'], proposal)
        result.append({**slot, 'vector': vector, 'positive_mass': acc['positive_mass'],
                       'negative_mass': acc['negative_mass'], 'observations': acc['observations'],
                       'affinity_bias': .08*acc['positive_mass']/(PRIOR+acc['positive_mass'])
                           -.15*acc['negative_mass']/(PRIOR+acc['negative_mass']),
                       'angle': round(math.degrees(math.acos(dot(slot['anchor'],vector))), 4)})
    return result, skipped


def match(slots, document):
    routing = route(slots, document['vector'], document['domains'], strict=False)
    if not routing:
        return None
    # Max over local interests avoids diluting a strongly matching specialised article.
    best, _ = max(routing, key=lambda pair: dot(pair[0]['vector'], document['vector']) + pair[0].get('affinity_bias',0.))
    cosine = dot(best['vector'], document['vector'])
    effective = max(-1., min(1., cosine+best.get('affinity_bias',0.)))
    tags = document.get('tags', [])
    nearest = max(tags, key=lambda t: dot(best['vector'], t['vector']))['label'] if tags else None
    return {'slot_id': best['id'], 'category': best['category'], 'topics': best['topics'],
            'cosine': round(cosine, 5), 'anchor_cosine': round(dot(best['anchor'],document['vector']), 5),
            'affinity_bias': round(best.get('affinity_bias',0.),5),
            'score': round(max(0, min(100, (effective-.2)/.8*100)), 2),
            'nearest_tag': nearest, 'observations': best['observations']}
