# 项目架构与实现总览

本文描述 newspaper_agent 当前实际实现，是项目结构和运行行为的统一入口。维护时直接改写受影响章节，不追加开发日记、版本流水或“新增/修复了什么”。每次修改的目的、范围和验证结果单独记录在 docs 中的时间命名文件。

## 1. 产品定位与运行方式

项目是本机运行的个人信息与研究助理，桌面名称为“知闻”。目标是根据用户领域和兴趣采集信息、筛选证据、组织带引用的中文解读，并通过明确反馈调整个人档案和来源。

- 主要入口：PySide6桌面应用，包含今日简报、研究对话、兴趣与领域、反馈与改进、信息来源、连接设置。
- 命令行入口：research提供研究业务和账号操作；普通app入口保留Echo、固定工具演示、真实模型对话及离线信息检索教学功能。
- 开发环境：Python 3.11、src包布局、PyCharm；运行、测试、安装使用项目根目录.venv。
- 运行形态：依赖项目虚拟环境的桌面源码运行版，不是已打包的独立安装程序，也不是互联网多租户服务。

本机登录可记住账号名；自愿勾选“保持登录30天”用Windows DPAPI保护的设备令牌恢复，不保存明文密码。账号安全入口可验证密码后生成一次性恢复码、取消记住；忘记密码以事先保存的码重置并撤销所有设备令牌，不删除业务资料。没有邮箱注册、邮件发信、云账号或资料同步服务。详见[本机账号记忆](local-account-memory.md)。

```powershell
.venv\Scripts\python.exe -m newspaper_agent.desktop
.venv\Scripts\python.exe -m newspaper_agent research --help
.venv\Scripts\python.exe -m newspaper_agent --mode demo
```

桌面也可通过项目根目录“启动知闻.cmd”启动。默认本地数据位于data/research.sqlite3。

## 2. 分层结构与依赖边界

```text
桌面 desktop / 研究命令行 research.cli
                  │
             ResearchSession              组装业务流程与最终回答
        ┌─────────┼────────────┐
        │         │            │
    AgentLoop  ResearchSources  SynthesisService
        │         │            │
    ResearchTools │        校验与有限修正 → 渲染
        │         │            │
        └─ 来源发现/检索 ────────┘
                  │
        来源目录 / 适配器 / 安全HTTP / 本地筛选

各模型调用 → BudgetModel → OpenAICompatibleClient → 用户选择的服务
各持久化操作 → database仓库边界 → SQLite
阶段事件与取消 → RunControl → Qt Job信号 → 界面
```

agent_core不依赖Qt、具体搜索服务或模型SDK，只依赖消息、协议和工具执行边界。research组织业务；sources、model、synthesis各自承担取证、通信、综合。database承担账号范围和事务边界。桌面负责输入、展示、任务调度，不把界面文本直接等同于模型会话历史。

| 目录 | 实际职责与主要文件 |
|---|---|
| agent_core/ | messages.py定义消息/工具调用/状态；protocols.py定义接口；loop.py执行模型—工具循环；runtime.py提供阶段事件与协作取消 |
| research/ | session.py组装搜索、聊天、档案建议、重新整理；news_pipeline.py合成四指标分栏并按共同数量解读；efficient_scoring.py按预算组批、两路评分、逐条验收保存和只修复缺项；importance_scoring.py校验重要性量表；article_reading.py调度正文缓存与独立于文风的分段断点；service.py管理来源/证据；tools.py绑定业务工具；subscriptions.py采集订阅；model_runtime.py管理线程安全预算和usage |
| sources/ | contracts.py定义来源与证据契约；catalog.py加载和路由目录；live_adapters.py实现真实适配器；query.py规范查询；filtering.py筛选；time_windows.py控制香港当天/滚动24小时；annotations.py提供原文定位的有限词汇标签；network.py约束网络访问 |
| model/ | client.py调用Chat Completions或Responses；tool_wire.py编码消息和解析原生工具调用；settings.py加载配置；diagnostics.py分类错误并限量读取响应 |
| synthesis/ | service.py生成/修正知识对象；validation.py验证结构和引文；rendering.py生成Markdown；editorial.py管理栏目；evaluation.py运行固定合成案例 |
| database/ | research_repository.py管理账号/档案/事件/来源/报告；news_repository.py分开保存新闻、AI兴趣分与单条解读；news_feedback.py管理账号赞踩、打开、视口曝光、分栏权重及重要性缓存；desktop_repository.py管理历史评价/会话/上下文设置；recommendation_repository.py保存私有探索设置、选择概率与明确反馈；model_connections.py保存加密连接；feed_repository.py保存原始订阅池 |
| desktop/ | app.py负责页面与后台任务；chat_view.py管理聊天卡片；rich_text.py显示富文本；model_settings.py管理连接表单；interest_editor.py编辑细分兴趣及阶段目标；theme.py提供视觉样式 |
| tools/ | 白名单工具注册、参数校验和执行；包含本地计算、搜索及教学信息工具 |
| memory/、mcp/、observability/ | 记忆抽象、MCP占位接口、基础日志；不能据目录存在推断其已接入主研究链路 |

