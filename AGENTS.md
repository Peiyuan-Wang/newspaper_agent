# newspaper_agent 协作入口

## 项目级规则

完整 AI 开发规则见 [`CLAUDE.md`](CLAUDE.md)。每实现一个用户可识别功能，必须创建或更新对应的 `docs/*.md`；修改已有功能时同步维护相关 Markdown 文档。

## 开发环境

- 使用 PyCharm 管理项目和虚拟环境。
- Python 固定为 3.11。
- 实际运行、测试、编译和依赖安装必须使用项目根目录 `.venv` 中的 Python。
- Windows 命令示例：`.venv\\Scripts\\python.exe -m pytest -q`。

## 约束

- 使用 Python 3.11，使用 `src/` 包布局；PyCharm 是本项目的主要维护工具。
- 在 PyCharm 中将项目解释器设置为项目根目录的 `.venv`，并选择 Python 3.11。
- 模型通信使用 OpenAI-compatible Chat Completions；DeepSeek、OpenAI 和 custom provider 共用 `OpenAICompatibleClient`。
- 模型协议说明位于 `docs/model-communication.md`；真实 API key 只能通过环境变量注入。
- 不读取或复制 Codex 的 `auth.json`、`cc-switch.db` 或 tcodex 内部认证文件。

- 密钥、token、数据库、日志和缓存不得提交。
- Prompt 放在 `prompts/`；架构约定放在 `docs/`；运行期数据放在 `data/`。
- 修改后运行 `python -m compileall src tests` 和 `python -m pytest -q`。
- 未经明确要求不创建 Git commit，不执行破坏性 Git 操作。

## 依赖方向

`agent_core` → protocols；`tools`、`model`、`mcp`、`memory` 实现 protocols；`database` 为持久化边界；`app` 负责组装。
