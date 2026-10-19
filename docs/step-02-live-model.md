# 第二步：真实模型接入命令行

## 目标与状态

CLI 已接入 OpenAICompatibleClient，支持单轮和多轮，DeepSeek、OpenAI、custom 共用客户端。默认仍是离线 Echo，显式 `--mode live` 才发送请求。用户已报告 custom_6sol 的 Responses 真实连接测试成功。YAML 现支持 `wire_api: chat/responses`（默认 chat）；参数省略与协议细节见 [模型通信协议](model-communication.md)。其他 provider 尚未在本轮进行真实验证。

## 使用方法

在仓库根目录的 PowerShell Terminal 执行，不在 Python Console 执行：

```powershell
# 离线，无需 key
.venv\Scripts\python.exe -m newspaper_agent --mode echo --message "你好"

# 在当前终端设置你自己的 key，再启动真实单轮
$env:DEEPSEEK_API_KEY = "替换为你自己的 key"
.venv\Scripts\python.exe -m newspaper_agent --mode live --provider deepseek --message "用两句话解释 AgentState"

# 真实多轮
.venv\Scripts\python.exe -m newspaper_agent --mode live --provider deepseek --interactive
```

OpenAI 使用 OPENAI_API_KEY，custom 使用 NEWSPAPER_AGENT_CUSTOM_API_KEY。真实 key 只在本机环境变量或 PyCharm Run Configuration 设置，不发到聊天、不写入配置文件。程序不自动读取 `.env`。

| 参数 | 行为 |
|---|---|
| --mode echo/live | 默认 echo；live 调用真实模型 |
| --provider NAME | live 中选择 YAML provider；省略时使用 default_provider |
| --config PATH | 默认当前工作目录下的 model_config.yaml；外部目录启动时传绝对路径 |
| --message TEXT | 单轮输入，默认 newspaper_agent ready |
| --interactive | 多轮模式，优先于 --message |

echo 配合 --provider 会报错，避免误以为调用真实模型。custom 的 example.com/example-model 是占位符，使用前替换。加载器校验 YAML 结构、HTTP(S) URL、环境变量名、模型名和数值范围，拒绝不支持的字段（包括 api_key）。加载器已移入 model/settings.py，避免安装包后依赖仓库外的 config 目录；旧导入保留兼容。

## 学习顺序

```text
main 解析参数
 → create_model_client 读取 YAML 和 key 环境变量
 → 创建 OpenAICompatibleClient
 → AgentLoop.run 将历史和当前输入交给 complete
 → wire_api=chat：POST {base_url}/chat/completions；responses：POST {base_url}/responses
 → 按协议读取 choices[0].message 或 output 中的 assistant 文本，转换成 Message
 → 更新会话并显示回复
```

1. 读 app.py，对比 EchoModel() 与 create_model_client(...)：循环相同，模型实现不同。
2. 读 factory.py 和 settings.py：YAML 保存非敏感连接参数，环境变量保存 key。
3. 读 client.py 的 complete：Message 列表转为 JSON；Authorization 头携带 key；响应经 _parse_response 转回 Message。
4. 读 test_live_cli.py：MockTransport 模拟远程服务，验证 CLI 到 HTTP 请求和输出的完整链路，不消耗额度。
5. 阅读失败处理：启动配置错误结束程序；单轮网络请求失败结束；多轮失败提示后继续。

## 错误与边界

参数、配置和缺 key 的启动错误退出码为 2。单轮请求失败退出码为 1。HTTP 错误显示状态码和排查方向：401 检查 key、403 检查权限、404 检查地址/模型、429 检查限流与额度；网络错误提示连接或超时。保留已有有限重试。

AgentLoop 先构造候选消息，成功后才把本轮 user 和 assistant 写入原会话。失败不会污染历史，交互模式可重新输入。该策略不能取消服务端已执行的请求，也不能保证失败重试没有费用。

交互模式显示 provider/model，支持 /reset、/exit、空行忽略和输入期间的 EOF/Ctrl+C。普通聊天模式仅接受 assistant 的字符串 content；第三步已支持显式 `--tools` 的自定义函数闭环，见 [第三步文档](step-03-tool-loop.md)。流式、搜索和多模态尚未接入。会话保存在内存，完整上下文会发送给选定 provider。自定义 URL 禁止包含凭据、query 和 fragment，建议使用 HTTPS。CLI 不显示 key、HTTP 响应正文或原始 YAML 解析错误。

## 验证

```powershell
.venv\Scripts\python.exe -m compileall src tests
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m newspaper_agent
```

测试覆盖三个 provider 的 CLI→factory→模拟 HTTP→输出、包外导入、无效配置、缺 key、401、非文本响应和失败后的会话恢复。真实验收：设置 key 后先运行单轮，再在多轮中输入“记住代号是蓝鲸”和“刚才的代号是什么？”，确认收到真实回复且携带上下文。尚无搜索能力，不能据此验证当天新闻。

格式依据：[官方 OpenAI Chat Completions 文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。第三方 provider 的可用模型与参数以对应服务为准。
