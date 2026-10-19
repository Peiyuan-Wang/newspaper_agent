# 模型通信协议

2026-10-15：读取采用有界HTTP分块缓冲（仍请求非流式JSON），增加分类诊断、4MB限制、取消与总等待检查。Responses不完整输出被区分为输出额度不足等原因，不执行截断工具。上下文按实际wire JSON计量，详情见[对话可靠性](conversation-reliability.md)。

## 双协议配置（2026-10-07）

客户端last_usage读取Chat/Responses的输入/输出/总Token及可用缓存输入、推理子集；在输出额度不足等已返回usage的失败中也保留统计。BudgetModel加锁累计，fork请求客户端隔离并发诊断，新闻评分最多两路。缺usage不是0消耗，HTTP重试用量可能未返回，指标不等于完整账单。模型内部KV张量不通过当前兼容接口导出；应用仅保存新闻特征/决策/向量缓存，稳定Prompt前缀有利于服务端缓存但不保证第三方命中。详见[评分效率和缓存边界](efficient-news-scoring.md)。

临时排查版已由用户报告真实连接测试成功。现已移除 `custom_6sol` 名称硬编码，
所有 provider 都通过 YAML 的 `wire_api` 显式选择协议；不按模型名称或域名猜测，
请求失败也不会自动切换协议，以避免隐藏配置错误或重复计费。

| wire_api | 请求路径 | 对话字段 | 回复来源 | max_tokens 的 HTTP 字段 |
|---|---|---|---|---|
| chat（默认） | /chat/completions | messages | choices[0].message.content | max_tokens |
| responses | /responses | input | output 中的 assistant output_text | max_output_tokens |

`protocol: openai-compatible` 保留不变，表示兼容服务类别；`wire_api` 才选择具体 API。
不填 `wire_api` 时保持旧配置的 Chat Completions 行为。其他值或错误类型在启动时被拒绝。
`base_url` 填 API 根地址（例如 `https://api.huanling.icu/v1`），不要包含完整请求路径。

已验证的 provider 配置如下；其他 provider 暂时保留 `chat`，可按服务文档自行修改：

```yaml
providers:
  custom_6sol:
    protocol: openai-compatible
    wire_api: responses
    base_url: https://api.huanling.icu/v1
    model: gpt-6-sol
    api_key_env: NEWSPAPER_AGENT_CUSTOM_API_KEY
    temperature: null
    max_tokens: null
```

`temperature` 和 `max_tokens` 填 `null` 表示省略 HTTP 参数，不是发送 JSON null。
省略或注释配置项会恢复默认值 0.2 / 2048。数值配置时恢复发送：Chat 使用
`max_tokens`，Responses 使用 `max_output_tokens`。某些模型不支持 temperature，
因此上述配置保留成功排查时的无采样参数、无输出上限请求；没有客户端输出上限
意味着服务端决定上限，可能产生更高费用。Responses 上限也包含推理 token。

Responses 始终发送 `store: false`、`stream: false`，每轮携带本地完整文本历史。
仅解析已完成的 assistant 文本消息，跳过 reasoning 项；未完成、无文本、拒绝输出
或格式错误返回通信异常，本轮不写入历史。普通聊天模式不执行工具，尚不支持流式、多模态、
服务端会话链。API key 仍只从环境变量读取，HTTP 错误不显示原始正文。

更新（2026-10-08）：显式加 `--tools` 已支持两种 API 的自定义 function 工具闭环。
Responses 工具模式请求 reasoning.encrypted_content 并保留原始 output 回放，
不再限于跳过推理项的普通文本模式。工具请求、结果和安全边界见
[第三步工具调用文档](step-03-tool-loop.md)。流式和 hosted tools 仍未实现。

在已配置环境变量的 PowerShell 中运行：

```powershell
cd C:\Users\13689\Documents\newspaper_agent
.\.venv\Scripts\python.exe -m newspaper_agent --mode live --provider custom_6sol --interactive
```

离线验证使用项目 `.venv` 执行 compileall 和 pytest；测试覆盖旧配置默认值、
协议枚举校验、参数省略及映射、任意 provider 名称、CLI 到 HTTP 完整链路。
离线模拟不能证明每个中转站模型支持该协议；服务端模型权限和字段限制仍需真实验证。

协议依据：[OpenAI Responses 迁移说明](https://developers.openai.com/api/docs/guides/migrate-to-responses)。

## 1. 目标

`newspaper_agent` 支持 **OpenAI-compatible Chat Completions 和 Responses** 作为模型通信边界。DeepSeek、OpenAI GPT 以及用户自己的兼容服务都通过同一个 `OpenAICompatibleClient` 调用，`AgentLoop` 不感知具体 provider 或 wire_api。

实现位置：

```text
src/newspaper_agent/model/client.py
src/newspaper_agent/model/factory.py
src/newspaper_agent/model/settings.py（config/settings.py 保留兼容入口）
```

## 2. 配置

仓库根目录的 `model_config.yaml` 只保存非敏感连接信息：

```yaml
providers:
  deepseek:
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
```

- `base_url`：provider 的 OpenAI-compatible API 根地址。
- `model`：具体模型名称。
- `api_key_env`：真实 API key 所在的环境变量名。
- `wire_api`：`chat`（默认）或 `responses`。
- `temperature`、`max_tokens`：数值时发送，`null` 时省略；默认值分别为 0.2 / 2048。
- `timeout`：请求超时，必须大于 0，不能填 null。

真实 key 不得写入 YAML、源码、日志或 Git。当前版本使用环境变量注入 API key。

### Windows PowerShell 临时配置

只对当前 PowerShell 窗口生效，适合开发和测试：

```powershell
$env:DEEPSEEK_API_KEY = "用户自己的 DeepSeek key"
$env:OPENAI_API_KEY = "用户自己的 OpenAI key"
$env:NEWSPAPER_AGENT_CUSTOM_API_KEY = "用户自己的自定义 provider key"
```

然后在同一个窗口启动程序：

```powershell
.venv\Scripts\python.exe -m newspaper_agent --mode live --provider deepseek --interactive
.venv\\Scripts\\python -m pytest -q
```

### Windows 持久配置

如果希望新开的终端也能使用，可以通过 Windows 用户环境变量配置：

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "用户自己的 DeepSeek key", "User")
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "用户自己的 OpenAI key", "User")
```

设置后需要重新打开 PyCharm 或终端，使其读取新的环境变量。更换 key 时重新执行命令即可；删除用户环境变量：

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", $null, "User")
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $null, "User")
```

