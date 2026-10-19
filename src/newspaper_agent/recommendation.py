"""Pure contextual exploration over explicit concept neighbours; no model/network/database."""
from hashlib import sha256
import json
import math
import random
from types import SimpleNamespace

from newspaper_agent.preferences import active_interest_units
from newspaper_agent.sources.annotations import content_tags
from newspaper_agent.news_options import DEFAULT_NEWS_LIMIT, validate_news_limit

POLICY_VERSION = 'concept-bandit-v1'
DEFAULTS = {'enabled': False, 'epsilon': .2, 'temperature': .15, 'radius': .6, 'learning_rate': .15}
# Deliberately finite, inspectable neighbourhoods, not pretrained semantic embeddings.
FAMILIES = (
    frozenset({'ai', 'agent', 'llm', 'rl', 'sft'}),
    frozenset({'ssg', 'physics', 'group_theory', 'projective_representation', 'cohomology'}),
)
DAILY = {'latest news', 'news', '最新新闻', '最近新闻', '最新消息', '看看最新消息'}


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) != set(DEFAULTS) or type(settings['enabled']) is not bool:
        raise ValueError('兴趣拓展设置无效')
    for key, low, high in (('epsilon', 0, .3), ('temperature', .02, 2), ('radius', 0, 1), ('learning_rate', .01, .3)):
        value = settings[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('兴趣拓展参数超出范围')
    return dict(settings)


def concepts(text):
    return {t['concept'] for t in content_tags(SimpleNamespace(title=text, snippet=''))
            if t['facet'] != 'contribution_mention'}


def anchors(profile):
    units = active_interest_units(profile)
    # Simple interests retain an explicit domain boundary; no guessed new domain.
    units += [dict(topic=t, domain=d, methods=[], question='', purpose='')
              for t in profile.get('interests', []) for d in profile.get('domains', [])
              if t not in {u['topic'] for u in units}]
    result = []
    for unit in units:
        tags = concepts(unit['topic'] + ' ' + ' '.join(unit['methods']))
        if not tags:
            continue
        identity = {k: unit.get(k) for k in ('topic', 'domain', 'question', 'purpose', 'methods', 'knowledge_level')}
        identity.update(profile_purpose=profile.get('purpose', ''), default_level=profile.get('knowledge_level', ''))
        key = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        result.append({'key': key, 'topic': unit['topic'], 'domain': unit['domain'], 'tags': tags})
    return result


def neighbour(record, profile, source_domains, radius):
    text = record.title + ' ' + record.snippet
    if any(t.casefold() in text.casefold() for t in profile.get('excluded', []) if t):
        return None
    tags = concepts(text)
    options = []
    for anchor in anchors(profile):
        if anchor['domain'] not in source_domains.get(record.source_id, ()) and 'general' not in source_domains.get(record.source_id, ()):
            continue
        for tag in tags:
            distance = (0 if tag in anchor['tags'] else .5 if any(
                tag in family and family & anchor['tags'] for family in FAMILIES) else 2)
            if distance <= radius:
                options.append((distance, anchor['key'], tag, anchor['topic']))
    if not options:
        return None
    distance, key, tag, topic = min(options)
    return {'context': key, 'tag': tag, 'anchor': topic, 'distance': distance}


def probabilities(utilities, temperature):
    peak = max(utilities)
    weights = [math.exp((u - peak) / temperature) for u in utilities]
    total = sum(weights)
    return [w / total for w in weights]


def select_news(ranked, scores, profile, domains, settings, learned=None, *, query='latest news', rng=None, max_news=DEFAULT_NEWS_LIMIT):
    settings = validate_settings(settings)
    learned = learned or {}
    rng = rng or random.Random()
    size = min(validate_news_limit(max_news), len(ranked))
    slots = math.floor(size * settings['epsilon']) if settings['enabled'] and query.strip().casefold() in DAILY else 0
    if not slots:
        return list(ranked[:size]), {}
    core = list(ranked[:size - slots])
    remaining = []
    for record in ranked[size - slots:]:
        match = neighbour(record, profile, domains, settings['radius'])
        if match and scores[record.id] >= 40:
            # Context is the confirmed anchor; actions share the candidate's concept feature.
            stats = learned.get((match['context'], match['tag']), {'sum': 0, 'count': 0})
            strength = 1 / settings['learning_rate']
            mean = (.5 * strength + stats['sum']) / (strength + stats['count'])
            utility = .65 * scores[record.id] / 100 + .25 * mean + .1 / math.sqrt(stats['count'] + 1)
            remaining.append((record, match, utility))
    picked, decisions = [], {}
    for _ in range(min(slots, len(remaining))):
        probs = probabilities([r[2] for r in remaining], settings['temperature'])
        draw, cumulative, index = rng.random(), 0, len(remaining) - 1
        for i, probability in enumerate(probs):
            cumulative += probability
            if draw < cumulative:
                index = i
                break
        record, match, _ = remaining[index]
        decisions[record.id] = {**match, 'role': 'explore', 'probability': probs[index],
                                'candidate_count': len(remaining), 'policy': POLICY_VERSION}
        picked.append(record)
        remaining.pop(index)
    result = core + picked
    # Insufficient eligible neighbours: fill deterministically, never relax domain/radius/quality.
    result += [r for r in ranked if r not in result][:size - len(result)]
    if not picked:
        return result, {}  # No neighbour selected: do not label an ordinary top ten as exploration.
    for record in result:
        decisions.setdefault(record.id, {'role': 'core', 'probability': 1., 'candidate_count': 1,
                                         'policy': POLICY_VERSION})
    return result, decisions
