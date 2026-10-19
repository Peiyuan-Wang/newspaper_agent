"""Layer contracts and controlled role-switch replay, not a real-model quality claim."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.sources.contracts import EvidenceRecord
from newspaper_agent.synthesis import SynthesisService
from newspaper_agent.synthesis.brief_quality import ModelBriefReviewer, RUBRIC, validate_review
from newspaper_agent.synthesis.reader_context import article_context
from newspaper_agent.synthesis.writing_framework import compose_news_prompt, framework_revision, FRAMEWORK_FILES, PROMPTS
from newspaper_agent.research.article_reading import prepare_reading


@pytest.fixture
def article():
    return EvidenceRecord('test:layers', 'synthetic', 'offline', 'Synthetic', '虚构对称群分类',
        'https://example.org/test', 'paper',
        '虚构资料：分类方法甲使用群表示构造分类，解释材料乙的能带简并。结论仅在忽略SOC时成立。',
        '2026-10-17', content_level='abstract', is_fixture=True)


def output(article, focus='', statement='分类方法甲使用群表示构造分类，结论仅在忽略SOC时成立。'):
    return json.dumps({'focus': focus, 'lead_subject': '分类方法甲', 'claims': [{
        'statement': statement, 'conditions': [], 'uncertainty': '', 'status': 'unverified',
        'evidence_refs': [{'evidence_id': article.id, 'quote': article.snippet}]}], 'follow_up': []})


class Replay:
    def __init__(self, replies):
        self.replies, self.inputs = iter(replies), []

    def complete(self, messages, *, tools=()):
        assert not tools
        self.inputs.append(messages)
        return Message('assistant', next(self.replies))


def test_structured_reader_context_is_bounded_private_and_not_mutated(article):
    profile = {'interests': ['群论'] * 40, 'knowledge_level': 'beginner',
               'domain_reading_rules': ['凝聚态：expert', 'AI：beginner'],
               'expression_rules': ['先讲核心'] * 40, 'password': 'never-send',
               'feedback_history': ['not approved'], 'proposal': 'unconfirmed'}
    before = json.dumps(profile)
    context = article_context('本次临时问题', (article,), profile)
    reader = context['reader_model']
    assert reader['confirmed_interests'] == ['群论']  # Repeated legacy interests are deduplicated.
    assert len(reader['expression']['expression_rules']) == 12
    assert reader['knowledge']['domain_rules'] == profile['domain_reading_rules']
    assert context['article_lens']['scope'] == 'current_article_only'
    assert 'never-send' not in json.dumps(context) and 'unconfirmed' not in json.dumps(context)
    assert json.dumps(profile) == before


def test_modular_prompt_only_loads_matching_examples(article):
    paper = compose_news_prompt((article,))
    news = compose_news_prompt((replace(article, kind='news'),))
    assert '<editorial_policy>' in paper and '<task_contract>' in paper
    assert '群论与投影表示研究者' in paper and 'Agent架构可靠性' not in paper
    assert 'Agent架构可靠性' in news and '群论与投影表示研究者' not in news


def test_prompt_contents_participate_in_revision(tmp_path):
    for name in FRAMEWORK_FILES:
        (tmp_path / name).write_text((PROMPTS / name).read_text(encoding='utf-8'), encoding='utf-8')
    before = framework_revision(tmp_path)
    file = tmp_path / 'editorial_policy.md'
    file.write_text(file.read_text(encoding='utf-8') + '\n测试编辑规范', encoding='utf-8')
    assert framework_revision(tmp_path) != before


def test_same_paper_two_confirmed_readers_get_separate_context_and_no_default_judge(article):
    texts = ['分类方法甲通过群表示构造分类，结论仅在忽略SOC时成立。',
             '分类方法甲解释了材料乙的能带简并，结论仅在忽略SOC时成立。']
    model = Replay([output(article, '群论', texts[0]), output(article, '材料', texts[1])])
    reports = [SynthesisService(model).generate('读论文', [article],
               profile={'interests': [focus]}, news_brief=True) for focus in ('群论', '材料')]
    assert len(model.inputs) == 2
    assert all('reader' not in json.loads(m[1].content) for m in model.inputs)
    assert all(r.metrics['validation_passed'] and r.metrics['semantic_review_calls'] == 0 for r in reports)
    assert all(r.knowledge_payload['writing_framework']['semantic_review'] == 'not_reviewed' for r in reports)
    assert [json.loads(m[1].content)['writing_context']['reader_model']['confirmed_interests']
            for m in model.inputs] == [['群论'], ['材料']]
    assert reports[0].knowledge_payload['claims'][0]['statement'] != reports[1].knowledge_payload['claims'][0]['statement']
    assert all('仅在忽略SOC时成立' in r.knowledge_payload['claims'][0]['statement'] for r in reports)


def test_semantic_review_feedback_revises_valid_but_poor_draft_without_discarding(article):
    model = Replay([output(article), output(article, statement='分类方法甲解释材料乙的能带简并，结论仅在忽略SOC时成立。')])
    judge_model = Replay([json.dumps({'ratings': {k: 3 if k == 'reader_relevance' else 4 for k in RUBRIC},
                                     'issues': ['reader_mismatch']}),
                          json.dumps({'ratings': dict.fromkeys(RUBRIC, 4), 'issues': []})])
    report = SynthesisService(model, brief_reviewer=ModelBriefReviewer(judge_model)).generate(
        '材料中的变化', [article], news_brief=True)
    assert report.metrics['model_calls'] == 4 and report.metrics['semantic_review_calls'] == 2
    assert report.metrics['repair_calls'] == 1 and report.metrics['validation_passed']
    assert 'reader_mismatch' in model.inputs[1][-1].content
    assert report.knowledge_payload['semantic_verification'] == 'unverified'


@pytest.mark.parametrize('bad', [True, 0, 6, '5'])
def test_review_scores_are_typed_and_bounded(bad):
    with pytest.raises(ValueError, match='brief_review_invalid'):
        validate_review(json.dumps({'ratings': dict.fromkeys(RUBRIC, bad), 'issues': []}))


def test_duplicate_or_list_prose_triggers_repair(article):
    data = json.loads(output(article))
    data['claims'] *= 2
    model = Replay([json.dumps(data), output(article)])
    report = SynthesisService(model).generate('核心', [article], news_brief=True)
    assert report.metrics['repair_calls'] == 1 and report.metrics['validation_passed']


@pytest.mark.parametrize('text', ['分类方法甲使用群表示。\n- 次要细节', '分类方法甲使用群表示。\n## 结果'])
def test_multiline_layout_is_revised_before_publication(article, text):
    model = Replay([output(article, statement=text), output(article)])
    report = SynthesisService(model).generate('核心', [article], news_brief=True)
    assert report.metrics['repair_calls'] == 1 and report.metrics['validation_passed']
    assert '\n' not in report.knowledge_payload['claims'][0]['statement']


def test_unresolved_semantic_review_keeps_draft_and_never_claims_success(article):
    model = Replay([output(article), output(article)])
    judge = Replay([json.dumps({'ratings': dict.fromkeys(RUBRIC, 3), 'issues': ['core_unclear']})] * 2)
    report = SynthesisService(model, max_repairs=1, brief_reviewer=ModelBriefReviewer(judge)).generate(
        '核心', [article], news_brief=True)
    assert report.metrics['model_calls'] == 4 and not report.metrics['validation_passed']
    assert report.knowledge_payload['claims'] == []
    assert len(report.knowledge_payload['validation_attempts']) == 2
    assert all(a['draft'] for a in report.knowledge_payload['validation_attempts'])
    assert 'brief_semantic_revision' in report.issues


def test_long_reading_and_writing_share_reader_context(article):
    text = ('对称性讨论与实验限制。' * 1300)
    seen = []

    class Reader:
        def complete(self, messages, *, tools=()):
            data = json.loads(messages[1].content)
            seen.append(data['writing_context'])
            b = data['blocks'][0]
            return Message('assistant', json.dumps({'passages': [{'block': b['id'], 'quote': b['text'][:30]}]}))

    evidence, status = prepare_reading(article, {'status': 'extracted', 'text': text}, Reader(),
                                      {'interests': ['群论'], 'domain_reading_rules': ['物理：expert']})
    assert evidence is not None and status['status'] == 'body_read'
    assert all(c['reader_model']['confirmed_interests'] == ['群论'] for c in seen)
    assert all(c['reader_model']['knowledge']['domain_rules'] == ['物理：expert'] for c in seen)


@pytest.mark.parametrize('passed', [True, False])
def test_live_evaluation_exit_status_reflects_contract_result_without_network(monkeypatch, capsys, passed):
    import newspaper_agent.synthesis.brief_evaluation as experiment
    import newspaper_agent.model.factory as factory
    monkeypatch.setattr('sys.argv', ['brief_evaluation', '--live', '--provider', 'test'])
    monkeypatch.setattr(factory, 'create_model_client', lambda _: None)
    monkeypatch.setattr(experiment, 'evaluate', lambda model, review=False:
                        {'outputs': [{'contract_passed': passed}]})
    if passed:
        experiment.main()
    else:
        with pytest.raises(SystemExit) as exc:
            experiment.main()
        assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)['outputs'][0]['contract_passed'] is passed
