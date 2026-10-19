"""Bounded, evidence-anchored importance rubric, alongside existing relevance scores."""
import json

DIMENSIONS = ('reach', 'impact', 'cross_domain', 'urgency', 'duration')
WEIGHTS = (.30, .25, .20, .15, .10)


def parse_importance(content, records):
    def unique(pairs):
        result = {}
        for k,v in pairs:
            if k in result:
                raise ValueError('invalid_scores')
            result[k] = v
        return result
    try:
        data = json.loads(content, object_pairs_hook=unique)
        if not isinstance(data, dict) or set(data) != {'scores'} or not isinstance(data['scores'], list):
            raise ValueError()
        rows = data['scores']
        stripped, importance = [], {}
        for row in rows:
            if set(row) == {'id', 'score'}:
                stripped.append(row)
                importance[row['id']] = {'score': None, 'mode': 'unavailable_legacy_response'}
                continue
            if set(row) != {'id', 'score', 'importance', 'quote'}:
                raise ValueError()
            rubric = row['importance']
            quote = row['quote']
            if rubric is None:
                if quote != '':
                    raise ValueError()
                importance[row['id']] = {'score': None, 'mode': 'insufficient_evidence'}
            else:
                if (not isinstance(rubric, dict) or set(rubric) != set(DIMENSIONS) or any(
                        type(v) is not int or not 0 <= v <= 100 for v in rubric.values())):
                    raise ValueError()
                if (not isinstance(quote, str) or not 1 <= len(quote) <= 160 or
                        type(row['id']) is not int or not 0 <= row['id'] < len(records) or
                        quote not in records[row['id']].snippet[:600]):
                    raise ValueError()
                importance[row['id']] = {'score': round(sum(rubric[k]*w for k,w in zip(DIMENSIONS,WEIGHTS))),
                    'dimensions': rubric, 'quote': quote, 'mode': 'model_abstract_estimate_not_fact_check'}
            stripped.append({'id': row['id'], 'score': row['score']})
        return json.dumps({'scores': stripped}), importance
    except (TypeError, ValueError, KeyError, AttributeError, IndexError, RecursionError):
        raise ValueError('invalid_scores') from None
