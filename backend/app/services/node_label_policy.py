"""Versioned, deterministic node-labelling policy backed by the tag dictionary.

The existing ``bid_tag_dict`` is the maintained business lexicon.  This module
maps its detailed tags to a small, stable set of routing domains and keeps a
hash of the effective policy on every labelled node.  A built-in baseline is
deliberately retained so parsing stays available during dictionary bootstrap
or a temporary metadata-query failure.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import BidTagDict

_POLICY_FORMAT_VERSION = "node-label-policy/v5"
_DOMAIN_CATEGORY_MAP = {
    "CAT03": "QUALIFICATION",
    "CAT04": "SUBMISSION",
    "CAT05": "BUSINESS",
    "CAT06": "TECHNICAL",
    "CAT07": "SCORING",
    "CAT08": "SUBMISSION",
    "CAT09": "BUSINESS",
    "CAT10": "RISK",
    "CAT11": "RISK",
}
_BASELINE_DOMAIN_RULES = {
    "QUALIFICATION": r"资格|资质|业绩|人员|证书|财务|审计|信用|失信|黑名单",
    "SCORING": r"评分|评审|分值|得分|评标",
    "BUSINESS": r"保证金|付款|报价|工期|交货|履约|验收|合同|发票",
    "TECHNICAL": r"技术|参数|规格|性能|标准|方案",
    "SUBMISSION": r"投标文件|递交|开标|截止|签章|密封",
}
_OBLIGATION = re.compile(
    r"不得|必须|应当|须(?!知)|需提供|应提供|应具备|应满足|否则|无效|否决|废标"
)
_BLOCKING = re.compile(r"不得|否决|废标|无效|不予")
_QUANTITATIVE = re.compile(r"\d+(?:\.\d+)?\s*(?:年|月|日|天|%|分|万元|元|项|人|套|份)")
_NOISE = re.compile(r"^(?:目录|招标文件|第?\d+页|共\d+页)$")
_BIDDER_SUBJECT = re.compile(r"投标人|供应商|申请人|联合体|我方")
_BIDDER_OBLIGATION = re.compile(
    r"(?:投标人(?!须知)|供应商|申请人|联合体|我方)"
    r"[\s，、：:（）()\-\w\u4e00-\u9fff]{0,36}?"
    r"(?:不得|必须|应当|应|须(?!知)|需要|提供|具备|满足|递交|提交|签署|声明)"
)
_BIDDER_DISQUALIFICATION = re.compile(
    r"(?:投标人(?!须知)|供应商|申请人|联合体|我方)"
    r"[^。；\n]{0,50}?(?:其(?:投标|资格)|投标(?:文件)?[^。；\n]{0,20}?(?:否决|废标|无效|失效))"
)
_BIDDER_ELIGIBILITY_NEGATION = re.compile(
    r"(?:投标人(?!须知)|供应商|申请人|联合体)"
    r"[^。；\n]{0,100}?"
    r"(?:未(?:在[^。；\n]{0,60})?(?:被)?列入|未曾有|不存在)"
)
_QUALIFICATION_LIST_REQUIREMENT = re.compile(
    r"(?:^|[（(]\s*\d+\s*[)）])\s*"
    r"(?:资质|业绩|财务|信用|信誉|人员|项目经理)[^。；\n]{0,20}?"
    r"要求\s*[：:]\s*(?:提供|具备|满足)"
)
_BIDDER_IMPLIED_ACTION = re.compile(
    r"(?:不得|必须|应当|须(?!知)|需|应)[^。；\n]{0,48}?"
    r"(?:投标报价|投标文件|投标保证金|投标有效期|递交(?:电子)?投标文件|"
    r"远程解密|电子签章|密封(?:递交)?)"
    r"|(?:投标报价|投标文件|投标保证金|投标有效期|递交(?:电子)?投标文件|"
    r"远程解密|电子签章|密封(?:递交)?)[^。；\n]{0,48}?"
    r"(?:不得|必须|应当|须(?!知)|需|应)"
)
_NON_BIDDER_SUBJECT = re.compile(
    r"招标人|采购人|评标委员会|评审委员会|招标代理(?:机构)?|"
    r"监理(?:人)?|发包人|承包人|甲方|乙方|丙方|中标人"
)
_PROCESS_ONLY = re.compile(
    r"重新招标|不再招标|评标活动有关的工作人员|评标纪律|异议|质疑|投诉"
)
_SCORING_CRITERIA = re.compile(r"评分(?:标准|因素|办法)|评审因素|得分|分值")
_QUOTED_TERM = re.compile(r"[\"“]([^\"”]{2,32})[\"”]")
_GENERIC_TERMS = frozenset({
    "查找", "要求", "项目", "投标", "招标", "文件", "时间", "标准", "相关", "其他",
})


@dataclass(frozen=True)
class DetailedTagRule:
    code: str
    domain: str
    terms: tuple[str, ...]


def _normalise_terms(tag_name: str | None, extraction_prompt: str | None) -> tuple[str, ...]:
    candidates = [tag_name or ""] + _QUOTED_TERM.findall(extraction_prompt or "")
    terms: list[str] = []
    for value in candidates:
        term = re.sub(r"\s+", "", value)
        if len(term) < 2 or term in _GENERIC_TERMS or term in terms:
            continue
        terms.append(term)
    return tuple(terms)


class NodeLabelPolicy:
    """Stable domain labels plus optional detailed matches from ``bid_tag_dict``."""

    def __init__(self, detailed_rules: Iterable[DetailedTagRule] = ()) -> None:
        self._domain_rules = {
            domain: re.compile(pattern) for domain, pattern in _BASELINE_DOMAIN_RULES.items()
        }
        self._detailed_rules = tuple(detailed_rules)
        self.version = self._fingerprint()
        self.source = "bid_tag_dict+baseline" if self._detailed_rules else "builtin-baseline"

    @classmethod
    def from_tag_rows(cls, rows: Iterable[Any]) -> NodeLabelPolicy:
        rules: list[DetailedTagRule] = []
        for row in rows:
            domain = _DOMAIN_CATEGORY_MAP.get(getattr(row, "category_code", None) or "")
            if not domain:
                continue
            terms = _normalise_terms(
                getattr(row, "tag_name", None), getattr(row, "extraction_prompt", None)
            )
            if terms:
                rules.append(DetailedTagRule(str(row.tag_code), domain, terms))
        return cls(rules)

    @classmethod
    def from_session(cls, session: Session) -> NodeLabelPolicy:
        """Load active business tags, falling back without blocking document parsing."""
        try:
            rows = (
                session.query(BidTagDict)
                .filter(BidTagDict.is_active.is_(True))
                .order_by(BidTagDict.tag_code)
                .all()
            )
        except SQLAlchemyError:
            return cls()
        return cls.from_tag_rows(rows)

    def label(self, chunk: dict[str, Any]) -> dict[str, Any]:
        content = str(chunk.get("chunk_text") or "")
        node_type = str(chunk.get("chunk_type", "paragraph")).lower()
        section_path = str(chunk.get("section_path") or "")

        def matched_domains_and_tags(text: str) -> tuple[set[str], list[str]]:
            domains = {
                name for name, pattern in self._domain_rules.items() if pattern.search(text)
            }
            matched_tag_codes: list[str] = []
            for rule in self._detailed_rules:
                if any(term in text for term in rule.terms):
                    domains.add(rule.domain)
                    matched_tag_codes.append(rule.code)
            return domains, matched_tag_codes

        # A heading can mention every chapter category ("技术、商务、资格"), while
        # the clause itself is usually specific.  Content must therefore drive
        # the route; section labels are only a fallback for terse statements
        # such as "投标人必须满足上述要求".
        domains, matched_tag_codes = matched_domains_and_tags(content)
        # "联合体" itself is not a qualification proof.  A clause such as
        # "联合体各方不得重复投标" is a bidding-conduct restriction and must
        # not be sent to enterprise-material matching.  When the text also
        # contains concrete qualification terms, the baseline rule above keeps
        # it in QUALIFICATION instead.
        if "联合体" in content and "QUALIFICATION" not in domains:
            domains.add("BUSINESS")
        if not domains and not matched_tag_codes:
            domains, matched_tag_codes = matched_domains_and_tags(section_path)

        eligibility_requirement = bool(
            _BIDDER_ELIGIBILITY_NEGATION.search(content)
            or _QUALIFICATION_LIST_REQUIREMENT.search(content)
        )
        mandatory = bool(_OBLIGATION.search(content)) or eligibility_requirement
        blocking = bool(_BLOCKING.search(content))
        quantitative = bool(_QUANTITATIVE.search(content))
        analysis_scope = self._analysis_scope(content)
        structural = node_type in {"section", "image"}
        noise = bool(_NOISE.match(content.strip()))
        candidate = (
            not structural
            and not noise
            and analysis_scope in {"BIDDER_REQUIREMENT", "SCORING_CRITERIA"}
            and bool(domains)
            and (mandatory or quantitative)
        )
        return {
            "node_kind": node_type,
            "domains": sorted(domains),
            "matched_tag_codes": matched_tag_codes,
            "mandatory_signal": mandatory,
            "blocking_signal": blocking,
            "quantitative_signal": quantitative,
            "analysis_scope": analysis_scope,
            "noise": noise,
            "requirement_candidate": candidate,
            "policy_version": self.version,
            "policy_source": self.source,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "format": _POLICY_FORMAT_VERSION,
            "version": self.version,
            "source": self.source,
            "detailed_tag_rule_count": len(self._detailed_rules),
        }

    def _fingerprint(self) -> str:
        payload = {
            "format": _POLICY_FORMAT_VERSION,
            "baseline": _BASELINE_DOMAIN_RULES,
            "signals": {
                "obligation": _OBLIGATION.pattern,
                "blocking": _BLOCKING.pattern,
                "quantitative": _QUANTITATIVE.pattern,
                "noise": _NOISE.pattern,
                "bidder_subject": _BIDDER_SUBJECT.pattern,
                "bidder_obligation": _BIDDER_OBLIGATION.pattern,
                "bidder_disqualification": _BIDDER_DISQUALIFICATION.pattern,
                "bidder_eligibility_negation": _BIDDER_ELIGIBILITY_NEGATION.pattern,
                "qualification_list_requirement": _QUALIFICATION_LIST_REQUIREMENT.pattern,
                "bidder_implied_action": _BIDDER_IMPLIED_ACTION.pattern,
                "non_bidder_subject": _NON_BIDDER_SUBJECT.pattern,
                "process_only": _PROCESS_ONLY.pattern,
                "scoring_criteria": _SCORING_CRITERIA.pattern,
            },
            "rules": [
                {"code": rule.code, "domain": rule.domain, "terms": rule.terms}
                for rule in self._detailed_rules
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _analysis_scope(content: str) -> str:
        """Classify who must act before treating a clause as matchable input.

        Tender documents frequently embed standard contract text and rules for
        the purchaser or evaluation committee.  Those statements are useful
        context but are not evidence that the bidding enterprise must possess
        a material.  Keep only bidder-facing requirements and actual scoring
        criteria in the Requirement extraction path.
        """
        bidder_obligation = bool(_BIDDER_OBLIGATION.search(content))
        # "重新招标后投标人少于三家" and similar process outcomes contain a
        # bidder reference plus a rejection word, sometimes followed by an
        # authority-facing "应当".  They are never bidder-material duties.
        if _PROCESS_ONLY.search(content):
            return "NON_BIDDER_PROCESS"
        bidder_must_act = (
            bidder_obligation
            or bool(_BIDDER_DISQUALIFICATION.search(content))
            or bool(_BIDDER_ELIGIBILITY_NEGATION.search(content))
            or bool(_QUALIFICATION_LIST_REQUIREMENT.search(content))
        )
        if (
            (_NON_BIDDER_SUBJECT.search(content) or _PROCESS_ONLY.search(content))
            and not bidder_must_act
        ):
            return "NON_BIDDER_PROCESS"
        if bidder_must_act:
            return "BIDDER_REQUIREMENT"
        if _SCORING_CRITERIA.search(content):
            return "SCORING_CRITERIA"
        if _BIDDER_IMPLIED_ACTION.search(content):
            return "BIDDER_REQUIREMENT"
        return "UNSCOPED"
