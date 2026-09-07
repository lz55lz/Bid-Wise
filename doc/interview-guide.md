# BidWise 面试讲解稿

> 用途：用于介绍本项目的设计、个人贡献和技术取舍。所有表述以当前仓库实现为准，不把规划中的能力当作已上线能力。

## 1. 一分钟项目介绍

BidWise 是面向企业私有部署的招投标智能分析与投标决策系统。它解决的不是“把招标文件总结一下”，而是“**当前绑定企业是否适合投这个项目，为什么，缺什么材料，风险是否可接受**”。

用户创建项目、绑定企业、上传招标文件后，系统会把文件解析为可引用的 Evidence，自动提取资格、评分、商务和技术要求；人工可以直接审批，也可以修改、补充后再审批。审批后的快照会驱动企业材料匹配、风险研判、投标决策和报告生成。项目内的 AI 问答则结合招标原文、绑定企业信息和会话上下文持续回答。

可以这样概括技术栈：**Vue 3 + TypeScript 前端，FastAPI 模块化单体，PostgreSQL（含 pgvector）作为业务与向量事实源，Redis + ARQ 处理异步任务，MinIO 管文件，外部模型、Embedding、Reranker 与 MinerU 提供 AI 能力。**

## 2. 面试中应突出的问题与价值

### 2.1 业务难点：结论不能只来自招标文件

很多 RAG 项目只能回答“招标文件要求什么”。但投标决策的核心是企业与招标要求之间的关系：企业有什么资质和业绩、缺什么、缺口是否会一票否决、是否值得继续投入。

我的解决方式是把“项目事实”和“企业事实”分开建模，再在分析阶段关联：

- 招标文件解析后形成可定位的 Evidence，任何原文结论能回到文件位置。
- 企业绑定、企业材料、匹配结果、风险和决策是独立的结构化业务对象。
- 报告的主线改为“企业适配度结论 → 优势与证据 → 决定性缺口 → 风险与推进条件”，而不是把抽取字段简单罗列出来。
- 对话中识别“绑定企业适配吗”之类问题后，优先加载企业适配上下文；如果用户没有指定项目，系统应该引导选择项目，而不是编造结论。

这使 LLM 是“解释和组织已有证据”的一层，而不是唯一的业务判定来源。

### 2.2 可信度难点：AI 不能无依据地下结论

招投标场景对可追溯性要求高。模型即使回答流畅，只要不能说明依据，用户就无法用于投标决策。

我的做法：

1. 文件解析时按章节和页码建立 Evidence，并保存原文、来源文档和位置信息。
2. 检索服务先召回候选，再重排、去重和补充邻近上下文，最后把 Evidence 作为回答上下文。
3. 对话工作流会校验引用：招标要求必须有项目 Evidence 支撑；企业信息允许引用已授权的业务数据，避免强行伪造“原文证据”。
4. 无证据时明确说“无法可靠判断”，同时提示用户补材料或选择项目；不让模型为了完整性虚构资格、业绩或风险。

面试表达重点：**我把“模型能说”与“系统允许作为结论说”区分开了。**

## 3. 架构与核心取舍

### 3.1 为什么是模块化单体，而不是一开始拆微服务

项目包含项目、文件、企业、抽取、匹配、风险、决策、报告、会话等强关联领域。早期拆成多个服务会增加接口契约、分布式事务、调试和部署成本。

因此后端采用 FastAPI 模块化单体：按 `app/modules/<domain>` 划分路由、服务、仓储、模型和 Schema，领域之间通过清晰服务边界协作。需要独立扩缩容的耗时工作（解析、索引、分析、报告）交给 ARQ Worker。这样同时获得：

- 单仓库、单数据库下的开发效率和事务一致性；
- 明确模块边界，后续可按压力点拆分；
- 不让文件解析或模型调用阻塞 HTTP 请求。

### 3.2 为什么用 PostgreSQL + pgvector，而不是 Milvus

