# Human-Centered Knowledge Synthesis：项目可参考的方法论

> **文档性质：非排他性设计参考（Reference, not specification）**\
> **面向对象：** 现有项目的开发者、Codex 等编码 Agent\
> **目标：** 在已有信息收集、研究、Agent 或知识管理系统基础上，按需提升输出的可理解性、有效性、简洁性、系统性与可验证性。\
> **状态：** 候选方法和实验假设；不是唯一指导、既定架构、必须实现的需求清单，也不代表所有引用方法都已在当前项目验证。

## 0. 给 Codex 的集成约定（请优先阅读）

**不要把本文档当作要求重构项目的指令。** 先了解当前代码、数据流、技术栈、测试体系、约束和用户已经实现的功能，再提出适配方案。

1. **先调查，再实施。** 阅读 README、架构说明、已有 prompts/skills、agents、检索链路、数据模型、UI、日志、测试与配置；总结目前哪些能力已经存在，哪些是真正缺口。
2. **复用优先。** 尽量在既有接口增加独立、可替换的解释与知识综合能力；不要为了契合本文而强制采用 GraphRAG、复杂多 Agent 架构或新的数据库。
3. **先列实施建议和取舍。** 说明哪些内容值得做、哪些暂缓、可能的兼容风险及最小改动方案。对重大架构变化先征求用户意见。
4. **增量、可回滚。** 优先以开关、插件、Skill、可插拔 pipeline stage 或单独 endpoint 接入；保持既有工作流和默认行为，除非用户明确同意修改。
5. **不要把设计假设说成研究结论。** 样例评分函数、数据结构、代理分工均为候选方案，应以真实数据和用户反馈验证。
6. **严格区分理解层与事实层。** 不允许为了通俗或简短省略改变结论的条件、适用范围、不确定性或反例。
7. **保留来源可追溯性。** 能追溯到文档、位置和原始片段的关键主张，才可标为已核查；不要把 LLM 自我判断等同于验证。
8. **提供可测量的增益。** 至少比较现有版本与新增功能在事实保真、可读性、用户任务完成、成本和延迟上的差异。

建议 Codex 第一轮输出：**现有架构梳理 → 能力缺口 → 2–3 种集成选项 → 推荐的最小 MVP → 改动文件与测试计划**。本文不要求一次实现所有建议。

---

## 1. 研究动机与目标边界

AI 对检索、推理、形式验证和内容生成的加速，不等于对人类理解的等比例加速。一个正确结论即使形式上可验证，也可能缺乏直觉、背景、关键假设、知识依赖和与既有工作的联系。项目希望研究的是：如何把高吞吐量的信息生产转化为**可被人类迅速理解、核验、记忆和复用的知识**。

核心区分：

- **Correctness / factuality：** 输出是否正确或有证据支持。
- **Conciseness：** 是否删掉了无关、重复、空泛的内容。
- **Comprehensibility：** 指定读者能否真正理解及复述。
- **Actionability：** 读者能否用所得知识解决后续问题。
- **Systematicity：** 单个知识点能否与已有概念、问题、方法组成可导航的知识结构。

**不建议将“token 越少越好”作为目标。** 有时定义、例子和必要铺垫会增加篇幅，却显著降低理解难度。更合适的目标是：在满足准确性与覆盖要求的条件下，提高**单位阅读时间获得的可使用知识**。

本文的范围主要是**信息收集 → 筛选 → 结构化 → 解释 → 核查 → 反馈**。它可作为现有搜索/RAG/研究 Agent 的后处理层，也可以逐步成为更深的知识中间表示层。它**不预设**必须训练新模型。

### 1.1 可检验的项目假设

> 在相同资料与模型条件下，带有证据关联、结构化知识中间层与读者适配解释规划的系统，相比直接摘要或简单“请简洁回答”提示，可以在不显著损害准确性的前提下，提高人类理解与应用效率。

这是**待检验假设**，不是已经证明的结论。

---

## 2. 设计原则：可供选择的机制

