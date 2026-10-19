from __future__ import annotations

import argparse
from pathlib import Path
import sys
from collections.abc import Callable, Sequence

from newspaper_agent.agent_core.loop import AgentLoop, AgentLoopError
from newspaper_agent.agent_core.messages import AgentState, Message
from newspaper_agent.model.client import ModelCommunicationError
from newspaper_agent.model.factory import create_model_client
from newspaper_agent.model.settings import ModelConfigurationError
from newspaper_agent.model.demo import DemoToolModel
from newspaper_agent.tools.registry import ToolRegistry
from newspaper_agent.tools.local import AddNumbersTool
from newspaper_agent.tools.information import SearchInformationTool, FetchEvidenceTool
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.service import SourceService


class EchoModel:
    def complete(self, messages: list[Message]) -> Message:
        return Message(role="assistant", content="已收到：" + messages[-1].content)


def run_interactive(
    loop: AgentLoop,
    *,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    mode_label: str = "离线 Echo 会话（不调用真实模型）",
    initial_messages: Sequence[Message] = (),
    on_reset: Callable[[], None] | None = None,
) -> AgentState:
    """Run an in-memory session; injected IO keeps tests offline."""
    state = AgentState(messages=list(initial_messages))
    write(mode_label + "；输入 /exit 退出，/reset 清空上下文。")
    while True:
        try:
            text = read("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            write("会话结束。")
            return state
        if text == "/exit":
            return state
        if text == "/reset":
            state = AgentState(messages=list(initial_messages))
            if on_reset is not None:
                on_reset()
            write("上下文已清空。")
            continue
        if not text:
            continue
        try:
            result = loop.run(text, state)
        except (ModelCommunicationError, AgentLoopError) as exc:
            write("请求失败：" + str(exc) + "；本轮未写入历史，可重新输入或 /exit。")
            continue
        write("助手> " + result.response.content)
        if loop.tools is not None:
            write(f"本轮执行工具 {result.tool_calls_executed} 次。")


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "research":
        from newspaper_agent.research.cli import main as research_main
        research_main(arguments[1:])
        return
    parser = argparse.ArgumentParser(description="newspaper_agent 离线或真实模型会话")
    parser.add_argument("--mode", choices=("echo", "live", "demo"), default="echo", help="echo 离线；live 真实模型；demo 固定的 12+30 工具演示")
    parser.add_argument("--tools", action="store_true", help="live 模式启用安全本地工具（demo 自动启用）")
    parser.add_argument("--information", action="store_true", help="live 模式提供离线样例检索与证据读取工具（搜索不联网）")
    parser.add_argument("--source-catalog", type=Path, default=Path("config/sources/catalog.yaml"))
    parser.add_argument("--provider", help="YAML 中的 provider 名称；省略时使用 default_provider")
    parser.add_argument("--config", type=Path, default=Path("model_config.yaml"), help="模型 YAML 配置路径")
    parser.add_argument("--message", default="newspaper_agent ready", help="单轮输入文本")
    parser.add_argument("--interactive", action="store_true", help="启动所选模型的多轮会话")
    args = parser.parse_args(argv)
    if args.mode != "live" and args.provider is not None:
        parser.error("--provider 需要同时指定 --mode live")
    if args.mode == "echo" and args.tools:
        parser.error("--tools 需要 --mode live；离线工具演示请使用 --mode demo")
    if args.information and args.mode != "live":
        parser.error("--information 需要 --mode live；无密钥演示用 -m newspaper_agent.sources demo")
    service = None
    initial_messages = ()
    try:
        if args.information:
            service = SourceService(SourceCatalog.load(args.source_catalog))
            prompt = Path("prompts/information.md").read_text(encoding="utf-8")
            if not prompt.strip() or len(prompt) > 8000:
                raise ValueError("检索提示词无效")
            initial_messages = (Message("system", prompt),)
        model = (EchoModel() if args.mode == "echo" else DemoToolModel() if args.mode == "demo"
                 else create_model_client(args.provider, config_path=args.config))
    except (ModelConfigurationError, ValueError, OSError) as exc:
        parser.exit(2, f"启动失败：{exc}\n")
    tools = None
    if args.tools or args.mode == "demo" or args.information:
        tools = ToolRegistry()
        tools.register(AddNumbersTool())
        if service is not None:
            tools.register(SearchInformationTool(service))
            tools.register(FetchEvidenceTool(service))
    loop = AgentLoop(model, tools)
    if args.interactive:
        label = ("离线 Echo 会话（不调用真实模型）" if args.mode == "echo" else
                 "离线工具演示（固定执行 12+30，不调用真实模型）" if args.mode == "demo" else
                 f"真实模型会话（provider={model.settings.provider}，model={model.settings.model}）")
        if tools:
            label += "；本地工具已启用"
        if service:
            label += "；检索仅返回虚构离线样例"
        run_interactive(loop, mode_label=label, initial_messages=initial_messages,
                        on_reset=service.clear if service else None)
    else:
        try:
            result = loop.run(args.message, AgentState(messages=list(initial_messages)))
            print(result.response.content)
            if tools:
                print(f"本轮执行工具 {result.tool_calls_executed} 次。")
        except (ModelCommunicationError, AgentLoopError) as exc:
            print(f"请求失败：{exc}", file=sys.stderr)
            raise SystemExit(1) from None
