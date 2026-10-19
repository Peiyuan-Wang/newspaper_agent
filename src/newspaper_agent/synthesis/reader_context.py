"""Compile confirmed preferences into bounded, provider-neutral task context."""
from newspaper_agent.preferences import active_interest_units, confirmed_topics

CONTEXT_VERSION = 2


def reader_profile(profile):
    result = {}
    if not isinstance(profile, dict):
        return result
    for key in ('purpose', 'knowledge_level', 'reading_level', 'language', 'style', 'output_format'):
        value = profile.get(key)
        if isinstance(value, str) and len(value) <= 300:
            result[key] = value
    for key in ('expression_rules', 'glossary', 'interests', 'domains', 'domain_reading_rules'):
        values = profile.get(key)
        if isinstance(values, list):
            maximum = 30 if key in ('interests', 'domain_reading_rules') else 12
            result[key] = [v for v in values[:maximum] if isinstance(v, str) and len(v) <= 120]
    result['interest_units'] = active_interest_units(profile)
    return result


def article_context(question, evidence, profile):
    """No inferred permanent interests, keyword ranking, or unconfirmed feedback."""
    reader = reader_profile(profile)
    return {
        'version': CONTEXT_VERSION,
        'reader_model': {
            'purpose': reader.get('purpose', ''),
            'confirmed_interests': confirmed_topics(reader),
            'interest_units': reader.get('interest_units', []),
            'domains': reader.get('domains', []),
            'knowledge': {'default': reader.get('knowledge_level', ''),
                          'domain_rules': reader.get('domain_reading_rules', [])},
            'expression': {k: reader.get(k, [] if k in ('expression_rules', 'glossary') else '')
                           for k in ('language', 'style', 'reading_level', 'expression_rules', 'glossary')},
        },
        'article_lens': {
            'scope': 'current_article_only', 'question': question[:2000],
            'article_types': sorted({r.kind for r in evidence}),
            'evidence_ids': [r.id for r in evidence],
            'content_levels': sorted({r.content_level for r in evidence}),
        },
    }
