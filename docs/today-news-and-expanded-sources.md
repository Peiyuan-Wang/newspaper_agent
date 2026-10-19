# 当天新闻与扩展公开信源

## 时间选择

桌面“今日简报”的时间下拉提供自动、今天（香港时间）、过去24小时、近7/30/90天。原来缺少当天选项是控件只列出7/30/90天，并不是数据量达到某个门槛才开放。“今天”和“过去24小时”是不同窗口：前者按UTC+8自然日，后者按当前时刻滚动24小时。明确查询“今天/today/过去24小时”也能识别窗口；工具对翻译后的查询保留原用户时间要求。

`ResearchSession.search`、`ResearchSources.search/collect_news`、`select_evidence`传递`time_scope=None/'today'/'24h'`。专业API以recent_days=1缩小候选，最终仍由本地精确检查。RSS采用保存的发布时间，不把抓取时间当发表时间。含时区时间戳转换到香港日期；未来时间拒绝。只有YYYY-MM-DD的条目可参与“今天”但不参与精确24小时；未知、只有年月/年份或不含时区的时间戳不纳入这两个严格窗口。原7/30/90天兼容原日期逻辑，不统一改为新的精确小时规则。

没有合格条目时显示“所选范围内暂无新闻”，不会启用模型编造、不用旧闻凑数；检索详情保留范围与来源失败。早晨、分类订阅为空、源更新频率、网络失败、单轮来源预算都可能影响数量。Feed可读不等于恰好有当天且相关的信息。

## 目录和实测

`config/sources/live.yaml`共54个入口：50个RSS及arXiv/PubMed/Crossref/Tavily。新增21个公开端点（不是21个不同网站），16个启用、5个暂停；加上原暂停项共10项暂停。无需搜索Key，仍沿用安全HTTP及RSS适配器，无登录Cookie、付费兜底、抓取代理或绕过反爬。AI评分/解读仍消耗用户模型额度；Feed免费不代表全文开放或可自由转载。

2026-10-17使用项目真实安全HTTP逐端点验证，一次采集结果如下；条数只是当次快照，不承诺持续可用：

| 分类 | 来源与端点 | 结果 |
|---|---|---|
| 中文通用科技 | [少数派](https://sspai.com/feed)、[IT之家](https://www.ithome.com/rss/)、[Solidot](https://www.solidot.org/index.rss) | 10/60/20条，启用 |
| 技术社区 | [Hacker News](https://news.ycombinator.com/rss)、[Hugging Face讨论区](https://discuss.huggingface.co/latest.rss) | 29/30条，启用 |
| 科研官方 | [NIST](https://www.nist.gov/news-events/news/rss.xml)、[NASA](https://www.nasa.gov/feed/) | 40/10条，启用 |
| 数学/物理报道 | [Quanta](https://www.quantamagazine.org/feed/)、[Phys.org物理](https://phys.org/rss-feed/physics-news/)、[ScienceDaily物理](https://www.sciencedaily.com/rss/matter_energy/physics.xml) | 5/30/60条，启用；报道不冒充论文原文 |
| AI工程报道 | [IEEE Spectrum AI](https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss) | 30条，启用 |
| arXiv学术分类 | [cs.AI](https://rss.arxiv.org/rss/cs.AI) | 200条，启用 |
| arXiv学术分类 | [cond-mat](https://rss.arxiv.org/rss/cond-mat)、[cs.LG](https://rss.arxiv.org/rss/cs.LG)、[cs.CL](https://rss.arxiv.org/rss/cs.CL)、[math.RT](https://rss.arxiv.org/rss/math.RT) | 有效Feed，均0条；启用，不伪报抓取失败 |
| 暂停待复核 | [APS Physics](https://physics.aps.org/rss)、[PRL](https://journals.aps.org/rss/recent/prl.xml) | http_error |
| 暂停待复核 | [36氪](https://36kr.com/feed)、[InfoQ](https://www.infoq.com/feed/)、[WHO](https://www.who.int/rss-feeds/news-english.xml) | invalid_xml / redirect_blocked / response_too_large |

12个端点有条目，4个有效但为空，5个失败暂停。故障不证明网站不存在或永久免费；没有为这些网站放松大小、重定向或身份权限限制。Hacker News、少数派、IT之家、Solidot是通用补充；HF/IEEE按AI，Quanta/物理报道/相关arXiv按物理数学主题匹配；NIST、NASA是官方科研新闻。通用可以参与各领域，但最后仍由AI按个人兴趣评分，不保证每条都相关。

复检命令（会读取公开互联网，不读私人数据库、不调模型，不自动改目录状态）：

```powershell
.venv\Scripts\python.exe -m newspaper_agent.research.catalog_smoke --expanded
```

单轮最多12源、3路并发；每源最多200条、本地池15分钟复用。扩容不等于每次采集50个RSS：按主题覆盖和最久未尝试轮换，详见[画像与覆盖](personalized-news-routing.md)。后台定期采集仍需用户在应用内显式开启。没有增加微信公众号、X、Instagram登录采集，也不把公开论坛内容当作权威事实。

## 验证与安全

日期离线测试固定UTC/HK跨日边界、24小时临界、未来/未知日期、带时区与纯日期；桌面控件离线测试确认参数传入业务层。公共端点实测不改变用户兴趣、源共享权限或旧报告。真实网络和模型效果需分别验收；此轮真实模型调用0次。
