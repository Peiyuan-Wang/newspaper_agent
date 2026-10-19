import json
import time
from pathlib import Path
from types import SimpleNamespace
import gzip
import httpx
import pytest
from newspaper_agent.agent_core.messages import Message, ToolDefinition, ToolCall
from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings, ModelRequestFailure
from newspaper_agent.research.model_runtime import BudgetModel
from newspaper_agent.research.session import ResearchSession
from newspaper_agent.research.service import ResearchSources
from newspaper_agent.research.connection_probe import probe_connection
from newspaper_agent.database.research_repository import ResearchRepository


def client(handler, **changes):
    return OpenAICompatibleClient(OpenAICompatibleSettings(**{
        'provider': 'offline', 'base_url': 'https://example.org/v1', 'model': 'offline',
        'api_key_env': 'OFFLINE_KEY', 'wire_api': 'responses', 'max_attempts': 1, **changes}),
        api_key='dummy-secret-only', transport=httpx.MockTransport(handler))


def reply(text='OK'):
    return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
        'status': 'completed', 'content': [{'type': 'output_text', 'text': text}]}]}


@pytest.mark.parametrize('error,code', [(httpx.ReadTimeout, 'read_timeout'),
    (httpx.ConnectTimeout, 'connect_timeout'), (httpx.ProxyError, 'proxy_error'),
    (httpx.RemoteProtocolError, 'remote_protocol'), (httpx.ConnectError, 'connect_error')])
def test_distinct_safe_errors(error, code):
    def handler(request):
        raise error('dummy-secret-only private-body upstream detail', request=request)
    model = client(handler)
    with pytest.raises(ModelRequestFailure) as caught:
        model.complete([Message('user', 'private-user-body')])
    assert caught.value.code == code
    assert all(s not in str(caught.value) + json.dumps(caught.value.diagnostics)
        for s in ('dummy-secret-only', 'private-user-body', 'upstream detail'))


def test_output_limit_is_not_a_network_error():
    model = client(lambda _: httpx.Response(200, json={'status': 'incomplete',
        'incomplete_details': {'reason': 'max_output_tokens'}, 'output': []}))
    with pytest.raises(ModelRequestFailure) as caught:
        model.complete([], tools=[ToolDefinition('test', 'test')])
    assert caught.value.code == 'output_limit'
    assert '并非网络错误' in str(caught.value)


def test_real_wire_size_does_not_double_count_response_replay():
    model = client(lambda _: httpx.Response(200, json=reply()))
    output = [{'type': 'function_call', 'call_id': 'one', 'name': 'test', 'arguments': 'x' * 10000}]
    message = Message('assistant', 'y' * 10000, tool_calls=(ToolCall('one', 'test', 'x' * 10000),),
                      model_data={'responses_output': output})
    assert model.request_characters([message]) < 15000
    budget = BudgetModel(model, max_context_chars=15000)
    assert budget.complete([message]).content == 'OK'


def test_compressed_json_and_unexpected_stream():
    model = client(lambda _: httpx.Response(200, content=gzip.compress(json.dumps(reply()).encode()),
        headers={'content-encoding': 'gzip', 'content-type': 'application/json'}))
    assert model.complete([]).content == 'OK'
    model = client(lambda _: httpx.Response(200, content='data: private-upstream',
        headers={'content-type': 'text/event-stream'}))
    with pytest.raises(ModelRequestFailure) as caught:
        model.complete([])
    assert caught.value.code == 'unexpected_stream'
    assert 'private-upstream' not in str(caught.value)


def test_synthetic_tool_probe_has_no_private_context():
    requests = []
    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        if len(requests) == 1:
            return httpx.Response(200, json={'status': 'completed', 'output': [
                {'type': 'function_call', 'call_id': 'call1', 'name': 'connection_echo', 'arguments': '{"value":"OK"}'}]})
        assert data['input'][-1]['type'] == 'function_call_output'
        return httpx.Response(200, json=reply())
    probe_connection(client(handler), tools=True)
    assert len(requests) == 2
    assert set(t['name'] for t in requests[0]['tools']) == {'connection_echo'}
    assert 'profile' not in json.dumps(requests) and 'history' not in json.dumps(requests)


def test_plain_chat_and_single_call_profile_proposal(tmp_path):
    repo = ResearchRepository(tmp_path / 'session.sqlite3')
    repo.register('reader', 'password123')
    sources = ResearchSources(repo, 'reader', Path(__file__).resolve().parents[2] / 'config/sources/live.yaml')
    calls = []
    class Model:
        def complete(self, messages, *, tools=()):
            calls.append(tools)
            return Message('assistant', '普通回复' if len(calls) == 1 else json.dumps({
                'changes': {'interests': ['AI']}, 'user_quote': '关注AI', 'reason': '用户明确描述'}))
    session = ResearchSession(repo, 'reader', sources, model=Model())
    assert session.chat('你好', allow_tools=False)['markdown'] == '普通回复'
    session.propose_profile('我关注AI')
    assert calls == [(), ()]
    assert repo.profile('reader')['interests'] == []
    assert len(repo.proposals('reader')) == 1


