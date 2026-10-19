# newspaper_agent 协作入口

## 项目级规则

完整 AI 开发规则见 [`CLAUDE.md`](CLAUDE.md)。每实现一个用户可识别功能，必须创建或更新对应的 `docs/*.md`；修改已有功能时同步维护相关 Markdown 文档。

## 文档同步约定

- `docs/architecture.md` 是项目现状总览。后续修改必须定位到对应实现章节直接改写，不以新增“更新记录”或开发步骤代替正文同步。
- 每次有明确目的的修改另建 `docs/YYYY-MM-DD-HHmmss-修改目的.md`，使用 Asia/Hong_Kong时间；记录目的、影响范围、验证结果和总览同步位置。
- 同步维护专项功能文档；总览不写版本流水，独立修改记录不替代总览。交付前同时检查“现状正文正确”与“本次记录已建立”。

## 开发环境

- 使用 PyCharm 管理项目和虚拟环境。
- Python 固定为 3.11。
- 实际运行、测试、编译和依赖安装必须使用项目根目录 `.venv` 中的 Python。
- Windows 命令示例：`.venv\\Scripts\\python.exe -m pytest -q`。

## 约束

- 使用 Python 3.11，使用 `src/` 包布局；PyCharm 是本项目的主要维护工具。
- 在 PyCharm 中将项目解释器设置为项目根目录的 `.venv`，并选择 Python 3.11。
- 模型通信支持 OpenAI-compatible Chat Completions 和 Responses，通过 provider 的 `wire_api: chat/responses` 选择（默认 chat）；共用 `OpenAICompatibleClient`，不按 provider 名称硬编码协议。
- 模型协议说明位于 `docs/model-communication.md`；真实 API key 只能通过环境变量注入。
- 不读取或复制 Codex 的 `auth.json`、`cc-switch.db` 或 tcodex 内部认证文件。

- 密钥、token、数据库、日志和缓存不得提交。
- Prompt 放在 `prompts/`；架构约定放在 `docs/`；运行期数据放在 `data/`。
- 修改后运行 `python -m compileall src tests` 和 `python -m pytest -q`。
- 未经明确要求不创建 Git commit，不执行破坏性 Git 操作。

## 依赖方向

`agent_core` → protocols；`tools`、`model`、`mcp`、`memory` 实现 protocols；`database` 为持久化边界；`app` 负责组装。
