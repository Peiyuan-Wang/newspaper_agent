# newspaper_agent

项目完整结构与当前实现见[架构与实现总览](docs/architecture.md)。总览原位维护现状；每次修改的目的和验证结果另存于docs下的时间命名Markdown。

新闻使用说明：[当天/24小时与公开信源](docs/today-news-and-expanded-sources.md)、[细分兴趣、内容标签与轮换覆盖](docs/personalized-news-routing.md)。

[紧凑评分与新闻K/V缓存](docs/efficient-news-scoring.md)：按预算分批、最多两路评分、逐条保存和只补缺项；重新整理复用有效解读，文风变化复用长文读取，并显示服务端已报告的Token用量。应用缓存不冒充模型内部Transformer KV张量。

可选[上下文兴趣探索](docs/contextual-exploration.md)是有限概念邻域的兼容路径，仅在语义画像未启用且无公共栏目配额时适用，不与向量排序重复叠加。

[账号记忆与恢复码](docs/local-account-memory.md)：可选保持登录30天，支持一次性本机恢复码；邮箱服务未接入。[简报数量](docs/news-output-limit.md)：点击“最多10条”输入1—100，按账号保存。

[四信号推荐](docs/multisignal-ranking.md)提供个人推荐、大事件、今日热榜，卡片赞踩/撤回、原文打开计数及账号权重设置。热度目前只是可见报道覆盖代理、可信度是来源参考，不是全网热搜或事实核验。三栏共享解读数量预算。[分层多兴趣语义画像](docs/semantic-interest-profile.md)保留用户命名的大类，在类内合并相近细分兴趣，用本地384维编码器直接匹配新闻并按赞踩/弱点击有界更新；不训练QKV、不做token解码。在“兴趣与领域→兴趣分类与语义画像”编辑、准备公开模型和重建，不需额外API Key。

轻量、provider-neutral 的新闻研究 Agent 骨架，面向 websearch、短期/长期记忆、MCP 和可替换模型适配。

## 第一版可用入口

[分层写作与个性化编辑](docs/layered-writing-framework.md)：独立编辑原则、确认读者模型、本篇视角和按类型示例；机械校验与可选测试语义评审分开，正常简报不新增评审请求。写作规则变化会重写解读，不重评兴趣分。

[AIHOT免费信源](docs/aihot-free-sources.md)：登记其18个公开RSS，16个已验证启用、2个暂停待复核，涵盖官方研究、行业媒体与个人技术观察。无需搜索服务密钥，评分和解读仍消耗所选模型额度。

简报采用[新闻库AI评分＋最终集合解读](docs/two-stage-news.md)：全量候选和兴趣分按账号保存，同批估计重要性，四指标分栏后仅解读最终集合，标题与分数直接显示、解读折叠，复用评分/风格缓存；首次大库评分可能有较多模型请求。信息获取可选[订阅简报](docs/subscription-news.md)或主动检索；订阅有独立公共来源名额，可选运行期间每30分钟无模型采集。

前十条使用[读者专属短解读](docs/personalized-news-brief.md)：按细分兴趣选择重点，中文正文最多180字符；“兴趣与领域”可分别设置不同领域的知识水平和阅读重点。格式、长度或引用不合格时反馈修订，不直接截断。

[新闻核心前置与校验恢复](docs/editorial-lead-and-source-expansion.md)：首句以具体核心对象为主语，先变化再指标；归档失败不挡住合格解读。文档同时列出论坛、政府RSS与微信公众号/X/Instagram/Reddit的接入边界，未擅自启用付费社媒。

[正文阅读与新闻库](docs/news-reading-and-library.md)：入选条目补读并缓存原网页，长文分段覆盖、失败断点续读；解读用连贯短段落。侧栏“新闻库”按公司、模型和研究方向浏览历史，具体事件可查看进展与原文证据。无法取正文时明确标为摘要，不伪装全文阅读。

最新桌面改进：独立聊天卡片、执行进度、停止/重试、并行来源、AI专业RSS、栏目与时间筛选、最近对话恢复。参考AIHOT配置与Pi事件机制实现，详见 [升级说明](docs/desktop-runtime-upgrade.md)。

**桌面界面：** 双击项目根目录的 `启动知闻.cmd`，进入「知闻」工作台：今日简报、逐条评分、研究对话、兴趣与领域、反馈与改进、信息来源与连接设置。首次使用创建本机账号；已有 CLI 账号可直接登录。详见 [桌面使用说明](docs/desktop.md)。当前为依赖项目虚拟环境的桌面源码运行版，尚未打包为独立安装程序。

桌面模型接入无需手改配置：可视化保存多份地址、模型、协议和密钥，按本机账号隔离，密钥使用 Windows 加密。重开后选择连接并启用即可，详见[连接档案](docs/model-connections.md)。简报阅读采用分栏、短摘录与依据链接，技术检索说明默认折叠。

对话可选「自动 / 仅对话 / 整理兴趣建议」。遇到问题可看「请求详情」或执行仅含固定测试数据的工具回环测试，详见[对话可靠性与现场排查](docs/conversation-reliability.md)。简报支持使用当前模型重新整理已保存证据，不覆盖原报告。

已加入真实来源检索、本地账号、共享/个人来源、可撤销兴趣反馈，以及带证据的多种输出策略。完整使用方法见 [第一版使用手册](docs/research-v1.md)。

```powershell
# 项目根目录；第一次创建账号并设置自己的本地口令
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex signup
# 无需模型或搜索密钥，真实检索已接入的新闻源
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex search "latest news" --domain news
# 已在同一终端设置模型密钥后，进入自然语言聊天
.\.venv\Scripts\python.exe -m newspaper_agent research --user alex --provider custom_6sol chat
```

