# 第一版研究助理

日期：2026-10-13。状态：第一版本地产品已实现；真实模型验收按用户选择延期，真实来源验收见文末。

第一版在原单 Agent 与 provider-neutral 模型客户端之上组装真实来源、独立本地账号、共享与个人来源、兴趣反馈、证据筛选和多种解释方法。旧 echo/demo/information 教学入口保留。真实来源使用单独的 config/sources/live.yaml，不混入虚构教学资料。

产品提供 CLI 和 [知闻桌面界面](desktop.md)，两者共用本机数据。账号保护应用内访问；电脑管理员和可读取 SQLite 文件的人仍可接触本机数据。它不是部署到互联网的多租户服务。个人偏好、反馈、报告与个人来源按账号隔离；共享来源只共享经过发布的来源元数据，不共享私人查询、兴趣或报告。

## 1. 启动与第一次真实检索

在 PyCharm 的 Terminal / PowerShell 中运行：

```powershell
cd C:\Users\13689\Documents\newspaper_agent
# 第一次创建自己的账号，口令隐藏输入，至少8字符；首个账号是本机共享来源管理员
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex signup

# 无模型、无搜索密钥：真实检索新闻或金融官方公告，并保存个人证据报告
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex search "latest news" --domain news
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex search "latest news" --domain finance

# 学术检索：arXiv、PubMed、Crossref；失败来源会单独提示，其余可继续
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex search "machine learning" --domain academic --kind paper
```

也可使用等价入口 `python -m newspaper_agent.research`（仍用项目 .venv 的 Python）。命令默认不调用模型：只展示检索到的原始片段、来源、日期、内容级别和限制，不伪造综合结论。注册/登录口令与模型密钥不同。运行数据库为项目 `data/research.sqlite3`，不要上传。

多个本地账号分别 signup，各自登录。首次账号管理员初始化仅适合本机，不把此 CLI 直接改成无保护的公网服务。全局选项 `--user`、`--database`、`--provider`、`--config`、`--catalog` 放在子命令之前。

## 2. 接上已有模型开始自然语言聊天

在同一终端配置已有 provider 所需的环境变量；例如：

```powershell
$env:NEWSPAPER_AGENT_CUSTOM_API_KEY = "在本机填入你的模型密钥"
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex --provider custom_6sol chat
```

也可使用 `--provider deepseek` 和 `DEEPSEEK_API_KEY`。不会读取 Codex/cc-switch 内部认证。不要把真实密钥发到聊天、写入 YAML 或 Git。`search ... --model` 只额外请求一次模型综合；`chat` 允许模型通过工具多轮检索/反馈，费用也会相应增加。

可尝试：

- “我关注 robotics 论文，请帮我找相关研究。”
- “我关注 embodied AI，但刚才没有搜到；请修正兴趣并补查。”
- “不要过滤 robotics，这类新闻对我有用。”
- “太长了，以后请简短回答，但保留适用条件。”
- “请从 https://某个实际公开网站 发现更多新闻来源。”

每次写入的事件/来源 ID 会显示。系统只从用户明确提到的主题调整偏好；有歧义时需要更清楚的表达。反馈分为兴趣缺口、筛选缺口、来源/查询缺口，用户质疑事实不直接变成“来源已证伪”。模型仍可能误解意图，事件记录和撤销用于纠正。

收到漏检反馈时，模型被要求记录反馈、修正关键词、重查，再视覆盖不足寻找来源。这是通过既有工具循环执行的策略，不是每句话都强制联网，也不保证单条反馈能完整学会一个专业领域。

聊天命令：

| 命令 | 作用 |
|---|---|
| `/profile` | 当前个人兴趣、排除词、领域、输出偏好 |
| `/interest robotics` | 明确增加兴趣，返回可撤销事件 ID |
| `/exclude sports` / `/unexclude sports` | 调整显式排除词 |
| `/style brief` | 选择 brief / standard / expert |
| `/search machine learning` | 直接真实检索；是否使用模型取决于当前会话 |
| `/sources` | 当前可用来源与个人/共享候选 |
| `/discover news https://example.org` | 在指定公开网站发现候选（请换实际网站） |
| `/events` / `/undo 事件ID` | 查看或撤销个人反馈；旧事件不得覆盖新偏好 |
| `/reports` / `/report 报告ID` | 查看自己保存的报告 |
| `/reset` / `/exit` | 重置会话/退出；保留已保存的偏好与报告 |

