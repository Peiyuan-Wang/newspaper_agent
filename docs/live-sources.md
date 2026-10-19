# 真实信息来源适配器

2026-10-16：[订阅采集与分类](subscription-news.md)在RSSAdapter新增collect接口，读取最多200条后按账号积累，桌面默认订阅简报。以下search/fetch预算描述继续适用于主动检索，不限制新的本地订阅池；新增6个实测可读Feed和3个暂停待复核端点。

更新：2026-10-13。模块沿用 SourceAdapter 的 search/fetch 接口，不依赖任何模型服务商 SDK；联网部分只使用 Python 3.11 标准库。它们由来源服务组装，单独构造适配器不会自动发起请求。

新增AI专业RSS、来源topics路由、并行读取与阶段事件，详见 [桌面体验升级](desktop-runtime-upgrade.md)。RSS对全部feed先检查主题/日期，再截取结果。CLI诊断 `.venv\Scripts\python.exe -m newspaper_agent.research.news_smoke` 各请求三个公开AI源一次，零模型请求；结果不写入私人数据库。

2026-10-14 相关性修复：日常表达先经 `sources/query.py` 提取主题；“了解一下ai，agent的大新闻”转换为 `ai agent`，识别 AI/人工智能/artificial intelligence、agent/agents/agentic/智能体、LLM 等有限同义词。所有适配器的候选均须通过本地主题检查，不再信任数据库模糊检索结果；AI 与 agent 同时提出时，两组主题都必须命中。英文短词匹配单词边界，避免 AI 误命中 chair/said；个人兴趣只排序，不能覆盖主题不匹配。