项目当前**没有使用 Milvus**。向量保存在 PostgreSQL 的 `pgvector` 字段中，Evidence 和知识库向量都与业务记录在同一数据库内。

原因是当前规模与业务要求下，PostgreSQL 更合适：

- 向量与项目权限、文档版本、Evidence 生命周期在同一事务和备份体系内。
- 支持向量相似度检索，也支持 PostgreSQL 全文检索；可以做稠密召回与词法召回的组合。
- 少一个独立服务，部署、监控、恢复和数据一致性更简单。

仓库曾遗留 Milvus/etcd 的 Compose 和部署说明，但代码没有依赖它；我核对实现后删除了这些无效配置，避免“文档写一套、实际运行另一套”。

可补充：当数据量、QPS 或索引规模明显超出 PostgreSQL 合理范围时，才评估引入专用向量数据库，并通过双写、回放和检索对账平滑迁移。

### 3.3 为什么异步任务用 Redis + ARQ

解析 PDF、调用 MinerU、批量 Embedding、完整分析和生成 DOCX/PDF 都可能耗时数十秒甚至更久。若放在 API 请求中，会超时、占满 Web Worker，也无法可靠展示状态。

因此 API 只创建任务和返回任务标识，ARQ Worker 消费任务并更新阶段状态。前端据此展示“待提取、需求复核、匹配分析、报告生成”等状态。任务失败会记录失败阶段和原因，不把失败伪装成成功。

这里我重点处理过状态一致性：

- 用户“重新提取”后，项目仍停留在“需求复核”等待提取的问题，根因是项目状态和任务状态没有在同一状态机下更新。
- 重新分析后报告阶段没有改变的问题，根因是报告生成与完整分析阶段没有联动。
- 解决方式是把状态变迁收敛到分析/提取服务，任务启动、成功、失败、重新执行时同步更新阶段，并让前端以阶段状态为唯一展示来源。

## 4. 技术深拆：文档如何切块与入库

> 对应源码：`backend/app/modules/retrieval/structured_chunking.py`、`backend/app/modules/documents/parsing_service.py`

### 4.1 为什么不用“每 500 字切一块”

固定字符切分实现简单，但在招标文件中会把“资格条件的标题”切到上一块、把“具体阈值和例外情况”切到下一块；检索到其中一块时，模型很容易漏掉限制条件。表格被横向截断后也失去“字段—取值”的关系。

所以我采用**结构优先、字符兜底**的方案：先利用解析器输出的章节、段落、表格、图片、页码等原子节点切分；只有单个结构单元超长时，才按句子再切，最后才采用带 overlap 的硬窗口。

```text
MinerU / DOCX 解析结果
        │
        ▼
DocumentNode 原子节点
  (SECTION / PARAGRAPH / TABLE / IMAGE，携带页码、顺序、章节路径)
        │
        ▼
结构优先聚合
  - 同章节才合并
  - 新条款编号通常断开
  - 连续短列表项、短同级条款允许合并
  - 表格、图片保持原子性
        │
        ▼
超长兜底切分
  段落 / 句子 → 硬窗口 + 120 字 overlap
        │
        ▼
Evidence + locator + embedding 入 PostgreSQL
```

当前关键参数不是拍脑袋的“token 数”：

```python
# backend/app/modules/retrieval/structured_chunking.py
def build_structured_chunks(
    atoms: list[ChunkAtom],
    *,
    target_chars: int = 1_800,  # 常规块接近该大小后换块
    max_chars: int = 2_400,     # 正常聚合绝不超过该上限
    overlap_chars: int = 120,   # 仅超长兜底块使用，避免大量重复向量
) -> list[StructuredChunk]:
    ...
```

面试可这样解释取舍：1800～2400 字符足以保留一段资格/评分条款的上下文，又不会把无关章节塞进模型；`overlap` 只用于超长兜底，不在正常块之间滑动，是为了降低重复召回和存储成本。

### 4.2 条款边界与表格的处理

