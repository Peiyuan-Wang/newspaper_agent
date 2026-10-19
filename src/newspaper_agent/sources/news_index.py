"""Title-level discovery hints, distinct from model-extracted subject membership."""
import re

COMPANIES = {
    'OpenAI': ('OpenAI', 'ChatGPT'), 'Anthropic': ('Anthropic', 'Claude'),
    'Google': ('Google', 'DeepMind', 'Gemini'), 'Meta': ('Meta', 'Llama'),
    'DeepSeek': ('DeepSeek', '深度求索'), 'Microsoft': ('Microsoft', '微软', 'Copilot'),
    'NVIDIA': ('NVIDIA', '英伟达'), 'Alibaba': ('Alibaba', '阿里', 'Qwen', '千问'),
    'Hugging Face': ('Hugging Face', 'SmolLM'),
}
TOPICS = {'Agent': r'\bagents?\b|智能体', '凝聚态': r'condensed.matter|凝聚态',
          'SSG': r'\bSSGs?\b|spin.space.groups?|自旋空间群',
          '强化学习': r'reinforcement.learning|强化学习', '投影表示': r'projective.representation|投影表示'}


def title_entities(title):
    found = []
    for name, aliases in COMPANIES.items():
        if any(re.search(r'(?<![A-Za-z])' + re.escape(a) + r'(?![A-Za-z])', title, re.I) for a in aliases):
            found.append({'name': name, 'kind': 'company'})
    for match in re.finditer(r'\b(?:GPT[- ]?\d[\w.-]*|Claude(?:[- ]+(?:Opus|Sonnet|Haiku))?(?:[- ]+\d[\w.-]*)?|Gemini[- ]+\d[\w.-]*|DeepSeek[- ]+[RV]\d[\w.-]*|Qwen\d[\w.-]*)\b', title, re.I):
        found.append({'name': match.group().strip().rstrip('.'), 'kind': 'model'})
    for name, pattern in TOPICS.items():
        if re.search(pattern, title, re.I):
            found.append({'name': name, 'kind': 'topic'})
    return found
