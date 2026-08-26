# 报告字段契约 V1

## 1. 目的与边界

本契约将报告定义为对**已确认业务事实**的查询与渲染，而不是让 LLM 在生成报告时再次通读招标文件并自行下结论。

```text
MinerU 原文节点
  -> TenderClause / Evidence
  -> ProjectField、Requirement（规则 + LLM 抽取并复核）
  -> MatchResult、Risk、Decision（确定性服务）
  -> ReportField Query
  -> 固定章节模板 + Evidence 引用
```

适用约束：

- `Evidence` 是招标原文、页码、章节和摘录的唯一事实源；业务字段只保存 `primary_evidence_id` 与可选的抽取快照，不能复制或伪造原文来源。
- 进入正式报告的项目事实和 Requirement 必须为 `CONFIRMED`；待复核或延后复核的数量进入分析覆盖说明，但不作为已确认结论。
- LLM 仅负责把完整条款转为候选字段、处理语义歧义和润色已确认事实；不得在报告阶段新增没有 Evidence 的事实或匹配结论。
- 企业标签只在企业匹配字段中作为查询条件；企业标签不是招标证据。任何“符合”结论必须同时带匹配到的 `Requirement Evidence`。

## 2. 字段规格

每个字段规格包含：`code`、展示章节、查询来源、过滤/排序、Evidence 要求和缺失表达。查询由后端固定注册，前端、请求参数与数据库都不能覆盖其语义。

| code | 报告章节 | 查询来源与召回条件 | 结果形态 | Evidence / 缺失策略 |
| --- | --- | --- | --- | --- |
| `PROJECT_PROFILE` | 项目概况 | `TenderProject` 人工值优先；否则 `ProjectField`：项目名称、编号、地点、范围、预算、工期 | 单值字段组 | 每个抽取字段需 `primary_evidence_id`；未抽到展示“待确认”，不编造 |
| `BID_SCHEDULE` | 关键时间与递交 | `ProjectFactResolver`：投标截止、开标；`Requirement`：保证金、递交、解密、签章 | 时间线 + 操作要求 | 每个条目回链 Requirement / ProjectField Evidence；仅日期须明确“具体时间待确认” |
| `QUALIFICATION_REQUIREMENTS` | 资格条件 | `Requirement(category=QUALIFICATION, CONFIRMED)`；按资质、人员、业绩、财务、信用业务域分组 | 条件清单 | 每条至少一条 Requirement Evidence；无确认项展示覆盖不足，不显示“无要求” |
| `SCORING_RULES` | 评分策略 | `Requirement(category=SCORING, CONFIRMED)`；优先 `score is not null`，按分值降序 | 评分项、分值、条件 | 无明确分值的条目只展示条件，不能补 0 分 |
| `BLOCKING_AND_RISKS` | 否决项与核心风险 | 显式阻断 Requirement + 当前 `Risk(status in PENDING/CONFIRMED)`，按严重度和截止期排序 | 风险、影响、动作 | 每项必须有 Evidence；未确认风险不提升为否决结论 |
| `ENTERPRISE_MATCHING` | 企业符合情况 | 项目绑定企业的 `EnterpriseMaterial.attributes` 归一化为标签 Query，反向召回 `QUALIFICATION/SCORING Requirement`，再由条件规则生成 `MatchResult` | 命中标签、状态、缺口 | 所有结果关联 Requirement Evidence；`MATCHED` 写明命中标签，`MISSING/UNCERTAIN` 写明未满足条件 |
| `ACTION_PLAN` | 行动计划 | `MISSING/UNCERTAIN MatchResult` + 未关闭 Risk + 临近 `BID_SCHEDULE` | 优先级待办 | 每个待办回链造成它的 Evidence；无待办时明确“当前未发现” |
| `ANALYSIS_COVERAGE` | 分析范围与局限 | Requirement / ProjectField 的确认、待复核、延后复核计数及 Evidence 覆盖率 | 数据质量说明 | 纯统计，无需单条 Evidence；禁止用低确认率包装为全量结论 |
| `BID_DECISION` | 综合建议 | 已生成的 `Decision`，只消费上述匹配、风险、事实 | 建议、原因、人工最终决策 | 关联支撑该建议的 Evidence；不由报告 LLM 重算建议 |
| `UNCLASSIFIED_ALERTS` | 特殊条款与异常发现 | `TenderClause` 中强制/阻断信号存在、但未映射到上述字段的已确认 Requirement | 兜底异常清单 | 必须回链 Evidence；保证固定模板不吞掉少见但重要的条款 |

