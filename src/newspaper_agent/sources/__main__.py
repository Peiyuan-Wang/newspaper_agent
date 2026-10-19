"""独立来源管理 CLI，只有离线执行；管理命令不会传给模型。"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from newspaper_agent.database.source_repository import SourceRepository
from newspaper_agent.agent_core.loop import AgentLoop
from newspaper_agent.agent_core.messages import Message, ToolCall
from newspaper_agent.tools.registry import ToolRegistry
from newspaper_agent.tools.information import FetchEvidenceTool, SearchInformationTool
from .contracts import DOMAINS, KINDS, SearchRequest, identifier
from .catalog import SourceCatalog
from .service import SourceService


class ResearchDemoModel:
    def __init__(self, request):
        self.request = request

    def complete(self, messages, *, tools=()):
        last = messages[-1]
        if last.role != "tool":
            args = asdict(self.request)
            args.pop("max_results")
            args.pop("max_sources")
            args.pop("recent_days")
            return Message("assistant", "", tool_calls=(ToolCall("search_demo", "search_information", json.dumps(args)),))
        output = json.loads(last.content)
        if not output["ok"]:
            return Message("assistant", "离线演示：工具失败。")
        result = output["result"]
        if "selected_sources" in result:
            if not result["results"]:
                return Message("assistant", "离线演示：没有符合筛选条件的来源；未联网。")
            return Message("assistant", "", tool_calls=(ToolCall("fetch_demo", "fetch_evidence",
                           json.dumps({"evidence_id": result["results"][0]["id"]})),))
        return Message("assistant", f"离线演示，非真实资料：{result['title']}\n来源：{result['source_id']}\n"
                       f"{result['snippet']}\n{result['url']}\n发布时间：未知。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="来源目录、离线检索与个人候选/反馈")
    parser.add_argument("--catalog", type=Path, default=Path("config/sources/catalog.yaml"))
    parser.add_argument("--database", type=Path, default=Path("data/sources.sqlite3"))
    parser.add_argument("--user", default="local")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for name in ("route", "demo"):
        sub = commands.add_parser(name)
        sub.add_argument("--query", default="信息检索教学")
        sub.add_argument("--domain", action="append", choices=DOMAINS, required=True)
        sub.add_argument("--kind", choices=KINDS)
        sub.add_argument("--region")
        sub.add_argument("--language")
    add = commands.add_parser("candidate-add")
    add.add_argument("--id", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--domain", choices=DOMAINS, required=True)
    add.add_argument("--url", required=True)
    commands.add_parser("candidates")
    feed = commands.add_parser("feedback")
    feed.add_argument("--source", required=True)
    feed.add_argument("--kind", choices=("useful", "irrelevant", "fact_error"), required=True)
    undo = commands.add_parser("undo-feedback")
    undo.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    try:
        identifier(args.user)
        catalog = SourceCatalog.load(args.catalog)
        if args.command == "list":
            result = [asdict(s) for s in catalog.sources]
        elif args.command in ("route", "demo"):
            # 只读目录/离线演示不创建数据库；已有反馈可影响同等级排序。
            prefs = SourceRepository(args.database).preferences(args.user) if args.database.exists() else {}
            request = SearchRequest(args.query, tuple(args.domain), args.kind, args.region, args.language)
            service = SourceService(catalog, prefs)
            if args.command == "route":
                selected, warnings = catalog.route(request, prefs)
                result = {"selected_sources": [s.id for s in selected], "warnings": warnings, "is_fixture": True}
            else:
                registry = ToolRegistry()
                registry.register(SearchInformationTool(service))
                registry.register(FetchEvidenceTool(service))
                response = AgentLoop(ResearchDemoModel(request), registry).run(args.query)
                print(response.response.content)
                print(f"本轮执行工具 {response.tool_calls_executed} 次；未调用真实模型或搜索服务。")
                return
        else:
            repository = SourceRepository(args.database)
            if args.command == "candidate-add":
                repository.add_candidate(args.user, args.id, args.name, args.domain, args.url)
                result = {"id": args.id, "status": "discovered", "enabled": False}
            elif args.command == "candidates":
                result = repository.candidates(args.user)
            elif args.command == "feedback":
                if args.source not in {s.id for s in catalog.sources}:
                    raise ValueError("只能对目录中已知来源反馈")
                result = {"feedback_id": repository.feedback(args.user, args.source, args.kind)}
            else:
                repository.undo_feedback(args.user, args.id)
                result = {"undone": True}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error):
        parser.exit(2, "来源操作失败：检查目录、参数、用户标识、数据库权限或重复 id。\n")


if __name__ == "__main__":
    main()
