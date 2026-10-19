# 第一版：人类中心知识综合与可回放评测

表达适配读取确认的用途、知识水平、分领域阅读规则、结构、表达规则和术语约定，不读取全部反馈历史。研究综合的表格由程序渲染；新闻简报在独立[两阶段流程](two-stage-news.md)中先评分，再以news_brief=True逐条解读前十名，使用[短解读契约](personalized-news-brief.md)（最多2条主张、中文180字符或英文700字符），不受研究综合长版模板约束。news_brief=False维持原接口。

日期：2026-10-13。状态：独立模块已实现；真实用户理解收益需要另行实验。方法论是参考，不将论文题名或样例当作已验证收益。

2026-10-14 阅读改进：输出先显示“本次找到什么”与资料数量，有模型时按编号显示重点；无模型时显示标题、日期、内容类型与最多两句/220字符的标注摘录。完整原始片段仍保存在知识对象，可在桌面展开，expert 层仍显示原文。只有元数据的条目明确标记没有摘要。摘录不被包装为模型理解或重要性评估；关键条件与未知边界仍保留。

排版补充：标题与数据概览压缩，来源按栏目分组、条目间分隔，附出版者/日期/资料类型；桌面阅读器统一中文行距、段落间距与表格留白。检索关键词、时间窗口和覆盖/失败提示在桌面「检索详情」默认折叠，不插入正文开头。必要的证据限定不隐藏。CLI 保持 Markdown 输出。

## 目标与接口

`synthesis` 接收已经检索、筛选的 `EvidenceRecord`，将资料组织为带引用锚点、条件和不确定性的主张，再渲染一个选定阅读层级。它不检索、不执行工具、不登录、不保存数据库，也不修改 AgentLoop 历史；由应用负责组装和按当前用户保存。

```python
from dataclasses import asdict
from newspaper_agent.synthesis import SynthesisService

report = SynthesisService(model).generate(
    "这项结果适用于什么情况？", evidence,
    profile={"purpose": "学习", "knowledge_level": "入门"},
    method="structured", style="standard",
)
print(report.markdown)
serializable = asdict(report)
```

`Report` 是 dataclass，字段为 `markdown`、`knowledge_payload`、`issues`、`metrics`。model 可以为 `None`，此时直接展示确定性证据列表，零模型调用。引用检查、降级、渲染均为本地 Python 标准库代码，无新依赖。

## 三种策略和三个层级

`method=baseline|prompt|structured` 分别为直接概括、清晰解释指令、先组织主张与条件再输出的指令。为了保护引用边界，三者共享严格 JSON 契约，因此 baseline 是本模块的受约束基线，不是旧 CLI 完整复刻。三者使用同一资料、接口、预算和确定性渲染器；可用于后续控制变量试验，不应预先假定 structured 更好。

`generate` 默认初稿加最多2轮校验修正，共最多3次 `model.complete(..., tools=())`；格式/引用不合格时带上一稿和错误反馈修正，网络失败不启动修正，不启用工具。显式 `compare` 对同一资料运行三个方法，最多9次逻辑调用，仍受调用方预算约束。详见[校验与修正循环](synthesis-repair.md)。

`style=brief|standard|expert` 分别显示最多 2、4、8 条主张。默认 standard。所有层级都显示关键条件和不确定性；被省略主张的条件也保留。expert 额外展示逐字锚点和资料原片段。只展示一种层级，允许下一轮展开。

## 知识对象与真实边界

每条主张具有 `statement`、`conditions`、`evidence_refs`、`uncertainty`、`status`。引用只能使用本次给定 ID，引文必须逐字出现在该资料的 `snippet`。代码计算 snippet 字符位置和 SHA-256 快照标识，模型不能编造页码或定位。

通过检查后的 `anchor_checked` **仅表示引用 ID 和原文锚点存在**；`semantic_verification` 始终是 `unverified`。这不等于主张被原文语义支持，更不等于现实事实正确。`disputed` 表示模型提议存在待核对分歧，不裁定真伪。所有 `is_fixture=true` 资料都有醒目标记；摘要/片段不会称为已读全文，未知发布时间仍未知。

条件保留有两层：Prompt 要求保留结论前提；本地代码额外保留资料 `payload.required_conditions` 中实际出现在 snippet 的文本，以及包含常见限制词的完整句子。后者是启发式兜底，不能保证识别所有专业语义条件。模型也可能给合法引文附上不受支持的断言，需要人工支持性抽检；不能把工程测试当事实正确率。

## 预算、输入和失败行为

最多使用8条资料，研究综合单个片段最多4000字符，新闻短解读单条最多16000字符，总量最多16000字符。新闻全文由独立阅读层获取和分段，不直接填充原摘要字段；选出的仍是原文而非模型摘要。超限整条不进入模型并报告context_evidence_omitted，不截取开头伪装完整。相同ID只保留本轮第一条。

问题最多2000字符，模型JSON回复最多20000字符。拒绝重复JSON键、未知字段、错误结构、无效引文、伪造来源、越权verified状态和模型工具请求。校验失败先修正；耗尽后保留有界草稿及错误，正文退回资料清单，不把未校验草稿当结论，也不回显提供方异常。空资料零模型调用，明确说资料不足。

profile发送purpose、knowledge_level、reading_level、language、style、output_format及有界expression_rules、glossary、interests、domains白名单；不发送任意字段或全部反馈历史。片段发送到用户选择的模型服务，不能宣传完全离线。研究综合知识对象随报告存储，新闻单条解读及修正稿独立按账号保存、由报告ID引用，不把十份修正历史重复塞进正文报告。

Markdown 文本中的 HTML、自带链接语法会转义，引用链接只从给定资料产生；这里只负责显示，不会访问任何链接。外部资料仍不可信，提示词无法替代上游访问控制或人工核查。

## 固定评测与真实实验

```powershell
.\.venv\Scripts\python.exe -m newspaper_agent.synthesis.evaluation
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_synthesis.py
```

`evals/synthesis-cases.json` 包含 12 个明确标注 synthetic 的固定案例，包括适用范围、相关/因果、资料分歧、未知日期、时间窗口、币种、英文条件、网页中的指令、条件性收益、反例、无资料和伪引文。三个方法一共 36 个离线回放；回放模型返回固定响应，完全不请求真实模型，不据此比较三种 Prompt 的效果。

离线报告分别记录条件保留、无效引用拒绝、虚构标记、字符数、墙钟耗时和模型接口调用次数。真实 token 当前协议没有提供，返回 `None`；不拿字符数冒充 token。阅读时间和理解正确率同样为 `None`。这里的模拟 model_calls 不代表真实计费请求，评测同时显式给出 actual_model_requests=0。

下一步的真实对照应固定同一材料和模型配置，显式调用 `compare`，把匿名结果按随机顺序提供给读者，记录阅读时间、复述/迁移题正确率、条件遗漏和人工证据支持性检查。分别报告这些指标；不把更短、模型自评高或用户更喜欢当作事实准确率或理解提升证明。
