# 投标分析报告模板 V1

> 定位：给投标负责人快速判断“能不能投、差什么、何时处理”。
>
> 规则：所有带事实结论的行均可定位到 `页码｜章节｜招标原文摘录`；没有足够依据时明确写“待确认”，不补写猜测性内容。

---

## 封面与报告说明

```text
《{project_name}》投标分析报告
项目编号：{project_code}
招标人：{purchaser_name | 待确认}
投标主体：{enterprise_names}
生成时间：{generated_at}
分析输入：招标文件 {document_name}（版本 {document_version}）
分析覆盖：已确认 {confirmed_requirement_count} 条 / 待复核 {pending_requirement_count} 条
```

需要字段：`PROJECT_NAME`、`PROJECT_CODE`、`PURCHASER_NAME`、`ENTERPRISE_NAMES`、`DOCUMENT_VERSION`、`ANALYSIS_COVERAGE`。

---

## 1. 一页决策摘要

```text
系统建议：{decision_suggestion}              人工最终决策：{final_decision | 尚未确认}

投标截止：{bid_deadline}                     距截止：{days_to_deadline}
资格匹配：{matched_count}/{matching_total}   高风险/否决项：{critical_risk_count}
待补齐：{missing_count} 项                   待人工复核：{pending_requirement_count} 条

建议原因：
1. {decision_reason_1}
2. {decision_reason_2}
3. {decision_reason_3}
```

需要字段：`BID_DEADLINE`、`MATCHING_SUMMARY`、`RISK_SUMMARY`、`ANALYSIS_COVERAGE`、`BID_DECISION`。

缺失口径：未生成 Decision 时显示“尚未生成决策，需先完成匹配与风险检查”。截止时间只有日期时增加“具体时间待确认”。

---

## 2. 项目与投标范围

```text
| 项目字段 | 内容 |
| --- | --- |
| 项目名称 | {project_name} |
| 项目编号 | {project_code} |
| 招标人 | {purchaser_name} |
| 建设/交付地点 | {location} |
| 项目范围 | {project_scope} |
| 工期/交货期 | {duration} |
| 预算/最高限价 | {budget} |
| 采购/招标方式 | {procurement_method} |
```

每一项后附：`依据：第 {page_no} 页｜{section_path}｜“{quoted_text}”`。

需要字段：`PROJECT_NAME`、`PROJECT_CODE`、`PURCHASER_NAME`、`LOCATION`、`PROJECT_SCOPE`、`DURATION`、`BUDGET`、`PROCUREMENT_METHOD`。

缺失口径：单字段缺失显示“待确认”，绝不以相近字段或模型推测补足。

---

## 3. 关键时间与递交清单

```text
| 事项 | 时间/要求 | 状态 |
| --- | --- | --- |
| 获取文件截止 | {document_acquisition_deadline} | {known / 待确认} |
| 现场踏勘/答疑 | {site_visit_or_qa} | {known / 不适用 / 待确认} |
| 保证金递交 | {bid_bond_requirement} | {known / 不适用 / 待确认} |
| 投标文件递交截止 | {bid_deadline} | {known / 待确认} |
| 开标时间及地点 | {bid_opening} | {known / 待确认} |
| 签章、密封、加密与递交方式 | {submission_rules} | {known / 待确认} |
```

每行附原文依据。截止期在 72 小时内或已过期时，行级标红并同步进入行动计划。

需要字段：`BID_SCHEDULE`、`BID_BOND`、`SUBMISSION_RULES`。

---

## 4. 资格条件与企业符合情况

```text
| 招标资格要求 | 企业命中标签 | 结论 | 原文依据 |
| --- | --- | --- | --- |
| {qualification_requirement} | {matched_enterprise_tags} | MATCHED / UNCERTAIN / MISSING | 第 {page_no} 页｜{section_path}｜“{quoted_text}” |
```

分组顺序固定为：资质许可 → 人员资格 → 类似业绩 → 财务能力 → 信用与合规 → 其他资格。

结论口径：

- `MATCHED`：Requirement 的全部条件被项目绑定企业的固定标签满足；展示具体标签，例如“建造师资格=机电工程二级注册建造师、工作经验=6 年”。
- `UNCERTAIN`：召回到相关企业标签但条件不完整、数值无法比较或有效期需要确认；列出待确认条件。
- `MISSING`：未召回可满足的企业标签；列出缺失条件与建议补齐动作。

需要字段：`QUALIFICATION_REQUIREMENTS`、`ENTERPRISE_TAGS`、`ENTERPRISE_MATCHING`。

---

## 5. 评分项与得分策略

```text
| 评分项 | 分值 | 得分条件 | 企业当前信息 | 建议 |
| --- | ---: | --- | --- | --- |
| {scoring_title} | {score | 未明确} | {scoring_conditions} | {match_summary} | {action} |
```

只展示招标原文明确给出分值的评分项；无明确分值的条件可在“其他评分要求”列出，但不估算分数或总分。

需要字段：`SCORING_RULES`、`ENTERPRISE_MATCHING`。

---

## 6. 否决项、核心风险与合同关注点

```text
| 优先级 | 风险/否决条款 | 影响 | 当前状态 | 建议动作 | 原文依据 |
| --- | --- | --- | --- | --- | --- |
| P0 | {risk_title} | {impact} | {risk_status} | {action} | 第 {page_no} 页｜{section_path}｜“{quoted_text}” |
```

排序：明确否决项 → `CRITICAL/HIGH` 风险 → 截止时间风险 → 普通风险。非投标人主体的合同履约义务不混入资格缺口，但可在“合同关注点”单独列示。

需要字段：`BLOCKING_REQUIREMENTS`、`RISK_ITEMS`、`CONTRACT_ALERTS`。

---

## 7. 行动计划

```text
| 优先级 | 截止时间 | 待办动作 | 触发原因 | 责任建议 |
| --- | --- | --- | --- | --- |
| P0 | {due_at} | {action} | {source_title} | {owner_role} |
```

行动由确定性规则生成：缺失资格、待确认人员条件、临近时间节点、未关闭高风险和待复核强制条款。每项可回链触发它的招标原文。

需要字段：`ACTION_PLAN`、`BID_SCHEDULE`、`ENTERPRISE_MATCHING`、`RISK_ITEMS`、`ANALYSIS_COVERAGE`。

---

## 8. 分析覆盖与待确认事项

```text
| 指标 | 数值 |
| --- | ---: |
| 已确认 Requirement | {confirmed_requirement_count} |
| 强制项已确认率 | {mandatory_confirmation_rate} |
| Requirement 证据可定位率 | {evidence_coverage_rate} |
| 待人工复核 | {pending_requirement_count} |
| 延后复核 | {deferred_requirement_count} |
```

随后列出最多 10 条最优先的待复核/未归类强制条款及其原文依据。该章节的目标是诚实披露分析边界，不把局部结论包装为全面结论。

需要字段：`ANALYSIS_COVERAGE`、`UNCLASSIFIED_ALERTS`。

---

## 字段反推：后续召回的唯一入口

模板中的粗体字段应注册为服务端 `ReportFieldSpec`。每一个字段都规定：

```text
字段 code
  -> 数据源（ProjectField / Requirement / MatchResult / Risk / Decision）
  -> 过滤条件和排序
  -> 是否必须 Evidence
  -> 空值文案
  -> 渲染位置
```

因此后续不是“为了报告把全文再交给 LLM”，而是根据模板的字段 code 向已确认事实库查询。抽取阶段负责让这些字段有值并有 Evidence；报告阶段只负责查询、组合和渲染。