### PyCharm 配置

在 PyCharm 中打开：

```text
Run -> Edit Configurations -> Environment variables
```

为当前运行配置添加对应变量，例如：

```text
DEEPSEEK_API_KEY=用户自己的 DeepSeek key
```

PyCharm 的运行配置只保存在本地，不要将包含真实 key 的运行配置文件提交到 Git。推荐检查 `.gitignore`，不要提交 `.env` 文件。

### 使用哪个变量

变量名由 `model_config.yaml` 中 provider 的 `api_key_env` 决定：

| provider | 配置字段 | 环境变量 |
|---|---|---|
| `deepseek` | `api_key_env: DEEPSEEK_API_KEY` | `DEEPSEEK_API_KEY` |
| `openai` | `api_key_env: OPENAI_API_KEY` | `OPENAI_API_KEY` |
| `custom` | `api_key_env: NEWSPAPER_AGENT_CUSTOM_API_KEY` | `NEWSPAPER_AGENT_CUSTOM_API_KEY` |

分发产品时，用户配置文件可以覆盖 provider 的 URL 和 model，但真实 key 应由环境变量或后续系统凭据存储提供。程序不会从 YAML 的 `api_key` 字段读取密钥。

不要使用以下方式：

```yaml
api_key: sk-真实密钥
```

也不要把 key 写入 URL 查询参数、源码、日志、截图或 Git 提交。
## 3. Chat Completions 请求（wire_api: chat）

客户端向以下地址发送请求：

```text
POST {base_url}/chat/completions
```

请求头：

```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

请求体：

```json
{
  "model": "deepseek-chat",
  "messages": [
    {"role": "user", "content": "你好"}
  ],
  "temperature": 0.2,
  "max_tokens": 2048
}
```

当选择 `chat` 时使用上述格式。选择 `responses` 时使用文首的独立请求与回复格式。

## 5. 轻量鲁棒性策略

通信层提供有限重试，不承诺无限恢复或生产级高可用：

- 通用客户端默认最多尝试3次；桌面端覆盖为最多2次，仅对502/503/504、remote_protocol/read_error响应中断重试，混合错误共享两次预算；连接探测关闭重试。
- 通用默认策略重试网络传输错误、408、429、500、502、503、504；桌面端通过retry_transport_codes白名单处理响应中断，不自动重发超时、建连、代理或写入异常。
- 400、401、403、404 等请求、认证和配置错误立即失败。
- 默认退避为 1 秒、2 秒、4 秒，并受 `max_retry_delay` 限制。
- 429 响应带有合法的 `Retry-After` 时优先使用该值，但仍受最大退避时间限制。
- 重试发生在当前模型HTTP请求内部，也适用于工具结果回传；不会重新运行AgentLoop或已执行的工具。服务端仍可能重复生成/计费，不能保证服务端exactly-once。
- 当前不实现无限重试、断路器、请求队列或流式响应。

可在 provider 配置中调整：

```yaml
max_attempts: 3
initial_retry_delay: 1.0
max_retry_delay: 8.0
```

这些策略用于应对短暂网络波动、限流和服务端临时故障；API key 错误、模型名称错误和 URL 配置错误不会通过重试掩盖。

桌面端的生效范围、取消、进度提示与离线验证见[网关错误恢复](gateway-recovery.md)。桌面端策略不通过上述YAML字段调整。

桌面模型请求现为[不限时等待](unlimited-model-wait.md)：HTTPX timeout=None，取消客户端连接/读取/写入/连接池及总响应时间截断，并移除桌面外层任务时间预算。YAML timeout仅继续约束其他有限时调用入口；服务商主动断连或网关超时仍会报告错误，停止操作不保证立即打断阻塞网络。


客户端读取兼容响应中的：

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "模型回答"
      }
    }
  ]
}
```

并转换为内部消息：

```python
Message(role="assistant", content="模型回答")
```

## 5. 调用示例

命令行接入、配置校验、失败恢复与学习方法见 [`step-02-live-model.md`](step-02-live-model.md)。默认入口仍是 Echo，必须显式加 `--mode live`。

```python
from pathlib import Path

from newspaper_agent.model.factory import create_model_client

client = create_model_client(
    "deepseek",
    config_path=Path("model_config.yaml"),
)
response = client.complete([
    Message(role="user", content="请总结今天的新闻"),
])
```

调用前必须设置 `DEEPSEEK_API_KEY`。测试环境应使用 `httpx.MockTransport`，不要在测试中访问真实服务。

## 6. 安全边界

- API key 只从 `api_key_env` 指定的环境变量读取。
- 异常信息不包含 Authorization header 或 API key。
- API key 不得放在 URL 查询参数中。
- 不读取或复制 Codex 的 `auth.json`、`cc-switch.db` 或 tcodex 内部认证文件。
- 配置加载器已校验 URL 为 HTTP(S)，并拒绝 URL 中的凭据、查询参数和片段。