prompts/存放指令，config/存放非敏感来源/版式配置，tests/存放单元与集成测试，evals/存放合成评测案例，docs/存放当前设计和独立修改记录，data/存放本机运行数据。根模块recommendation.py为兼容的离散探索策略，news_ranking.py为纯四信号合成/保守标题组/分栏预算策略，preferences.py为确认兴趣契约，semantic_profile.py为纯分层多兴趣向量/反馈更新策略。TextEncoder协议由model/embeddings.py的本地编码器实现；research/semantic_personalization.py组装编码缓存与排序，database/semantic_repository.py隔离账号存储，desktop/semantic_profile.py提供分类树和模型准备。不将Qt或模型SDK放入纯策略。

## 3. 数据契约与工具执行

- Message：role、文本、原生工具请求或工具返回关系；普通文字不是可执行命令。
- ToolDefinition：工具名称、说明、JSON参数约束；ToolCall描述模型请求调用的名称、参数和ID。
- AgentState：一次循环使用的消息状态；LoopResult返回最终回复、状态和已执行工具次数。
- SourceDescriptor：来源ID、领域、主题标签、资料类型、适配器、地址、状态与能力。
- SearchRequest：查询、领域和可选过滤，含有界结果/来源预算。
- EvidenceRecord：稳定证据ID、来源、标题、链接、片段、检索/发布时间、内容等级和溯源元数据。
- Report：markdown、knowledge_payload、issues、metrics。知识对象和证据快照是渲染与复核依据，不只保存一段展示文字。

AgentLoop将消息和已启用工具定义交给模型；通信层将原生工具调用解析成ToolCall；ToolRegistry只按已注册名称分派，验证参数后执行，工具结果以关联调用ID的消息回传。不会执行模型任意返回的Python或JSON字符串。研究聊天最多5轮、8个工具调用；业务和模型预算可更早终止。完成工具操作不会因随后模型失败自动回滚。

ResearchTools当前提供research_search、read_evidence、propose_profile、confirm_profile_proposal、record_feedback、discover_sources。档案写入、反馈和来源生命周期仍受代码校验与权限控制，不能仅靠模型声明完成。

## 4. 信息获取：订阅简报与主动检索

### 4.1 来源目录与分类

config/sources/live.yaml是实际研究来源目录；config/sources/catalog.yaml服务于独立的离线教学入口，二者不能混用。

大分类为academic、medical、finance、news、general；主题标签用于AI、agent、LLM等细分。专业来源负责领域覆盖；general来源可以跨领域采集，具体新闻是否感兴趣由AI结合用户档案评分，不用关键词兴趣硬筛选。OpenAI、Hugging Face等专门来源不视为覆盖所有行业的通用来源。

目录包含54项，其中50个RSS、arXiv/PubMed/Crossref和可选Tavily。AIHOT公开示例的18个RSS均已登记：官方/研究博客10个、媒体5个、个人/周刊3个，使用news+academic领域和ai/agent/llm主题标签，不冒充跨所有行业的general来源；详细分类与端点见[AIHOT免费信源](aihot-free-sources.md)。其中DeepMind、GitHub AI暂停，其余16个通过过一次真实读取。另有中文科技、技术社区、官方科研、物理数学报道及arXiv分类等21个公开端点，12个实测有条目、4个有效但为空、5个暂停；具体端点见[当天新闻与扩展信源](today-news-and-expanded-sources.md)。所有来源共10个暂停项，不参与检索；Tavily缺Key也不参与。原有MIT/Nature/联储/BBC保留。RSS不需要收费API，但AI评分/解读仍消耗模型额度；免费Feed不保证全文免费或可再分发，不保证所有语言和行业覆盖。

### 4.2 订阅简报流程

```text
当前账号可见且已启用的RSS来源
  → 兴趣领域路由 + 独立公共来源名额，合并不超过12源
  → 有界并行采集 → 按账号积累Feed池
  → 按来源可见性、去重、时间窗口确定候选新闻库
  → 同批AI关联/重要性估计 → 本地分层语义画像校正R → 合成R/C/I/H四指标 → 三栏共用N条去重选择
  → 按用户指定N（1—100，默认10）补读并缓存正文（长文分段覆盖）
  → 逐条自然段短解读、实体/事实归档与引用校验修正 → 折叠简报和历史新闻库
```

桌面默认选择“订阅简报”。RSSAdapter.collect先读取当前Feed最多200条，不按关键词先截出8条；不下载文章全文。每轮最多12个来源、3路并发。业务简报为兴趣路由先取最多8源，另从已启用BBC/联储/NIST/NASA/IT之家/Solidot公共白名单轮换最多4源，不受个人兴趣领域阻断；合并去重后按原路由补满12。原路由给通用来源最多2个位置、个人显式源优先，其他源争取多主题覆盖并按最久未尝试轮换；不是最终新闻排名的多样性重排。直接无模型采集调用默认不启用独立公共名额。