当前Crossref、BBC、美联储真实联网验收通过；arXiv限流、PubMed网络失败，Tavily和真实模型验收按用户选择延期。无Tavily时，搜索覆盖专业数据库与已接入RSS，不是任意网页的全网覆盖。下文保留各学习阶段的入口。

## 快速开始

本机项目路径为 `C:\Users\13689\Documents\newspaper_agent`，迁移与环境说明见 [`docs/local-development.md`](docs/local-development.md)。

本项目固定使用 Python 3.11，并建议使用 PyCharm 打开和维护仓库。

在 PyCharm 中：

1. 打开 `C:\Users\13689\Documents\newspaper_agent`。
2. 将 Project Interpreter 设置为 `.venv` 中的 Python 3.11。
3. 使用项目根目录的 `requirements.txt` 安装依赖。

命令行等价操作：

```bash
python3.11 -m venv .venv
.venv\\Scripts\\python -m pip install -r requirements.txt
.venv\\Scripts\\python -m newspaper_agent
.venv\\Scripts\\python -m pytest -q
```

项目当前提供 OpenAI-compatible Chat Completions 和 Responses 文本通信，支持 DeepSeek、OpenAI GPT 和自定义兼容 provider。在 `model_config.yaml` 中通过 `wire_api: chat`（默认）或 `wire_api: responses` 选择协议；`custom_6sol` 已设置为 responses。`temperature`、`max_tokens` 填 null 表示不发送该参数。通信格式、模型配置以及完整 API key 配置方法见 [`docs/model-communication.md`](docs/model-communication.md)。

## 最快开始：配置 DeepSeek API key

当前命令行入口默认使用离线 Echo。显式使用 `--mode live` 可调用真实模型，第二步学习与配置见 [`docs/step-02-live-model.md`](docs/step-02-live-model.md)。
第一步学习与运行方法见 `docs/step-01-foundation.md`：

```powershell
.venv\Scripts\python.exe -m newspaper_agent --message "研究人工智能新闻"
.venv\Scripts\python.exe -m newspaper_agent --interactive
```

真实模式的默认 provider 是 DeepSeek。在 PyCharm 的运行终端中执行：

```powershell
$env:DEEPSEEK_API_KEY = "替换为你自己的 DeepSeek API key"
```

然后在**同一个终端窗口**运行：

```powershell
.venv\Scripts\python.exe -m newspaper_agent --mode live --provider deepseek --interactive
.venv\\Scripts\\python.exe -m pytest -q
```

如果使用 PyCharm 的 Run Configuration：

```text
Run -> Edit Configurations -> Environment variables
```

添加：

```text
DEEPSEEK_API_KEY=替换为你自己的 DeepSeek API key
```

程序会根据 `model_config.yaml` 中的 `api_key_env: DEEPSEEK_API_KEY` 读取 key。真实 key 只通过环境变量注入，不要写入 `model_config.yaml`、README、源码、日志或 Git。OpenAI GPT 使用同样方式配置 `OPENAI_API_KEY`。

## 第三步：工具调用

```powershell
# 无密钥，固定的 12+30 工具闭环演示
.venv\Scripts\python.exe -m newspaper_agent --mode demo
# 真实模型显式启用安全本地 add_numbers 工具
.venv\Scripts\python.exe -m newspaper_agent --mode live --provider custom_6sol --tools --interactive
```

教学入口工具调用默认关闭；`--tools` 启用本地加法，`--information` 另外启用虚构离线检索工具。真实搜索使用上述 `research` 入口。
代码解读、验收方法及调用上限见 [第三步学习文档](docs/step-03-tool-loop.md)。

## 第四步：专业信息来源基础层

```powershell
# 无密钥、不联网的检索 → 读取证据 → 回答演示
.\.venv\Scripts\python.exe -m newspaper_agent.sources demo --domain academic --query "AI论文"
```

此命令仍是离线教学演示；真实来源、个人发现和共享发布已在独立 `research` 入口接入。
运行方法与代码框架见 [第四步学习文档](docs/step-04-source-foundation.md)。

## 目标规划与研究参考

[人类理解导向的适配规划](docs/goal-planning-human-centered-synthesis.md)记录当前能力审计、信息筛选、证据卡片、分层解释与效果评估的候选路线。
[方法论原文](docs/human_centered_knowledge_synthesis_methodology.md)仅供参考，不是唯一规范；轻量筛选、证据卡片和解释策略已在 [第一版](docs/research-v1.md)实施，复杂图谱、多Agent和模型训练继续暂缓。

## 项目开发规则

AI 开发和协作规则集中在 [`CLAUDE.md`](CLAUDE.md)。重要约定如下：

- 每实现一个功能，必须创建或更新对应的 Markdown 文档。
- 对已有功能的更新必须同步维护相关文档。
- 使用 PyCharm 管理项目虚拟环境。
- 项目固定使用 Python 3.11。
- 实际运行和测试必须使用项目根目录 `.venv` 中的 Python。


- `src/newspaper_agent/agent_core/`：消息、状态、循环和协议。
- `src/newspaper_agent/model/`：provider-neutral 模型客户端接口。
- `src/newspaper_agent/tools/`：本地工具及 websearch 适配边界。
- `src/newspaper_agent/sources/`：统一来源接口、目录、路由与离线适配器。
- `config/sources/`：来源元数据，不保存密钥。
- `src/newspaper_agent/mcp/`：MCP 客户端与工具注册边界。
- `src/newspaper_agent/memory/`：短期窗口和长期记忆服务。
- `src/newspaper_agent/database/`：SQLite 连接、模型和 repository。
- `prompts/`：可审查、可替换的 Markdown Prompt。
- `docs/`：架构和约定。
