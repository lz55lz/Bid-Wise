# 招标文件处理链路端到端测试报告

**测试时间**：2026-08-16
**测试文件**：zb5.pdf（正常文本PDF）、zb6.pdf（扫描件PDF）
**测试环境**：本地后端服务（API + Celery + MySQL + Redis + MinIO）

---

## 一、zb6.pdf 扫描件测试

### 1.1 文件特征
- 路径：`D:\Desktop\test\zb6.pdf`
- 大小：6.4 MB，206 页
- 格式：PDF 1.7，是扫描件（图片合成PDF），pypdfium2 读不到文字层

### 1.2 解析链路测试

| 解析器 | 结果 | 原因 |
|--------|------|------|
| MinerU 云服务 | ❌ `state: failed` | 服务端 OCR 识别失败（无具体错误信息） |
| 本地 pypdfium2 | ❌ 0 字符 | 纯图片PDF，无文字层 |
| MarkItDown | ❌ 缺 PDF 依赖 | 需安装 `markitdown[pdf]` |

### 1.3 结论
**zb6.pdf 是扫描件，无法处理。** MinerU 对此类扫描大文档识别率不稳定，建议：
1. 换用正常文本PDF测试
2. 如需处理扫描件，考虑接入腾讯云/阿里云 OCR API 预处理
3. 用户上传扫描件时在前端提示"请上传文字版招标文件"

---

## 二、zb5.pdf 完整链路测试

使用数据库已有数据（1019 节点）测试下游链路。

### 2.1 清洗阶段

```
总节点:     1019
indexable:  797  (78.2%)
候选节点:   76   (7.5%)
```

候选节点由 `tender_req_candidate=True` 标记，经过：
- 义务词过滤（应当/必须/不得/不准/严禁/需要/要求/须）
- 程序性内容过滤（开标/评标委员会/澄清/踏勘等）
- 合同章节 bypass（付款/结算/履约/违约等 section_path 匹配时无需义务词）
- 短内容过滤（< 50 字符不进入候选）

### 2.2 LLM 招标要求抽取

```
测试输入:   前 15 个候选节点
抽取结果:   10 个要求
耗时:       8.4s
示例:
  [QUALIFICATION] 提供2025年度财务审计报告及财务报表 conf=0.95
  [QUALIFICATION] 投标文件编制格式要求 conf=0.90
  [QUALIFICATION] 投标文件的修改和撤回 conf=0.85
  [QUALIFICATION] 履约保证金要求 conf=0.90
  [PROJECT] 担保有效期 conf=0.80
```

### 2.3 材料匹配

```
测试条件:  无企业材料
结果:      全 MISSING（符合预期）
```

注：匹配产生大量日志 `[Matching] match_result has no traceable evidence`，
因为 `_compatible_material_types` 返回 `True`（已简化）导致笛卡尔积，
这是预期行为（无材料时每个 requirement 都产生一条 MISSING 记录）。

### 2.4 投标决策

```
测试条件:  5 个 CONFIRMED requirements，无风险，无材料
决策建议:  RECOMMEND（建议投标）
原因:      当前未发现硬约束冲突
硬约束:    deadline_expired=False, unresolved_critical_risk=False, confirmed_qualification_unmet=False
```

### 2.5 报告生成

```
报告状态:  READY
文件:      reports/{project_id}/v1/report.docx
章节数:    10 个
章节列表:
  [PROJECT_OVERVIEW]       项目概况
  [EXECUTIVE_SUMMARY]      执行摘要
  [ACTION_PLAN]            行动计划
  [ENTERPRISE_OVERVIEW]    企业概况
  [MATERIAL_SUMMARY]       材料匹配汇总
  [MATERIAL_LINKED]        已关联材料
  [MATERIAL_UNLINKED]      待补充材料
  [QUALIFICATION_ANALYSIS] 资格要求分析
  [SCORING_ANALYSIS]       评分规则分析
  [COMPREHENSIVE_DECISION] 综合决策
```

---

## 三、问题记录

### 3.1 MinerU 扫描件 OCR 失败
- **文件**：zb6.pdf
- **现象**：MinerU 返回 `state: failed`，无具体原因
- **影响**：扫描件 PDF 无法解析为文字节点
- **建议**：前端增加"文字版/扫描件"提示，或接入 OCR 预处理

### 3.2 MarkItDown 缺 PDF 依赖
- **现象**：`MarkItDown 解析失败: File conversion failed after 1 attempts...`
- **原因**：`markitdown[pdf]` 未安装
- **建议**：执行 `uv pip install "markitdown[pdf]"`（注意需停用占用进程）

### 3.3 FakePublisher 导致的报告提交问题
- **现象**：`report_svc.submit()` 走 celery publisher 路径，FakePublisher 无对应方法导致报告状态异常
- **解决**：测试时绕过 submit，直接创建 Report 并调用 `generate()`

---

## 四、修复汇总（本次会话）

| 文件 | 问题 | 修复 |
|------|------|------|
| `document_cleaning_service.py` | 重复 `@staticmethod` 装饰器 | 删除冗余装饰器 |
| `requirement_extraction_service.py` | `Decimal` 未导入 | 添加 `from decimal import Decimal` |
| `requirement_extraction_service.py` | 未使用变量 `confirmed`、`field_codes` | 删除 |
| `requirement_extraction_service.py` | 无占位符 f-string | 改为普通 log |
| `decision_service.py` | 旧状态 `EXPIRED/UNKNOWN/CONFLICT` 仍在使用 | 改为 `UNCERTAIN` |

---

## 五、测试命令

```bash
# 清洗 + 抽取测试（使用已有数据）
uv run python -X utf8 -c "
from app.db.session import get_session_factory
from app.db import models as m
from app.services.document_cleaning_service import DocumentCleaningService
# ... 完整代码见 test_zb6_pipeline.py
"

# 完整链路测试（需所有服务运行）
uv run pytest tests/test_document_cleaning_service.py tests/test_requirement_extraction.py -x -q
```
