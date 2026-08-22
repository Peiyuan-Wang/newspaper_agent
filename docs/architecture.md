# 架构

```text
用户输入
   ↓
AgentLoop ──→ ModelClient
   │               │
   ├──→ ToolRegistry ──→ WebSearch / 本地工具
   ├──→ MCPRegistry ────→ MCPClient
   └──→ MemoryService ──→ Repository ──→ SQLite
```

`AgentLoop` 只依赖协议和数据结构。模型、工具、MCP 与记忆均可替换；第一阶段不实现具体网络调用。长期记忆通过 repository 持久化，短期记忆只管理当前会话上下文窗口。
