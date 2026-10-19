# 读者专属短解读

## 目标与入口

新闻全库评分和排序完成后，只为前十条生成简短、针对读者的介绍；不在评分阶段生成长篇筛选理由。标题、兴趣分、折叠AI解读维持现有界面，历史报告不自动重写。启用模型后点击“AI重新整理”可用已存新闻和分数生成新短解读。

“兴趣与领域 → 阅读与表达偏好”的“分领域阅读偏好”每行描述一个领域的专业程度、关心的问题和表达习惯，例如“凝聚态：expert，优先群论构造与投影表示”“AI：beginner，先讲具体变化并解释必要术语”。保存直接生效；聊天归纳使用domain_reading_rules候选，必须经原有确认/撤销流程生效。每账号私有，旧档案默认空列表，不推断未确认偏好。

## 实现与契约

SynthesisService.generate(news_brief=True)由writing_framework.py按稳定编辑原则、任务契约、按论文/新闻类型选择的虚构对照示例装配提示；不再叠加研究长综合策略。研究综合仍使用explanation.md。完整职责、输入协议和评审边界见[分层写作框架](layered-writing-framework.md)。借鉴Claude的职责分离、Codex的信息组织和Pi的模块装配，不安装这些运行时，不绑定模型供应商。

评分不增加全库“文章×兴趣”筛选记录。解读请求发送当前查询、单篇证据和writing_context：reader_model按用途、确认兴趣、知识程度与表达约定分组，article_lens限定本篇问题和证据范围；不重复发送平铺reader。由模型从confirmed_interests原样选择非空focus；没有直接命中或属于公共事件时可为空，以通用视角介绍有依据的变化。focus为解读视角而非搜索命中证明；无兴趣时必须为空。不因与兴趣无关而清空有依据的claims，不写回新增兴趣。只在最终所选新闻做这一选择，无额外的模型选择调用。长文阅读复用相同上下文编译器和本次临时问题。

正文使用最多2条内部claims，由程序合为连贯自然段，不固定加阅读建议、项目符号或条件标签。中文目标80—140字符、上限180字符，英文700字符；字数合计statement、conditions和uncertainty，不计引文。最多2个必要条件、无follow_up。允许更短，不凑字，关键限定优先。部分套话由本地brief_style_invalid检查触发修订，不仅依赖模型自觉。每条必须有当前原文片段的连续引用，语义支持性仍未自动验证。

本地校验focus、lead_subject开头、主张数、长度、条件和引用，不合格最多修正2轮。主体必须是发生核心变化的具体对象，先核心变化再讲读者关注的方法和指标，不以无背景的跑分开场。代码围栏和纯空白差异可无模型修复，仍拒绝杜撰引用。前十条通过[正文阅读层](news-reading-and-library.md)补读网页、断点续读。短解读可同时提取实体/事实，但归档失败只标待校验，不丢弃已经通过正文检查的解读。详见[核心前置与校验恢复](editorial-lead-and-source-expansion.md)。

domain_reading_rules最多30条×120字符，模型按文章领域适用；interests最多30条，expression_rules/glossary最多12条传入。分领域规则优先于全局knowledge_level，但不能覆盖事实与输出契约。这仍是模型的语义适配，不是严格类型化的领域知识图谱。用户反馈经确认更新规则；尚无自动挑选个人满意文章作为few-shot、自动微调或自动采集领域术语。

短解读缓存版本BRIEF_VERSION=4与编辑/任务/阅读/修订/示例文件内容摘要、上下文版本及用户档案/问题/连接元数据共同组成。改写风格文件后不再误用旧解读，但不使兴趣分失效；已有历史报告仍可读取。新报告ranked_items.reader_focus保存该条选择的兴趣，界面不新增冗余标签。机械校验额外拒绝重复主张与换行列表；默认semantic_review=not_reviewed。语义评审与机械验收分开，只有显式实验注入评审器才增加请求，并将低分问题带回有限修订循环。

## 验证与费用

tests/unit/test_news_brief.py覆盖同文双画像请求隔离、分领域规则传递、长度/兴趣/引用错误修订、账号隔离、确认与撤销。回放模型结果是预设的，只证明管线契约，不证明真实模型理解能力。

真实小规模对照入口：

```powershell
.\.venv\Scripts\python.exe -m newspaper_agent.synthesis.brief_evaluation --live --connection-user 你的应用账号
```

也可--live --provider配置名读取环境变量密钥。不加--live不发送请求。脚本仅发送公开论文的标注概述、明确虚构的新闻和测试画像，不读私人档案；使用当前应用账号已选连接时密钥只在内存解密，不输出。4个初稿、每个最多1轮修正，最多8次逻辑请求；连接HTTP尝试固定1次。不设置新的等待时限，可用Ctrl+C中止。

结果包含两组相同资料、不同画像的解读、字数、兴趣视角、实际逻辑请求数及关键词辅助检查。文本差异或关键词命中不能证明推荐正确或事实准确，仍应人工对照证据判断；测试不声称已提高人类理解效率。

## 参考

- [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：精简高信号上下文、清晰指令与代表性示例。
- [Anthropic internal-comms示例](https://github.com/anthropics/skills/blob/main/skills/internal-comms/examples/general-comms.md)：读者、用途、语气和格式分别明确。
- [Pi Skills](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md)：专用规则按任务加载。
- [SSG论文](https://arxiv.org/abs/2307.10364)：对照用公开论文；测试概述不冒充逐字原文摘录。
