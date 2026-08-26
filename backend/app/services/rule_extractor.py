"""
规则抽取引擎：从文档节点中用正则/NER 抽取简单字段，零 token 消耗。

基于 tender-extract patterns.py 改写，适配 LEI 的 ProjectField 模型。
"""
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import ProjectField
from app.services.project_field_registry import (
    canonical_project_field_code,
    compatible_project_field_codes,
)


@dataclass
class PatternDef:
    pattern: str
    confidence: float
    description: str
    flags: int = re.IGNORECASE | re.MULTILINE


@dataclass
class ExtractionResult:
    field_code: str
    value: Any
    confidence: float
    source: str  # 'rule' | 'ner' | 'llm'
    pattern: str | None
    node_id: str | None


# ============================================================
# 金额模式（字段专属，禁止把一个金额复制到预算、限价和报价）
# ============================================================
BID_AMOUNT_PATTERNS = [
    PatternDef(
        r'投标(?:总)?报价[：:]\s*(?:人民币)?[（(]?(?:大写)?[)）]?\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零]+(?:元[整]?)?)',
        0.95, "大写金额-投标报价"),
    PatternDef(
        r'投标(?:总)?报价[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:万)?元',
        0.95, "数字金额-投标报价"),
    PatternDef(
        r'投标金额[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:万)?元',
        0.95, "数字金额-投标金额"),
]

BUDGET_PATTERNS = [
    PatternDef(
        r'(?:项目预算|预算金额|采购预算)[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,4})?)\s*万元',
        0.95, "预算-万元"),
    PatternDef(
        r'(?:项目预算|预算金额|采购预算)[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*元',
        0.95, "预算-元"),
]

MAX_PRICE_PATTERNS = [
    PatternDef(
        r'(?:最高限价|最高投标限价|招标控制价)[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,4})?)\s*万元',
        0.95, "最高限价-万元"),
    PatternDef(
        r'(?:最高限价|最高投标限价|招标控制价)[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*元',
        0.95, "最高限价-元"),
]

# ============================================================
# 保证金模式
# ============================================================
DEPOSIT_PATTERNS = [
    PatternDef(
        r'投标保证金[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:万)?元',
        0.95, "投标保证金-数字"),
    PatternDef(
        r'投标保证金[：:]\s*(?:人民币)?[（(]?(?:大写)?[)）]?\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零]+(?:元[整]?)?)',
        0.95, "投标保证金-大写"),
    PatternDef(
        r'履约保证金[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:万)?元',
        0.90, "履约保证金"),
    PatternDef(
        r'(?:质量|工程)保证金[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:万)?元',
        0.85, "质量/工程保证金"),
    PatternDef(
        r'保证金[：:]\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:万)?元',
        0.80, "通用保证金"),
]

# ============================================================
# 投标截止模式（禁止把开标日、有效期和任意日期写成投标截止日）
# ============================================================
BID_DEADLINE_PATTERNS = [
    PatternDef(
        r'(?:投标文件(?:递交)?|投标)截止(?:日期|时间)?\s*(?:为|[：:])\s*'
        r'(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日(?:\s*(?:上午|下午)?\s*\d{1,2}\s*(?:时|[：:])\s*\d{2}\s*分?)?)',
        0.95, "投标截止日期"),
]

# ============================================================
# 项目/招标编号模式
# ============================================================
PROJECT_NUMBER_PATTERNS = [
    PatternDef(
        r'项目编号[：:]\s*([A-Za-z0-9\-_/]{5,30})',
        0.95, "项目编号"),
    PatternDef(
        r'招标编号[：:]\s*([A-Za-z0-9\-_/]{5,30})',
        0.95, "招标编号"),
    PatternDef(
        r'(?:项目|招标)编号[：:]\s*([A-Za-z0-9\-_/]{5,30})',
        0.90, "通用编号"),
]

