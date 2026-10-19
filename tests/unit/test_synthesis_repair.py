import json
import pytest
from newspaper_agent.agent_core.messages import Message
from newspaper_agent.agent_core.runtime import RunControl, RunCancelled
from newspaper_agent.synthesis import SynthesisService
from newspaper_agent.synthesis.evaluation import case_evidence, load_cases


def content(record, quote):
    return json.dumps({'claims': [{'statement': '测试观点', 'conditions': [],
        'evidence_refs': [{'evidence_id': record.id, 'quote': quote}],
        'uncertainty': '仍需人工核对', 'status': 'unverified'}], 'follow_up': []}, ensure_ascii=False)


class SequenceModel:
    def __init__(self, outputs):
        self.outputs, self.messages = iter(outputs), []
        self.control = None
    def complete(self, messages, *, tools=()):
        assert not tools
        self.messages.append(messages)
        item = next(self.outputs)
        if isinstance(item, Exception):
            raise item
        return Message('assistant', item)


def test_invalid_anchor_is_repaired_and_original_draft_retained():
    record = case_evidence(load_cases()[0])[0]
    bad, good = content(record, 'invented-quote'), content(record, record.snippet)
    model = SequenceModel([bad, good])
    report = SynthesisService(model).generate('测试问题', [record])
    assert report.knowledge_payload['claims'] and not report.issues
    attempts = report.knowledge_payload['validation_attempts']
    assert attempts[0]['draft'] == bad and attempts[1]['draft'] == good
    assert attempts[0]['location'] == 'claims[0].evidence_refs[0]'
    assert attempts[1]['passed'] is True
    assert 'invented-quote' not in report.markdown
    feedback = json.loads(model.messages[1][-1].content)
    assert feedback['previous_draft'] == bad and feedback['error'] == 'invalid_evidence_anchor'
    assert report.metrics['model_calls'] == 2 and report.metrics['repair_calls'] == 1
    assert report.metrics['model_output_characters'] == len(bad) + len(good)


def test_multiple_validation_failures_loop_until_valid_without_growing_history():
    record = case_evidence(load_cases()[0])[0]
    model = SequenceModel(['not-json', content(record, 'bad'), content(record, record.snippet)])
    report = SynthesisService(model).generate('测试问题', [record])
    assert report.metrics['validation_passed'] and report.metrics['model_calls'] == 3
    assert len(model.messages[1]) == len(model.messages[2]) == 3
    assert 'not-json' not in model.messages[2][-1].content


def test_failed_repairs_stop_preserve_drafts_and_never_present_them_as_valid():
    record = case_evidence(load_cases()[0])[0]
    bad = content(record, 'invented-quote')
    model = SequenceModel([bad] * 3)
    report = SynthesisService(model).generate('测试问题', [record])
    assert len(model.messages) == 3 and 'validation_repair_exhausted' in report.issues
    assert len(report.knowledge_payload['validation_attempts']) == 3
    assert not report.knowledge_payload['claims'] and not report.metrics['validation_passed']
    assert 'invented-quote' not in report.markdown


def test_transport_failure_during_repair_does_not_start_another_loop():
    record = case_evidence(load_cases()[0])[0]
    model = SequenceModel(['bad-json', RuntimeError('secret-upstream')])
    report = SynthesisService(model).generate('测试问题', [record])
    assert len(model.messages) == 2 and report.metrics['model_calls'] == 2
    assert len(report.knowledge_payload['validation_attempts']) == 1
    assert 'secret-upstream' not in str(report)


def test_cancel_before_repair_does_not_send_again():
    record = case_evidence(load_cases()[0])[0]
    model = SequenceModel(['bad-json'])
    def progress(event):
        if event['type'] == 'synthesis_repair':
            model.control.cancel()
    model.control = RunControl(progress, max_seconds=None)
    # Notification can cancel the control; check before starting the next model call.
    with pytest.raises(RunCancelled):
        SynthesisService(model).generate('测试问题', [record])
    assert len(model.messages) == 1


def test_repairs_can_be_disabled_for_a_controlled_baseline():
    record = case_evidence(load_cases()[0])[0]
    model = SequenceModel(['bad-json'])
    report = SynthesisService(model, max_repairs=0).generate('测试问题', [record])
    assert report.metrics['model_calls'] == 1
    with pytest.raises(ValueError):
        SynthesisService(model, max_repairs=3)
