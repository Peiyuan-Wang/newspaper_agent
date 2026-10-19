"""Explicit Hong Kong calendar-day and rolling-hour windows; never invent dates."""
from datetime import datetime, timedelta, timezone
import re

NEWS_TIMEZONE = timezone(timedelta(hours=8), 'Asia/Hong_Kong')


def scope_from_query(text):
    if re.search(r'过去\s*24\s*小时|近\s*24\s*小时|\blast\s+24\s+hours\b', text, re.I):
        return '24h'
    return 'today' if re.search(r'今天|当天|今日|\btoday\b', text, re.I) else None


def in_explicit_window(value, scope, *, now=None):
    if scope not in ('today', '24h'):
        raise ValueError('未知新闻时间范围')
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=NEWS_TIMEZONE)
    current = current.astimezone(NEWS_TIMEZONE)
    if not isinstance(value, str):
        return False
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return scope == 'today' and datetime.fromisoformat(value).date() == current.date()
        published = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if published.tzinfo is None:
            return False  # No timezone on a timestamp: cannot establish an exact boundary.
        published = published.astimezone(NEWS_TIMEZONE)
        if published > current:
            return False
        return (published.date() == current.date() if scope == 'today'
                else current - timedelta(hours=24) <= published)
    except (ValueError, TypeError):
        return False