系统会补充稳定的条款键，例如 `3.2.1` 的父条款是 `3.2`；中文“第 X 章 / 节 / 条”保留为层级锚点。连续很短的同级条款可以合并，避免每一句话一个向量；出现明确新条款、跨章节、达到目标长度、或即将超过最大长度时切开。

```python
# 简化后的实际断块条件
must_break = (
    current_section != atom_section
    or (is_explicit_clause_start(text) and not is_short_child_item)
    or current_chars >= target_chars
    or current_chars + len(text) + 1 > max_chars
)
if must_break:
    flush()
current.append(atom)
```

表格不按普通正文拆散。用于检索时，会将 HTML 表格转换成“表头 + 行键值对”，例如：

```text
表格字段：评分项；分值；条件
第 1 行：评分项：类似业绩；分值：10；条件：近三年完成 2 个同类项目
```

这样用户问“类似业绩要几项、多少分”时，Embedding、关键词检索和 Reranker 都能看到完整字段关系；而前端引用仍可回到原始表格，不用改写原文。

### 4.3 Evidence 为什么是核心数据模型

Evidence 不是“给 LLM 的文本字符串”，而是项目范围内可授权、可引用的检索单元。它保存原文、文档版本、节点关系以及 `locator`（文档名、章节路径、页码、原始顺序等）。Embedding 单独存在 `evidence_embeddings` 表，使用 PostgreSQL `pgvector` 的 HNSW 索引。

```python
# backend/app/modules/retrieval/models.py（结构简化）
class EvidenceEmbedding(Base):
    __tablename__ = "evidence_embeddings"
    evidence_id = mapped_column(ForeignKey("evidences.id"), unique=True)
    embedding = mapped_column(Vector(1024), nullable=False)

    __table_args__ = (
        Index(
            "ix_evidence_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
```

这套拆分的好处是：原文版本可替换、向量可重建、权限可先过滤、报告和对话可引用同一份事实源。

## 5. 技术深拆：RAG 如何召回、排序与防幻觉

> 对应源码：`backend/app/modules/retrieval/candidate_service.py`、`ranked_service.py`、`evidence/repository.py`

### 5.1 整体检索链路

```text
用户问题
  │
  ├─ 先校验 project_members 项目权限
  │
  ├─ Dense：问题 → Embedding → pgvector cosine top-K
  │
  ├─ Lexical：问题 → 中文全文检索（条款号/金额/专有名词）top-K
  │
  ├─ RRF 融合（Dense 0.65 + Lexical 0.35，K=60）
  │
  ├─ Reranker 对候选精排（不可用则降级为融合排序）
  │
  ├─ MMR 去重（相关性 0.8 - 相似冗余 0.2）
  │
  └─ 同章节邻居扩展（最多约 2000 字） → 受控上下文 → LLM
```

**先授权、再检索**是这里的重要安全点。候选召回一开始就调用项目访问校验，而不是检索完以后再过滤；数据库查询也带 `project_id` 与“当前文档版本”条件。因此用户无法通过调参数或猜 UUID 检索到其他项目内容。

```python
# backend/app/modules/retrieval/candidate_service.py
async def search_project_evidences(project_id, actor, query, limit):
    await ProjectService(self._session).require_project_access(project_id, actor)
    vector = (await self._embedding_client.embed([query]))[0]
    dense = await self._evidences.list_search_candidates(project_id, vector, candidate_limit)
    lexical = await self._evidences.list_bm25_search_candidates(project_id, query, candidate_limit)
    return reciprocal_rank_fusion(dense, lexical)
```

### 5.2 为什么是 Dense + 词法混合召回

Dense 擅长同义表达，例如“履约能力”与“业绩要求”；但容易漏掉精确的编号、金额、日期和公司名。中文全文检索擅长这些精确条件，但不理解改写和语义相近表达。

系统使用 RRF（Reciprocal Rank Fusion）而不是直接合并分数，因为向量距离和 BM25 分数的尺度不可直接比较：

