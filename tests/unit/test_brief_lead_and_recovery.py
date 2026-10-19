import json
from dataclasses import replace
import pytest

from newspaper_agent.agent_core.messages import Message
from newspaper_agent.sources.contracts import EvidenceRecord
from newspaper_agent.synthesis import SynthesisService
from newspaper_agent.synthesis.news_brief import validate_brief


@pytest.fixture
def record():
    return EvidenceRecord('paper:lead', 'test', 'rss', 'Test', 'CodeModel synthetic SFT',
        'https://example.org/paper', 'paper',
        'CodeModel improves code generation using synthetic SFT data.\nThe mean pass@1 improves by 7.2 percentage points.',
        '2026-10-17', is_fixture=False)


def draft(record, **updates):
    data = {'focus': '', 'lead_subject': 'CodeModel', 'claims': [{
        'statement': 'CodeModel通过合成数据改善代码生成，单次生成通过率（pass@1）提高7.2个百分点。',
        'conditions': [], 'uncertainty': '', 'status': 'unverified',
        'evidence_refs': [{'evidence_id': record.id, 'quote': record.snippet}]}], 'follow_up': []}
    data.update(updates)
    return data


def test_specific_core_subject_and_real_metric_are_preserved(record):
    checked = validate_brief(json.dumps(draft(record)), (record,), {})
    text = checked['claims'][0]['statement']
    assert text.startswith(checked['lead_subject']) and 'pass@1' in text and '7.2个百分点' in text
    assert '7.2%' not in text


def test_lead_mismatch_repaired_instead_of_generic_company_focus(record):
    wrong = draft(record, lead_subject='OtherModel')
    replies = iter([json.dumps(wrong), json.dumps(draft(record))])
    class Model:
        def complete(self, messages, *, tools=()):
            return Message('assistant', next(replies))
    report = SynthesisService(Model()).generate('核心变化', [record], news_brief=True)
    assert report.metrics['repair_calls'] == 1 and report.metrics['validation_passed']
    assert report.knowledge_payload['validation_attempts'][0]['error'] == 'brief_lead_invalid'


@pytest.mark.parametrize('fenced', [False, True])
def test_only_whitespace_quote_differences_are_recovered_to_original(record, fenced):
    data = draft(record)
    data['claims'][0]['evidence_refs'][0]['quote'] = record.snippet.replace('\n', ' ')
    encoded = json.dumps(data)
    if fenced:
        encoded = '```json\n' + encoded + '\n```'
    checked = validate_brief(encoded, (record,), {})
    assert checked['claims'][0]['evidence_refs'][0]['quote'] == record.snippet


def test_wrong_archiving_quote_does_not_discard_valid_interpretation(record):
    data = draft(record, news={'entities': [], 'event': {'subject': 'CodeModel', 'action': 'improves',
        'object': 'code', 'occurred_at': None, 'quote': 'invented', 'continues': None}})
    checked = validate_brief(json.dumps(data), (record,), {})
    assert checked['claims'] and checked['metadata_issues'] == ['metadata_pending'] and not checked['news_metadata']


def test_main_claim_fake_quote_still_rejected(record):
    data = draft(record)
    data['claims'][0]['evidence_refs'][0]['quote'] = 'pass@1 improves by 72 percentage points.'
    with pytest.raises(ValueError, match='invalid_evidence_anchor'):
        validate_brief(json.dumps(data), (record,), {})


def test_prose_around_json_not_silently_accepted(record):
    with pytest.raises(ValueError, match='invalid_json'):
        validate_brief('Here are instructions: ' + json.dumps(draft(record)), (record,), {})


def test_general_view_allowed_even_with_unrelated_confirmed_interests(record):
    checked = validate_brief(json.dumps(draft(record)), (record,), {'interests': ['unrelated topic']})
    assert checked['reader_focus'] == '' and checked['claims']


def test_unconfirmed_nonempty_focus_is_still_rejected(record):
    with pytest.raises(ValueError, match='brief_focus_invalid'):
        validate_brief(json.dumps(draft(record, focus='invented topic')), (record,), {'interests': ['confirmed']})


def test_focus_repair_can_switch_to_general_view_without_dropping_claims(record):
    replies = iter([json.dumps(draft(record, focus='invented')), json.dumps(draft(record))])
    class Model:
        def complete(self, messages, *, tools=()):
            return Message('assistant', next(replies))
    report = SynthesisService(Model()).generate('news', [record], news_brief=True, profile={'interests':['confirmed']})
    assert report.metrics['validation_passed'] and report.metrics['generation_calls'] == 2
    assert report.knowledge_payload['claims'] and report.knowledge_payload['reader_focus'] == ''
