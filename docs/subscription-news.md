# 分类信源采集与订阅简报

## 目标与入口

桌面简报默认“订阅简报”：采集来源、积累本机池，候选全量入新闻库，同批兴趣/重要性估计后四指标分栏，三栏共享用户指定N条解读（1—100、默认10）。[四信号推荐](multisignal-ranking.md)预留公共新闻名额，旧[日常探索](contextual-exploration.md)仅在无公共分配时沿用。选择“主动检索”先查询专业API/已配置搜索服务，不自动扩大公共源池或启用付费兜底。聊天research_search仍是明确问题关键词取证，见[两阶段契约](two-stage-news.md)。

来源页按“金融+通用”等分类查看；大分类为academic/medical/finance/news/general，AI/agent/LLM为主题标签。[AIHOT免费信源](aihot-free-sources.md)中18个公开RSS已登记，覆盖官方/研究博客、媒体和个人技术观察；16个启用，2个暂停待复核。它们属于news+academic专门资讯，不放入general；BBC世界/科技/科学环境可跨领域补充。通用不代表一定感兴趣：AI根据用户档案评估每篇分数，不按本地关键词兴趣硬淘汰。

## 采集、周期和预算

RSSAdapter.collect读取当前Feed最多200条，仍用安全HTTP和5分钟进程缓存，不访问正文、不绕过登录反爬。业务订阅简报include_public=True：原兴趣路由先取8源，加最多4个不受兴趣领域限制的已启用公共白名单源，合并后补满最多12源；白名单是BBC世界/科技/科学、联储、NIST、NASA、IT之家、Solidot，按最久未尝试轮换。仍3路并发，总预算不扩。直接collect_news默认include_public=False，保留原12源路由、最多2个通用位置和显式源优先规则。subscription_checks按账号+来源+端点记录失败/缓存/成功尝试，不混淆成功刷新时间。subscription_feeds按账号每源最多200条、URL判重、新快照替旧；15分钟内复用，失败用旧池并标状态，不冒称刚更新。

默认手动更新，不自动联网。信息来源页可勾选“应用运行时每30分钟采集订阅”：开启立即采集，以后每30分钟检查一次，忙碌时跳过且不积压任务；关闭选项停止后续调度，当前请求仍按原取消机制收尾。关闭应用后不运行，重启默认关闭，不是系统后台常驻服务。此定时流程只采集不调用模型、不生成报告，不覆盖聊天的检索上下文。采集可能漏掉两次轮询之间已从Feed移除的条目，不能保证完整历史覆盖。

泛化查询取活跃细分兴趣的主题+方法（优先级降序）和简单兴趣，去重后最多12个路由查询，AI评分使用完整已确认档案；本地不做兴趣硬筛选。默认30天或用户所选今天（香港时间）、过去24小时、7/30/90天。严格窗口排除未知/不精确日期、未来时间；纯日期仅参加今天，空结果不补旧闻。每轮最多2400候选按账号独立存储、20条一批评分，前十名再逐条解读。规则与边界见[画像与覆盖](personalized-news-routing.md)、[时间和信源](today-news-and-expanded-sources.md)及[two-stage-news](two-stage-news.md)。

## 分类来源与实测（2026-10-16）

目录config/sources/live.yaml共54项，包含50个RSS和arXiv/PubMed/Crossref/Tavily检索入口。AIHOT公开RSS的端点、分类及暂停原因见[AIHOT免费信源](aihot-free-sources.md)；另21个中文科技、技术社区、科研官方和物理数学分类端点的2026-10-17实测见[扩展信源](today-news-and-expanded-sources.md)。以下是原有9个RSS的2026-10-16验证结果：

| 类别 | 来源 | 本机单次公开Feed验证 |
|---|---|---|
| AI/学术 | MIT AI | 成功50条 |
| 物理/学术 | MIT Physics、Nature Physics | 成功50/8条 |
| 医学/学术 | Nature Medicine | 成功8条 |
| 通用科技/科学 | BBC Technology、Science & Environment | 成功21/42条 |
| 医学 | NIH NIBIB | 请求失败，suspended |
| 金融 | 欧洲央行 | 请求失败，suspended；原有联储源保留 |
| 科学/航天 | NASA JPL | 请求失败，suspended |

失败仅代表当前网络下未能读取，不证明源不存在；待复核项不默认参与采集。成功仅为单次XML读取验收，不保证长期可用、内容覆盖或版权再分发许可。中文补充少数派、IT之家、Solidot，不能宣称完整中文覆盖。

来源依据：[MIT主题页](https://news.mit.edu/topic/artificial-intelligence2)、[ECB RSS目录](https://www.ecb.europa.eu/home/html/rss.en.html)、[NIBIB RSS目录](https://www.nibib.nih.gov/rss-feeds)、[JPL RSS目录](https://www.jpl.nasa.gov/rss/)。可运行 `.venv\Scripts\python.exe -m newspaper_agent.research.catalog_smoke` 复检新增公开Feed；只打印来源ID/状态/条数，不读账号或使用模型和搜索Key，不写私人数据库。

## 隔离与验证

来源仍经ResearchSources.descriptors的当前账号可见性过滤，未验证/未启用来源不采集。共享只共享来源元数据；资料池各账号分开，不读取其他账号私人记录。停用或改变端点的来源，旧池不会重新进入筛选或可读取证据。数据库在本机，电脑管理员仍可读取，不能宣称数据库加密。未增加动态执行采集代码、网页列表抓取或JSON采集器；这些是后续模块化扩展，不冒称已实现。

离线测试覆盖跨领域通用来源预留、分类边界、跨会话缓存、账号隔离、停用/换端点隔离、失败旧池兜底、URL判重、容量上限、多兴趣筛选及目录状态；原有隐私/工具/模型回归继续运行。用户新增来源优先级调整为先于内置一手来源，防止扩容后用户来源永远无法参与。

报告search_audit只存来源、查询、状态等紧凑摘要，不保存results或逐条decisions；新闻和分数在news_articles，解读/修正稿在news_interpretations，报告保存前十条展示与库引用ID。新式报告不再用词法复核覆盖AI排名；旧报告仍保留原有阅读兼容。内置暂停源和未配置搜索Key显示状态但不采集。

用项目虚拟环境运行compileall及完整pytest；离线覆盖日期边界、兴趣确认、标签定位、轮换及账号隔离。真实网络与测试结果由时间命名交付记录保存；公共RSS实测零模型请求。没有修改真实账号的兴趣、共享权限或旧报告，也未创建Git提交。
