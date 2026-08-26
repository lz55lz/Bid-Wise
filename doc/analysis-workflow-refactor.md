# 投标分析工作流重构基线

## 目标

将投标文件解析、企业材料匹配、风险检查、决策和报告收敛成项目级的单一分析运行（Analysis Run）。系统应能复现任一报告所依据的输入版本、规则版本和证据集合。

## 边界

- 文档处理流水线只负责产生可追溯的结构化事实：文档节点、抽取标签、需求候选和 Evidence。
- 项目分析运行负责冻结输入快照，并按依赖执行匹配、风险、决策和报告。
- PostgreSQL 是分析运行和快照的事实源；MinIO 只保存源文件和已生成报告。
- 不部署或调用病毒扫描服务。上传安全由服务端文件扩展名、大小、魔数和访问授权控制。
- 决策门槛和评分必须由确定性规则生成；LLM 只用于抽取、解释与报告组织，所有业务结论必须关联 Evidence。

## 目标流程

```text
文档上传与解析完成
  -> 创建 AnalysisRun / 冻结 AnalysisSnapshot
  -> 材料匹配 + 规则风险检查
  -> 确定性投标决策
  -> 带 Evidence 的正式报告
```

AnalysisRun 只允许一个阶段在运行，失败后可从失败阶段重试。每个阶段的结果 ID、状态、耗时、错误和证据引用写入快照，避免新文件或材料覆盖历史报告的依据。

报告异步任务的终态必须同时回写 `AnalysisRun` 与快照中的 `stage_outputs.REPORT`（`SUCCEEDED` / `FAILED`、完成时间、报告 ID 与错误信息）；项目详情页的阶段时间线只能读取该统一事实，不能以报告任务入队状态推断已完成。

`MatchResult` 在项目维度维护“当前视图”，但缺口行（`material_id IS NULL`）在数据库中按 Requirement 唯一。每次新的 Analysis Run 先失活旧结果、再重用同一 Requirement 的历史缺口行并更新为当前，保证重跑不会因唯一键冲突失败；历史报告仍只引用其已冻结的 Evidence 与渲染结果。

在 `QUEUED`、`RUNNING`、`WAITING_HUMAN` 状态下，数据库触发器拒绝修改被快照引用的企业材料、项目 Requirement、项目属性和规则版本。运行结束后再允许维护输入；输入变化必须通过新的 AnalysisRun 重新分析。
Requirement Evidence 与企业材料的证明文件关联也在同一保护范围内，防止分析运行中替换证据来源。

## 兼容策略

已有 `bid_*` 表保留给文档 LangGraph 内部抽取和旧接口读取；项目最终事实以 `app.requirements`、`app.match_results`、`app.risks`、`app.decisions`、`app.reports` 和新增分析运行表为准。

## 条款驱动文档理解（V2）

`document_nodes` 是解析器输出的版面事实，不能同时承担业务条款和检索分块职责。V2 在它之后增加不可变的派生层：

```text
DocumentNode (页码、坐标、原始版面)
  -> TenderClause (章节上下文、完整条款、强制信号、节点标签)
  -> ClauseCandidateRecall (多路召回、融合分数、选送决定)
  -> Requirement (可复核业务要求)
```