```python
# 实际权重与核心公式
RRF_K = 60
DENSE_WEIGHT = 0.65
LEXICAL_WEIGHT = 0.35

score = DENSE_WEIGHT / (RRF_K + dense_rank)
score += LEXICAL_WEIGHT / (RRF_K + lexical_rank)
```

这允许一段内容只要在任一通路排名较靠前就进入候选池，也让双通路同时命中的内容自然靠前。

### 5.3 为什么还要 Rerank、MMR 和邻居扩展

召回阶段目标是“不要漏”，因此候选池会宽一些；生成阶段却不能把几十块都塞给模型。

- **Reranker**：逐对判断“问题—候选文本”的相关性，解决仅凭向量相近但答非所问的问题。
- **MMR**：防止前 8 个结果都是同一条款的重复切片。系统用 Jieba 搜索分词和 Jaccard 相似度计算冗余，`0.8 * relevance - 0.2 * redundancy` 选取多样化结果。
- **邻居扩展**：命中块不足 600 字时，在相同章节中向前/向后补邻居，最多累计约 2000 字，避免只命中“不得”而丢失后面的例外或条件。

```python
# backend/app/modules/retrieval/ranked_service.py
try:
    scores = await self._reranker.rerank(question, candidate_texts)
except RerankerUnavailable:
    scores = [item.similarity for item in candidates]  # 降级而非整体失败

selected = self._mmr_diversify(ranked, limit=8)
for item in selected:
    item.context_text = await self._expand_neighbors(project_id, item)
```

### 5.4 如何控制幻觉与提示词注入

RAG 不是把检索文本直接拼到 prompt 就结束了。我把检索内容视为**不可信数据**：即使原文里出现“忽略上述要求”之类文字，也只能作为招标正文，不能改变系统指令。

```text
系统提示的关键约束：
1. 只能根据本轮服务端提供的、已授权的上下文回答项目事实。
2. 项目事实必须使用本轮真实暴露的 Evidence UUID 引用。
3. 禁止编造 Evidence / Legal / Report ID。
4. 证据不足时明确说明，不得补写金额、日期、条款或结论。
```

模型输出后，服务端再次解析引用，只接受本轮上下文里真实存在的 ID；如果最终回答没有覆盖该问题要求的数据源，服务端用“证据不足”替换回答。这是“生成前限制 + 生成后校验”的双保险。

## 6. 技术深拆：LLM 编排与多轮对话

> 对应源码：`backend/app/modules/conversations/retrieval_plan.py`、`assistant_workflow.py`、`service.py`

### 6.1 为什么不是自由 Tool-Calling Agent

项目没有让 LLM 自己决定“查哪个库、能否访问哪个项目、调用什么工具”。这是因为业务事实源有限且权限敏感，自由 Agent 容易出现工具误用、循环调用和越权检索，也不利于稳定测试。

当前是**规则规划 + 显式受控工作流**：

```text
请求到达
  → 会话/项目所有权校验
  → retrieval plan（规则识别：项目、法规、报告、企业适配、闲聊）
  → 服务端按 plan 调用固定检索服务
  → 预算裁剪与上下文标记
  → LLM 生成 / SSE 流式输出
  → 过滤 <think>、校验引用、持久化消息与 trace
```

`RetrievalPlan` 是可测试的策略对象，而不是模型返回的 JSON。比如“适配度 / 我司 / 缺口 / 能不能投”命中企业适配模式；“报告里”命中报告模式；法律问题根据是否涉及当前项目决定查询法规库还是“项目 Evidence + 法规”双证据。

```python
# backend/app/modules/conversations/retrieval_plan.py（简化）
if enterprise_fit:
    return RetrievalPlan(
        mode="ENTERPRISE_FIT",
        enterprise_fit=True,
        memory=True,
        require_enterprise_fit=True,
    )

if legal:
    return RetrievalPlan(
        mode="HYBRID" if project else "LEGAL",
        project=project,
        legal=True,
        require_project=project,
        require_legal=True,
    )
```

### 6.2 上下文预算与模型输出控制

