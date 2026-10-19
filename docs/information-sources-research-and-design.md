# 专业信息源调研与可演化信息收集接口

调研日期：2026-10-09（Asia/Hong_Kong）。状态：用户已确认搭建方向，分阶段实现；不是全部能力已经完成。

实现进展：目录/接口、离线路由、证据读取及部分候选/反馈基础已落地，详见 [第四步基础层](step-04-source-foundation.md)。下文是完整目标设计，真实来源适配、自动发现和完整审批生命周期仍待实现。

本文不会启用搜索服务、安装插件、创建账户、配置凭证或产生付费 API 请求。此前第四步的搜索草稿仍未接入主程序，不能视为已验收功能。

## 1. 结论与调研范围

目标不是选定一个万能搜索 API，而是建立“任务匹配来源 → 受控访问 → 保留证据 → 用户反馈 → 扩充来源”的系统。

来源目录按任务、地域、语言、资料类型、访问条件分类，而非仅按网址分类。下表是具有代表性、官方可核对的候选，不是市场份额排名或各领域的穷尽清单。地域先覆盖中国大陆、香港、美国及国际机构；其他司法辖区按用户需求补充。未登录服务账户，也未测试付费接口，因此可用性不能等同于已验证接入。

必须区分：

| 类别 | 功能 | 示例 | 不应混淆的边界 |
|---|---|---|---|
| 通用搜索/发现 | 找网页、链接、相关片段 | Brave、Tavily、Exa | 搜索摘要不等于完整原文，也不保证事实真实 |
| 专业数据库检索 | 找领域对象、结构化记录 | arXiv、PubMed、OpenAlex | 论文元数据不代表有全文许可 |
| 原始发布机构 | 获取公告、法律、统计数据 | SEC、统计局、交易所 | 官方资料也有版本、修订和范围限制 |
| 全文获取/解析 | 读取已经找到的页面或 PDF | Firecrawl 的抓取能力、合法全文位置 | 能获取页面不等于允许存储或再分发 |
| 私有知识源 | 检索用户授权的资料 | Zotero、个人文件、企业知识库 | 不应自动把私有检索词或文档发给公共搜索服务 |

## 2. 通用搜索服务候选

接入条件仅作定性标记；具体额度、收费、商业使用及再分发条款必须在接入时复核。下述能力来自官方说明，并非本项目实测质量结论。

