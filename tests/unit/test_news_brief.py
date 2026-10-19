"""Contract and role-switch regressions. Replay is not an LLM quality benchmark."""
import json

import pytest

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.synthesis import SynthesisService
from newspaper_agent.synthesis.news_brief import validate_brief
from newspaper_agent.sources.contracts import EvidenceRecord
from newspaper_agent.database.research_repository import ResearchRepository


@pytest.fixture
def paper():
    # Short factual paraphrase for offline wiring tests, not a verbatim paper quotation.
    return EvidenceRecord('paper:1', 'arxiv', 'api', 'arXiv', 'Spin Space Groups',
        'https://arxiv.org/abs/2307.10364', 'paper',
        'O(N) representations classify spin space groups. Electronic states form projective representations. '
        'SSGs are identified for 1604 magnetic structures. SOC applicability needs care.',
        '2024-05-18', is_fixture=False, content_level='abstract')


def draft(paper, focus, text, **changes):
    result = {'focus': focus, 'lead_subject': text[:2], 'claims': [{'statement': text, 'conditions': [],
        'uncertainty': '', 'status': 'unverified',
        'evidence_refs': [{'evidence_id': paper.id, 'quote': paper.snippet}]}], 'follow_up': []}
    result.update(changes)
    return json.dumps(result, ensure_ascii=False)


class Replay:
    def __init__(self, replies):
        self.replies, self.inputs = iter(replies), []

    def complete(self, messages, *, tools=()):
        assert not tools
        self.inputs.append(messages)
        return Message('assistant', next(self.replies))


def test_same_paper_two_readers_pass_distinct_focus_and_rules(paper):
    theory = {'interests': ['投影表示'], 'domain_reading_rules': ['凝聚态：expert，关注群论构造'],
              'knowledge_level': 'beginner', 'secret': 'never send'}
    materials = {'interests': ['材料能带'], 'domain_reading_rules': ['材料：关注具体体系和SOC限制']}
    # Explicit replay outputs test routing/validation, NOT that a real model learned style.
    model = Replay([draft(paper, '投影表示', '论文用O(N)表示分类SSG，并讨论分类构造与电子态投影表示的联系。'),
                    draft(paper, '材料能带', '论文为1604个磁结构识别SSG。建议先核对目标材料的电子结构预测及SOC适用条件。')])
    reports = [SynthesisService(model).generate('读这篇论文', [paper], profile=p, news_brief=True)
               for p in (theory, materials)]
    assert all(r.metrics['validation_passed'] for r in reports)
    assert reports[0].knowledge_payload['claims'] != reports[1].knowledge_payload['claims']
    assert [r.knowledge_payload['reader_focus'] for r in reports] == ['投影表示', '材料能带']
    for messages, profile in zip(model.inputs, (theory, materials)):
        reader = json.loads(messages[1].content)['writing_context']['reader_model']
        assert reader['knowledge']['domain_rules'] == profile['domain_reading_rules']
        assert 'secret' not in reader


@pytest.mark.parametrize('failure', ['long', 'focus', 'anchor', 'too_many', 'follow_up'])
def test_failed_brief_is_revised_not_truncated_or_silently_accepted(paper, failure):
    bad = json.loads(draft(paper, '群论', '短结论'))
    if failure == 'long':
        bad['claims'][0]['statement'] = '长' * 181
    elif failure == 'focus':
        bad['focus'] = '未确认兴趣'
    elif failure == 'anchor':
        bad['claims'][0]['evidence_refs'][0]['quote'] = 'invented'
    elif failure == 'too_many':
        bad['claims'] *= 3
    else:
        bad['follow_up'] = ['无须追问']
    model = Replay([json.dumps(bad), draft(paper, '群论', '论文用O(N)表示分类SSG，建立分类构造与群论的联系。')])
    report = SynthesisService(model).generate('阅读', [paper], profile={'interests': ['群论']}, news_brief=True)
    assert report.metrics['repair_calls'] == 1 and report.metrics['validation_passed']
    assert len(report.knowledge_payload['validation_attempts']) == 2
    assert 'previous_draft' in model.inputs[1][-1].content


def test_length_counts_qualifiers_and_keeps_short_text_and_empty_focus(paper):
    obj = json.loads(draft(paper, '', '结论'))
    obj['claims'][0]['conditions'] = ['条件' * 90]
    with pytest.raises(ValueError, match='brief_too_long'):
        validate_brief(json.dumps(obj), (paper,), {})
    assert validate_brief(draft(paper, '', '证据有限。'), (paper,), {})['claims']
    with pytest.raises(ValueError, match='invalid_json'):
        validate_brief('{"focus":"","focus":"","claims":[],"follow_up":[]}', (paper,), {})


def test_domain_rules_confirmed_account_scoped_and_undoable(tmp_path):
    repo = ResearchRepository(tmp_path / 'profile.sqlite3')
    repo.register('alice', 'password123')
    repo.register('bob', 'password123')
    rules = ['凝聚态：expert，保留群论术语', 'AI：beginner，解释必要术语']
    proposal = repo.propose_profile('alice', {'domain_reading_rules': rules}, '按领域区分阅读深度', 'explicit test')
    assert repo.profile('alice')['domain_reading_rules'] == []
    event = repo.resolve_proposal('alice', proposal, accept=True)
    assert repo.profile('alice')['domain_reading_rules'] == rules
    assert repo.profile('bob')['domain_reading_rules'] == []
    repo.undo('alice', event)
    assert repo.profile('alice')['domain_reading_rules'] == []
