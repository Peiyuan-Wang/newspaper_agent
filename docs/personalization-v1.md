# 第一版：账户、个人偏好与共享来源

新增[兴趣确认与表达适配](interest-and-expression-strategies.md)：长期档案补充用途、知识水平、输出结构、表达规则和术语约定；AI推断先存入候选，用户确认后生效，保持账户隔离、版本保护和撤销。下文原有字段继续兼容，新增字段定义见策略说明。

本模块位于 `src/newspaper_agent/database/research_repository.py`，提供 SQLite 持久化边界。应用界面负责先验证账户，并把认证后的用户标识绑定到后续调用；模型输出不能更换当前账户、管理员身份或数据库路径。仓库模块不联网，不执行插件代码。

## 账户与运行数据

`ResearchRepository(path)` 按需创建数据库；应用应把路径放在忽略提交的 `data/` 下。`register(user, password)` 注册，`authenticate(user, password)` 返回真假，`is_admin(user)` 查询管理员身份。用户标识使用小写字母开头，最多 64 个字母、数字、下划线或短横线。密码至少 8 个字符，最多 1024 个字符。

首个成功注册的账户成为管理员；注册事务使用 SQLite 写锁，避免同时注册产生多个首位管理员。密码以独立随机盐和 600,000 轮 PBKDF2-HMAC-SHA256 摘要保存，认证使用恒时摘要比较，不保存明文。未知账户也进行一次同等成本的摘要计算。

这是本机应用的账户边界，并非用于直接暴露公网的身份服务。数据库、报告和来源 URL 尚未加密；能直接读取或修改数据库文件的人能够绕开应用权限。运行数据应由设备文件权限保护，不能提交 Git。公网部署需另外设计会话、限速、CSRF 和管理员初始化流程。

## 偏好、反馈与撤销

`profile(user)` 返回：

```json
{
  "interests": [],
  "excluded": [],
  "domains": [],
  "style": "standard",
  "language": "zh",
  "version": 0
}
```

- `interests` 是感兴趣的具体主题，`excluded` 是排除主题；每个列表最多 30 项，每项最多 120 字符。
- `domains` 限定为 `academic`、`medical`、`finance`、`news`、`general`。
- `style` 是 `brief`、`standard`、`expert`；`language` 是 `zh` 或 `en`。
- `version` 由数据库维护，调用者不能自行设置。

`update_profile(user, changes, reason)` 只更新白名单字段并返回事件 ID。`feedback(user, kind, detail, changes=None, evidence_id=None)` 保存聊天反馈；类别是 `missed`、`irrelevant`、`useful`、`fact_error`、`too_long`、`unclear`。应用可以把用户表达转成明确的偏好变更，再把变更和反馈一起提交。单纯说“有事实错误”只记录用户质疑，不直接判定来源造假，也不修改其他人的偏好。

`events(user, limit=20)` 只显示当前用户自己的事件。`undo(user, event_id)` 把事件标记为撤销；带偏好修改的事件还会恢复修改前的偏好。写事务与版本检查确保旧事件不会覆盖后来的修改。版本始终递增，因此目前只支持撤销尚无后续偏好修改的那一次变更；撤销新事件后，旧事件仍不能再次自动回滚，应明确编辑当前偏好。没有附带偏好变更的反馈可独立撤销。

本模块不凭一次“遗漏”反馈自动猜测全部兴趣，不生成新的偏好内容；推断属于应用层，变更内容和理由保存在事件记录中供用户检查。

## 私密与共享来源

`store_source(user, name, url, domain, scope='private')` 新增 RSS 候选；返回值含 `id`、`owner`、`name`、`url`、`domain`、`scope`、`status`、`adapter` 和时间。这里只接收实际 Feed 端点；候选校验沿用现有 `public_url`：仅 HTTPS，禁止凭证、查询参数、片段、非标准端口和明显非公共地址。网络 DNS、重定向与内容校验由网络适配层处理，本模块的语法检查不能替代它们。

新记录固定为 `discovered`，不允许调用者在新建时直接启用来源或选择任意 Python 模块。每个用户在同一 private/shared 范围内的 URL 唯一。不同用户可有相同私密来源，不会通过“重复记录”错误泄露别人的来源是否存在。

`list_sources(user)` 返回自己的全部私密来源以及已发布的共享来源。检索组装层还需筛选 `status == 'enabled'`，不能把候选直接接入检索。`include_pending=True` 增加当前用户贡献的共享候选；管理员可以查看全部共享候选。`source(user, id)` 同样进行可见性检查，管理员也看不到别人的私密来源。

状态流程：

```text
discovered → reviewed / validated → enabled → suspended
                    ↘ rejected / archived
```

应用完成真实网络验证后才调用 `set_source_status(user, id, 'validated')`。私密来源由所有者启用；共享来源的验证和管理由管理员执行，共享启用必须经过单独的 `publish_source(admin, id)`，且此前状态必须为 `validated`。仓库记录状态但不声称自己已经执行网络验证。

`share_source(user, private_id)` 为自己的私密来源创建一条新的共享候选。它只复制来源名称、URL、领域和固定 RSS 适配器，不复制个人偏好、反馈、查询、证据或研究报告。原有私密来源保持原状。贡献者可把自己尚未发布的共享候选设置为 `archived` 来撤回；已发布的共享条目由管理员管理，个人仍可保留独立私密条目。归档是终止状态，要重新贡献需使用新的候选 URL 或后续扩展专门的恢复功能。

模型不应获得发布共享来源或改变账户的工具。当前账户和这些高权限入口必须由应用控制。

## 研究记录

`save_report(user, question, report)` 保存 JSON 报告并返回 ID，问题最多 4000 字符，报告最多 1,000,000 JSON 字符且不接受 NaN 等非标准 JSON 值。`reports(user, limit=10)` 返回个人历史摘要，`report(user, id)` 返回完整记录。管理员同样不能通过这些接口读取其他人的报告。

## 错误与验证

无效字段、无权限来源/报告、重复 URL、非法状态变更、版本冲突均抛出简短 `ValueError`，不在异常中回显密码、完整输入或私密内容。所有 SQL 参数绑定传入，写操作使用事务。应用负责捕获错误并显示给用户。

离线测试覆盖口令摘要、首位管理员、账户隔离、反馈审计与撤销、旧版本保护、私密来源隔离、共享发布前置条件、贡献撤回、来源 URL 和适配器限制、报告隔离与大小限制：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_research_repository.py
```

此模块不自动迁移旧 `SourceRepository` 的未认证用户记录，也不把旧的 `--user` 分组直接认定为已认证账户。
