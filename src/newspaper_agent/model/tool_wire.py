"""将 provider-neutral 工具消息转换为两种 HTTP 协议。"""

from copy import deepcopy

from newspaper_agent.agent_core.messages import Message, ToolCall


def encode_messages(messages, responses):
    result = []
    for message in messages:
        if responses and message.model_data.get("responses_output"):
            result.extend(deepcopy(message.model_data["responses_output"]))
        elif message.role == "tool":
            if not message.tool_call_id:
                raise ValueError("Missing tool result ID")
            if responses:
                result.append({"type": "function_call_output", "call_id": message.tool_call_id,
                               "output": message.content})
            else:
                result.append({"role": "tool", "tool_call_id": message.tool_call_id,
                               "content": message.content})
        else:
            item = {"role": message.role, "content": message.content}
            if message.tool_calls:
                if responses:
                    raise ValueError("Missing Responses replay data")
                item["tool_calls"] = [{"id": call.id, "type": "function", "function": {
                    "name": call.name, "arguments": call.arguments,
                }} for call in message.tool_calls]
            result.append(item)
    return result


def parse_call(call_id, name, arguments):
    if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
        raise ValueError("Invalid tool call ID or name")
    if not isinstance(arguments, str):
        raise ValueError("Tool arguments must be JSON text")
    return ToolCall(call_id, name, arguments)


def parse_tool_reply(result, responses):
    if not isinstance(result, dict):
        raise ValueError("Expected response object")
    if not responses:
        message = result["choices"][0]["message"]
        if message["role"] != "assistant":
            raise ValueError("Expected assistant")
        content = message.get("content")
        calls = []
        raw_calls = message.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise ValueError("Expected tool calls array")
        for item in raw_calls:
            if item["type"] != "function":
                raise ValueError("Unsupported tool type")
            function = item["function"]
            calls.append(parse_call(item["id"], function["name"], function["arguments"]))
        if content is None and calls:
            content = ""
        if not isinstance(content, str) or (not content.strip() and not calls):
            raise ValueError("Missing text or tool calls")
        return Message("assistant", content, tool_calls=tuple(calls))

    if result.get("status") != "completed" or not isinstance(result.get("output"), list):
        raise ValueError("Response incomplete")
    calls, texts = [], []
    for item in result["output"]:
        kind = item["type"]
        if kind == "function_call":
            if item.get("status", "completed") != "completed":
                raise ValueError("Function call incomplete")
            calls.append(parse_call(item["call_id"], item["name"], item["arguments"]))
        elif kind == "message":
            if item.get("role") != "assistant" or item.get("status") != "completed":
                raise ValueError("Invalid assistant message")
            if not isinstance(item.get("content"), list):
                raise ValueError("Expected content array")
            for part in item["content"]:
                if part["type"] != "output_text" or not isinstance(part.get("text"), str):
                    raise ValueError("Expected text output")
                texts.append(part["text"])
        elif kind != "reasoning":
            raise ValueError("Unsupported output type")
    text = "".join(texts)
    if not text.strip() and not calls:
        raise ValueError("Missing text or function calls")
    return Message("assistant", text, tool_calls=tuple(calls), model_data={
        "responses_output": deepcopy(result["output"])
    })
