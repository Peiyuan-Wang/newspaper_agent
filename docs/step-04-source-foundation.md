# 第四步：专业信息来源基础层

更新日期：2026-10-09。已实现离线基础层，不代表真实联网搜索已经完成。

后续进展：2026-10-13已新增独立真实研究入口，详见 [第一版使用手册](research-v1.md)。本页继续说明原有离线教学命令，它们仍不联网。

## 1. 现在能做什么

- 用统一接口表示来源、检索请求、证据和检索结果。
- 按 academic（学术）、medical（医学）、finance（金融）、news（新闻）、general（通用）选择来源；支持资料类型、地域、语言筛选。
- 多领域请求优先覆盖不同领域；不支持的筛选条件会产生警告，不悄悄忽略条件。
- 通过两个模型工具完成“检索 → 读取证据 → 回答”。不把每个来源都变成一份工具定义。
- 将用户提供的 HTTPS 来源登记为候选；按用户标识保存反馈，并允许撤销。

默认目录只有六个虚构样例来源启用（学术有两个，用于比较反馈排序）。arXiv 和 SEC 是未启用的真实来源条目，没有实现访问适配器。样例不进行关键词匹配，不提供最新信息，也不能用于论文、医疗、投资或新闻事实判断。

## 2. 先运行不需要密钥的演示

在项目根目录的 PowerShell 中运行，不是在 Python 的 `>>>` 中运行：

```powershell
.\.venv\Scripts\python.exe -m newspaper_agent.sources demo --domain academic --query "AI论文"
.\.venv\Scripts\python.exe -m newspaper_agent.sources route --domain academic --domain medical --kind paper
.\.venv\Scripts\python.exe -m newspaper_agent.sources list
```

`demo` 使用固定的模拟模型，执行两次工具调用，显示虚构样例和未知发布时间。它既不调用真实 LLM，也不联网。`route` 只显示来源选择；没有已有数据库时，这两个命令不创建数据库。

如需让已经配置的真实模型决定是否调用离线检索工具：

```powershell
.\.venv\Scripts\python.exe -m newspaper_agent --mode live --provider custom_6sol --information --interactive
```

这里的 **模型请求仍然联网并可能计费，检索工具不联网**。模型不一定每句话都调用工具。该模式还保留加法工具，并加载 `prompts/information.md`。`/reset` 清空对话和证据缓存，但保留检索安全提示。真实模型的工具调用仍需用户单独验收；离线测试不是中转站实测。

## 3. 文件与调用流程

```text
用户输入 → AgentLoop → 模型返回 search_information 调用
                             ↓
                       ToolRegistry 校验参数
                             ↓
                     SourceService.search
                             ↓
                  SourceCatalog.route 选择来源
                             ↓
                     FixtureAdapter 返回证据
                             ↓
                 工具结果回传模型（带 evidence_id）
                             ↓
           模型可调用 fetch_evidence → 读取已返回的证据
                             ↓
                    模型组织最终回答与引用
```

| 文件 | 作用 |
|---|---|
| `sources/contracts.py` | `SourceDescriptor` 描述来源；`SearchRequest` 描述需求；`EvidenceRecord` 保存来源、链接、片段、时间、资料类型；`SourceAdapter` 规定 search/fetch 方法 |
| `config/sources/catalog.yaml` | 来源元数据与启用状态，不保存密钥，不导入任意 Python 代码 |
| `sources/catalog.py` | 加载和校验目录，筛选来源并排序 |
| `sources/service.py` | 统一调用适配器、汇总结果，缓存本轮可读取的证据 |
| `tools/information.py` | 把 search/fetch 包装成模型看得懂的工具定义 |
| `database/source_repository.py` | SQLite 候选和反馈持久化边界 |
| `sources/__main__.py` | 人工管理命令和固定离线演示 |
| `prompts/information.md` | 提醒模型区分虚构资料、未知日期和不可信外部内容 |

模型给出任务分类；服务按结构化条件选择来源，并非靠硬编码关键词识别自然语言。日期筛选、全文解析、领域专用实体规范和正式接口版本迁移尚未实现。

## 4. 登记个人候选和反馈

```powershell
.\.venv\Scripts\python.exe -m newspaper_agent.sources --user local candidate-add --id my_arxiv --name "我的学术来源" --domain academic --url "https://arxiv.org"
.\.venv\Scripts\python.exe -m newspaper_agent.sources --user local candidates
.\.venv\Scripts\python.exe -m newspaper_agent.sources --user local feedback --source fixture_academic_secondary --kind useful
# 将返回的 feedback_id 替换到下一条命令
.\.venv\Scripts\python.exe -m newspaper_agent.sources --user local undo-feedback --id "替换为feedback_id"
```

管理命令按需创建 `data/sources.sqlite3`，不应提交到 Git。全局参数 `--user`、`--database`、`--catalog` 放在子命令之前。用户标识只能使用小写字母开头的字母、数字、下划线或短横线。

反馈类别：`useful` 加一，`irrelevant` 减一，`fact_error` 只记录、不直接调整排序。偏好分数限制在 -2 到 2，只影响同等级来源排序，不能越过领域匹配、原始发布来源优先级或启用状态。CLI 的 `route/demo` 会读取已有个人反馈；主程序 `--information` 尚未加载个人反馈。

`--user` 是本地数据分组，不是登录认证或安全隔离。候选状态只能是 `discovered`，不会自动加入启用目录。模型没有登记、修改、审批来源或反馈的工具。

## 5. 安全和兼容性边界

- 当前只允许固定的 fixture 适配器实际执行；真实条目只能使用未实现的适配器并保持禁用。不能仅修改 YAML 就接入任意服务。
- 候选 URL 只做语法检查：要求 HTTPS，拒绝凭证、查询参数、片段、非标准端口和明显私有地址。**这不是完整 SSRF 防护**；未来访问前还需 DNS、重定向和最终连接地址校验。
- `fetch_evidence` 只接受本服务已返回的证据 ID，不接受任意网址。虚构资料以 `is_fixture` 和内容标记区分；不存在的发布时间保持为空。
- 单次检索默认最多两个来源、三条结果；请求上限为三个来源、五条结果。循环仍默认最多四轮模型请求、八次工具执行，工具输入/输出字符限制沿用第三步。
- 适配器失败返回脱敏错误，允许其余来源继续；外部资料只是证据，不是可执行指令。未来仍需防提示注入与输出校验，不能只靠提示词保证安全。
- 已保留早期 Tavily 草稿，但它没有接入当前主程序，也没有在本次启用或验证。

## 6. 下一阶段

本次实现了设计中的目录/接口、离线路由和部分候选/反馈基础；未实现自动寻找新来源、完整审批生命周期、真实来源适配、权限与费用策略或自动升级代码。

下一步先选一个真实专业来源，补齐访问规则、限流、超时、网络安全、证据规范与测试，再经过人工确认启用。之后再做“发现候选 → 校验 → 人工批准 → 启用 → 反馈与暂停”的完整演化流程。

验证命令：

```powershell
.\.venv\Scripts\python.exe -m compileall src tests
.\.venv\Scripts\python.exe -m pytest -q
```

本阶段离线测试覆盖领域路由、跨领域覆盖、筛选限制、候选校验、反馈撤销、用户分组、部分失败、证据读取、重置，以及 Chat/Responses 两种协议的模拟 HTTP 工具闭环。