FeedRepository按账号、来源ID和端点保存订阅池，每个来源最多200条，以URL判重并替换新快照；15分钟内复用本地池。subscription_checks单独保存尝试时间，失败/缓存复用也参与轮换，不刷新旧池成功抓取时间。读取失败可使用旧池并标失败；停用、不可见或端点变化的来源不会借旧缓存进入当前筛选。审计只存适用/本轮/待轮换来源数与时间排除数量，不复制逐条筛选记录。

泛化查询以活跃细分兴趣（按优先级，主题+方法）及简单兴趣去重后最多12个查询辅助路由；AI评分使用完整已确认档案，显式查询作为本次重点。订阅默认30天；可选今天（香港时间）、过去24小时、近7/30/90天。今天按UTC+8自然日，24小时要求精确带时区时间戳；纯日期仅参与今天，未知/部分日期及未来时间不充数，空结果不补旧闻或误导检查模型。时间范围内候选按账号写入news_articles，不保存逐条兴趣筛选日志。四信号按栏目加权选择，默认三栏共用十条去重解读；旧式日常拓展只在无公共栏分配时沿用，不要求兴趣词逐字出现。

annotations.py给新闻添加有限的主题/方法/贡献词汇提及标签，保存标题或摘要的原文跨度、版本和mention_only标记。标签在账号新闻快照内，卡片显示最多4个，评分输入只带标签名及原片段；不代表主要贡献或事实核验，无标签不等于无关。没有全库语义标注或向量召回。标签与细分兴趣的边界见[画像与覆盖](personalized-news-routing.md)。

可勾选“应用运行时每30分钟采集订阅”：默认关闭，开启即采集，忙碌时跳过；只取资料、不调用模型、不生成报告，不替换聊天证据上下文。关闭应用后停止，重启不自动开启，不是系统常驻定时服务。

### 4.3 主动检索流程

主动检索保留ResearchSources.search路径，查询专业API及已配置搜索服务。适配器包括arXiv官方Atom查询、PubMed E-utilities、Crossref元数据、RSS和Tavily basic搜索。Tavily需要独立环境变量TAVILY_API_KEY；不存在Key时不偷偷使用付费替代服务。

单次SearchRequest最多3源、每源最多5条，单轮最多3次业务检索。SourceCatalog结合领域、资料类型、支持的过滤与来源偏好路由。query.py使用显式规则整理查询和有限同义词，不是语义搜索，也没有通用自动翻译。arXiv普通词转为all字段AND查询，近期请求按提交时间排序，不读取PDF全文。

简报主动检索入口也将返回的候选交给AI评分，保留时间与URL去重，不再把本地关键词相关度当用户兴趣分。研究聊天的research_search仍保留有界关键词取证，用于回答明确问题，不代表整库新闻排名。日期未知仍标注未知；“未搜到”不代表事件不存在。RSS仅覆盖Feed和本机历史，不等于全网搜索。

## 5. 研究对话与上下文

ResearchSession.chat在当前账号范围构造研究指令、档案/待确认建议/来源摘要和有界最近历史，使用临时AgentState执行工具链；临时工具协议消息不全部持久化到下一轮。取得证据后使用独立SynthesisService生成最终展示文本；没有证据则给普通回复或档案操作确认。

界面提供自动、仅对话、整理兴趣建议三种模式。仅对话不提供业务工具，但仍使用该聊天入口构造的账号上下文，不应宣传为“完全不发送个人档案”。独立兴趣建议模式只处理本次输入，不携带对话历史，验证后保存待确认建议。

会话history最多保留最近3对完成问答；context_history另有约8000字符选择预算。界面的上下文上限约束实际序列化模型请求，包括工具说明与原生回放，不等同于扩充历史轮数。Qt聊天卡片、错误提示、输入草稿与模型历史分离；失败重试复用本轮卡片并保留正在编辑的草稿。

快速研究在证据已取得时可结束工具循环，减少无意义的模型往返；档案建议/确认完成后也可直接生成确定性说明，不再单为确认保存结果要求模型再次回放。

## 6. AI综合、质量检查与输出

### 新闻评分与用户指定数量的解读

news_pipeline.generate_news组织评分与最终集合解读两个阶段。全量候选先按账号存库，news_feature_kv保存稳定内容指纹及精简特征，news_score_cache复用同内容/档案/模型的有效分数。efficient_scoring按实际输入/输出预算组批（最多40条），兼容客户端最多两路并发，独立诊断/加锁计数、主线程落库。fast评分Prompt使用紧凑数组返回兴趣整数、重要性五项及短引文，支持旧对象和完整JSON围栏。逐条验收、正确行即时保存；非法id/分只补该项，重要性引文失败仅使I未知，不丢弃正确兴趣分。每条初次后最多两轮缩批修复，不回传整批长草稿或保存推理。五维重要性仍按30/25/20/15/10加权，属于摘要模型估计，非事实核验。详见[紧凑评分与K/V](efficient-news-scoring.md)。

