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

项目当前提供 OpenAI-compatible Chat Completions 通信，支持 DeepSeek、OpenAI GPT 和自定义兼容 provider。通信格式、模型配置以及完整 API key 配置方法见 [`docs/model-communication.md`](docs/model-communication.md)。

## 最快开始：配置 DeepSeek API key

当前默认 provider 是 DeepSeek。开发时最简单的方式是在 PyCharm 的运行终端中执行：

```powershell
$env:DEEPSEEK_API_KEY = "替换为你自己的 DeepSeek API key"
```

然后在**同一个终端窗口**运行：

```powershell
.venv\\Scripts\\python.exe -m newspaper_agent
.venv\\Scripts\\python.exe -m pytest -q
```

如果使用 PyCharm 的 Run Configuration：

```text
Run -> Edit Configurations -> Environment variables
```

添加：

```text
DEEPSEEK_API_KEY=替换为你自己的 DeepSeek API key
```

程序会根据 `model_config.yaml` 中的 `api_key_env: DEEPSEEK_API_KEY` 读取 key。真实 key 只通过环境变量注入，不要写入 `model_config.yaml`、README、源码、日志或 Git。OpenAI GPT 使用同样方式配置 `OPENAI_API_KEY`。

## 项目开发规则

AI 开发和协作规则集中在 [`CLAUDE.md`](CLAUDE.md)。重要约定如下：

- 每实现一个功能，必须创建或更新对应的 Markdown 文档。
- 对已有功能的更新必须同步维护相关文档。
- 使用 PyCharm 管理项目虚拟环境。
- 项目固定使用 Python 3.11。
- 实际运行和测试必须使用项目根目录 `.venv` 中的 Python。


- `src/newspaper_agent/agent_core/`：消息、状态、循环和协议。
- `src/newspaper_agent/model/`：provider-neutral 模型客户端接口。
- `src/newspaper_agent/tools/`：本地工具及 websearch 适配边界。
- `src/newspaper_agent/mcp/`：MCP 客户端与工具注册边界。
- `src/newspaper_agent/memory/`：短期窗口和长期记忆服务。
- `src/newspaper_agent/database/`：SQLite 连接、模型和 repository。
- `prompts/`：可审查、可替换的 Markdown Prompt。
- `docs/`：架构和约定。
