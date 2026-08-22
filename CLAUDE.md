# newspaper_agent 项目级开发规则

## 1. 项目定位

`newspaper_agent` 是一个轻量、可替换、provider-neutral 的 Python Agent 项目。AI 开发助手必须优先遵循本文件，其次遵循 `AGENTS.md`、`docs/` 中的项目文档和现有代码约定。

## 2. Python 与 PyCharm 环境

- 项目固定使用 **Python 3.11**，不使用 Python 3.12 或更高版本作为项目解释器。
- 使用 **PyCharm** 管理项目和虚拟环境。
- PyCharm Project Interpreter 必须指向项目根目录下的 `.venv`。
- 项目实际运行、测试、编译和依赖安装必须使用当前项目 `.venv` 中的 Python，不使用 workspace 全局 Python。
- Windows 下统一使用：

```powershell
.venv\Scripts\python.exe
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -q
```

- 修改依赖时同步更新 `requirements.txt`；不得只修改本地虚拟环境。

## 3. 功能开发与文档要求

每实现一个可识别的功能，必须同时创建或更新一个对应的 Markdown 文档。代码与文档不得分离完成。

- 功能设计、协议、配置和使用方式：写入 `docs/<feature-name>.md`。
- 重大架构选择：写入 `docs/decisions/`。
- 对已有功能的行为、配置、接口或目录进行更新时，必须同步更新对应文档、`README.md` 或 `AGENTS.md`。
- 文档至少说明：目标、使用方式、配置项、输入输出、错误处理、安全边界和验证方法。
- 如果功能尚未完成，文档必须明确标记当前状态和未完成边界，不得把草稿描述成已支持能力。
- 不为纯内部重命名或明显机械格式调整强制创建新文档，但涉及用户可见行为的修改仍必须更新文档。

## 4. AI 开发流程

AI 助手每次修改代码前必须：

1. 阅读本文件、`AGENTS.md` 以及相关功能文档。
2. 检查目标模块现有接口、测试和配置。
3. 先确定修改边界，避免无关重构和新增依赖。
4. 为新功能建立对应文档，再实现代码，或在同一变更中同步完成文档。
5. 添加或更新测试，优先使用离线、可重复的测试。
6. 使用项目 `.venv` 执行编译、测试和冒烟验证。
7. 检查 `git diff`、`git diff --check` 和 `git status`。

AI 助手不得：

- 修改 `agent-eval`、`marvis-agent` 等受保护共享项目。
- 将真实 API key、token、密码、数据库、日志或缓存提交到 Git。
- 将真实密钥写入 YAML、源码、Markdown 示例或测试代码。
- 未经明确要求创建 Git commit、推送远程仓库或执行破坏性 Git 操作。
- 在核心层直接绑定具体模型 SDK、搜索后端或 MCP 实现。
- 为解决测试问题而跳过测试、绕过 hooks 或删除用户已有修改。

## 5. 项目结构与依赖方向

- 源码：`src/newspaper_agent/`
- Prompt：`prompts/`
- 功能文档：`docs/`
- 测试：`tests/unit/`、`tests/integration/`
- 运行期数据：`data/`，默认不提交
- 依赖方向：`agent_core` 依赖协议；`model`、`tools`、`mcp`、`memory` 提供实现；`database` 提供持久化边界；`app` 负责组装。

## 6. 验证命令

在项目根目录执行：

```powershell
.venv\Scripts\python.exe -m compileall src tests
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m newspaper_agent
```

如果依赖尚未安装：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

所有无法执行的验证必须在交付说明中明确写出原因，不得声称验证已通过。