评分随新闻内容、兴趣档案、临时问题及模型连接版本复用；快照ID变化但内容未变可恢复缓存，兴趣或内容变化重评，纯表达风格变化不重评。同ID内容改变会使当前评分/重要性/正文缓存失效；旧有效分先导入缓存，保留评分语义版本，不因紧凑编码变化整库重评。认证/限流等停止派发，可识别服务错误连续三次停止，已在途结果仍保存；未取得分数明确待评分，不伪造0分。全库未评分完只显示局部预览，不确定前N或冒称完成，下次只补缺项。无模型不进行AI排名。

其次由news_ranking合成关联性R、来源可信参考C、材料重要性I、报道覆盖热度H。三栏默认R/C/I/H权重分别为个人75/15/7/3、大事件5/25/65/5、热榜5/20/10/65；未知分项不重新归一化、只计已获贡献的保守下界。大事件需公共内置源、C≥55、近7天且I≥65；24小时热榜需公共内置源、C≥45、发布时间在生成时刻向前24小时内且至少两个组织覆盖。signals.recent_24h包含恰好24小时边界、排除未来/仅日期/无时区时间；today仍保留日历含义。策略版本four-signals-v2-rolling24h，旧分栏快照不自动重排。保守URL/长标题候选组不是事实核验或完整事件聚类。三栏共享N条不同事件预算：各预留floor(N/5)大事件与热榜，其余个人，空余补个人，重复展示不重复解读。桌面“最多N条”输入1—100并按账号保存，默认10，超过10先确认费用。权重可视化保存，下次生成生效；数量/权重/赞踩不使原AI评分缓存失效，历史不改写。无公共栏分配时才沿用已启用的局部探索，不能随机化公共价值。只给最终集合补读/解读。ArticleReader优先JSON-LD/正文容器，再用article/main；arXiv摘要地址转HTML全文，不读PDF/脚本、不绕过登录付费墙。每页最多2MB、正文48000字符；12000内直接解读，长文按3500字符分块/4块一批/最多2次尝试。分段失败不标为全文已读。详见[四信号推荐](multisignal-ranking.md)及[数量与预算](news-output-limit.md)。

短解读使用SynthesisService.generate(news_brief=True)，由writing_framework.py依次组装editorial_policy.md稳定编辑原则、news_brief.md任务契约和news_examples.json按论文/新闻类型选择的虚构对照示例，不叠加研究长综合策略。reader_context.py将确认档案编译为reader_model（用途/兴趣/领域、分领域程度、表达约定）及article_lens（本次问题、类型、证据和内容等级）；新闻阅读与解读共用，不重复发送平铺reader。模型依据单篇证据选择focus：非空必须属于确认兴趣，公共事件或没有直接命中时允许为空，使用通用事实视角；兴趣不匹配不是证据不足，不强行绑定兴趣。不在评分阶段增加筛选记录或把临时视角写回永久兴趣。分领域规则仍由模型判断适用性，不是严格领域分类器。详见[分层写作框架](layered-writing-framework.md)。

正文最多2条主张，由程序合为自然段。lead_subject为具体模型/产品/方法/政策等核心对象，首条必须原样以它开头；先核心变化及有依据的用途，再讲方法/数值，分类融入句子，不固定追加阅读建议或小标题。中文目标80—140字符、上限180字符，英文700字符。保留并按任务解释pass@1等合法指标，区分百分点/百分比，不以次要跑分取代主题。本地检查字面主体开头、兴趣、篇幅、引用、套话、换行列表和重复主张，失败最多修订2轮，不截断；机械通过不证明语义正确。默认writing_framework.semantic_review=not_reviewed，无额外线上评审费用。测试可显式注入ModelBriefReviewer，按核心前置/读者相关/清晰/证据支持四维评审，将未通过意见送回同一有限修订循环；评审通过也不标为事实已核验。

brief_contract接受完整单一JSON代码围栏和引文的纯空白差异，恢复实际原文跨度后计算定位；不接受任意前后说明、重复键或改写引文。长文最终材料保留最多1800字符开篇定位和个性化原文选段，避免仅有训练细节而丢失主题。正文合格而news归档不合格时仅标metadata_pending，不更新正式索引，也不舍弃解读。检索详情显示待归档数；正文未通过提供安全原因，保留草稿和分数。正文未取得可标为摘要，分段失败本条待重试。短正文获取成功但解读失败保持body_ready，不能改成reading_incomplete；旧报告相同状态组合只纠正显示，不改数据库。ranked_items保留安全model_diagnostic，展示响应中断、输出额度、网关状态及尝试次数，不再一律归成阅读失败。缓存绑定正文、档案/模型、BRIEF_VERSION=4以及编辑/任务/修订/阅读/示例文件内容摘要和上下文版本；规则变化重写解读、不重评摘要分，原网页缓存仍复用；旧报告不自动改写。