总上下文预算为 14,000 字符，但不是平均分。企业适配单独问时给 5,000；项目+法规时项目 9,000、法规 5,000；多源混合时再缩小。单条项目 Evidence 最多暴露 2,000 字，避免一块文本吞掉整轮上下文。

```python
# backend/app/modules/conversations/assistant_workflow.py
_CONTEXT_BUDGET = 14_000

exposed = budgets["project"].take(body, per_item=2_000)
context_blocks.append(
    f"[PROJECT_EVIDENCE id={evidence_id}]\n{exposed}\n[/PROJECT_EVIDENCE]"
)
```

回答默认要求“先结论、最多 5 点、约 300 字”，只有用户明确要求逐条或详细分析时才展开。流式输出时还会过滤 Provider 意外输出的 `<think>...</think>`，避免把内部推理过程展示给用户。

### 6.3 多轮对话如何延续上下文

短期记忆不依赖模型“自己记住”，而是会话消息持久化在数据库；每轮取最近 **12 条**消息，并受 12,000 字符预算约束。

对于“第一个缺口怎么补？”这类短追问，系统会检测代词/追问词，并把最近一条用户问题拼入检索 query：

```python
# backend/app/modules/conversations/retrieval_plan.py
if len(question) <= 40 and has_followup_word(question):
    previous_user = last_user_message(history)
    return f"{previous_user}\n追问：{question}"[:2_000]
```

企业适配模式是否延续，不信任模型自己说“我刚才查过”。系统根据**上一轮服务端 trace**里是否有成功的 `enterprise_fit_retrieval` 判断，这是为了防止模型通过自述伪造上下文。

```python
# backend/app/modules/conversations/service.py（简化）
has_fit_history = any(
    trace["tool_name"] == "enterprise_fit_retrieval"
    and trace["status"] == "completed"
    for trace in assistant_message.traces
)
plan = plan_retrieval(question, has_enterprise_fit_history=has_fit_history)
```

### 6.4 长期记忆如何避免污染业务事实

长期记忆只保存用户主动维护的 **PREFERENCE**（如“默认先给结论、回答简洁”），归属到用户，也可归属某一项目；每轮非闲聊问题最多召回 8 条。它只影响表达方式，不能作为招标事实或企业事实证据。

```python
# backend/app/modules/memories/service.py
item = UserMemory(
    user_id=actor.id,
    project_id=project_id,
    memory_type="PREFERENCE",
    content=content.strip(),
)

# recall：只返回当前用户的全局/当前项目偏好，最多 8 条
return (matched or memories)[:8]
```

这解决了两类风险：一是不同用户的偏好不串；二是模型一次误判不会被写入“长期记忆”，污染后续投标判断。

## 7. 技术深拆：权限、数据隔离与审计

> 对应源码：`backend/app/core/security.py`、`modules/identity/service.py`、`modules/projects/service.py`

### 7.1 三层权限模型

```text
身份层：JWT + Argon2 密码哈希 + 用户启用状态 + 实时系统角色
        │
项目层：project_members（OWNER / EDITOR / VIEWER）
        │
资源层：文档、Evidence、报告、会话、下载、检索都先回查项目成员关系
```

系统角色包含 `SYSTEM_ADMIN`、`BID_SPECIALIST`、`LEGAL_COMPLIANCE`、`MATERIAL_ADMIN`、`READ_ONLY`；项目内再细分 OWNER、EDITOR、VIEWER。系统管理员有全局权限，普通用户必须是项目成员；VIEWER 不能上传项目文件，OWNER/管理员才可做成员管理、索引重建等高影响操作。

### 7.2 为什么 JWT 里不放角色

JWT 只包含用户 ID、唯一 `jti`、签发时间和过期时间，角色每次请求都回查数据库。这样禁用用户、调整角色、重置密码或撤销令牌，下一请求就生效，避免“令牌未过期但权限已变”的窗口。