暂不配置模型时可运行 `... research --user alex chat --no-model`，使用这些斜杠命令。普通自然语言聊天会提示需要模型，而不会冒充已调用模型。

## 3. 按领域选来源与筛选机制

默认真实目录：`config/sources/live.yaml`。

| 领域 | 默认来源 | 实际覆盖 |
|---|---|---|
| 学术 | arXiv、PubMed、Crossref | 论文元数据和可用摘要；不抓付费全文 |
| 医学 | PubMed、Crossref | 文献检索；不是临床数据库全集，也不等于医学事实核验 |
| 金融 | Federal Reserve 官方 RSS | 联储发布的信息，不是完整行情/财报/中国金融覆盖 |
| 新闻、通用 | BBC World RSS；可选 Tavily | RSS仅当前feed；Tavily有密钥时扩展到网页搜索 |
| 个人细分主题 | 已启用的个人/共享 RSS + 兴趣关键词 | 五个上层领域内的具体兴趣，例如机器人、半导体政策 |

未配置 Tavily 时会明确显示覆盖限制；中文问题可由模型改写为英语检索词。无模型命令不自动翻译，查询英文来源时建议使用英文。第一版通用领域不是全网索引；搜索零结果也不代表事件不存在。

来源路由优先领域匹配和显式启用来源。研究聊天取证每次最多3源、5条最终结果，可用词法主题门槛；其过程记录不嵌入报告。简报入口则走[两阶段新闻流程](two-stage-news.md)：采集范围内所有候选按账号存库、AI语义兴趣评分、前十条个性化解读，不能把聊天词法排序误当作新闻兴趣分。报告只保存紧凑采集摘要和新闻库引用。

泛化查询 `latest news` / “最新消息”等会使用首个明确兴趣词，并显示改写提示。更复杂的多兴趣检索由模型规划，最多三次检索。失败来源在当前进程暂缓60秒，不会因一个用户的临时网络失败暂停所有人的共享来源；`/reset` 清除该缓存。

## 4. 两条自进化流程

个人流程：聊天反馈 → 明确偏好修正 → 重新检索 → 从用户网址或当前结果站点发现 RSS/Atom → 验证 feed → 在个人范围启用。每轮最多两次发现、每次最多检查两个站点、最多六个候选；当前聊天工具最多验证启用这些候选。只支持现有 RSS 适配器，不自动下载插件、生成并执行 Python 代码或任意 API 适配器。

管理命令也可手动执行相同流程：

```powershell
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex source add --name "我的专业来源" --domain news --url "https://实际网站/rss.xml"
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex source validate source_返回的ID
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex source enable source_返回的ID
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex source suspend source_返回的ID
```

直接 `discover` 命令只登记候选；聊天 `discover_sources` 工具会继续验证并个人启用。验证只说明端点可以解析为 feed，不保证所有内容真实或分类准确；保留停用/归档入口。现阶段不支持登录网站、内网地址或带令牌的 RSS；“私密”指这个账号专用的来源配置与使用记录。

共享流程：用户个人发现 → 用户明确贡献 → 管理员验证 → 发布共享目录 → 所有用户可路由使用。

```powershell
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex source share source_个人ID
# 以下使用管理员账号和新返回的共享候选ID
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex source validate source_共享候选ID
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex source publish source_共享候选ID
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex sources --pending
```

共享只复制来源名称、URL、领域和适配器；私人兴趣、反馈、查询、报告与原个人来源不会自动公开。模型没有共享发布权限。管理员通过来源接口也不能读取他人私密来源和报告。共享扩充采用人工发布门槛，个人发现可自动进行，因此不是无人监管的全自动共享系统。

## 5. 输出与方法比较

输出支持 baseline（受共同JSON契约约束的直接概括）、prompt（清晰简洁指令）、structured（按主张/条件/证据组织），默认 structured。三者复用同一模型接口与渲染器，未假定某个方法一定最好。

```powershell
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex --provider custom_6sol search "machine learning" --domain academic --model --method structured
# 对同一份已保存证据运行三个方法；最多额外三次模型请求，不重复联网搜索
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex --provider custom_6sol compare report_报告ID
# 已保存知识对象换阅读层级：不调用模型，不重新检索
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex report report_报告ID --style expert
```

