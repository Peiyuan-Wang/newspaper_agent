# AIHOT公开免费信源

## 范围与使用

参考[AIHOT公开示例目录](https://github.com/KKKKhazix/AIHOT/blob/main/industry/sources.json)，将其中全部18个RSS地址登记到config/sources/live.yaml。原有OpenAI、Hugging Face、TechCrunch AI保留原ID，不重复创建；其余15个为新增来源。这里只复用公开订阅地址，未复制其采集程序或接入付费服务。

重启桌面应用，在“信息来源”选择“新闻”或“学术研究”查看；今日简报选择“订阅简报”、AI相关兴趣和时间窗口后更新。来源按领域、主题和原有容量限制路由，不保证18源每轮全部参与。每轮最多12源、3路并发，每源200条上限；通用源仍预留最多2个位置。新增来源没有改动用户兴趣、私人订阅或共享审批权限。

## 分类、地址与真实验证

2026-10-17使用项目RSS适配器对18个地址各执行一次collect。以下条数为这次Feed解析结果，不是当天新闻数量或永久可用性承诺。

| 分类 | 名称 / ID | 公开Feed | 结果 |
|---|---|---|---|
| 官方 | OpenAI / ai_openai | https://openai.com/news/rss.xml | 启用，200条 |
| 官方研究 | DeepMind / ai_deepmind | https://deepmind.google/blog/rss.xml | 暂停，unsupported_encoding |
| 官方研究 | Google Research / ai_google_research | https://research.google/blog/rss/ | 启用，100条 |
| 官方开源 | Hugging Face / ai_huggingface | https://huggingface.co/blog/feed.xml | 启用，200条 |
| 官方研究 | Microsoft Research / ai_microsoft_research | https://www.microsoft.com/en-us/research/feed/ | 启用，10条 |
| 官方 | NVIDIA / ai_nvidia | https://blogs.nvidia.com/feed/ | 启用，18条 |
| 官方教程 | AWS ML / ai_aws_ml | https://aws.amazon.com/blogs/machine-learning/feed/ | 启用，20条 |
| 官方开源 | GitHub AI / ai_github | https://github.blog/ai-and-ml/feed/ | 暂停，response_too_large |
| 官方 | Mistral / ai_mistral | https://mistral.ai/news/rss | 启用，90条 |
| 学术研究 | Berkeley AI Research / ai_bair | https://bair.berkeley.edu/blog/feed.xml | 启用，10条 |
| 媒体 | The Verge AI / ai_verge | https://www.theverge.com/rss/ai-artificial-intelligence/index.xml | 启用，10条 |
| 媒体 | TechCrunch AI / ai_techcrunch | https://techcrunch.com/category/artificial-intelligence/feed/ | 启用，20条 |
| 媒体 | Ars Technica AI / ai_ars | https://arstechnica.com/ai/feed/ | 启用，20条 |
| 媒体 | MIT Technology Review AI / ai_mit_review | https://www.technologyreview.com/topic/artificial-intelligence/feed | 启用，10条 |
| 媒体 | The Decoder / ai_decoder | https://the-decoder.com/feed/ | 启用，10条 |
| 个人技术观察 | Simon Willison / ai_simon | https://simonwillison.net/atom/everything/ | 启用，30条 |
| 个人周刊 | Import AI / ai_import | https://importai.substack.com/feed | 启用，20条 |
| 技术访谈 | Latent Space / ai_latent | https://www.latent.space/feed | 启用，20条 |

表中编辑分类用于说明来源属性；当前界面仍沿用news/academic领域和ai/agent/llm主题，不新增一套栏目或分类控件。官方/研究博客使用primary角色，媒体/个人使用discovery角色。它们不标为general，以免纯金融或医学任务被AI资讯挤占。

## 输入输出、失败与安全边界

输入为固定公开HTTPS订阅地址；输出为现有EvidenceRecord，保留source_id、标题、链接、日期、订阅片段和feed内容等级。读取Feed不等于阅读全文，正文仍只对评分前十名另行读取；不能绕过付费墙或登录。

DeepMind的响应编码当前不支持，GitHub响应超过客户端默认1MB限制。本次不放宽安全边界，不解压未知响应，不使用付费代理兜底；这两个来源登记但暂停，不参与正常采集。复检成功后仍须明确维护目录状态，诊断命令不会自动启用它们。

RSS本身不需要搜索服务Key或按次数据费，但网络流量、模型评分/解读可能有费用；文章可能仅有摘要或存在付费全文，公开订阅不等于允许转载。未加入SocialData、微信公众号付费转RSS、Jina收费渲染，也没有宣称全网/X/公众号覆盖。

## 验证

```powershell
.venv\Scripts\python.exe -m newspaper_agent.research.catalog_smoke --aihot
.venv\Scripts\python.exe -m pytest -q tests/unit/test_aihot_sources.py tests/unit/test_subscriptions.py
```

第一条仅主动访问上述18个公开Feed（包含暂停项），输出来源ID、状态、条数或安全错误码；不读取账号、不调用模型、不写数据库和来源状态。离线测试覆盖目录完整性、端点去重、RSS解析、领域路由、预算、通用预留与暂停隔离。
