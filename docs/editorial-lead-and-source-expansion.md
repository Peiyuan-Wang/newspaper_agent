# 新闻核心前置与来源扩展边界

## 输出逻辑

新闻短解读的内部顺序是：具体核心对象是什么 → 核心变化及有证据的用途/意义 → 与读者有关的方法、指标和条件。分类自然写进句子，不输出“分类、意义、主体”的固定小标题。首句不能被某个次要消融或跑分占据，也不能为了吸引眼球编造突破。

模型返回lead_subject，第一条statement必须以它原样开头。例如新闻核心是某模型的代码生成改进，主语应为该模型，不泛泛写其公司；公司融资/组织变化则可以公司为主语。长文最终输入保留最多1800字符的开篇原文定位片段，再加个性化选段，避免选段只剩训练参数而丢失文章主题。代码检查字面开头，真正核心性和意义仍依赖模型理解与人工评价，不能把此检查当语义验证。

pass@1是合法技术指标，不是笔误。在生成任务中可解释为只生成一个候选答案时的通过率；具体定义依原文任务与评测约定，不能统一改写成准确率。需要时写“单次生成通过率（pass@1）”，先交代被改进的具体对象，再讲指标。百分点与百分比不能混用；原文没有给出的测试集、基线或收益不能自行补齐。SFT按语境写监督微调，不用“合成SFT”代替“用合成数据进行监督微调”。

## 校验与可恢复性

正文claims与归档news分开校验。正文引用、字段、主语开头、篇幅与表达必须合格；事件或实体字段失败只标metadata_pending，不进正式事件索引，不挡住已经合格的正文。界面检索详情显示待归档数量；显式重新整理可重试归档，不自动增加额外全库模型请求。

brief_contract.py接受单一完整JSON代码围栏，但不从混杂解释文字中任意截取JSON；重复字段、非有限数字仍拒绝。引文仅在空白差异时恢复对应原文的真实连续跨度，仍拒绝翻译、改数字、改标点和拼接，定位与快照按原文计算。正文失败继续按原预算修订，保留失败稿；界面显示安全分类原因而非笼统“未通过校验”，不回显提供方正文或密钥。

BRIEF_VERSION=3使旧解读缓存失效，不影响兴趣分。历史报告不自动改写；重启、启用模型并点击“AI重新整理”生成新报告。

## 来源扩展调查（2026-10-17）

本轮核查与规划，不默认启用新来源、不采购服务、不登录社媒、不建立抓取账号。

| 类别 | 可走的接入方式 | 当前边界 |
|---|---|---|
| 计算机论坛 | Hacker News官方RSS；其他站公开RSS/Atom | 现有RSS适配器即可，论坛热度不等于事实可信度 |
| AI论坛 | Hugging Face Discourse及其他开放Discourse的栏目/标签RSS | 可细分栏目，内容可能是提问或意见，不能包装成官方发布 |
| 政府与机构 | 官方网站RSS、公告API；没有Feed的站另建受控列表适配器 | 优先官网原件，官号帖子与正式政策文件分开，未知生效时间不猜 |
| 微信公众号 | 用户有权使用的文章/RSS服务，或评估合法授权的采集服务 | 没有接入任意公众号通用抓取；不能把公众号URL当RSS，更不要求上传私人微信Cookie |
| X | 官方API或评估授权数据服务 | 官方API按量计费；AIHOT示例用SocialData key；本项目尚无X适配器，不自动花钱 |
| Reddit | 申请获批的Data API及其认证流程 | 不保证免费/无限；具体用途和商业使用须核对平台许可，不用匿名.json绕限制 |
| Instagram | 依账号和权限确定可用的官方接口 | 官方接口并不等于任意公开账号搜索器；当前尚未接入，Meta文档请求受限，需接入时再核对权限范围 |

使用本项目SafeHTTPClient对三个公开候选进行只读试取：

- https://news.ycombinator.com/rss：返回30条。
- https://discuss.huggingface.co/latest.rss：返回30条。
- https://www.nist.gov/news-events/news/rss.xml：返回40条。

以上仅单次可达性，不保证持续可用或已进入当前简报。来源页可按现有流程新增、校验和启用RSS；每轮最多12源、保留通用补充的路由预算未变，扩充来源不等于每轮全量抓取。

后续适配器需先确定账号/平台、采集方式、鉴权、预算、原帖与转述身份、删除/撤回同步及私有/共享边界。图片视频帖不能只凭描述声称读完内容；来源账号“官方”也不代表其宣传已被核实。共享只分享来源元数据，社媒凭证与个人订阅仍私有。

## 资料

- [HumanEval指标实现](https://github.com/openai/human-eval/blob/master/human_eval/evaluation.py)
- [Discourse公开RSS说明](https://meta.discourse.org/t/finding-discourse-rss-feeds/264134)
- [Reddit数据访问官方说明](https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data)
- [X按量计费说明](https://docs.x.com/x-api/getting-started/pricing)
- [AIHOT信源配置](https://github.com/KKKKhazix/AIHOT/blob/main/docs/sources.md)
- [Meta维护的Instagram API资料](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login)