实体与具体事件由解读同时提取news_metadata，校验名字在材料出现、事件引文连续命中、日期格式和前序ID属于本次候选；这不是语义事实核验。news_entities保存主体及标题关联提示，news_event_links保守合并主体/动作/对象/已知发生日期完全相同的事实；未知日期不跨报道自动合并。只有模型明确关联的候选事件建立后续关系，不能仅因同公司就合并。新闻库按公司/模型/方向分页、搜索、按报道时间正反序浏览，事件对话框显示进展和原文证据，不在打开页面时调用模型。既有新闻仅补标题级索引，明确区别正文主体确认；事件结构目前仅覆盖完成新解读的条目，不自动全库调模型回填，非向量语义聚簇、非完整知识图谱。

新闻库保存所有候选与原始兴趣分，重要性独立缓存；反馈残差与C/H随重排重算，报告仅快照最终集合的四项信号/推荐分及分栏权重/ID。单条解读（含修正草稿）独立保存于news_interpretations，报告只携带共同预算内的展示内容和新闻/解读引用ID。采集审计仅保留来源、查询、状态、数量等摘要，不嵌入新闻数组和逐条筛选过程。库容量不通过复制进一份报告实现，保留报告100万字符边界。

### 单条解读与研究综合的公共模块

SynthesisService接收问题、已筛选证据和白名单阅读偏好，不自行检索、登录或执行工具。最多8条证据，研究综合单条最多4000字符，新闻短解读单条最多16000字符，总片段最多16000字符；超预算整条省略，不悄悄截断关键条件。研究综合使用explanation.md及synthesis_repair.md；新闻使用分层写作装配与news_brief_repair.md，长文原文片段选择使用独立news_reading.md。可选editorial_review.md仅用于显式语义实验，不默认注入桌面流程。源正文由research/article_reading.py调度，不把完整正文和阅读历史复制进报告。

三种综合方法baseline、prompt、structured共享JSON契约：claims与follow_up；每条主张有statement、conditions、evidence_refs、uncertainty、status。引用必须使用本次来源ID，quote必须为对应snippet连续逐字原文。程序计算字符定位和SHA-256快照。

生成后验证JSON/字段/数量/状态/引用；失败时把同一问题、证据、上一稿和错误反馈交给模型修正，最多2轮修正、共3次逻辑调用。只带上一稿，不无限累积历史；网络失败、取消或全局预算不足不再触发额外修正。合规claims=[]表示资料不足，不强迫凑结论。

各轮有界草稿和校验状态保存在当前账号知识对象的validation_attempts中；每稿最多20000字符。新闻解读知识对象独立存储，研究综合仍随个人报告保存。通过后渲染有效对象；耗尽时明确未通过并保留草稿，不把草稿当正式结论。原有未保存草稿的旧报告不能被自动恢复。

通过检查的anchor_checked只表示引文锚点存在；semantic_verification仍为unverified，不代表事实正确、因果成立或论文已被同行评审。当前不是完整事实benchmark，也没有以模型自评代替真实质量评测。

研究综合的rendering/editorial按栏目和阅读层级展示重点、来源、条件与不确定性；新闻简报由desktop/news_view.py按三栏显示共同预算内的折叠卡片。AI重新整理从已存新闻读取当前账号可见内容，复用有效分数和解读，只补缺失/失效内容，另存报告，不重新搜索或覆盖旧报告；显式force_explanations=True才强制重写。解读缓存以实际reader_profile和正文文本/状态为版本，不受无关档案版本号/抓取时间抖动影响。长文原文片段缓存以信息需求、正文、阅读Prompt和模型为版本，与输出文风分开；只改表达规则复用已读事实，不重新发送全文。

## 7. 个性化与受控演化

卡片赞踩、原文打开、R/C/I/H分栏及账号权重见[四信号推荐](multisignal-ranking.md)。[分层多兴趣语义画像](semantic-interest-profile.md)将用户命名category与可演化细分向量分开：大类不编码，类内种子以同一固定384维多语言模型编码、complete-link阈值0.72保守合并；初始锚点固定，反馈后向量可变。不将语义向量当词表概率，不训练QKV或编码器。未分类旧主题独立保留，不擅自聚成大方向。

本地CPU编码新闻标题/摘要及少量主题/方法标签，直接以单位向量余弦匹配，标签不是必要条件。来源domain先粗约束，锚点相似度至少0.30；反馈最佳两类差小于0.04时跳过，否则仅在最佳类内softmax分配。基于当前有效反馈从原锚点重建，以先验3、正向0.75/负向0.35和90天半衰期更新，偏转不超过25°；有限affinity_bias处理共线负反馈。具体公式/短文本截断边界见专项文档。

完整候选AI评分后、最终集合选择前使用语义分S校正R：Δ=clip(0.25×(S-A)+同篇显式票±4,-20,20)，A为保留的原AI分。语义路径替换有限标签残差，不双重计数；未匹配不修正，C/I/H不被个人反馈训练。编码缺失/关闭/异常明确回退。默认账号开关开启，但模型需显式准备，不自动下载；正常编码离线，无额外聊天API调用。画像页可分组、查看、重建；历史报告不自动重写。