```python
# backend/app/core/security.py
token = jwt.encode(
    {"sub": user_id, "jti": str(uuid4()), "iat": now, "exp": expires_at},
    secret,
    algorithm="HS256",
)

# identity/service.py：验签后再验证用户状态、jti 撤销与实时角色
user = await repository.get_active_user(user_id)
if await repository.is_access_token_revoked(jti):
    deny()
roles = await repository.list_system_role_codes(user.id)
```

### 7.3 避免资源存在性泄漏

对无权限的普通用户，项目不存在与“项目存在但无权访问”都返回同一个 `RESOURCE_NOT_FOUND`。这样用户不能靠遍历 UUID 判断其他项目是否存在。

```python
# backend/app/modules/projects/service.py
project = await self._projects.get_active(project_id)
if project is None:
    raise DomainError("RESOURCE_NOT_FOUND", "项目不存在", 404)
if not is_system_admin(actor):
    member = await self._projects.get_member(project_id, actor.id)
    if member is None:
        raise DomainError("RESOURCE_NOT_FOUND", "项目不存在", 404)
```

对话也绑定 owner：项目会话必须同时匹配 `conversation_id + project_id + user_id`，全局会话也只能读取自己的记录。所有重要变更写审计日志，但审计摘要不写文件正文、令牌或原始敏感材料。

## 8. 核心业务链路：可按图讲解

```text
招标文件上传
  → 文件解析 / 清洗 / 分块
  → Evidence + Embedding 入库
  → 需求字段提取
  → 人工审批（直接审批 或 修改补充后审批）
  → 冻结项目、材料、规则版本
  → 企业材料匹配 → 风险研判 → 决策建议 → 企业适配度报告
  → 项目 AI 问答与多轮追问
```

### 8.1 为什么要有人审，而不是完全自动审批

招标文件常有扫描件、表格、例外条款与歧义表达。字段抽取可以极大提效，但不应直接成为最终业务事实。

审批设计支持两条路径：

- **直接审批**：自动提取准确时，快速确认。
- **修改/补充后审批**：用户修正字段、补充遗漏，再进入后续分析。

关键点不是把人工审核理解为额外流程，而是把它作为“把不确定的模型输出转成可执行业务事实”的关口。审批时冻结版本，保证后续匹配、风险、决策和报告引用的是同一份输入，避免分析中途文件或材料变化导致结果不可复现。

### 8.2 企业匹配、风险、决策为什么分层

它们解决不同问题：

| 层次 | 回答的问题 | 产出 |
| --- | --- | --- |
| 匹配 | 企业是否有对应材料或能力 | 已满足项、缺失项、匹配证据 |
| 风险 | 缺失或条款会造成多大影响 | 风险等级、原因、建议动作 |
| 决策 | 是否值得推进投标 | 建议、前置条件、需决策事项 |

分层优点是可解释、可复核、可替换规则。比如“缺少某资质”先是匹配缺口；结合资格审查是否一票否决后才成为高风险；再结合补办周期和截止日期，才形成“继续 / 谨慎 / 不建议”的决策。

## 9. 典型问题、定位与修复方式

### 问题一：字段提取时间类型校验失败

现象：例如 `TIME_BID_OPEN` 的值不符合 `datetime` 类型，导致完整分析失败。

排查思路：先区分是模型输出格式问题、解析层规范化缺失，还是 Pydantic/数据库 Schema 过早强校验。招标时间往往包含中文日期、区间、时区或“详见公告”等非标准文本。

解决原则：在进入强类型字段前做规范化与容错解析；无法可靠转为时间时保留原始文本和不确定状态，交给人工复核，而不是让单字段阻断整条链路。错误信息要指向具体字段和原始值，方便修正。

### 问题二：重新提取后项目阶段不变

现象：用户点击重新提取，但页面仍显示“需求复核，等待发起字段提取”。

根因：异步任务状态、项目阶段状态和前端缓存没有统一更新。

解决：把重跑入口收敛到同一个服务状态机；提交时立即进入“提取中”，Worker 成功后进入“需求复核”，失败则显式进入失败阶段；前端轮询/刷新读取服务端阶段而不是根据按钮操作本地猜测。

