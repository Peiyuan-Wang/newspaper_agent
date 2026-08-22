# 模型通信协议

## 1. 目标

`newspaper_agent` 使用 **OpenAI-compatible Chat Completions** 作为模型通信边界。DeepSeek、OpenAI GPT 以及用户自己的兼容服务都通过同一个 `OpenAICompatibleClient` 调用，`AgentLoop` 不感知具体 provider。

实现位置：

```text
src/newspaper_agent/model/client.py
src/newspaper_agent/model/factory.py
config/settings.py
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
- `temperature`、`max_tokens`、`timeout`：请求参数。

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
.venv\\Scripts\\python -m newspaper_agent
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
## 3. HTTP 请求

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

DeepSeek 和 OpenAI GPT 使用相同的请求协议，只替换 `base_url`、`model` 和 API key 环境变量。

## 5. 轻量鲁棒性策略

通信层提供有限重试，不承诺无限恢复或生产级高可用：

- 默认最多尝试 3 次。
- 网络传输错误、408、429、500、502、503、504 会重试。
- 400、401、403、404 等请求、认证和配置错误立即失败。
- 默认退避为 1 秒、2 秒、4 秒，并受 `max_retry_delay` 限制。
- 429 响应带有合法的 `Retry-After` 时优先使用该值，但仍受最大退避时间限制。
- 重试只适用于当前无副作用的文本 Chat Completions 请求。
- 当前不实现无限重试、断路器、请求队列或流式响应。

可在 provider 配置中调整：

```yaml
max_attempts: 3
initial_retry_delay: 1.0
max_retry_delay: 8.0
```

这些策略用于应对短暂网络波动、限流和服务端临时故障；API key 错误、模型名称错误和 URL 配置错误不会通过重试掩盖。


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
- 用户自定义 URL 只能使用 `http://` 或 `https://`，并应在产品边界层做校验。
