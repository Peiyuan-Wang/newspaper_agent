# newspaper_agent

轻量、provider-neutral 的新闻研究 Agent 骨架，面向 websearch、短期/长期记忆、MCP 和可替换模型适配。

## 快速开始

本项目固定使用 Python 3.11，并建议使用 PyCharm 打开和维护仓库。

在 PyCharm 中：

1. 打开 `C:\\workspace\\newspaper_agent`。
2. 将 Project Interpreter 设置为 `.venv` 中的 Python 3.11。
3. 使用项目根目录的 `requirements.txt` 安装依赖。

命令行等价操作：

```bash
python3.11 -m venv .venv
.venv\\Scripts\\python -m pip install -r requirements.txt
.venv\\Scripts\\python -m newspaper_agent
.venv\\Scripts\\python -m pytest -q
```

项目当前只提供边界清晰的最小实现，不绑定 Anthropic SDK、LangChain、LlamaIndex 或具体搜索服务。模型密钥只能通过环境变量注入。

## 目录

- `src/newspaper_agent/agent_core/`：消息、状态、循环和协议。
- `src/newspaper_agent/model/`：provider-neutral 模型客户端接口。
- `src/newspaper_agent/tools/`：本地工具及 websearch 适配边界。
- `src/newspaper_agent/mcp/`：MCP 客户端与工具注册边界。
- `src/newspaper_agent/memory/`：短期窗口和长期记忆服务。
- `src/newspaper_agent/database/`：SQLite 连接、模型和 repository。
- `prompts/`：可审查、可替换的 Markdown Prompt。
- `docs/`：架构和约定。
