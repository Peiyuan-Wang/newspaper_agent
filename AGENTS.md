# newspaper_agent 协作入口

## 约束

- Python 3.11+，使用 `src/` 包布局。
- 核心逻辑依赖协议，不直接绑定具体模型、搜索后端或 MCP SDK。
- 密钥、token、数据库、日志和缓存不得提交。
- Prompt 放在 `prompts/`；架构约定放在 `docs/`；运行期数据放在 `data/`。
- 修改后运行 `python -m compileall src tests` 和 `python -m pytest -q`。
- 未经明确要求不创建 Git commit，不执行破坏性 Git 操作。

## 依赖方向

`agent_core` → protocols；`tools`、`model`、`mcp`、`memory` 实现 protocols；`database` 为持久化边界；`app` 负责组装。