- `DocumentNode` 原样保存 MinerU 的版面节点；`TenderClause` 只从单个节点派生，或在同一节点内按文档明确写出的条款号、阶段事项和列表项拆分。两层都不得以 token/字符上限拆分、合并或截断正文，表格、单元格和列表绝不跨越。
- `ClauseEvidence` 保留 Clause 到全部源 Evidence 的多对多关联；Requirement 至少保留一个主 Evidence，报告可沿此链展示原文、页码和章节。
- Requirement 抽取优先使用 `TenderClause.contextualized_content`，在没有 Clause 的历史版本上才回退 `document_nodes`。
- 清洗阶段输出的业务域、细粒度 `bid_tag_dict` 命中、强制词和量化约束必须随 `TenderClause.quality_metadata` 传递；清洗阶段的节点预算仅供历史节点链路兼容，**不再决定**最终 LLM 输入。`TenderClause` 生成后必须重新以完整条款正文打标并执行候选召回，避免一个 MinerU 节点内的不同条款共用错误的候选结论。
- `ClauseCandidateRecall` 以 `TenderClause` 为唯一候选和 LLM 输入单位，按“规则/标签路由、关键词路由、同版本 BGE-M3 + BM25 混合路由”过召回；各路只保存名次，使用加权 RRF（`k=60`）融合。混合检索命中的 `SearchChunk` 只能作为定位器，通过 `source_node_id` 映射回完整 Clause，绝不能把索引碎片直接交给 LLM。
- 召回先过取最多为 LLM 配额五倍的候选，再按融合名次去重，并按业务域轮转选择，防止高频“保证金”等单一主题挤占全部预算。显式阻断条款、单域明确义务条款走规则直出且不占 LLM 配额；只有其余歧义条款中融合分数靠前者发送给 LLM。向量服务不可用时，规则与关键词路由仍可独立完成可审计的降级选择，不能回退为全文灌入模型。
- 每个 Clause 在 `quality_metadata.candidate_recall` 保存策略版本、各路名次、RRF 分数、最终名次、是否选送 LLM、未选原因和混合路由状态；`document_versions.cleaning_summary` 同时汇总本次候选数量、规则直出数量、LLM 选送数量和降级原因，供快照、报告和人工复核追溯。
- 规则直出 Requirement 保持 `extraction_source=rule`、Evidence 与 `PENDING` 复核状态；它不是自动确认。LLM 不得重新处理已由规则无损保留的条款。
- Requirement 的 `is_mandatory` 必须由原文中的真实义务或否决语义决定，不能因为它来自规则路径、资格路径或评分路径而默认设为真。LLM 项目字段和 Requirement 必须回引本次输入内存在的 Evidence order；空锚点、跨批锚点或无法解析为 Evidence 的输出直接丢弃，禁止静默改绑到第一条 Clause。评分项只有原文明确给出分值时才保存 score，禁止以 0 或估算值伪装未知分值。
- 结构化输出的校验边界以“局部丢弃、整体保留”为原则：单个项目字段缺少非空 `value_json` 时只丢弃该字段，不能让同一批中有合法 Evidence 的 Requirement 一并失败。规则分类仅将“项目名称、编号、范围、工期、交货期、概况”等明确事实归入 `PROJECT`；“本项目”这类泛称不得覆盖递交、解密等投标操作性要求的 `BUSINESS` 归类。
- `联合体`本身不是资质证明：含资质、资格、业绩、人员或证书的联合体条款才归入 `QUALIFICATION`；“不得重复投标”等联合体投标行为约束归入 `BUSINESS`。只有 `QUALIFICATION` 与 `SCORING` Requirement 进入企业材料匹配和“缺少材料证据”风险检查，项目事实和投标行为约束不得被误报为企业材料缺口。
- 节点标签不另建可随意扩展的字典，复用受控的 `bid_tag_dict` 作为细粒度词库，并保留内置基线以保证字典初始化或查询失败时仍可解析。每个节点与 `document_versions.cleaning_summary` 写入有效策略的 SHA-256 指纹；AnalysisSnapshot 的输入清单必须冻结该指纹。
- `clean` 必须在任何标签、向量化、条款聚合或 LLM 调用之前执行文本质量闸门：替换字符、控制字符和异常文字体系（例如 PDF 乱码后的希腊/西里尔字符）、目录导引页和超过 1,200 可见字符的未解析大块，分别写入 `GARBLED_TEXT`、`CONTENTS_PAGE` 或 `OVERSIZED_CHUNK`，且统一 `indexable=false`、不得进入候选队列。该判定由两条清洗入口共用，禁止一条链路把低质量文本当作可用正文放行。
- MinerU 产物直接作为 `DocumentNode` 落库，不在 Parser 内拼接相邻节点；`TenderClause` 只识别文本显式给出的语义边界。没有可恢复边界的超长原文不得被任意截断，须完整保留并由质量闸门拒绝进入索引、标签器和 LLM，等待重解析。
- 节点业务域以正文为主、章节路径只作正文无域时的回退，避免“技术/商务/资格”这类综合标题把每个子条款标成多域。`clean` 保留 `tender_req_candidate` 的一级章节预算（4 条/一级章节、全局 48 条）用于历史节点抽取兼容；Clause 链路的最终预算由 `ClauseCandidateRecall` 独占。后续 `annotate` 不得扩大节点预算，只基于 MinerU 已落库的节点写入分类元数据，供标签召回使用。
- 节点标签还必须标明 `analysis_scope`：仅投标人/供应商/申请人/联合体需履行的要求及实际评分标准可进入通用 Requirement 抽取；招标人、评标委员会、监理人、甲乙丙方、承包人或中标人的流程/合同义务不得占用材料匹配和 HITL 队列，但保留在带 Evidence 的 `TenderClause` 中，供专门的合同风险与报告阶段使用。无主体的“投标报价、投标文件、保证金、递交、解密、签章”等明确投标动作仍视为投标人要求。
- 含“不得、否决、废标、无效、不予”等显式阻断语义的**投标人要求**另标 `blocking_signal`。它们即使超出 LLM 预算也由规则直接保留为带 Evidence 的 `PENDING` Requirement，绝不静默丢弃；非投标人主体的阻断语义不进入材料匹配路径。这条保全路径不调用 LLM，也不代表自动确认。
- V2 与原节点链路并行，允许用同一文档比较强制项召回、证据可定位率和人工复核量后再扩大使用范围。

验收指标：强制/废标项召回率不低于 95%，Requirement 证据可定位率为 100%，并且报告必须呈现页码、章节和原文摘录而不是内部 ID。

## Human-in-the-loop 队列策略

人工复核不是让用户逐条录入模型候选。抽取完成后，系统按证据完整性、显式强制信号、类别和置信度进行归并与优先级排序：

