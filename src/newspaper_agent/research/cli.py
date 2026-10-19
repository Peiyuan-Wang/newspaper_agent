"""Local authenticated research CLI. All network/model use is explicit."""
import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import sqlite3

from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.model.client import ModelCommunicationError
from newspaper_agent.sources.contracts import DOMAINS, KINDS
from .model_runtime import create_research_model
from .service import ResearchSources
from .session import ResearchSession

ROOT = Path(__file__).resolve().parents[3]


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _show_report(result):
    print(result["markdown"])
    for warning in result.get("search_audit", {}).get("warnings", ()):
        print("提示：" + warning)
    for failure in result.get("search_audit", {}).get("failures", ()):
        print(f"来源未完成：{failure['source_id']} ({failure['error']})")
    if result.get("issues"):
        print("综合状态：" + ", ".join(result["issues"]))
    for action in result.get("actions", ()):
        _print(action)
    if result.get("report_id"):
        print("个人报告 ID：" + result["report_id"])
    print("本轮模型请求：" + str(result.get("session_metrics", {}).get("model_calls", 0)))


def interactive(session, *, read=input):
    print("研究助理 v1；/help 查看命令，/exit 退出。")
    print("当前为模型聊天模式。" if session.model else "无模型模式：用 /search 直接检索；自然语言聊天待配置模型密钥。")
    while True:
        try:
            text = read("你> ").strip()
            if not text:
                continue
            command, _, tail = text.partition(" ")
            if command == "/exit":
                return
            if command == "/help":
                print("/search 关键词 | /profile | /interest 主题 | /exclude 主题 | /unexclude 主题 | /style brief|standard|expert")
                print("/sources | /discover 领域 HTTPS网址 | /events | /undo 事件ID | /reports | /report 报告ID | /reset | /exit")
            elif command == "/reset":
                session.reset()
                print("会话与证据缓存已清空；账号偏好与保存报告保留。")
            elif command == "/profile":
                _print(session.repository.profile(session.user))
            elif command in ("/interest", "/exclude", "/unexclude", "/style"):
                profile = session.repository.profile(session.user)
                if not tail.strip():
                    raise ValueError("请提供主题或风格")
                if command == "/style":
                    changes = {"style": tail}
                else:
                    field = "interests" if command == "/interest" else "excluded"
                    values = profile[field]
                    changes = {field: [v for v in values if v.casefold() != tail.casefold()] if command == "/unexclude"
                               else list(dict.fromkeys([*values, tail]))}
                event = session.repository.update_profile(session.user, changes, text)
                print("偏好已更新；撤销 ID：" + event)
            elif command == "/events":
                _print(session.repository.events(session.user))
            elif command == "/undo":
                session.repository.undo(session.user, tail)
                print("反馈已撤销。")
            elif command == "/sources":
                _print({"built_in": [s.id for s in session.sources.descriptors()],
                        "personal_and_shared": session.repository.list_sources(session.user, include_pending=True)})
            elif command == "/discover":
                domain, _, url = tail.partition(" ")
                session.sources.begin_turn()
                _print(session.sources.discover(query="latest news", domain=domain, website=url))
            elif command == "/reports":
                _print(session.repository.reports(session.user))
            elif command == "/report":
                print(session.repository.report(session.user, tail)["report"]["markdown"])
            elif command == "/search":
                _show_report(session.search(tail))
            elif command.startswith("/"):
                print("未知命令；输入 /help。")
            else:
                _show_report(session.chat(text))
        except (EOFError, KeyboardInterrupt):
            print("会话结束。")
            return
        except (ValueError, ModelCommunicationError) as exc:
            print("操作未完成：" + str(exc))
        except (OSError, sqlite3.Error):
            print("操作未完成：检查本机文件或数据库权限。")