| 候选 | 能力与定位 | 接入方式/边界 | 官方证据 |
|---|---|---|---|
| Brave Search API | 网页、新闻等搜索接口 | 商业 API 凭证；有独立新闻端点；不是免费无限访问 | [API 文档](https://api-dashboard.search.brave.com/documentation)、[新闻接口](https://api-dashboard.search.brave.com/api-reference/news/news_search/get) |
| Tavily | 搜索结果、相关内容片段，可选择新闻类别和时间范围 | API key；日期可能是发布时间或更新时间估计，未知日期应保留未知 | [Search](https://docs.tavily.com/documentation/api-reference/endpoint/search) |
| Exa | 网页检索及内容提取，适合资料发现 | REST API 或其服务提供的 MCP；MCP 是传输方式，不是另一个搜索引擎 | [Search](https://exa.ai/docs/reference/search) |
| Serper | 将 Google 搜索及新闻等结果转换为结构化数据 | 第三方服务，不是 Google 官方 API；账号额度与条款需复核 | [官网与返回示例](https://serper.dev/) |
| SerpApi | 多搜索引擎结果解析 | 第三方商业 API；不因它提供结果就获得原网页的再分发许可 | [搜索接口](https://serpapi.com/search-api) |
| 博查 Bocha | 自然语言网页搜索，官方展示中文搜索及返回字段 | API key；中文覆盖、时效和召回需用我们的任务集实测 | [开放平台](https://open.bochaai.com/) |
| Parallel Search | 为研究任务返回网页搜索内容片段 | 独立 API，需核对凭证、额度及内容使用条件 | [Search 快速入门](https://docs.parallel.ai/search/search-quickstart) |
| Firecrawl Search | 搜索并按需抓取结果页面 | API/部署条件另核对；搜索、抓取、解析要分别记账 | [Search 文档](https://docs.firecrawl.dev/features/search) |
| SearXNG | 自托管元搜索，对多个已配置引擎发起查询 | 软件开源，但服务器、上游可达性和维护不是零成本；JSON 输出需实例启用，公共实例不可视作稳定生产依赖 | [Search API](https://docs.searxng.org/dev/search_api.html) |

模型服务的内置搜索属于另一条接入路线：供应商负责检索，客户端接收回答和来源；需要确认具体模型、端点、中转站是否支持，而不能从“Responses-compatible”推断支持所有工具。本轮不选择供应商，不涉及模型内置工具实现。

### 2.1 旧教程的兼容性风险

- Google Custom Search JSON API 已关闭新客户接入；官方要求现有客户在 2027-01-01 前迁移。不要将它选为新项目默认方案。[官方说明](https://developers.google.com/custom-search/v1/overview)
- Microsoft 旧 Bing Search APIs 已于 2025-08-11 退役；其他 grounding 产品不是原始搜索 API 的直接同构替换。[官方生命周期公告](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement)
- 开源项目中存在一个 adapter，不代表今天仍能申请账户或运行成功；每个候选都需要记录最后核验日期。

## 3. 各领域专业信息源地图

API 型来源、网页型来源和授权型来源分开列出。网页可浏览不表示已确认公开、稳定、允许自动化调用的 API。

### 3.1 跨学科学术研究与 AI 论文

| 来源 | 适用任务 | 获取与限制 | 官方入口 |
|---|---|---|---|
| arXiv | 预印本、AI/计算机/物理等研究发现、版本跟踪 | 公共元数据 API；全文另走合法下载；预印本不应自动标为已同行评审 | [API](https://info.arxiv.org/help/api/index.html) |
| OpenAlex | 跨学科论文、作者、机构、引用图、学术统计 | REST API；当前文档允许无 key 基本使用，规模使用需考虑 key 和预算；不是全部论文全文库 | [API](https://help.openalex.org/api/)、[认证](https://help.openalex.org/api/authentication/) |
| Semantic Scholar | 论文/作者/引用/推荐，关联研究发现 | Academic Graph API；限额与 key 条件逐端点核对；不保证全部全文 | [API](https://api.semanticscholar.org/api-docs/) |
| Crossref | DOI、书目信息匹配、引用核验 | REST 元数据接口；DOI 的存在不是研究结论正确性的证明 | [REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) |
| CORE / Unpaywall | 开放获取论文发现 / 根据 DOI 找合法开放全文位置 | CORE 提供元数据和全文服务；Unpaywall 为全文位置查询，不替代主题搜索；权限和规模使用条件需分别核对 | [CORE API](https://core.ac.uk/services/api)、[Unpaywall API](https://data.unpaywall.org/products/api) |
| Scopus、Web of Science、CNKI | 跨学科引文检索、机构订阅资源、中文研究 | 候选授权型来源；不默认有个人可用 API，更不绕过机构登录或下载限制 | [Scopus](https://www.elsevier.com/products/scopus)、[Web of Science](https://clarivate.com/academia-government/scientific-and-academic-research/research-discovery-and-referencing/web-of-science/)、[CNKI](https://www.cnki.net/) |

建议路由：学术发现 → arXiv/OpenAlex/Semantic Scholar；引用核验 → Crossref 等原始记录；全文 → 已获授权或开放获取的页面。不能用一个引用次数排序替代相关性和研究质量判断。

### 3.2 医学、药品与临床研究

| 来源 | 适用任务 | 获取与限制 | 官方入口 |
|---|---|---|---|
| PubMed / PMC | 生物医学论文 / 可获取的全文集合 | NCBI E-utilities；PubMed 收录不代表文章有免费全文，PMC 也要检查具体许可 | [NCBI API](https://www.ncbi.nlm.nih.gov/home/develop/api/) |
| Europe PMC | 生命科学文献、引用与相关数据 | Articles REST API；本次主文档返回 403，能力由 EMBL-EBI 官方说明交叉确认；具体认证/限额待接入核对 | [官方培训说明](https://www.ebi.ac.uk/training/online/courses/embl-ebi-programmatically/europe-pmc-programmatically/) |
| ClinicalTrials.gov | 临床试验登记与结果记录 | 官方 Data API；登记记录不等于试验已完成或疗法已获批 | [API 入口](https://clinicaltrials.gov/data-api/api) |
| openFDA | 药品标签、不良事件、召回等公开记录 | 分类端点与 JSON 返回；官方明确不是所有数据均验证为临床/生产用途，不良事件记录不能单独证明因果 | [API 与免责声明](https://open.fda.gov/apis/) |

医学任务还需按国家、疾病和证据等级补充主管机构与指南发布者；研究资料搜集不等于个体诊断或治疗建议。

### 3.3 金融、公司研究与宏观经济

| 子任务 | 优先候选 | 接入与关键区别 |
|---|---|---|
| 美国公司公告/财报 | [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | 公共公司提交历史与 XBRL API；无需 API key，但遵守公平访问政策；不是行情服务；公司财年可能不同 |
| A 股公司公告 | [巨潮资讯](https://www.cninfo.com.cn/) | 公告原文与披露检索；本轮未确认可直接使用的公开开发者 API，先登记为网页/授权评估来源 |
| 港股公司披露 | [HKEXnews](https://www.hkexnews.hk/) | 标题/内容检索与披露；公开网页不等于拥有批量抓取和再分发授权 |
| 美国宏观与数据修订 | [FRED / ALFRED](https://fred.stlouisfed.org/docs/api/fred/) | 经济序列、观测值、发布及历史版本；必须保留单位、频率和 vintage/as-of 信息 |
| 跨国经济比较 | [世界银行](https://datahelpdesk.worldbank.org/knowledgebase/topics/125589-developer-information)、[IMF](https://data.imf.org/en/Resource-Pages/IMF-API) | 指标及统计数据接口；不能混同估计、预测和实际值，不同指标频率不同 |
| 中国官方统计 | [国家统计局国家数据](https://data.stats.gov.cn/) | 官方发布库；未确认公开稳定 API；保留指标口径、地域、报告期和修订情况 |
| 个人研究型市场数据 | [Tushare](https://tushare.pro/document/1?doc_id=5)、[Alpha Vantage](https://www.alphavantage.co/documentation/) | Token/key 及端点额度限制；覆盖、实时/延迟/历史及价格复权方式必须逐字段核对 |
| 专业授权市场数据 | [LSEG](https://developers.lseg.com/)、[Bloomberg](https://www.bloomberg.com/professional/products/data/)、[Wind](https://www.wind.com.cn/) | 机构/个人合同、数据权限与再分发限制；本轮不是价格调查，不承诺具体账户能调用 |

金融任务至少拆成“公告、财务事实、行情、宏观序列、新闻评论”五种能力。不能把新闻摘要当成行情，也不能把 USD 与 CNY、日线与实时、报告期与披露日混用。

### 3.4 新闻、媒体与时事

候选：[Brave News](https://api-dashboard.search.brave.com/api-reference/news/news_search/get)、[NewsAPI](https://newsapi.org/docs)、[GDELT](https://gdeltproject.org/)，以及用户指定媒体/组织官方发布页面或正式 RSS。

- API 聚合是发现层，不一定提供完整新闻原文；文章存储、摘录和再分发需逐供应商及原发布者核对。
- 公司宣传稿、新闻报道、评论和社交转载必须标注不同来源角色。
- 多家转载同一通讯社稿件不算多个独立证据；保留原始出处、事件时间、发布时间和更新时间。
- 非中文服务的中文覆盖、地方媒体召回和可达性不能凭营销介绍保证。

### 3.5 法律、政策与公共行政

候选：[中国国家法律法规数据库](https://flk.npc.gov.cn/search)、[CourtListener](https://www.courtlistener.com/help/api/rest/)、[GovInfo](https://www.govinfo.gov/developers)。

- 中国数据库本轮抓取失败，由官方域名搜索结果确认入口；不声称已确认可用公开 API。
- CourtListener 有法律检索及相关 REST 能力，GovInfo 有开发者服务；它们不是全球法律库。
- 请求必须携带司法辖区、适用日期、法律版本/效力；判例、法规、征求意见稿、解读文章不能混为一类。
- 专业法律意见仍需相应专业核查，用户来源偏好不能覆盖有效法原文要求。

### 3.6 专利、技术情报与研发竞争

候选：[EPO Espacenet / OPS](https://www.epo.org/en/searching-for-patents/data/web-services)、[USPTO / PatentsView](https://www.uspto.gov/ip-policy/economic-research/patentsview)。

EPO OPS 提供标准化 XML 访问；PatentsView 官方入口存在向 USPTO Open Data Portal 的迁移说明，因此旧开源 adapter 必须重新核对。申请、公开、授权、法律状态不同；同一专利族不能当成多项独立发明。中国专利与行业商业情报库作为后续地域专项，尚未完成接口调查。

### 3.7 软件工程、AI 产品与开源生态

候选：[GitHub Search REST API](https://docs.github.com/en/rest/search)、[Hugging Face Hub API](https://huggingface.co/docs/hub/api)，以及软件厂商的版本化官方文档、发布说明和安全公告。

适合检索代码、Issue、仓库、模型、数据集；某个仓库能运行不代表其依赖安全，也不代表模型/数据集允许商用。查文档时保留产品版本和发布日期。GitHub 和 Hugging Face 的认证、私有资源权限、搜索限额分开处理。

### 3.8 网络安全与漏洞情报

候选：[OSV](https://osv.dev/)、[NVD](https://nvd.nist.gov/developers/vulnerabilities)、[CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)。

OSV 已核对到按包版本/commit 查询的 API；NVD/KEV 作为官方候选，本轮页面无正文/403，细节待接入验证。漏洞记录、受影响版本、修复版本、已被利用状态是不同字段；不要凭一个 CVE 编号判断当前机器必定受影响。

### 3.9 能源、天气与环境

候选：[EIA Open Data](https://www.eia.gov/opendata/)、[美国 NWS API](https://www.weather.gov/documentation/services-web-api)。

能源统计与天气预测/观测需专门数据接口，不应用搜索摘要代替数值。NWS 不是全球天气服务；其他地域需接入对应气象机构或获得授权的商业服务。空间分辨率、预测生成时间、观测时间、单位和覆盖区域必须保留。

### 3.10 生物多样性与地理知识

候选：[GBIF 开发者入口](https://www.gbif.org/developer/summary)、[Wikidata 数据访问](https://www.wikidata.org/wiki/Wikidata:Data_access)、[OpenStreetMap](https://www.openstreetmap.org/copyright)。

GBIF 页面本次返回 403，具体端点待验证；Wikidata 可做实体与标识符辅助，但社区记录不是所有事实的最终依据。OSM 需遵守 ODbL 等许可；本轮未验证具体地理查询端点，不能把主站当成无限公共 API。

### 3.11 国际贸易、产业与供应链

候选：[UN Comtrade](https://comtradeplus.un.org/)、[WTO 数据集](https://www.wto.org/english/res_e/statis_e/trade_datasets_e.htm)，加对应统计机构和企业披露。

本次入口为 JS/访问受限，具体 API、账号和限额未核验，应保持候选状态。业务请求需明确 HS 分类版本、货物流向、报告国、伙伴国、数量单位和币种。企业访谈、估算市场规模与官方贸易统计不能作为同一类型观测值。

### 3.12 社区、消费反馈与视频

候选：[Reddit API](https://www.reddit.com/dev/api/)、[YouTube Data API Search](https://developers.google.com/youtube/v3/docs/search/list)。

用于观点发现、用户问题和视频发现，不作为医学/金融/法律事实的唯一依据。搜索视频不等于获得字幕或整段视频的下载许可。需额外核对授权、配额、个人信息、内容删除与平台使用条件。

### 3.13 未完成范围

教育、农业、材料化学、标准规范、招投标、房地产、交通及各国地方信息源尚需按真实用户任务细化。商业数据库、中文受限站点及社会平台本轮没有合同级审核。它们不是“已兼容工具”。可将这些方向纳入下述候选发现流程，而非一次接入几十个未经验证的接口。

## 4. 保证来源特异性：任务到来源的匹配

建议先解析任务为：领域 + 子任务 + 对象标识 + 地域/司法辖区 + 语言 + 时间范围 + 资料类型 + 所需证据层级 + 预算/隐私策略。

例如“最近 AI 医疗研究”横跨 AI 与医学：arXiv 用于方法发现，PubMed 用于医学文献，ClinicalTrials.gov 用于试验核对；不是只能选择其中一个领域。

推荐策略：

1. 先按权限、预算、能力、地域及时间硬约束排除不适用来源。
2. 优先专用接口和原始发布者；通用搜索发现补充来源、别名、相关新闻和遗漏材料。
3. 对高风险结论要求原文和交叉核验；“官方”也不是一个全局真伪标签。
4. 对未知领域先使用允许的通用发现工具，再提出新来源候选。
5. 不支持的筛选参数返回 `unsupported_filter` 或显式说明降级，不可静默忽略。
6. 记录实际使用的来源、时间覆盖、失效来源和未搜索的范围；不能声称穷尽。

## 5. 兼容接口设计（拟议，不是现有代码）

不要要求所有供应商拥有相同 URL、认证和 JSON 协议；兼容性应在我们自己的边界上实现。

### 5.1 分成三个层次

```text
用户任务 → 任务解析/来源路由 → 少量检索工具
                              ↓
                        SourceRegistry（目录、状态、权限）
                              ↓
                        SourceAdapter（供应商适配器）
                              ↓
                        外部 API / RSS / 批准的网页 / MCP
                              ↓
                        规范化证据 + 引用 + 用户反馈
```

`agent_core` 依赖协议；供应商 HTTP/解析留在适配层；`app` 负责组装；用户个性化状态通过 `database` 持久化。暂不调整现有循环或引入新框架。

### 5.2 必要的数据类型

| 类型 | 主要字段与职责 |
|---|---|
| SourceDescriptor | 稳定 id、名称、领域/地域/语言、来源角色、允许域名、adapter id、认证引用、官方文档、条款链接、schema/version、核验日期、状态 |
| Capabilities | search、fetch、citations、timeseries 等能力；支持的筛选、分页、正文、日期精度；不支持能力明确标注 |
| SearchRequest | query、任务类别、标识符、地域、日期、过滤条件、结果数上限、预算、截止时间；私有上下文不自动外发 |
| EvidenceRecord | source id、provider id、原发布者、原始 URL/稳定标识、文档类型、标题、摘要/正文层级、发布时间/更新时间/抓取时间、版本、内容哈希、许可及来源定位 |
| 专业 payload | paper：DOI/arXiv id/作者/出版状态；finance：币种/单位/频率/报告期/vintage；law：辖区/生效与失效日期；trial：注册编号/状态。不能只剩一个 snippet |
| SearchBatch | 结果、游标、警告、失败来源、实际筛选、费用已知/未知、耗时、是否部分成功；供应商评分只能用于该来源内部，不直接跨来源比较 |
| Feedback | user id、任务与结果 id、反馈类别、用户原话或经同意摘要、时间；排名偏好与事实质量分开保存 |

API key 仅保存环境变量/安全凭证引用，不放来源 YAML、返回结果、日志或 prompt。用户数据与公共目录分开，默认不提交 Git。

### 5.3 接入类型与新增来源难度

| 类型 | 新增方式 | 能否只加配置 |
|---|---|---|
| 已审查 RSS/Atom adapter | 新 feed、领域标签、访问规则 | 通常可以，但 URL 与许可仍需审查 |
| 同一已审查 API 的新数据集 | 新数据集标识、过滤与权限 | 能力/协议未改变时可配置化 |
| 新 REST API | 新请求/认证/分页/字段映射 | 不保证；通常要开发和测试 adapter |
| 网页/PDF | 已批准域名的受控获取和解析 | 原文读取可复用；站点搜索与可靠抽取不保证只靠配置 |
| MCP | 审查服务、工具 schema 和可访问范围 | MCP 不统一供应商字段、许可或安全性；不能自动信任新 server |

只有审查过的声明式 GET 映射可在通用 adapter 中使用；配置不允许任意表达式、shell、动态导入或访问任意 URL。OpenAPI 文件帮助生成候选适配方案，但不是启用许可。

### 5.4 避免工具数量爆炸

初期可向模型只暴露 `search_information` 和 `fetch_evidence`，由后台目录和路由选择已批准来源；专业复杂能力再增加 `get_timeseries`、`expand_citations` 等少量工具。来源增多不必同时增加同等数量的 prompt/schema。

schema 使用模型支持的简单、明确参数；供应商特殊筛选在程序内转换。现有 Chat/Responses 的自定义工具通信层原则上可以复用，但扩展 schema 的双协议兼容性仍需测试，不能在本轮声称已兼容。

## 6. 用户专用助理的受控自进化

自进化首先是“目录和路由策略演化”，不是模型自己训练，也不是无限修改自己的源码。

```text
用户反馈/提供链接
  → 分析缺口（领域、时效、语言、资料类型、覆盖或格式）
  → 用已批准工具发现候选（受发现预算约束）
  → 登记候选与官方证据，不直接启用
  → 检查身份、接口、许可、隐私、权限、费用
  → 用户允许时，有限验证与隔离试运行
  → 审批启用 / 拒绝 / 待补充凭证
  → 学习用户偏好、观察质量
  → 降权、暂停、回滚或再次验证
```

状态建议：`discovered → reviewed → validated → enabled`，并支持 `rejected`、`suspended`、`archived`；每次迁移记录理由、执行者、时间、版本和审批。

### 6.1 不同反馈触发不同变化

- “摘要太长”：调整输出偏好，不必新增搜索来源。
- “缺少中文论文”：提出中文来源候选，或调整现有语言路由。
- “总给过时文章”：检查日期过滤、缓存和覆盖；未知日期不得伪造。
- “只想看公司原始公告”：调整资料类型和来源角色，不是只改 query。
- “这个数字不对”：登记事实错误、核对单位/修订/原文，不能只因用户喜欢另一结论就改变真实性评分。
- “请加入这个网站”：创建候选；确认其可接入方式与授权范围后启用。

### 6.2 自动操作的权限等级

| 操作 | 默认建议 |
|---|---|
| 根据同意保存的反馈更新个人偏好 | 可自动，有限调整、可查看、可撤销 |
| 用已批准工具、在已有预算内寻找新来源 | 用户预先允许发现后可自动；不是无限后台运行 |
| 写入候选来源目录 | 可自动，但 disabled/pending 状态 |
| 新公共域名的验证请求 | 先做 URL/条款检查；仅在用户授权的发现/测试政策内发出 |
| 新凭证、付费、私有信息外发、登录 | 用户提供授权，不能从反馈推断权限 |
| 启用同类型来源 | 初期逐次审批；后续可预授权低风险范围、域名和预算 |
| 新 adapter、新 Python 插件、新 MCP 服务 | 开发审查、隔离测试、明确批准；禁止网页触发自动安装执行 |

### 6.3 偏好学习不应形成信息茧房

偏好、可信度、覆盖度、时效、成功率、成本分别维护。初期不需要复杂强化学习：使用可解释规则、有限权重变化、小规模对照集和可撤销记录即可。

用户“点赞”表示有用，不证明事实正确。不得删除必要的反证或把低频领域来源永久排除。来源排名与任务相关：SEC 对财报权威，不适合回答生物医学疗效。

## 7. 建议的文件职责（尚未创建这些模块）

```text
config/sources/                 公共、经审核的来源定义，允许进 Git；不含秘密
src/newspaper_agent/sources/    目录、能力、adapter、路由与验证；不是大量复制工具
src/newspaper_agent/tools/      少量面向模型的检索/阅读工具包装
prompts/                       领域检索、引用和外部数据处理规则
database/ 持久化边界            来源状态、版本、反馈、审计
data/users/<user_id>/           私有偏好、用户候选与运行数据，不进 Git
docs/                          来源契约、接入说明与验收记录
```

路径与模块名是提案。用户自定义来源写入私有配置/数据库；共享仓库里的工具代码不能成为每个人的动态数据存储。确实需要新代码时，通过正常开发流程增加 adapter。

## 8. 安全、质量与运行边界

- 防 SSRF：外部 URL 需限制协议、域名及端口；解析实际 IP、阻止 loopback/内网/云元数据地址、逐次验证重定向、防 DNS rebinding；已批准私有源需单独隔离和显式授权。仅 `url.startswith('https')` 不够。[OWASP SSRF 指南](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- 防 prompt 注入：网页、论文、来源描述、MCP 结果都属于不可信数据；不能修改权限、请求密钥或注册新工具。prompt 提醒只是一个措施，还需要程序权限与输入输出校验。[OWASP 指南](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- 凭证按来源绑定，只在批准端点发送；重定向不可携带至其他域名；日志移除 headers、敏感 query、响应中的 token。
- 限制单次/整任务/用户每日的请求、费用、重试、字节数和工具轮次；费用未知的候选不默认为零成本。
- 403/验证码/登录/付费墙不自动绕过；遵守 API 条款和 robots 策略，robots 本身也不是版权许可。
- 保留原始作者/发布者、稳定标识和证据位置；日期缺失、摘要级资料、未核验来源必须明确标记。
- 去重与独立性：DOI/arXiv id、公告 id、canonical URL、专利族、内容哈希；转载集群只算同一出处，保留多个检索路径。
- 区分检索成功与核验成功。文章真实存在、作者有声望、来源受欢迎，都不等同于每个论断可信。
- 多用户配置、索引、缓存与凭证按用户隔离；删除/导出、撤销授权、来源停用和版本回滚是必要能力。

## 9. 第四步的建议拆分与验收

| 阶段 | 实质性成果 | 验收 |
|---|---|---|
| 4A 契约与目录 | 数据结构、状态机、离线样例来源、目录校验 | 核心不依赖具体供应商；错误/空结果/日期未知可解释 |
| 4B 两类真实来源 | 一种学术来源 + 一种通用服务，经用户选择 | 同一检索工具可切换；不支持筛选显式反馈；有实际引用 |
| 4C 特异性路由 | 学术/新闻/财报等任务用不同来源计划 | 固定测试集检查来源选择，交叉领域可多源，未知任务诚实降级 |
| 4D 来源发现 | 用户链接/反馈 → 候选来源条目 | 候选不能直接执行；新付费/认证必须等待授权 |
| 4E 个性化与评估 | 私有偏好、审计、暂停/回滚、质量指标 | 多用户隔离；偏好不篡改事实评分；回滚恢复旧路由 |

质量评估建议：相关性、专用来源命中、原始来源比例、独立来源数量、日期准确性、引用可核查性、许可明确程度、失败率、延迟、每个有用结果的实际成本。人工标注任务集应覆盖 AI 论文、中国公司公告、美国财报、宏观时间序列、医学、法律及未知领域。

安全测试必须覆盖恶意网页、私网 URL/重定向、超大响应、密钥泄露、未知 schema、429/超时、空结果、过期日期、重复转载、未批准工具与用户隔离。离线测试通过后才做用户授权的真实最小请求；没有实际请求的 provider 不标为 validated。

## 10. 本轮交付与限制

交付仅为本调研设计文档，没有新增运行代码、执行第四步草稿、启用服务或购买额度。已有用户修改保持不动。本轮 Markdown 文档不需要运行模型或真实搜索接口；未声称运行测试或通过真实接入验收。

官方访问情况：Semantic Scholar 产品页、CORE、Europe PMC、GBIF、CISA、Bloomberg 等部分页面返回 403/无正文；已注明限制，必要时用其他官方页面或官方搜索摘录交叉确认。Chinese 网页与商业服务的稳定 API、认证和许可需后续单独验证。供应商文档只能证明其声明的能力，不能替代本机连通性、中文质量和合同验证。