### P1. 先形成事实与关系，再生成语言

如果资源允许，将系统划分为：

```text
Sources
  → Retrieve & Filter
  → Atomic Claims + Evidence
  → Concepts / Dependencies / Conflicts
  → Explanation Plan (reader- and task-aware)
  → Text / Table / Diagram / Layered UI
  → Verification + Feedback
```

不是所有场景都必须经过全部阶段。简单问答可以直接回答；跨论文综合则更需要中间层。

### P2. 原子事实与证据锚点

把重要断言拆解成可单独确认的主张，标记其条件、来源、证据位置、证据等级、冲突及不确定性。保证压缩和改写后仍能回溯。参考 FActScore 的原子事实评测思想。

候选结构（仅示意，优先兼容现有 schema）：

```json
{
  "id": "claim-001",
  "statement": "条件 A 下得到结论 B",
  "assumptions": ["条件 A"],
  "source_refs": [{"document_id": "paper-001", "locator": "Section 3, Theorem 2"}],
  "status": "source_supported",
  "uncertainty": "尚未完成独立形式验证",
  "prerequisite_concepts": ["concept-001"],
  "relations": [{"type": "extends", "target": "claim-older"}]
}
```

避免只有 `confidence=verified` 而无可审计证据。区分 `source_supported`、`cross_checked`、`formally_verified`、`disputed`、`unverified` 等状态是否适合项目现状。

### P3. 概念依赖与全局知识组织

关注概念之间的 `prerequisite`、`generalizes`、`contradicts`、`uses_method`、`equivalent_under`、`open_question` 等关系。可选实现：

- 小规模：关系型表、JSON、Markdown 链接或已有知识库；
- 中规模：层次化摘要 + 可检索概念索引；
- 大规模跨文献：GraphRAG/知识图谱、社区摘要或其他图检索。

**图不是目的。** 若普通结构化索引足够，不需要引入图数据库。图中每条重要边应尽可能有来源。

### P4. 读者适配与解释规划

根据可知的读者背景、问题意图和可用时间，决定：先给结论还是先给背景，哪些概念需要解释，哪里该给例子，哪些推导需要保留。不要猜测敏感画像；无法判断背景时提供简明默认版与按需展开入口。

可选择的展开层：

- **快速版（约 30 秒）：** 直接回答 + 关键结论 + 最重要的限定；
- **标准版（约 2 分钟）：** 核心逻辑、必要概念、示例与适用边界；
- **专业版：** 定义、推导、原始证据、例外、关联文献与争议。

三层不是严格字数配额；所有层级都应避免关键事实失真。

### P5. 以理解成本为优化对象

候选编辑策略：结论前置、定义就地解释、每段只推进一个主要论点、减少无意义连接词、将可比较信息排成表格、将概念依赖用图表示、把非关键技术细节放到可展开部分。

不要机械地追求短句、列表或图表；数学证明、争议性论证和技术限制常需要完整文字。**信息密度不等于认知可用性。**

### P6. 表达之后必须回查来源

最终输出逐条映射回原子事实，检查：断言是否有证据、前提是否省略、相关性是否夸大、因果是否被错误推断、不同来源是否冲突、引文是否匹配。不确定时标注，不要自动伪造共识。

### P7. 按需使用工具，而非无条件多 Agent

先量化现有流水线的瓶颈。只有在任务独立性高、并行搜索有明显收益时才引入多个研究 Agent；否则优先单 Agent + 确定性处理步骤。拆分角色时避免“多个 Agent 相互表扬”代替真实来源校验。

---

## 3. 可选架构与 Harness 策略

### 3.1 三档架构（按复杂度递增）

**A. 轻量 Skill / Post-processor（推荐最先试验）**

```text
Existing response → extract key claims → rewrite for reader → quality check → final response
```

- 优点：改动小、容易做 A/B 测试、便于回滚。
- 限制：对复杂跨来源问题的全局组织能力有限；若没有原始资料，无法真正核查。
- 适用：现有项目已经有可信检索/研究结果，主要希望提升表达质量。