def test_automatic_profile_ack_does_not_need_a_second_model_round(tmp_path):
    repo = ResearchRepository(tmp_path / 'auto.sqlite3')
    repo.register('reader', 'password123')
    sources = ResearchSources(repo, 'reader', Path(__file__).resolve().parents[2] / 'config/sources/live.yaml')
    calls = []
    class Model:
        def complete(self, messages, *, tools=()):
            calls.append(1)
            assert len(calls) == 1, 'No replay call just to acknowledge a saved proposal'
            return Message('assistant', '', tool_calls=(ToolCall('one', 'propose_profile', json.dumps({
                'changes_json': json.dumps({'interests': ['AI']}), 'user_quote': '关注AI', 'reason': '明确关注'})),))
    session = ResearchSession(repo, 'reader', sources, model=Model())
    result = session.chat('我关注AI，请整理兴趣')
    assert '尚未生效' in result['markdown'] and len(calls) == 1
    assert len(repo.proposals('reader')) == 1 and repo.profile('reader')['interests'] == []


def test_response_deadline_closes_stream(monkeypatch):
    from newspaper_agent.model import diagnostics
    clock = [0.0]
    monkeypatch.setattr(diagnostics.time, 'monotonic', lambda: clock[0])
    class Slow(httpx.SyncByteStream):
        closed = False
        def __iter__(self):
            clock[0] += .01
            yield b'{}'
        def close(self):
            self.closed = True
    stream = Slow()
    model = client(lambda _: httpx.Response(200, stream=stream), timeout=.001)
    with pytest.raises(ModelRequestFailure) as caught:
        model.complete([])
    assert caught.value.code == 'response_deadline' and stream.closed


def test_resynthesize_keeps_original_and_does_not_search(tmp_path):
    from dataclasses import asdict
    from datetime import datetime, timezone
    from newspaper_agent.sources.contracts import EvidenceRecord
    from newspaper_agent.synthesis import SynthesisService
    repo = ResearchRepository(tmp_path / 'resynthesize.sqlite3')
    repo.register('reader', 'password123')
    stamp = datetime.now(timezone.utc).isoformat()
    record = EvidenceRecord('e1', 'offline', 'rss', 'Offline', 'AI agents use tools',
        'https://example.org/a', 'paper', 'AI agents use tools only in a test environment.',
        stamp, published_at=stamp, content_level='abstract', is_fixture=False)
    old = asdict(SynthesisService(None).generate('AI agents', [record]))
    identity = repo.save_report('reader', 'AI agents', old)
    class Model:
        def complete(self, messages, *, tools=()):
            return Message('assistant', json.dumps({'claims': [{'statement': 'AI agents使用工具。',
                'conditions': ['仅测试环境'], 'uncertainty': '不能推断生产效果', 'status': 'unverified',
                'evidence_refs': [{'evidence_id': 'e1', 'quote': 'AI agents use tools'}]}], 'follow_up': []}))
    result = ResearchSession(repo, 'reader', None, model=Model()).resynthesize(identity)
    assert result['report_id'] != identity and result['knowledge_payload']['claims']
    assert repo.report('reader', identity)['report']['markdown'] == old['markdown']


def pump(app, window):
    deadline = time.monotonic() + 5
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert not window.busy


def test_retry_reuses_user_card_and_nonchat_does_not_leave_progress(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from newspaper_agent.desktop.app import Window
    from newspaper_agent.desktop.theme import STYLE
    from newspaper_agent.model.client import ModelCommunicationError
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setStyleSheet(STYLE)
    repo = ResearchRepository(tmp_path / 'ui.sqlite3')
    repo.register('reader', 'password123')
    window = Window(repo, 'reader')
    previous = window.chat_status.text()
    window.run_job(lambda: 'done')
    pump(app, window)
    assert window.chat_status.text() == previous
    window.chat_view.clear()
    window.session.model = SimpleNamespace(control=None)
    attempted_modes = []
    def failed(text, **options):
        attempted_modes.append(options)
        raise ModelCommunicationError('offline failure')
    window.session.chat = failed
    window.chat_input.setPlainText('test')
    window.send_chat()
    pump(app, window)
    window.chat_input.setPlainText('keep draft')
    window.dialogue_mode.setCurrentIndex(window.dialogue_mode.findData('plain'))
    window.retry_chat()
    pump(app, window)
    assert [m['role'] for m in window.chat_view.messages] == ['user', 'error']
    assert window.chat_input.toPlainText() == 'keep draft'
    assert attempted_modes[-1] == {'allow_tools': False}
    window.feedback_text.setPlainText('only-selected-feedback')
    window.discuss_feedback()
    assert 'only-selected-feedback' in window.chat_input.toPlainText()
    assert not window.busy
    window.show()
    for _ in range(5):
        app.processEvents()
        time.sleep(.01)
    window.grab().save(str(tmp_path / 'conversation-reliability-preview.png'))
    window.close()
