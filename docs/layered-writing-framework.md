# 分层写作与个性化编辑框架

## 目标和使用

在现有全库评分→前十名正文阅读→短解读流程内，将内容选材与语言表达分开，把稳定规则、确认偏好、当前任务、示例、校验和界面各自归位。无需安装Claude/Pi或更换模型，没有新依赖，也不修改用户数据库结构。

重启桌面后，新简报自动使用分层规则。“兴趣与领域”继续填写细分兴趣、用途、分领域程度、表达规则和术语；聊天归纳的偏好仍经候选确认/撤销，不因一次检索自动写入档案。历史报告保留原样；“AI重新整理”使用新规则。未增加冗余风格按钮。

## 七层职责及实现

| 层次 | 当前实现 | 边界 |
|---|---|---|
| 模型默认行为 | 沿用现有provider/模型连接 | 没有训练或微调，模型差异仍存在 |
| 稳定编辑原则 | prompts/editorial_policy.md | 事实优先、具体对象开头、连贯短段落；不是编码人格 |
| 任务规则和示例 | news_brief.md、news_reading.md、news_examples.json；writing_framework.py组装 | 论文/新闻按需选择一组同文双读者示例；所有示例显式虚构，不当事实证据 |
| 长期读者偏好 | reader_context.py从现有已确认档案编译reader_model | 按用途、兴趣、领域、knowledge、expression分组，不读取未确认建议或历史私密反馈，不改变存储层 |
| 本篇视角与上下文 | article_lens包含本次问题、资料类型、证据ID及内容等级；模型返回focus及带依据claims | 视角不是额外的评分阶段，不增加全库筛选理由，不把临时请求变成长期兴趣 |
| 校验和修订 | validate_brief机械检查；brief_quality.py可选语义评审 | 默认只机械校验；测试显式选择语义评审，不冒充事实核验或默认额外收费 |
| 用户呈现 | 现有标题/分数/折叠短解读与独立进度/详情 | 模型不控制按钮、排版、评分和状态；不凭空生成图或时间线 |

## 读者上下文和请求装配

reader_profile采用白名单：简单兴趣、分领域规则各30条，表达规则、术语等各最多12条；额外读取最多12个已确认且未过期的interest_units。confirmed_interests由简单兴趣及单元主题合并去重，最多42个。上下文版本为2；单元的具体问题、方法、用途和领域程度与表达规则分开，详见[画像与覆盖](personalized-news-routing.md)。article_context编译得到：

```text
writing_context
├─ reader_model
│  ├─ purpose / confirmed_interests / domains
│  ├─ interest_units: topic / domain / question / purpose / methods / knowledge_level / priority / expires_on
│  ├─ knowledge: default / domain_rules
│  └─ expression: language / style / reading_level / expression_rules / glossary
└─ article_lens
   ├─ scope: current_article_only
   ├─ question
   └─ article_types / evidence_ids / content_levels
```

新闻阅读和解读仅发送该结构，不另外重复发送平铺reader。研究长综合保留原reader协议和baseline/prompt/structured，不强行套用短新闻规范。短解读不再叠加研究长综合策略，以免“全面概括/拆成要点”与自然短段落冲突。

分领域规则仍是用户可编辑的确认文本，由模型根据文章领域判断适用性；结构化分组不等于已经实现领域分类器、严格知识级别推理或个人知识图谱。全局程度是默认，适用的分领域约定优先，事实和任务边界高于表达偏好。

compose_news_prompt按editorial_policy→task_contract→synthetic_examples组装带标签的提示。模型先找文章核心变化，再选择与确认兴趣有关的事实；非空focus必须原样属于confirmed_interests，公共事件或未直接命中兴趣时允许为空并使用通用事实视角。无兴趣命中不等于证据不足，不强行绑定或新增用户兴趣。长文选段同样得到本次问题与读者模型；最终仍保留开篇定位和相关原文，不把评论当证据。

## 缓存与机械验收

BRIEF_VERSION=4。framework_revision对编辑原则、任务契约、修订规则、阅读规则、示例文件及上下文版本计算摘要，并进入解读/阅读断点版本。修改上述规则后解读缓存失效，不影响兴趣评分缓存；完整原网页正文缓存仍可复用。旧报告不自动重写。

继续检查字段、focus、lead_subject开头、最多2条主张、中文180/英文700字符、引文、条件数量及套话。额外拒绝换行列表/标题及重复主张，失败沿用最多2轮修订，不直接截断。机械通过不证明核心、关联、因果或事实正确。归档失败仍与解读分离。

writing_framework记录revision与semantic_review；默认not_reviewed，而不是把格式通过标为语义通过。正常桌面没有新增模型调用；本次没有自动抽样评审任务。

## 可选语义评审与费用

ModelBriefReviewer是显式注入SynthesisService的独立接口，使用editorial_review.md。按core_first、reader_relevance、clarity、evidence_support四维给1—5整数分，要求全部至少4分且issues为空才通过；这是一项测试阈值，不是科学准确率。问题码仅允许core_unclear、reader_mismatch、wording_unclear、unsupported_claim、missing_qualifier，不将评审任意文本当新指令或事实。

启用时，机械通过的草稿交评审；不通过把维度分和问题码送回写作模型，在同一有限修订循环内重写，只有通过的最终稿才进入claims。通常最多3次写作+3次评审；失败保留有界草稿/反馈，不把未合格内容伪装成成品。API错误仍终止当前生成，不新增无限修订。传输层重试按原连接策略另计。

metrics.model_calls包含写作和评审调用，generation_calls、semantic_review_calls分列；repair_calls只计写作修订。input/output字符统计标记character_metrics_scope=generation_only，不冒充完整评审流量。评审通过依然保留semantic_verification=unverified；模型打分不是独立事实核验，建议真实质量实验使用不同评审模型并结合人工复核。

固定实验入口：

```powershell
# 无--live：零请求
.venv\Scripts\python.exe -m newspaper_agent.synthesis.brief_evaluation
# 显式使用固定公开/虚构材料及测试画像，至多8次逻辑请求
.venv\Scripts\python.exe -m newspaper_agent.synthesis.brief_evaluation --live --connection-user 你的应用账号
# 显式增加语义评审与反馈修订，至多16次总逻辑请求
.venv\Scripts\python.exe -m newspaper_agent.synthesis.brief_evaluation --live --review --connection-user 你的应用账号
```

实验沿用已有连接读取权限边界，只读取选中连接，不读取私人档案；评审复用实验模型，可能存在同模型自评偏差。不加--live不能启用--review；--live的任一输出未通过，打印结果后返回非零状态，便于测试流程识别失败。本次仅运行离线回放，没有运行上述真实实验或消耗模型额度。

## 验证与尚未实现

test_writing_framework.py覆盖偏好白名单/限长/不变性、示例类型选择、提示内容版本、同文双画像、默认零评审、语义意见触发修订、非法评分和重复正文，以及阅读/解读上下文一致；test_news_pipeline.py检查规则版本变化只重写解读、不重评兴趣。回放模型使用预设输出，只证明接口、预算和恢复流程，不证明真实理解与文风改善。

尚未实现自动学习个人满意文章的示例库、默认线上语义评审、持续训练、知识图谱或自动因果图。现有分领域规则适用性、核心选择和语义评判仍需要真实模型与用户对照验收。