**B. Structured Knowledge Layer**

```text
Existing research output + provenance
  → Claim/Concept extraction
  → Evidence-linked knowledge store
  → Explanation planner
  → Renderer + verifier
```

- 优点：同一知识可反复渲染为不同深度；便于核查与复用。
- 限制：需要中间表示和转换测试；抽取错误会被后续流程放大。
- 适用：跨多轮或多文档的持续研究与知识管理。

**C. Full Research & Knowledge Synthesis Harness**

```text
Planner → Search/Reader agents → Claim dedup & conflicts
        → Knowledge organization → Reader model → Explain
        → Source validation → Human feedback / memory
```

- 优点：全流程可控，可做深度研究。
- 限制：成本、延迟、上下文和维护复杂度显著增长。
- 适用：现有系统确认在资料综合与知识复用方面存在瓶颈之后。

### 3.2 可选 Agent / Skill 职责

| 能力 | 输入 | 输出 | 验证条件 |
|---|---|---|---|
| `source-filter` | 搜索结果、资料 | 去重和相关来源 | 不误删关键反例或权威来源 |
| `claim-extractor` | 原始正文 | 带定位的原子主张 | 可以回查文本位置 |
| `conflict-checker` | 多来源主张 | 一致/冲突/待查 | 不把分歧强行合并 |
| `concept-organizer` | 主张与术语 | 概念依赖和关系 | 关键关系有来源或标为推断 |
| `explanation-planner` | 知识结构、读者需求 | 解释顺序与重点 | 不遗漏理解必需前提 |
| `conciseness-editor` | 草稿 | 压缩版本 | 保留条件与证据结论 |
| `answer-verifier` | 成稿、证据 | 问题清单与修正 | 不用“模型自信”代替核验 |
| `comprehension-evaluator` | 输出、测试题 | 理解/迁移评估 | 有人类测试的校准 |

这些是逻辑职责，可以合并在一个 Agent、不同函数或流水线阶段中，无需分别启动八个 Agent。

### 3.3 Context Engineering

- 上下文优先包含：当前问题、任务约束、高可信来源、必要的结构化状态、尚未解决的冲突。
- 不把大量重复的长摘要直接拼进 prompt。
- 在长任务中区分可持久保存的知识状态与临时思考记录。
- Compaction 后必须保留关键假设、来源和未解决事项；可记录压缩前后丢失率。
- 对不同阶段采用不同的信息预算和输出契约，但不要为了压缩破坏可追溯性。

### 3.4 输出方式

建议将**内容表示**与**展现组件**分离：相同知识对象既能生成 Markdown，也能转为短卡、可折叠长解释、对比表、依赖图、时间线。只在用户任务明显受益时才使用可视化；不应以图替代理由和证据。

---

## 4. 示例：Human-Centered Explanation Skill

如项目支持 Skills，可将以下内容拆成独立 Skill；否则可作为 prompt/pipeline checklist。**示例，不强制原样采用。**

```markdown
# Human-Centered Explanation

Goal: Improve human comprehension while maintaining factual fidelity.

1. Identify the user's exact question, likely prerequisites, and intended use.
2. Extract the main conclusion, assumptions, supporting claims, limits, and sources.
3. Plan the explanation in a dependency-aware order.
4. Give the direct answer first when appropriate.
5. Define required unfamiliar terms near first use; prefer concrete examples when helpful.
6. Remove repeated claims, generic introductions, and unnecessary jargon.
7. Keep exceptions, evidence qualification, and uncertainty.
8. Provide optional deeper layers only when useful or requested.
9. Verify the final interpretation against the original evidence.
10. Evaluate whether the reader could restate and apply the key idea.

Do not optimize for minimal token count at the expense of comprehension.
```

一个值得引入的**双输出契约**是：模型内部先得到 `knowledge_payload`（主张/证据/条件/关系），再生成 `reader_view`（适合阅读的表达）；最后检查二者语义一致。`knowledge_payload` 不必展示给普通用户，但应在必要时可审计。