### 问题三：报告没有体现绑定企业适配度

根因：早期报告偏向“招标内容汇总”，没有把绑定企业和匹配结果作为一等输入。

解决：在报告快照中加入绑定企业、企业材料、匹配、风险和决策；重构报告章节顺序，以企业适配度结论开篇，并给出证据、缺口和推进条件。LLM 只负责叙述与归纳，核心字段来自冻结快照。

### 问题四：AI 回答“证据不足”但没有引导下一步

根因：全局对话缺少项目上下文，或企业适配检索未命中时只返回兜底文本。

解决：识别企业适配意图；若当前会话未绑定项目，提示用户选择项目；若项目存在但企业材料不足，明确指出缺哪一类材料，并引导补充。这样把“无法回答”变成可执行的下一步。

## 10. 面试常见追问与参考回答

### Q：项目中你最有价值的设计是什么？

我会回答：把“企业适配度”而不是“文档摘要”作为分析和报告主线，同时用 Evidence、审批快照和结构化匹配/风险/决策保证结论可追溯。这让 AI 能力进入真实业务闭环。

### Q：为什么不完全依赖大模型？

大模型适合抽取、归纳、解释和自然语言交互，但不适合作为业务事实源。资格条件、企业资质、风险等级和投标建议需要有版本、规则、证据和人工复核；否则无法解释，也难以审计。

### Q：如何保证任务幂等和失败可恢复？

任务以数据库状态为准，Worker 启动前检查当前阶段和版本；解析、索引、分析、报告分别记录状态。失败保留失败阶段和错误原因；重试从明确入口重新提交，而不是依赖前端重复点击。对于版本化输入，旧版本不覆盖当前版本，降低重试造成的数据污染。

### Q：pgvector 的局限是什么？

它与 PostgreSQL 集成好、管理简单，适合当前数据量和权限过滤需求。但当向量规模和高并发检索显著增长时，索引构建、资源隔离和检索延迟可能成为瓶颈。届时会以指标驱动评估专用向量库，而不是预先引入复杂度。

### Q：如果没有 Evidence，系统为什么不直接让模型回答？

可以给通用解释，但不能把它包装成“本项目结论”。系统会区分通用说明、项目原文结论和企业事实结论；后两者必须有授权上下文或 Evidence。这是为了避免幻觉直接影响投标判断。

## 11. 个人贡献表述模板

请按实际负责范围选择，不要照搬未参与内容：

> 我主要负责招标分析与智能问答链路的完善。工作包括：梳理人工复核到分析报告的状态机；将绑定企业、材料匹配、风险与决策纳入报告和对话上下文；实现/优化基于 PostgreSQL pgvector 的混合检索与引用约束；处理重新提取、报告状态联动、时间字段校验和多轮对话引导等问题；最后补齐测试、README、部署配置清理和 Git 仓库卫生。

## 12. 演示顺序（5 分钟）

1. 打开项目：展示已绑定企业和已上传招标文件。
2. 进入需求复核：说明可以直接审批，也可以修改补充再审批。
3. 发起分析：展示匹配、风险、决策和报告状态的顺序。
4. 打开报告：先展示企业适配度结论、决定性缺口与推进条件。
5. 进入项目问答：先问“当前绑定企业适配这个项目吗？先给结论”，再追问“第一个缺口怎么补？”
6. 最后强调每个结论的证据、材料或规则来源，以及无上下文时系统会要求选择项目。

## 13. 诚实边界与后续计划

- 目前是模块化单体，不是已完成的微服务架构。
- Milvus 不在实际运行链路中，向量能力由 PostgreSQL `pgvector` 提供。
- 模型服务、MinerU、对象存储和 Redis 需要按环境独立配置；仓库不包含生产密钥或真实业务文件。
- 后续可补充：大规模检索压测指标、异步任务可观测性、基于真实标注集的抽取/RAG 评测，以及更精细的企业材料自动归类。