长期用户档案保存在research_profiles，而不是模型参数或旧memory模块。字段包括interests、interest_units、excluded、domains、purpose、knowledge_level、language、style、output_format、expression_rules、glossary、domain_reading_rules。interest_units最多12项，每项为主题、领域、具体问题、用途、方法、领域程度、1—5优先级及可选截止日期；香港日期超过截止日不再用于召回/评分/阅读，不删除历史。简单兴趣仍兼容；表单“细分兴趣与阶段目标”或待确认聊天建议均走同一仓库校验。

- 明确表单输入直接通过仓库校验更新个人档案。
- 聊天兴趣建议提取changes、user_quote、reason，校验原话与字段后先保存候选；确认/拒绝受当前用户及明确授权校验约束。
- record_feedback仅依据当前用户明确原话记录反馈及有限偏好调整，可通过事件与撤销入口检查。not_new、too_shallow、too_hard、source_quality、not_now仅记录重复/深度/表达/来源/暂时意图原因，不自动修改长期兴趣、排除或风格；长期变化另提待确认建议。
- AI兴趣分0–100保存在news_articles，与人工1–5分article_ratings分开。星级不训练局部探索，也不自动改写档案。明确档案变化使评分缓存失效；独立的拓展方向反馈仅影响后续探索概率，不重评原分数。
- 主卡片赞踩互斥、可撤回，按账号+规范URL跨报告去重。系统接受原文打开才累计次数，但学习只用是否打开，重复打开不刷新学习时间；显式赞踩覆盖弱点击0.1。向量路径从各5000条记录中合并最近1000个有效观测，按正/负方向重建；可替换、撤回，清除同时删除派生画像，关闭学习只保留初始向量。模型未就绪时沿用有限标签奖励1/−0.3、±15残差。均不改变用户大类或C/I/H，无点击不是负例。前台卡片≥50%可见持续1秒记录曝光，不训练CTR。历史星级与旧式bandit方向反馈独立。
- 新闻评分发送已确认兴趣、活跃单元、领域、用途、程度、排除偏好和domain_reading_rules；解读发送白名单阅读上下文，含最多30条简单兴趣与最多12个单元主题（合并去重）、活跃单元和domain_reading_rules、最多12条表达规则/术语约定，不发送全部操作/反馈历史。分领域程度优先于全局程度，不微调权重。研究聊天使用更完整的账号档案。反馈“讨论”只将选中内容放入草稿，不自动发送全部私人记录。

来源演化是受控的元数据生命周期：发现RSS候选→个人私密登记→验证→启用/停用；贡献共享需明确确认，经管理员审核发布。共享只发布来源名称、地址和领域，不发布私人兴趣、报告或订阅池。不自动生成或执行新采集代码，也没有无监督、自主扩权的“自进化”。

表达适配通过已确认阅读偏好、表达规则和术语约定影响Prompt与渲染；没有实现领域语言模型微调、自动学习全部用户写作风格或自动更新术语库。

“兴趣与领域→兴趣拓展·可选”按账号保存enabled（默认false）、epsilon（默认0.2，最多0.3）、temperature（0.02—2）、radius和learning_rate。recommendation.py以确认锚点为离散上下文，以同领域概念为共享动作特征，平滑正/负奖励估计加原始分数及反馈不足的启发式项，softmax逐位置不放回选择。radius当前是有限概念图距离：同标签0、AI或物理同组0.5，未知不探索；不是预训练语义向量或全网邻域召回。启用后独立“这个方向有用/不相关”才学习，未点/太难/星级不学习；锚点、用途和程度变更隔离旧上下文。反馈可替换、撤回、清除；跨报告同context/tag/新闻去重。保存计划不等于展示，桌面卡片渲染后标displayed；这不保证用户看过，仍须明确评价。保存的是条件抽样概率与紧凑元数据，不是逐条筛选理由，不提供完整列表的无偏反事实评测。详见[上下文兴趣探索](contextual-exploration.md)。

上述离散bandit只在语义画像未active且无公共栏分配时兼容使用；语义画像active不叠加旧随机重排。当前没有真正向量邻域采样或全网向量召回。

## 8. 模型连接、预算与故障处理

OpenAICompatibleClient通过wire_api选择Chat Completions或Responses，provider名称不决定协议。请求地址统一为base_url加/chat/completions或/responses；保存连接时规范重复的完整端点后缀。tool_wire处理两种协议的工具格式及原生回放。

桌面连接界面保存每个本机账号的名称、API地址、模型、协议、服务商链接和加密Key；密钥用Windows DPAPI保护，解密时只在构造客户端的运行期使用，不写入源码/YAML。预设连接仍支持环境变量。重启可选择已保存连接，但不会未经用户启用就发模型请求。

桌面模型HTTP与外层任务均不限时；服务商仍可能主动返回网关超时或断开。同步阻塞读取期间不能保证“停止任务”立即中断；数据块、退避、工具阶段之间会检查取消。来源抓取仍保留独立网络超时。通用客户端/CLI仍支持有限时参数，不能把桌面策略误写成所有入口都无限等待。