# ============================================================
# 公司名称模式（采购人、招标人和代理机构不是同一个项目事实）
# ============================================================
PURCHASER_PATTERNS = [
    PatternDef(
        r'(?:采购人|项目单位|建设单位)[^：:]*[：:]\s*([^，。\n]{4,60}(?:有限公司|集团|公司|企业|局|中心|处))',
        0.95, "采购人名称"),
]

TENDERER_PATTERNS = [
    PatternDef(
        r'招标人[：:]\s*([^，。\n]{4,60}(?:有限公司|集团|公司|企业|局|中心|处))',
        0.95, "招标人名称"),
]

# ============================================================
# 联系人/人员模式（字段专属）
# ============================================================
LEGAL_REPRESENTATIVE_PATTERNS = [
    PatternDef(
        r'法定代表人[：:]\s*([一-龥]{2,4})',
        0.95, "法定代表人"),
]

CONTACT_PERSON_PATTERNS = [
    PatternDef(
        r'联系人[：:]\s*([一-龥]{2,4})',
        0.85, "联系人"),
]

# ============================================================
# 联系方式模式（电话和邮箱绝不共享正则）
# ============================================================
CONTACT_PHONE_PATTERNS = [
    PatternDef(
        r'(?:手机|电话|联系电话)[：:]\s*(1[3-9]\d{9})',
        0.95, "标注手机号"),
    PatternDef(
        r'(?:电话|联系电话)[：:]\s*(0\d{2,3}[-\s]?\d{7,8})',
        0.95, "标注座机号"),
]

CONTACT_EMAIL_PATTERNS = [
    PatternDef(
        r'(?:邮箱|电子邮[件箱])[：:]\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})',
        0.95, "标注邮箱"),
]

# ============================================================
# 字段 → 模式映射
# ============================================================
FIELD_PATTERNS: dict[str, list[PatternDef]] = {
    'bid_amount': BID_AMOUNT_PATTERNS,
    'budget': BUDGET_PATTERNS,
    'max_price': MAX_PRICE_PATTERNS,
    'deposit': DEPOSIT_PATTERNS,
    'bid_deadline': BID_DEADLINE_PATTERNS,
    'project_number': PROJECT_NUMBER_PATTERNS,
    'purchaser': PURCHASER_PATTERNS,
    'tenderer': TENDERER_PATTERNS,
    'legal_representative': LEGAL_REPRESENTATIVE_PATTERNS,
    'contact_person': CONTACT_PERSON_PATTERNS,
    'contact_phone': CONTACT_PHONE_PATTERNS,
    'contact_email': CONTACT_EMAIL_PATTERNS,
}


def _compile_patterns(patterns: list[PatternDef]) -> list[tuple[re.Pattern, float, str]]:
    compiled = []
    for pdef in patterns:
        try:
            compiled.append(
                (re.compile(pdef.pattern, pdef.flags), pdef.confidence, pdef.description)
            )
        except re.error:
            pass
    return compiled


_COMPILED: dict[str, list[tuple[re.Pattern, float, str]]] = {
    field: _compile_patterns(pats) for field, pats in FIELD_PATTERNS.items()
}


def extract_field(text: str, field_code: str) -> list[ExtractionResult]:
    """从文本中抽取指定字段的所有匹配结果"""
    results = []
    compiled_list = _COMPILED.get(field_code, [])
    if not compiled_list:
        return results

    for regex, confidence, description in compiled_list:
        for m in regex.finditer(text):
            results.append(ExtractionResult(
                field_code=field_code,
                value=m.group(1).strip(),
                confidence=confidence,
                source='rule',
                pattern=description,
                node_id=None,
            ))
    return results


def extract_all(text: str, node_id: str | None = None) -> list[ExtractionResult]:
    """从文本中抽取所有字段"""
    results = []
    for field_code in _COMPILED:
        for r in extract_field(text, field_code):
            r.node_id = node_id
            results.append(r)
    return results


