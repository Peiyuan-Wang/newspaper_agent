# 文件放置约定

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
