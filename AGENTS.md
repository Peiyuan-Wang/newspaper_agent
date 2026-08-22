# newspaper_agent 协作入口

## 约束

- 使用 Python 3.11，使用 `src/` 包布局；PyCharm 是本项目的主要维护工具。
- 在 PyCharm 中将项目解释器设置为项目根目录的 `.venv`，并选择 Python 3.11。
- 项目依赖统一维护在根目录 `requirements.txt`；安装命令为 `python -m pip install -r requirements.txt`。
- 核心逻辑依赖协议，不直接绑定具体模型、搜索后端或 MCP SDK。
- 密钥、token、数据库、日志和缓存不得提交。
- Prompt 放在 `prompts/`；架构约定放在 `docs/`；运行期数据放在 `data/`。
- 修改后运行 `python -m compileall src tests` 和 `python -m pytest -q`。
- 未经明确要求不创建 Git commit，不执行破坏性 Git 操作。

## 依赖方向

`agent_core` → protocols；`tools`、`model`、`mcp`、`memory` 实现 protocols；`database` 为持久化边界；`app` 负责组装。
