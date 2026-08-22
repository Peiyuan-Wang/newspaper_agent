# 文件放置约定

## 开发环境

- 使用 PyCharm 维护本项目。
- Python 版本固定为 3.11，不使用 3.12 或更高版本作为项目解释器。
- PyCharm Project Interpreter 应指向项目根目录 `.venv` 中的 Python 3.11。
- 项目依赖以根目录 `requirements.txt` 为准；新增或升级依赖时同步更新该文件。
- 文本文件统一使用 LF，Windows 脚本例外规则见根目录 `.gitattributes`。

| 内容 | 位置 |
|---|---|
| Python 源码 | `src/newspaper_agent/` |
| 单元测试 | `tests/unit/` |
| 集成测试 | `tests/integration/` |
| Prompt | `prompts/*.md` |
| 非敏感配置 | `model_config.yaml`、`config/` |
| 架构和决策 | `docs/`、`docs/decisions/` |
| 可重建运行数据 | `data/`，默认忽略 |
| 密钥和 token | 仅环境变量，不进入文件或 Git |

新模块应先定义协议，再提供最小实现；不要在核心层引入 provider SDK。