知识对象保存主张、条件、不确定性和逐字引文锚点；代码检查 ID 和引文是否在片段中。`anchor_checked` 不等于事实正确或结论得到语义支持。JSON/引文检查失败会展示原始证据清单及问题代码，不显示失败模型的自由文本为核验结论。

brief/standard/expert只展示选定层级；保留改变结论的条件。已保存知识对象可以重新渲染，持续积累的报告形成最小个人研究档案；当前不提供向量检索、跨报告自动知识图谱或旧报告自动事实更新。

评测材料在 `evals/synthesis-cases.json`，12个人工虚构案例 × 三种策略 = 36次离线契约回放。它验证条件保留、锚点和降级，不证明真实模型或人类理解效果。后续真实比较应记录阅读时间、复述/应用题、条件遗漏、人工支持性核对与费用。

## 6. 预算、会话与隐私

- 真实检索默认不调用模型。聊天每轮最多六次模型请求（含最终综合），工具循环最多五轮/八次调用；单请求输出上限默认1800 tokens（若原配置更小则沿用），一次尝试、最长45秒的模型请求配置。
- 每轮模型上下文上限30000字符，包含工具定义和协议回放；累计时间预算240秒，在发起下一次模型请求前检查。这不是可强制中断所有底层请求的硬墙钟超时。
- token实际用量当前未由客户端暴露，报告记录请求数、字符数和耗时，tokens为null，不伪造计费统计。
- 每轮在独立工具对话中工作；下一轮保存用户问题与最终展示答案，保留最近三轮，避免只改屏幕却把旧草稿作为历史。`/reset`不会删除持久化报告。
- 个人反馈和来源变化立即生效；后续模型失败也会显示已写入的事件/来源ID，不能宣称全部自动回滚。个人偏好可撤销，来源可停用。
- 账号数据不在用户之间共享，但启用真实模型会把当前必要兴趣/来源配置、问题与证据发送给你选择的模型服务；搜索服务收到具体检索词。它们不是完全离线私密计算。模型密钥只来自环境变量。
- 网络使用固定公网IP连接与原域名TLS校验，拒绝私有地址、重定向、Cookie和隐式代理；RSS和发现链接不能含查询参数/凭证。细节见 [真实来源说明](live-sources.md)。

## 7. 文件分工与后续代码解读顺序

1. `research/cli.py`：注册、登录、命令入口与终端交互。
2. `database/research_repository.py`：每个用户的资料、反馈、来源状态和报告。
3. `research/service.py` + `sources/catalog.py`：选择可见来源、发现候选、执行检索和审计。
4. `sources/network.py` + `live_adapters.py`：实际HTTP请求、来源格式与统一证据。
5. `sources/filtering.py`：去重、兴趣排序、显式排除与原因。
6. `research/tools.py` + 既有 `AgentLoop`：模型如何触发上述功能。
7. `research/session.py` + `model_runtime.py`：一次会话的组装、预算、历史与报告保存。
8. `synthesis/` + `prompts/`：主张提取、引用检查、分层输出与对照实验。

无需新增框架依赖。沿用 Python 3.11、httpx、PyYAML、SQLite、dataclass；核心循环不绑定搜索服务。旧账户分组数据库不自动迁入新认证数据库。

## 8. 验证与剩余边界

```powershell
.\.venv\Scripts\python.exe -m compileall src tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m newspaper_agent.synthesis.evaluation
# 明确联网：三个公共来源，各请求一次，不调用模型/付费服务；临时测试数据自动清理
.\.venv\Scripts\python.exe -m newspaper_agent.research.smoke
```

2026-10-13真实验收：Crossref、BBC World RSS、Federal Reserve RSS均返回真实资料并完成报告保存/读取；无真实模型或付费API请求。arXiv此前返回429，PubMed请求失败，本轮没有反复重试，不能标为真实连通验收通过。Tavily只做离线参数/认证测试；用户选择稍后配置。

自然语言模型链路在Chat与Responses两协议的模拟HTTP中验证了检索→综合→保存→下轮历史一致性；漏检反馈→个人发现启用→共享发布隔离也有集成测试。真实模型效果、中文跨语种检索质量、各领域覆盖质量与人类理解收益仍需后续实际验收。

最终离线验收：247项pytest通过，compileall通过，36/36固定资料回放通过。新入口help与旧demo均通过冒烟检查。本次未创建Git提交，保留此前未提交修改；全仓diff检查仍有此前model_config.yaml末尾空行提示，与本次实现无关。