BudgetModel加锁控制请求次数和usage，研究聊天最多6次。新闻候选M最多2400；根据实际上下文与输出额度规划待评分批数B，评分调用上限B+max(20,2B)，每条最多初次加两轮补评；全部缓存时无评分请求。最终K=min(N,M)条每篇预留最多8次长文阅读+3次短解读；N仍1—100默认10，超过10确认费用。不自动提高用户上下文/输出额度，不偷偷换模型。metrics区分评分/阅读/解读、复用/修复项以及服务端报告的输入/输出/缓存/推理Token；后两项为子集，不重复相加。缺usage/HTTP重试用量未知，不能当完整账单；字符数不是Token数。稳定前缀便于服务端缓存，但应用无法下载内部Transformer KV张量或保证中转站缓存命中。

桌面对502/503/504以及remote_protocol/read_error响应中断自动重试一次，共享最多两次HTTP尝试，退避最多2秒；连接探测不自动重试。通过retry_transport_codes白名单恢复中断，不扩展到超时、建连/代理/写入、认证、限流或输出格式错误。重发同一HTTP正文不重新执行已完成的业务工具，但服务商仍可能重复生成/计费。综合修正次数与HTTP重试次数独立，二者都不是无限恢复。

响应限量读取4MB；区分传输错误、HTTP状态、无效JSON、意外事件流、输出额度耗尽与未完成工具响应。不会执行截断工具调用。请求详情只展示安全错误码、状态、次数、协议、字符数、用时和预算，不直接展示密钥或上游错误正文。基础连接测试和虚构echo工具回环测试是不同验收，基础成功不能证明完整工具兼容。

## 9. 持久化与隐私边界

| 仓库/数据表 | 保存内容 |
|---|---|
| ResearchRepository / research_users | 本机账号、盐和PBKDF2口令派生结果、管理员标记 |
| AccountSecurity / account_recovery、remembered_devices | 一次性恢复码摘要、30天设备令牌摘要及有效期；重置在事务内消耗恢复码并撤销令牌 |
| DesktopRepository / desktop_news_preferences | 当前账号的简报输出数量，1—100，默认10 |
| research_profiles / research_events / research_profile_proposals | 生效档案、操作与撤销记录、待确认档案建议 |
| research_sources | 来源元数据、领域、状态、私密/共享范围与所有者 |
| research_reports | 问题、证据快照、知识对象、渲染文本、采集审计、修正草稿、指标 |
| DesktopRepository | 最近完成对话、上下文设置、逐条资料评分 |
| RecommendationRepository / recommendation_settings、recommendation_decisions、recommendation_feedback | 账号私有的探索参数、报告/新闻选择角色及条件概率、渲染标记与可替换/撤回的明确方向奖励；只聚合最近5000条并按新闻上下文去重 |
| ModelConnectionRepository / model_connections | 账号所属连接元数据和DPAPI加密密钥 |
| FeedRepository / subscription_feeds、subscription_checks | 账号隔离的订阅快照、成功抓取时间及端点绑定的最近尝试/轮换时间 |
| NewsRepository / news_articles | 全量候选新闻快照（含有限词汇提及标签及原文跨度）、当前兴趣分、评分状态及缓存版本 |
| NewsFeatureCache / news_feature_kv、news_score_cache | 账号私有稳定内容Key及精简新闻Value、按评分/重要性版本的有效决策；跨快照ID复用，不跨账号共享；与semantic_documents的向量缓存配合，不是服务端注意力KV |
| NewsFeedback / news_votes、news_opens、news_impressions | 账号规范URL赞踩/打开、按报告栏目会话去重的可见曝光；仅反馈残差学习，不上传、不跨账号聚合 |
| NewsFeedback / news_ranking_preferences、news_importance | 三栏R/C/I/H权重、反馈学习开关，以及账号新闻的重要性量表/材料引文/模型Prompt版本缓存 |
| SemanticRepository / semantic_settings、semantic_text_vectors、semantic_documents、semantic_profiles | 账号语义开关、按模型/特征版本隔离的文本摘要向量与新闻/标签特征、固定分类和锚点/当前向量/反馈质量/偏移；不上传，不在报告复制全库向量 |
| NewsRepository / news_interpretations | 按新闻与读者版本保存的解读、引用和修正草稿；报告通过ID引用 |
| NewsRepository / news_documents | 账号独立的正文快照、获取状态和版本；成功缓存一天，失败15分钟重试 |
| NewsRepository / news_reading_states | 按账号/新闻/正文与读者版本保存长文阅读断点、已选原文和有界失败稿，不重复读已完成批次 |
| NewsRepository / news_entities / news_event_links | 公司/模型/方向索引、归属依据、保守事件身份和显式后续关系 |

数据库采用SQLite事务，业务仓库在访问私人记录时检查账号范围；共享来源可见性不等于共享所有者的私人数据。客户端本机账号隔离不等于数据库整体加密，电脑管理员或能读取文件的人仍可接触档案、报告和草稿。

RememberedLogin将账号名及DPAPI加密设备凭证保存到data中的数据库路径绑定JSON，原子替换、不保存密码；损坏/过期/解密失败回普通登录，无明文降级。恢复码仅生成时显示、数据库只存摘要。密码和恢复码同时遗失时没有邮件或管理员绕过机制。