class RuleExtractor:
    """
    规则抽取器：扫描文档节点，正则抽简单字段，写入 ProjectField。

    设计原则：
    - 规则结果 review_status = CONFIRMED（规则正则高精度，不需要复核）
    - 只写 ProjectField，不碰 Requirement（复杂条款留给 LLM）
    """

    def __init__(self, session: Session, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id
        self._existing: dict[str, ProjectField] = {}
        self._now = datetime.now(UTC)

    def process_nodes(self, nodes: list[dict]) -> list[ExtractionResult]:
        """
        批量处理节点，返回所有规则抽取结果。
        nodes: list[dict]，每个 dict 包含 id, content 字段
        """
        all_results: list[ExtractionResult] = []

        for node in nodes:
            content = node.get('content', '')
            node_id = node.get('id', '')
            if not content:
                continue
            results = extract_all(content, node_id)
            all_results.extend(results)

        return all_results

    def persist_results(
        self,
        results: list[ExtractionResult],
        evidence_map: dict[str, UUID] | None = None,
    ) -> int:
        """
        将抽取结果写入 ProjectField 表。

        规则结果：review_status = CONFIRMED，不覆盖已确认的字段。
        只写入 confidence >= 0.8 的结果。

        Args:
            results: 正则抽取结果列表，ExtractionResult.node_id 指向来源节点
            evidence_map: {node_id: evidence_id} 映射，用于写入 primary_evidence_id
        """
        evidence_map = evidence_map or {}
        persisted = 0

        # 按 field_code 分组，相同 field_code 保留最高 confidence
        best: dict[str, ExtractionResult] = {}
        for r in results:
            if r.confidence < 0.80:
                continue
            field_code = canonical_project_field_code(r.field_code)
            existing = best.get(field_code)
            if existing is None or r.confidence > existing.confidence:
                best[field_code] = r

        # 加载已有字段
        existing_fields = {
            canonical_project_field_code(f.field_code): f
            for f in self._session.query(ProjectField)
            .filter(
                ProjectField.project_id == self._project_id,
                ProjectField.review_status == 'CONFIRMED',
            )
            .all()
        }

        for field_code, result in best.items():
            existing = existing_fields.get(field_code)
            if existing is not None:
                # 已存在且已确认，不覆盖
                continue

            # 查找 pending 状态的旧字段覆盖
            pending = (
                self._session.query(ProjectField)
                .filter(
                    ProjectField.project_id == self._project_id,
                    ProjectField.field_code.in_(compatible_project_field_codes(field_code)),
                    ProjectField.review_status == 'PENDING',
                )
                .first()
            )

            value = self._parse_value(field_code, result.value)
            now = self._now
            primary_evidence_id = evidence_map.get(result.node_id) if result.node_id else None

            if pending:
                pending.field_code = field_code
                pending.value_json = {'value': value}
                pending.confidence = Decimal(str(result.confidence))
                pending.primary_evidence_id = primary_evidence_id or pending.primary_evidence_id
                pending.updated_at = now
            else:
                field = ProjectField(
                    id=uuid4(),
                    project_id=self._project_id,
                    field_code=field_code,
                    value_json={'value': value},
                    confidence=Decimal(str(result.confidence)),
                    review_status='CONFIRMED',
                    extraction_source='rule',
                    primary_evidence_id=primary_evidence_id,
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(field)
            persisted += 1

        if persisted > 0:
            self._session.flush()
        return persisted

    def get_confirmed_fields(self) -> set[str]:
        """返回当前已 CONFIRMED 的 field_code 集合"""
        confirmed = self._session.query(ProjectField.field_code).filter(
            ProjectField.project_id == self._project_id,
            ProjectField.review_status == 'CONFIRMED',
        ).all()
        return {canonical_project_field_code(f[0]) for f in confirmed}

    def _parse_value(self, field_code: str, raw: str) -> Any:
        """根据字段类型转换值"""
        if field_code in ('BID_AMOUNT', 'BUDGET', 'MAX_PRICE', 'BID_BOND'):
            # 金额：去掉逗号，尝试转 float
            cleaned = raw.replace(',', '').replace('，', '')
            try:
                return float(cleaned)
            except ValueError:
                return raw
        if field_code == 'BID_DEADLINE':
            return raw  # 保持字符串格式，前端展示时再解析
        return raw