def build_parser():
    parser = argparse.ArgumentParser(description="第一版：真实来源、个人偏好、共享/私密演化与证据报告")
    parser.add_argument("--user", default="local", help="本地账号，首次运行用 signup 创建")
    parser.add_argument("--database", type=Path, default=ROOT / "data/research.sqlite3")
    parser.add_argument("--catalog", type=Path, default=ROOT / "config/sources/live.yaml")
    parser.add_argument("--config", type=Path, default=ROOT / "model_config.yaml")
    parser.add_argument("--provider")
    parser.add_argument("--password-env", help="从指定环境变量读取账号口令；省略则隐藏输入")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("signup")
    sub.add_parser("profile")
    sub.add_parser("events")
    sub.add_parser("reports")
    report = sub.add_parser("report")
    report.add_argument("id")
    report.add_argument("--style", choices=("brief", "standard", "expert"), help="以已保存知识对象重新渲染，不调用模型")
    undo = sub.add_parser("undo")
    undo.add_argument("id")
    profile = sub.add_parser("profile-set")
    profile.add_argument("--interest", action="append")
    profile.add_argument("--exclude", action="append")
    profile.add_argument("--domain", action="append", choices=DOMAINS)
    profile.add_argument("--style", choices=("brief", "standard", "expert"))
    profile.add_argument("--language", choices=("zh", "en"))
    chat = sub.add_parser("chat")
    chat.add_argument("--no-model", action="store_true")
    chat.add_argument("--method", choices=("baseline", "prompt", "structured"), default="structured")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--domain", action="append", choices=DOMAINS)
    search.add_argument("--kind", choices=KINDS)
    search.add_argument("--model", action="store_true", help="额外调用一次模型综合；默认仅原始证据卡片")
    search.add_argument("--method", choices=("baseline", "prompt", "structured"), default="structured")
    compare = sub.add_parser("compare")
    compare.add_argument("id", help="对同一已保存资料比较三个提示策略")
    compare.add_argument("--no-model", action="store_true", help="只核对流程；不代表真实质量比较")
    sources = sub.add_parser("sources")
    sources.add_argument("--pending", action="store_true")
    source = sub.add_parser("source")
    actions = source.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add")
    add.add_argument("--url", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--domain", required=True, choices=DOMAINS)
    for name in ("validate", "enable", "suspend", "archive", "share", "publish"):
        actions.add_parser(name).add_argument("id")
    discover = sub.add_parser("discover")
    discover.add_argument("--query", default="latest news")
    discover.add_argument("--domain", required=True, choices=DOMAINS)
    discover.add_argument("--website")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        repository = ResearchRepository(args.database)
        password = os.getenv(args.password_env) if args.password_env else getpass("本地账号口令（不会显示）: ")
        if password is None:
            raise ValueError("指定的账号口令环境变量未配置")
        if args.command == "signup":
            if not args.password_env and getpass("再次输入口令: ") != password:
                raise ValueError("两次口令不一致")
            repository.register(args.user, password)
            print("账号已创建：" + args.user + ("（本机共享来源管理员）" if repository.is_admin(args.user) else ""))
            return
        if not repository.authenticate(args.user, password):
            raise ValueError("账号或口令错误")
        del password
        sources = ResearchSources(repository, args.user, args.catalog)
        use_model = ((args.command == "chat" and not args.no_model)
                     or (args.command == "search" and args.model)
                     or (args.command == "compare" and not args.no_model))
        model = create_research_model(args.provider, config_path=args.config) if use_model else None
        session = ResearchSession(repository, args.user, sources, model, method=getattr(args, "method", "structured"))
        if args.command == "chat":
            interactive(session)
        elif args.command == "search":
            _show_report(session.search(args.query, args.domain, kind=args.kind))
        elif args.command == "compare":
            for method, result in session.compare(args.id).items():
                print("方法：" + method)
                _show_report(result)
        elif args.command == "profile":
            _print(repository.profile(args.user))
        elif args.command == "profile-set":
            changes = {k: v for k, v in {"interests": args.interest, "excluded": args.exclude,
                       "domains": args.domain, "style": args.style, "language": args.language}.items() if v is not None}
            _print({"event_id": repository.update_profile(args.user, changes, "CLI显式设置")})
        elif args.command == "events":
            _print(repository.events(args.user))
        elif args.command == "undo":
            repository.undo(args.user, args.id)
            print("反馈已撤销。")
        elif args.command == "reports":
            _print(repository.reports(args.user))
        elif args.command == "report":
            saved = repository.report(args.user, args.id)["report"]
            if args.style:
                from newspaper_agent.synthesis.rendering import render
                print(render(saved["knowledge_payload"], style=args.style))
            else:
                print(saved["markdown"])
        elif args.command == "sources":
            _print({"built_in": [{"id": s.id, "name": s.name, "domains": s.domains} for s in sources.descriptors()],
                    "personal_and_shared": repository.list_sources(args.user, include_pending=args.pending)})
        elif args.command == "discover":
            _print(sources.discover(query=args.query, domain=args.domain, website=args.website))
        elif args.command == "source":
            if args.action == "add":
                _print(repository.store_source(args.user, args.name, args.url, args.domain))
            elif args.action == "validate":
                _print(sources.validate(args.id))
            elif args.action == "share":
                _print(repository.share_source(args.user, args.id))
            elif args.action == "publish":
                repository.publish_source(args.user, args.id)
                print("共享来源已发布。")
            else:
                repository.set_source_status(args.user, args.id,
                    {"enable": "enabled", "suspend": "suspended", "archive": "archived"}[args.action])
                print("来源状态已更新。")
    except (ValueError, ModelCommunicationError) as exc:
        parser.exit(2, "操作未完成：" + str(exc) + "\n")
    except (OSError, sqlite3.Error):
        parser.exit(2, "操作未完成：检查本机文件、数据库权限或网络。\n")
    except (EOFError, KeyboardInterrupt):
        parser.exit(130, "操作已取消。\n")
