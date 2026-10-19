"""Synthetic model/relay checks. Never include an account, profile or history."""
import json
from newspaper_agent.agent_core.messages import Message, ToolDefinition


def probe_connection(client, *, tools=False):
    if not tools:
        client.complete([Message('user', 'Reply with OK only.')], tools=())
        return
    definition = ToolDefinition('connection_echo', 'Echo the supplied value for a connection test.',
        {'type': 'object', 'properties': {'value': {'type': 'string'}},
         'required': ['value'], 'additionalProperties': False})
    messages = [Message('user', 'Call connection_echo exactly once with value OK, then reply OK. This is only a connection test.')]
    response = client.complete(messages, tools=(definition,))
    if len(response.tool_calls) != 1:
        raise ValueError('模型未按要求调用测试工具，暂不能确认工具调用兼容性。')
    call = response.tool_calls[0]
    try:
        valid = call.name == 'connection_echo' and len(call.arguments) <= 256 and json.loads(call.arguments) == {'value': 'OK'}
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('测试工具的名称或参数不符合协议；没有执行任何实际业务工具。')
    messages.extend((response, Message('tool', '{"value":"OK"}', name=call.name, tool_call_id=call.id)))
    final = client.complete(messages, tools=(definition,))
    if final.tool_calls or not final.content.strip():
        raise ValueError('测试工具返回后模型未完成回答，工具回放兼容性尚未确认。')
