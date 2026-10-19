"""Configured editorial grouping; deliberately no invented popularity score."""
from functools import lru_cache
from pathlib import Path
import re
import yaml

from newspaper_agent.sources.query import ALIASES, matches


@lru_cache(maxsize=1)
def policy():
    path = Path(__file__).resolve().parents[3] / 'config/editorial.yaml'
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('categories'), list):
        raise ValueError('简报分类配置无效')
    return data


def category(record):
    config = policy()
    if record.get('kind') == 'paper':
        return 'research'
    text = record.get('title', '').casefold()
    return next((c['id'] for c in config['categories'] if any(
        matches(v, text) for t in c['terms'] for v in ALIASES.get(t, (t,)))), config['default_category'])


def groups(evidence):
    """Merge only identical normalized headlines, preserving every source link."""
    result = {}
    for record in evidence:
        key = re.sub(r'[\W_]+', '', record['title'].casefold())
        key = key or record['id']
        if key not in result:
            result[key] = {'primary': record, 'sources': []}
        result[key]['sources'].append(record)
    return list(result.values())