---

## 5. 评测设计：先验证是否有效

### 5.1 最小对照实验

选择一批真实问题，至少对比：

- **Baseline：** 现有项目输出；
- **Prompt-only：** 现有输出 + 简洁清晰指令；
- **Skill：** 有明确提取、重写、核查流程的输出；
- **Structured：** 若已实现，再与加入知识中间层的输出比较。

控制输入资料、模型版本、温度/采样、预算和目标读者背景，减少混淆变量。问题集包含：简单问答、多文档综述、复杂概念解释、带相反证据的议题、数学推导。

### 5.2 评测维度

| 维度 | 建议测法 | 常见失败 |
|---|---|---|
| 事实正确 / 证据支持 | 原子主张核对来源；人工抽检 | 伪引用、误引、超出证据 |
| 关键条件完整性 | 检查假设、例外、适用域 | 简洁化后结论过强 |
| 结构与覆盖 | 必需概念/关系召回 | 只有漂亮摘要但无体系 |
| 真实理解 | 阅读后复述、选择与解释题 | 用户以为懂了但复述错误 |
| 知识迁移 | 新情境应用题 | 只能记住原文 |
| 阅读效率 | 时间 + 测试正确率分别报告 | 只看 token 数 |
| 用户体验 | 感知负荷、满意度、阅读偏好 | 过度结构化、冗余 UI |
| 工程成本 | tokens、调用数、延迟、价格 | 质量提升很小而成本大幅增加 |

可以把 `理解测试正确率 / 阅读时间` 作为探索性指标，但**不要只优化这一比值**，因为它会掩盖准确率下降的代价。LLM-as-judge 可用于开发阶段，但须以盲测、人工评审和真实读者行为校准。

### 5.3 建议的最小验收标准（由项目自行调整）

- 不引入新的严重事实错误或引用错误；
- 重要限定条件保留；
- 至少一个明确的人类理解指标不劣于 baseline，理想情况下显著提升；
- 成本与延迟增幅可接受，且可关闭新功能；
- 输出仍适合现有用户使用方式。

不要事先宣称一定有百分之多少的提升；收集样本后设定统计分析方案。

---

## 6. 渐进式实施路线（仅供参考）

**Phase 0 — Audit：** 理清当前架构、输入输出、来源追溯、已有 Skill 与评测；选取具体失败案例。

**Phase 1 — Minimal Skill：** 加入可选的解释与精简流程，设计固定输出约束和回查；进行小规模 A/B。

**Phase 2 — Evidence-linked Claims：** 如果 Phase 1 暴露事实丢失或多文档综合失败，再增加原子主张及可追溯来源；写数据契约和单元测试。

**Phase 3 — Knowledge Structure：** 对反复出现的概念构建依赖、等价与冲突关系；按规模决定是否使用层次摘要、GraphRAG 或图数据库。

**Phase 4 — Progressive Reader UI：** 视现有前端而定，加入短版/标准版/专业版切换、术语展开、证据回溯；不要强行改变既有页面布局。

**Phase 5 — Comprehension Benchmark：** 加入人类理解、迁移、阅读时间和工程成本评测；基于数据决定是否进行强化学习、偏好优化或专用模型训练。

每期应提供：改动边界、风险、测试、回滚方式与样例对比。没有明确收益时，可以停止在轻量方案。

---

## 7. 风险与反模式

1. **过度摘要：** 把非平凡前提、边界条件、反例删除，让表述显得更“确定”。
2. **伪知识体系：** 模型自动生成看似合理的概念联系，但无法找到来源。
3. **多 Agent 过度工程：** 消耗大量 token，却没有可测量收益。
4. **自我评价闭环：** 由生成模型自己给自己高分，误以为真实用户理解更好。
5. **通俗化失真：** 用不严格的类比代替定义，却没标清类比的有效范围。
6. **只做形式优化：** 让回答更短或更漂亮，但事实质量、理解效率毫无改善。
7. **忽略专业用户：** 一味避免术语导致专业表达冗长；对专家应允许高密度严格语言。
8. **引用污染：** 引用存在，但不支持所附结论；需要按主张核查。
9. **不可控默认变更：** 对原项目正常功能产生意外变化；建议保留 feature flag 与回归测试。

