"""Assemble stable editorial policy, task contract, and task-specific examples."""
from hashlib import sha256
import json
from pathlib import Path

from .reader_context import CONTEXT_VERSION

PROMPTS = Path(__file__).resolve().parents[3] / 'prompts'
FRAMEWORK_FILES = ('editorial_policy.md', 'news_brief.md', 'news_brief_repair.md',
                   'news_reading.md', 'news_examples.json')


def framework_revision(root=None):
    root = Path(root) if root else PROMPTS
    content = [str(CONTEXT_VERSION)] + [(root / name).read_text(encoding='utf-8') for name in FRAMEWORK_FILES]
    return sha256('\n'.join(content).encode()).hexdigest()


def compose_news_prompt(evidence, root=None):
    root = Path(root) if root else PROMPTS
    examples = json.loads((root / 'news_examples.json').read_text(encoding='utf-8'))
    if examples.get('version') != 1 or examples.get('synthetic') is not True:
        raise ValueError('invalid_explanation_prompt')
    genre = 'paper' if any(r.kind == 'paper' for r in evidence) else 'news'
    selected = [e for e in examples['examples'] if e['genre'] == genre][:2]
    sections = {
        'editorial_policy': (root / 'editorial_policy.md').read_text(encoding='utf-8'),
        'task_contract': (root / 'news_brief.md').read_text(encoding='utf-8'),
        'synthetic_examples': json.dumps(selected, ensure_ascii=False),
    }
    prompt = '\n\n'.join(f'<{name}>\n{text}\n</{name}>' for name, text in sections.items())
    if not selected or not all(sections.values()) or len(prompt) > 12000:
        raise ValueError('invalid_explanation_prompt')
    return prompt
