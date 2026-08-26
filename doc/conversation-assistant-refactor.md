# 统一对话助手

PC 与企业微信/飞书共用 `POST /api/v1/chat/stream` 的会话核心；平台适配层只负责验签、消息解析、幂等入队与回复投递。

```text
PC Web ───────┐
              ├─ ConversationStreamService
IM / ARQ ─────┘          ├─ 鉴权与会话归属
                         ├─ 闲聊 / 项目 / 法律路由
                         ├─ 项目事实、风险、匹配与 Evidence
                         └─ 原生 LLM token 流（招标、法律）
```

核心约束：

- 每轮都回查用户身份、项目成员资格和会话归属；会话不是授权凭据。
- 闲聊与项目分析结论不调用模型；招标与法律检索使用原生模型流。
- 项目分析回答只读取 `project_fields`、`risks`、`match_results` 与 `evidences`，并携带可追溯原文依据。
- 一轮对话只持久化一条用户消息和一条助手消息。
- `POST /api/v1/chat/qa`、项目专属流接口、法律专属流接口和旧 IM LangGraph 已删除。