新闻报告保存requested_limit；payload超过650000字符时压缩evidence展示快照为原摘要和标签、记录evidence_storage与interpretation_id；仍过大则不重复嵌入claims聚合副本，标claim_storage=news_interpretations。完整校验主张和证据仍在独立解读中，展示解读与分数不变。100万字符报告上限不提高，不把紧凑快照当全文引用核验材料。

公开来源访问受公共HTTPS地址、DNS/内网检查、重定向和响应体积等规则约束，不将任意来源URL当作可执行代码。Key、数据库、运行日志、缓存不得提交；data、logs、outputs中的运行数据和生成报告由Git忽略，实验报告需在本机按文档重新生成。不读取Codex或其他客户端内部认证文件。网页、用户输入与模型草稿均为不可信数据，Prompt不替代权限检查。

## 10. 配置与扩展位置

| 目标 | 优先修改位置 |
|---|---|
| 增补/分类内置来源 | config/sources/live.yaml；需要新访问方式时扩展sources白名单适配器和测试 |
| 路由、查询、信息筛选 | sources/catalog.py、query.py、filtering.py与research/subscriptions.py |
| 用户档案和反馈规则 | database/research_repository.py、research/tools.py、session.py及相应表单 |
| 大类与细分语义画像 | preferences.py、semantic_profile.py、model/embeddings.py、research/semantic_personalization.py、database/semantic_repository.py、desktop/semantic_profile.py；模型文件在data/embedding_models，显式下载 |
| 综合表达和修正要求 | prompts/explanation.md、synthesis_repair.md；结构约束改validation.py |
| AI评分、四信号分栏与赞踩 | prompts/interest_scoring_fast.md实际请求、interest_scoring.md语义版本兼容；research/efficient_scoring.py、importance_scoring.py、news_pipeline.py、news_ranking.py、database/news_feature_cache.py、news_feedback.py、news_repository.py |
| 栏目和阅读布局 | config/editorial.yaml、synthesis/rendering.py、desktop富文本组件 |
| 模型协议和诊断 | model/client.py、tool_wire.py、diagnostics.py |
| 本机连接与密钥输入 | desktop/model_settings.py、database/model_connections.py |
| 采集调度 | desktop/app.py中当前窗口的可选QTimer；独立后台调度需另行实现 |

不将商业搜索、浏览器自动化或MCP能力写成已接入：目前没有OpenAI托管web_search后端、SearXNG/OpenSERP适配器、Chrome DevTools采集器，也没有完整网页列表/任意JSON新闻采集器。arXiv全文PDF读取、引用网络、通用语义检索与自动术语翻译不在当前实现内。

## 11. 验证与运行边界

tests/unit覆盖查询/路由、真实接口的离线模拟、筛选、档案隔离、密钥加密、工具执行、协议、预算、修正循环、订阅池与Qt交互；tests/integration覆盖已认证研究链路及HTTP工具往返。固定合成案例用于契约回归，不证明现实新闻正确率或用户理解收益。

```powershell
.venv\Scripts\python.exe -m compileall -q src tests
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m newspaper_agent.synthesis.evaluation
.venv\Scripts\python.exe -m newspaper_agent.research.catalog_smoke
.venv\Scripts\python.exe -X utf8 -m newspaper_agent.research.semantic_evaluation
.venv\Scripts\python.exe -X utf8 -m newspaper_agent.research.feedback_experiment --seeds 20
```

前两项为本地编译/测试，合成评测不调用真实聊天模型。catalog_smoke会明确访问新增公开订阅并打印状态，不发送私人档案或使用模型Key。semantic_evaluation使用已准备的真实本地384维编码器、固定虚构材料及临时账号数据库验证合并/赞踩方向/跨类隔离/撤回，不访问生产档案、不调用聊天API；缺模型需先显式运行model.embeddings --download或界面准备。feedback_experiment复用正式编码、新闻特征和evolve/match，在独立反馈/评估材料、固定多种子下比较静态、赞踩、误点、弱打开和偏好反转；输出指标CSV/JSON、新闻K/V特征、槽向量轨迹、SVG/PNG与HTML/Markdown报告到outputs。K=V为应用层语义特征，非Transformer KV张量；融合基分及用户反馈为模拟，不冒充真人A/B或实测CTR。见[反馈演化实验](feedback-evolution-experiment.md)。真实模型测试需控制次数/输出额度，优先固定虚构探测，不自动重发私人资料。外部服务故障不能由离线测试保证修复。

更细的接口和边界见[新闻两阶段流程](two-stage-news.md)、[订阅简报](subscription-news.md)、[真实来源](live-sources.md)、[用户档案](interest-and-expression-strategies.md)、[综合模块](synthesis-v1.md)、[修正循环](synthesis-repair.md)、[模型通信](model-communication.md)、[桌面入口](desktop.md)。这些细节文档与本总览一起按实现维护；历史修改原因放在独立时间命名记录，不混入本总览。