---

## 8. 推荐阅读与出处

以下是供进一步调研的**参考文献而非强制技术依赖**。论文的具体结论、年份、适用场景和官方实现，应在落地前检查原始资料及最新版本。尤其不要把研究论文和厂商 Harness 博文混为一类证据。

### 研究论文

1. Tao, *Mathematics in the Age of AI*（2026）：数学 AI 与人类数学理解的关系。https://arxiv.org/abs/2608.16753
2. *The Sufficiency–Conciseness Trade-off in LLM Self-Explanation from an Information Bottleneck Perspective*（2026）：解释的简洁性与充分性。https://aclanthology.org/2026.lrec-1.167/
3. Goyal et al., *From Local to Global: A Graph RAG Approach to Query-Focused Summarization*（2024）：跨文档图式综合。https://arxiv.org/abs/2404.16130
4. Sarthi et al., *RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval*（2024）：层次化摘要与检索。https://arxiv.org/abs/2401.18059
5. Min et al., *FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation*（2023）：原子事实准确性。https://arxiv.org/abs/2305.14251
6. Liu et al., *Lost in the Middle: How Language Models Use Long Contexts*（2023/2024）：长上下文中信息位置的影响。https://arxiv.org/abs/2307.03172
7. Liu et al., *G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment*（2023）：基于模型的文本质量评价与限制。https://arxiv.org/abs/2303.16634
8. Adams et al., *From Sparse to Dense: GPT-4 Summarization with Chain of Density Prompting*（2023）：迭代提升摘要信息密度。https://arxiv.org/abs/2309.04269
9. Asai et al., *Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection*（2023/2024）：检索与自我核查。https://arxiv.org/abs/2310.11511

### 公开工程与 Harness 经验

10. Anthropic, *Effective Context Engineering for AI Agents*：上下文选择、压缩与状态管理。https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
11. Anthropic, *Equipping Agents for the Real World with Agent Skills*：模块化流程与按需加载。https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
12. Anthropic, *How We Built Our Multi-Agent Research System*：研究型 Agent 编排。https://www.anthropic.com/engineering/multi-agent-research-system
13. OpenAI, *Unlocking the Codex Harness*：Codex Harness 的公开工程思路。https://openai.com/index/unlocking-the-codex-harness/
14. OpenAI, *Introducing Deep Research*：深度检索与研究工作流。https://openai.com/index/introducing-deep-research/
15. Microsoft Research, GraphRAG 项目：https://github.com/microsoft/graphrag

**参考原则：** 论文提供实验方法和局部证据；官方博客介绍对外公开的工程实践。对于闭源模型/服务，不能从博客推断全部私有训练策略、模型内部结构或完整 harness。

---

## 9. 可直接交给 Codex 的任务描述

> 请把 `human_centered_knowledge_synthesis_methodology.md` 当作**可选的方法论参考**，不要当作唯一指导或必须全部实现的规范。首先分析我目前项目的目标、目录结构、已有 Agent/Skill、检索与知识表示流程、界面和测试。识别与本文思路的重叠、真实缺口及兼容约束。优先设计**最小、可插拔、可回滚**的“人类可理解的知识组织与解释”能力，保留现有项目功能与设计。先提交一份架构审计、候选集成方案与测试计划；经确认后分阶段实现。重点关注事实可追溯、重要条件不丢失、分层解释、可选的知识关联、真实理解效率评估，以及成本与延迟。可以合理拒绝本文不适用的方法，并解释取舍；不要盲目新增多 Agent、图数据库或大规模重构。

---

## 10. 一句话定位

**不是让 AI 单纯说得更少，而是让它以可验证、可组织、可适配读者的方式，帮助人类更快获得真正可使用的知识。**