- 标题归一化后相同或互为包含的候选合并为一个 Requirement，并合并其 Evidence 锚点。
- 只有带明确硬性措辞（如“必须”“不得”“应当”“否决”）的条目才标记为强制项；“资格”一词本身不构成强制信号。
- 最高优先级的有限队列（当前为 20 条）保持 `PENDING`，其余有效但低优先级候选标为 `DEFERRED`，在关键项处理完成后再进入队列；它们不是被删除或驳回。
- 仅对有来源 Evidence、非强制、项目事实类且置信度不低于 0.95 的候选自动确认为 `CONFIRMED`，并写入自动确认原因。评分、资格和显式强制条款永不自动确认。

报告需分别展示待人工复核、延后复核和已确认数量，避免把延后候选误报为已处理。

## 企业标签反向召回与匹配

企业材料在本项目中是人工维护且受信任的结构化标签，不要求上传企业证明文件或进入人工复核。匹配以项目绑定企业的已确认材料为唯一范围，并采用“企业标签查询招标要求”的反向召回：

```text
EnterpriseMaterial.attributes（固定资质 / 人员 / 业绩 / 财务标签）
  -> 标签归一化与业务域查询
  -> 在已确认的 QUALIFICATION / SCORING Requirement 中定向召回
  -> 条件规则判定（全满足 / 部分满足 / 缺失）
  -> MatchResult + Requirement Evidence
```

- 企业标签只参与确定性召回和条件比较，不发送给 LLM；LLM 的职责止于从招标原文抽取并回链 Requirement。
- 反向召回先按材料类型和业务域缩小范围，再以标签键、标签值和 Requirement 条件进行匹配，避免 Requirement × 全部企业材料的笛卡尔遍历。
- `MATCHED` 必须列出命中的企业标签；`MISSING` / `UNCERTAIN` 必须列出未满足或待确认的条件。企业标签的可信来源可在结果中说明，但不能替代招标侧证据。
- 每个 MatchResult 无论结果如何，都必须关联 Requirement 的 Evidence。报告对“企业符合”的表述必须同时呈现：企业命中标签，以及 `页码｜章节｜招标原文摘录`，明确该标签满足的是哪一条招标要求。
- 企业证明文件若未来接入，可作为增强佐证追加到 MatchEvidence；其缺失不得在当前的标签维护模式下把已满足的企业标签降级为 `MISSING`。

## 质量评测闭环

`doc/evals/*.json` 保存人工维护的黄金 Requirement：类别、原文页码、关键词和强制性。`backend/scripts/evaluate_requirement_quality.py` 对项目已抽取 Requirement 输出黄金集召回、候选精确率、证据可定位率与 HITL 压缩率。黄金集只接受人工复核后的条目；模型输出不得反向成为黄金标签。

## 报告事实渲染约束

报告不是新的推理来源，只渲染已冻结的 Requirement、Match、Risk、Decision 与 Evidence。

- 执行摘要必须展示 Requirement 确认率、强制项确认率、已确认项材料匹配率和待人工复核量。
- 风险、缺口与已确认要求使用读者可定位的 Evidence 形式：`页码｜章节｜原文摘录`；禁止展示内部 UUID。
- 当确认率或强制项覆盖率不足时，报告必须显式暴露该不确定性，不能把局部结论包装为全量投标结论。

## 已确认项目事实的统一消费

`ProjectField` 是从招标文件抽取并回链 Evidence 的项目事实源，`TenderProject` 是人工维护的项目事实源。匹配、风险、决策、报告和任务幂等哈希必须统一经 `ProjectFactResolver` 读取，人工维护的非空值优先于已确认的 `ProjectField`；禁止各下游服务直接只读取 `TenderProject.bid_deadline`。

- 截止值若只抽取到日期，精度为 `DATE`：证书有效期可按该日期比较，但当日不得在 `00:00` 被判为过期，报告须写明“具体时间待确认”，决策保持 `CAUTION`。
- 只有原文或人工输入提供日期和时刻时，精度才是 `DATETIME`，可执行精确逾期判断。
- 已确认的 `ProjectField`（值、置信度、Evidence 与更新时间）必须进入 `AnalysisSnapshot.input_manifest`；变更后只能新建 AnalysisRun，不能覆盖历史运行依据。

规则字段抽取也必须采用字段专属模式：电话与邮箱、预算与最高限价、投标截止日与开标日等不同业务事实不得共享宽泛正则。规则路径的原则是“字段明确才写入，宁缺毋滥”；不明确的候选交给带 Evidence 的 LLM / 人工复核路径，避免错误事实污染检索、决策和报告。

LLM 结构化抽取必须强制单一命名函数调用；失败重试的最后一次使用严格提示。单个候选若缺少有效的 `confidence`、非空值或输入批次内存在的 Evidence 锚点，只丢弃该候选，不能让整批字段或 Requirement 失效。截止日期只有原文给出时刻才可返回时刻，禁止把日期补造成 `23:59`。