“新闻/最新/近期/进展”查询采用90天窗口（当前为固定规则）；已知过期或未来日期剔除，未知或跨窗口的部分日期仍标注，不伪造精确日期。普通研究关键词不强制近期窗口。`SearchRequest.recent_days` 为可选正整数；Crossref 使用 [from-pub-date](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/)，PubMed 使用 [reldate/pdat](https://www.ncbi.nlm.nih.gov/books/NBK25499/)，arXiv 使用 [submittedDate 排序](https://info.arxiv.org/help/api/user-manual.html)。本地时间检查仍负责最终筛选；RSS/Tavily的日期缺失不代表近期发布。日期窗口不等于“大新闻”重要性判断，学术论文不能替代产品发布或行业动态。

一次真实 Crossref 验收：`ai agent`、90天、最多5候选，主题筛选保留3条，剔除2条；保留标题均含 AI agents，未使用模型密钥。本次只验证论文查询，不宣称获取了完整行业大新闻。

## 已实现的能力

内置目录共有54个来源，其中50个RSS。[AIHOT公开免费信源](aihot-free-sources.md)完整登记18个RSS，16个启用、2个因本机读取限制暂停；另21个公开分类/社区/中文/官方科研端点中16个启用（4个实测有效但为空），5个暂停，详见[扩展信源](today-news-and-expanded-sources.md)。原有3个暂停源保留，共10个暂停项。不新增付费API或隐式兜底。运行 `.venv\Scripts\python.exe -m newspaper_agent.research.catalog_smoke --aihot` 或 `--expanded` 显式复检相应端点（含暂停项）；只读公开Feed，不使用账号、模型或搜索Key，不修改状态和私人数据库。单轮仍最多采集12源，按主题覆盖和最近尝试轮换，并非所有来源每轮都会访问。

| 适配器 | 请求与证据 | 凭证和限制 |
| --- | --- | --- |
| arxiv | 官方 Atom 查询 API；论文标题、摘要、作者、初次发表时间和版本更新时间 | 无密钥；预印本不能视为已通过同行评审 |
| pubmed | ESearch 找 PMID，再用 EFetch 获取文献元数据与摘要 | 无密钥；缺少摘要时仅返回元数据，不读取付费全文 |
| crossref | 学术 DOI 元数据与有提供时的摘要 | 无需注册或密钥；没有摘要时明确为metadata |
| rss | HTTPS RSS 2.0、RSS 1.0、Atom；在当前 feed 条目内进行关键词检索 | 无密钥；历史范围由 feed 决定，不代表全网搜索 |
| tavily | 固定官方搜索端点，basic 深度，返回网页/新闻摘要与链接 | 可选；需要环境变量中提供的搜索密钥，本轮未进行真实收费调用 |

通用搜索尚未配置时，可使用学术数据库和新闻订阅来源；这不能保证任意领域、语言或网站的广泛覆盖。RSS 本身不自动把中文兴趣翻译成英文，查询规划层需要提出适合来源语言的关键词。搜索返回空列表意味着未找到，不用其他无关新闻补足条数。

## 接口、输入与输出

代码在 `src/newspaper_agent/sources/live_adapters.py`：

```python
adapter = build_adapter(source, endpoint=None, api_key=None, http=None)
records = adapter.search(request)
record = adapter.fetch(records[0].id)
```

`source` 为 SourceDescriptor；`request` 为 SearchRequest，每次最多五条结果。RSS 使用 endpoint（缺省为 source.homepage）；arXiv、PubMed、Tavily 使用固定官方 HTTPS 端点，不能将 API key 发往其他地址。`http` 是离线测试的注入点，正式运行使用 SafeHTTPClient。

EvidenceRecord 保留 source_id、provider_id、publisher、URL、检索时间、发布时间和内容等级；payload 中保存 schema_version=1、locator、snapshot_id，以及 PMID、DOI、arXiv 版本或 feed 地址等定位数据。ID 为来源 ID 与 URL+文本快照的稳定摘要，正文变化不会覆盖旧ID。未知日期保持 None，只有年份或年月时保留日期粒度，不伪造具体日期。

搜索片段最多 1800 字符，fetch 返回已检索缓存内最多 6000 字符的内容。`abstract` 表示摘要，`metadata` 表示元数据，`feed` 表示订阅内容，`snippet` 表示搜索摘要。fetch 不会将这些内容升级标记为全文；不接受任意 URL，不下载 PDF。每个适配器缓存最多 200 条证据、50 个查询；arXiv 相同查询缓存一天，PubMed/Tavily 五分钟，RSS feed 五分钟。缓存随进程结束消失。

## 来源发现

`discover_feed_links(page_url, http=None)` 只访问一次公开 HTTPS 网页，从 HTML 的 `link rel=alternate` 中提取 RSS/Atom 候选，最多八个。它不执行 JavaScript、不跟随普通文章链接、不递归扫描、不自动启用候选。返回空列表只说明页面没有可识别声明，不证明网站没有 feed。

候选需要由上层来源管理保存、验证和启用。这个函数本身不提供账户登录、私有网站认证或来源信誉判断。

## 网络边界和错误处理

`sources/network.py` 提供 SafeHTTPClient：

- 只接受 HTTPS 443，拒绝 URL 用户名/密码、片段、控制字符、本地/私有/保留地址；RSS/发现入口还拒绝查询参数，避免保存带令牌的 feed 链接。
- 每次请求解析 DNS，所有结果都必须是公网地址；直接连接选定 IP 并检查实际 peer。TLS SNI 与证书验证仍使用原始主机名，避免再次按域名解析导致 DNS 重绑定。
- 禁止自动重定向，不使用环境代理、系统 Cookie 或登录状态，不解压远程压缩内容。依赖代理才能访问互联网的网络需要后续显式代理设计；不会静默降级安全检查。
- 默认连接/读取预算 12 秒，响应最多 1 MB（可配置但上限 2 MB），无自动重试。DNS 使用系统解析器，其超时由操作系统控制；读取期间另检查总时间。
- 单进程 arXiv 请求间隔至少 3.1 秒、PubMed 至少 0.4 秒，其他主机至少 0.15 秒。多个独立进程不共享限流，部署多实例时需集中协调。
- XML 不获取外部 DTD，拒绝内部实体、UTF-16 等含 NUL 输入；只解析有界文档。HTML 内容清理脚本/样式后作为证据字符串，不能作为指令执行。

错误只暴露稳定代码，例如 unsafe_url、unsafe_dns、unsafe_peer、redirect_blocked、request_timeout、response_too_large、rate_limited、access_denied、missing_search_key。不包含响应正文、API key 或原始异常。网络安全检查不等于来源真实性或内容真实性验证；相关性和证据判断属于后续筛选模块。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_live_adapters.py
.\.venv\Scripts\python.exe -m compileall src tests
```

离线测试覆盖 API 参数、证据解析、空结果、缓存、原始日期、RSS 过滤、Tavily 固定凭证目的地、feed 候选发现、私有地址、DNS 混合地址、IP 固定与 TLS 主机名、连接 peer 校验、重定向、大小限制及脱敏。真实网络验收结果由项目交付记录单独说明；离线通过不能证明各服务此刻可用。

2026-10-13 本机小规模验收（各执行一次 search，最多一条结果）：BBC World RSS 与 Federal Reserve press_all RSS 均成功返回真实条目；arXiv 返回 HTTP 429（rate_limited）；PubMed 返回 request_failed，未继续重试。后两者完成离线协议验证，但本次未通过真实网络验收。Tavily 未配置或调用。此时本模块 33 项离线测试通过，编译检查通过。

实现参考官方资料（2026-10-13 查阅）：[arXiv API 手册](https://info.arxiv.org/help/api/user-manual.html)、[NCBI E-utilities 使用规范](https://www.ncbi.nlm.nih.gov/books/NBK25497/)、[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[RSS 2.0](https://www.rssboard.org/rss-specification)、[Atom RFC 4287](https://www.rfc-editor.org/rfc/rfc4287)。

补充：2026-10-13按[Crossref官方REST说明](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)加入无需密钥的学术备用源，并通过真实查询。产品级 smoke 对Crossref/BBC/Federal Reserve各请求一次，验证资料→报告→SQLite读取成功，无付费或模型请求。
