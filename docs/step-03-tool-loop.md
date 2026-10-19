# 第三步：工具调用闭环

更新日期：2026-10-08。

## 目标与状态

已实现最小闭环：模型提出工具调用 → 程序验证并执行 → 结果回传模型 → 最终回答。
支持 Chat Completions 与 Responses 的非流式自定义 function 工具。
项目 `.venv` 编译通过，全套 112 项离线测试通过，CLI 离线演示通过。
尚未进行本步骤的真实模型工具调用验收；第二步聊天连接成功不代表工具协议一定可用。
尚无新闻搜索、MCP、文件操作、shell 工具或持久化。

## 先运行，再读代码

在 PowerShell Terminal（不是 Python Console）运行：

```powershell
cd C:\Users\13689\Documents\newspaper_agent
.\.venv\Scripts\python.exe -m newspaper_agent --mode demo
```

预期输出：

```text
离线演示：工具算出 12 + 30 = 42。
本轮执行工具 1 次。
```

demo 是固定剧本的假模型：不需要密钥，不访问网络，不理解自然语言；
无论输入什么都演示 12+30。工具本身确实由程序执行，不是直接写死回答 42。
也可以加 `--interactive`，验证多轮、`/reset` 和 `/exit`。

已设置项目密钥时，使用你已验证的 provider：

```powershell
.\.venv\Scripts\python.exe -m newspaper_agent --mode live --provider custom_6sol --tools --interactive
```

输入：`请调用 add_numbers 工具计算 125 + 378，不要自行心算。`
应得到 503，且显示实际执行工具次数。若显示 0 次，模型只是直接回答，不能算工具验收通过。
下一轮可问“刚才的结果是什么”，确认保留历史；一般无需再次调用工具。
如果中转站不支持 tools/schema/include 字段，程序会报告 HTTP 错误，不会偷偷退回普通聊天。
Chat 协议也支持同一入口，但具体模型的工具/推理参数兼容性以服务文档为准，
本步骤不自动设置 reasoning effort，优先使用已配置的 Responses provider 验收。

默认 Echo 和不带 `--tools` 的 live 行为保持不变。
`--tools` 是明确的执行开关，不带它不注册工具；demo 自动注册示例工具。
Echo 不能配合 `--tools`，会提示改用 demo。

## 核心流程

```text
用户消息
  → complete(messages, tools=工具说明)
  → assistant 返回 ToolCall(id, name, arguments)
  → ToolRegistry 按 name 找到已注册工具
  → JSON 参数解析 + 工具自己的严格参数校验
  → tool 消息携带执行结果和相同调用 ID
  → 再次 complete（包含整条调用链）
  → assistant 最终文本 → 提交本轮历史并显示
```

模型只提出“想调用什么、传什么参数”，不会自动执行 Python。
模型看到的 schema 是说明书，不是安全保障；程序必须独立验证参数。
当前工具 `add_numbers` 只接收 left/right 两个有限数字，拒绝布尔值、字符串、
缺项、额外字段和绝对值大于 1e12 的输入。不用 eval，不执行表达式或模型代码。
工具返回 `{"ok":true,"result":{"sum":503}}`，模型据此组织最终回答。

## 按顺序学习这些文件

1. `agent_core/messages.py`：ToolDefinition 是工具说明书，ToolCall 是调用请求；
   Message 增加 tool_calls/tool_call_id，使工具请求与结果能匹配。
2. `tools/local.py`：AddNumbersTool 定义参数 schema，并在 invoke 内独立校验后计算。
3. `tools/registry.py`：register 登记白名单；execute 解析参数、执行工具、包装成功或错误。
   当前没有通用 JSON Schema 校验器，每个工具必须自己实现校验。
4. `agent_core/loop.py`：从原来“一次模型请求”变成有上限的循环；只有最终成功才提交历史。
5. `model/tool_wire.py`：将统一消息翻译成 API 格式，核心循环不认识 HTTP JSON 字段。
6. `model/demo.py`：假模型第一轮提出调用，收到 tool 结果才生成回答；用于离线学习与验证。
7. `tests/integration/test_tool_http.py`：模拟两次 HTTP 请求，证明工具结果真的回传给模型。

动手练习：先把 demo 的 12/30 改成另外两个数字，观察工具结果。
注意 demo 的显示文案也写了 12+30，修改时同步改文案，不要只观察打印文字。
再尝试工具未知名称、缺 right、传字符串，读对应测试里的错误结果。

## 协议与历史

| 内容 | Chat Completions | Responses |
|---|---|---|
| 工具定义 | tools[].function | tools[] 平铺 name/parameters |
| 模型调用 | assistant.tool_calls[].id | output[].function_call.call_id |
| 回传结果 | role=tool, tool_call_id | type=function_call_output, call_id |

两种格式均使用 `strict: true` 的工具 schema。
Responses 保持 `store: false`，请求 `include: [reasoning.encrypted_content]`，
在内存中保留并按顺序回放原始 output（含推理项、调用项和最终消息）。
`Message.model_data` 是模型适配器的内部回放数据，不打印、不落盘，核心不解析。
回传使用 call_id，不是 function_call 的 item id。
会话不要中途更换协议或裁断工具消息链；`/reset` 清空全部历史与回放数据。

依据：[OpenAI 官方 Function calling 文档](https://developers.openai.com/api/docs/guides/function-calling)。

## 错误、安全与边界

- 每次用户输入最多 4 轮模型请求、8 次工具执行；可在 AgentLoop 构造参数调整，暂未开放 CLI 配置。
  最后一轮若仍请求工具，会停止，不执行无法回传的新调用。HTTP 重试次数独立于模型轮数。
- 同一轮多个调用按顺序执行；调用 ID 必须非空且在当前会话不重复。
- 未知工具返回 unknown_tool；参数 JSON/校验失败返回 invalid_arguments；内部执行异常
  返回 tool_execution_failed，不暴露原始异常、路径或密钥；模型可下一轮纠正。
- 输入 JSON 最多 8192 字符；工具输出最多 16384 字符（不是 token 预算）。
- HTTP 失败、模型格式错误或循环上限导致本轮消息不写入旧会话。
  这不能撤销已经执行的工具；当前只允许无副作用本地工具。以后接入写操作必须另设授权和事务。
- 轮数限制不是墙钟超时；工具目前没有进程隔离或执行超时，不能注册任意不可信/耗时工具。
- 注册表拒绝重名工具；模型不能靠名字执行未注册的 Python、shell、网络或文件操作。
- API key 仍只由环境变量注入；测试不调用真实服务。真实调用可能产生额外模型请求费用。
- 暂不支持流式、内置 hosted tools、并行执行、工具确认 UI、通用 schema 执行器及真实联网搜索。
  第四步已增加离线检索/证据读取工具，见 [来源基础层](step-04-source-foundation.md)；候选与反馈持久化仅通过人工管理 CLI，不在模型工具循环中执行。

## 验证

```powershell
.\.venv\Scripts\python.exe -m compileall src tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m newspaper_agent --mode demo
```

离线测试覆盖成功闭环、历史关联、多调用、未知工具、错误参数、执行异常脱敏、
失败回滚、调用 ID 与预算、默认不启用、两种 API 的工具 schema 和回传、
Responses 推理回放及第二轮会话。不把离线结果描述成真实服务验收。

第三步真实验收：看到实际工具次数大于 0，结果正确，第二轮能引用上下文。
验收后再进入第四步“新闻搜索适配”，把这个执行闭环复用到 WebSearchTool。
