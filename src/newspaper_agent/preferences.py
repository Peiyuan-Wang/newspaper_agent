"""Pure profile contract shared by persistence, retrieval, and writing."""
from datetime import date, datetime, timezone

DOMAINS = ('academic', 'medical', 'finance', 'news', 'general')
LEVELS = ('beginner', 'intermediate', 'expert')


def validate_interest_units(values):
    if not isinstance(values, list) or len(values) > 12:
        raise ValueError('细分兴趣最多12项')
    result, topics = [], set()
    allowed = {'topic', 'domain', 'question', 'purpose', 'methods', 'knowledge_level', 'priority', 'expires_on'}
    for item in values:
        if not isinstance(item, dict) or not allowed <= set(item) or set(item) - allowed - {'category'}:
            raise ValueError('细分兴趣字段无效')
        clean = dict(item)
        if 'category' in clean:
            if not isinstance(clean['category'], str) or len(clean['category']) > 60 or '\x00' in clean['category']:
                raise ValueError('兴趣大类最多60字符')
            clean['category'] = clean['category'].strip()
        for key, maximum in (('topic', 120), ('question', 180), ('purpose', 180)):
            if not isinstance(clean[key], str) or len(clean[key]) > maximum or '\x00' in clean[key]:
                raise ValueError('细分兴趣文字无效')
            clean[key] = clean[key].strip()
        if not clean['topic'] or clean['topic'].casefold() in topics:
            raise ValueError('细分兴趣主题为空或重复')
        topics.add(clean['topic'].casefold())
        if clean['domain'] not in DOMAINS or clean['knowledge_level'] not in LEVELS:
            raise ValueError('细分兴趣领域或程度无效')
        if type(clean['priority']) is not int or not 1 <= clean['priority'] <= 5:
            raise ValueError('细分兴趣优先级需为1到5')
        methods = clean['methods']
        if (not isinstance(methods, list) or len(methods) > 6 or any(
                not isinstance(m, str) or not 1 <= len(m.strip()) <= 60 or '\x00' in m for m in methods)):
            raise ValueError('方法偏好最多6条，每条60字符')
        clean['methods'] = list(dict.fromkeys(m.strip() for m in methods))
        if clean['expires_on'] is not None:
            try:
                if (not isinstance(clean['expires_on'], str) or len(clean['expires_on']) != 10
                        or date.fromisoformat(clean['expires_on']).isoformat() != clean['expires_on']):
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError('阶段目标截止日期需为YYYY-MM-DD或空') from None
        result.append(clean)
    return result


def active_interest_units(profile, *, today=None):
    # Stored values have passed the repository gate. Invalid legacy/caller data is not sent onward.
    try:
        units = validate_interest_units(profile.get('interest_units', []))
    except (ValueError, AttributeError):
        return []
    from newspaper_agent.sources.time_windows import NEWS_TIMEZONE
    current = today or datetime.now(timezone.utc).astimezone(NEWS_TIMEZONE).date()
    return [u for u in units if u['expires_on'] is None or date.fromisoformat(u['expires_on']) >= current]


def confirmed_topics(profile):
    values = [v for v in profile.get('interests', []) if isinstance(v, str)]
    values.extend(u['topic'] for u in active_interest_units(profile))
    return list(dict.fromkeys(values))[:42]