## 3. 查询与渲染接口

字段注册表应在服务端用不可变结构实现，ReportService 不再直接在章节方法中自行拼装查询：

```python
@dataclass(frozen=True)
class ReportFieldSpec:
    code: str
    section_code: str
    source_types: tuple[str, ...]
    require_evidence: bool
    empty_message: str
    resolver: Callable[[ReportQueryContext], ReportFieldValue]
```

`ReportQueryContext` 在一次报告生成中批量预加载：确认的 `ProjectField`、`Requirement`、`MatchResult`、`Risk`、`Decision`、企业标签和所有 Evidence 映射。字段 resolver 只能从这个上下文读取，禁止 N+1 查询和临时 LLM 调用。

`ReportFieldValue` 返回结构化条目而不是 Markdown：

```json
{
  "code": "ENTERPRISE_MATCHING",
  "status": "READY",
  "items": [
    {
      "title": "项目经理资格",
      "status": "MATCHED",
      "value": {"matched_tags": ["建造师资格=机电工程专业二级注册建造师"]},
      "evidence_ids": ["requirement-evidence-id"]
    }
  ],
  "empty_reason": null
}
```

最后由统一 renderer 把字段值转为 Markdown / PDF，并在渲染末尾按 `evidence_ids` 输出 `页码｜章节｜招标原文摘录`。这使报告的自然语言布局可演进，而事实查询、证据链和缺失口径保持稳定。

## 4. 企业匹配字段的具体口径

`ENTERPRISE_MATCHING` 是字段查询的一种，不是独立的报告链路：

1. 将项目绑定企业已确认材料的 `material_type`、`name`、`level`、`amount` 和 `attributes` 扁平为受控标签；例如 `建造师资格=机电工程专业二级注册建造师`、`工作经验=6`。
2. 由标签键和材料类型生成 Query，在已确认的 `QUALIFICATION/SCORING Requirement` 中先按业务域定向召回，再比较 `conditions`。
3. 所有条件满足为 `MATCHED`；有相关标签但字段不足或值不明确为 `UNCERTAIN`；没有可满足标签为 `MISSING`。
4. `MatchResult.reason` 记录命中的标签摘要或缺失条件，`MatchEvidence(side=REQUIREMENT)` 强制关联招标 Requirement Evidence。
5. 企业证明文件若存在，仅作为增强 Evidence；当前人工维护企业标签的模式下，证明文件缺失不能把标签匹配结果降为 `MISSING`。

## 5. V1 实施顺序

1. 增加服务端 `ReportFieldSpec` 注册表与批量 `ReportQueryContext`，先迁移 `PROJECT_PROFILE`、`BID_SCHEDULE`、`QUALIFICATION_REQUIREMENTS`、`BLOCKING_AND_RISKS` 四类字段。
2. 改造 `MatchingService` 为企业标签反向召回，产出带命中标签摘要和 Requirement Evidence 的 MatchResult。
3. 将现有 ReportService 各章节改为消费字段值，并增加 `UNCLASSIFIED_ALERTS` 兜底章节。
4. 为每个字段写“有数据、缺 Evidence、无数据、待复核”测试；再用 zb5 / zb12 从真实 API + ARQ 端到端回归，检查报告每个结论是否能定位原文。
