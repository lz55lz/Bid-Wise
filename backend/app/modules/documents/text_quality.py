"""文档解析文本的确定性质量门控。

这里不判断一段文字是否“重要”，只判断它能否安全进入检索、条款与 LLM。
将该逻辑集中，避免清洗、抽取和索引各自维护一套目录/乱码判断而产生偏差。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

GARBLED_RATIO_THRESHOLD = 0.35
MAX_INDEXABLE_CHUNK_CHARS = 1_200
_ALLOWED_PUNCTUATION = frozenset(
    "，。；：、（）()【】[]《》〈〉“”‘’—–-+/%.:;,_!！?？#&=|~<>≤≥×√℃㎡"
)
_CONTENTS_TITLE = re.compile(r"^\s*(?:目\s*录|contents?)\b", re.IGNORECASE)
_DOT_LEADER = re.compile(r"(?:[.．·…]{4,}|_{4,})")


@dataclass(frozen=True, slots=True)
class TextQuality:
    """文本可见字符和异常字符的统计结果。"""

    visible_characters: int
    garbled_characters: int

    @property
    def garbled_ratio(self) -> float:
        return self.garbled_characters / self.visible_characters if self.visible_characters else 1.0

    @property
    def is_garbled(self) -> bool:
        return self.garbled_ratio > GARBLED_RATIO_THRESHOLD


def _is_expected_letter_or_digit(character: str) -> bool:
    return (character.isascii() and character.isalnum()) or "一" <= character <= "鿿"


def assess_text_quality(content: str) -> TextQuality:
    """识别 PDF 乱码、控制字符和异常文字体系。

    不能仅使用 ``str.isalnum``：乱码常会显示为希腊/西里尔字母，仍会被
    Python 视为字母，却无法支撑中文条款匹配。
    """
    visible = [character for character in content if not character.isspace()]
    garbled = sum(
        character == "�"
        or (unicodedata.category(character).startswith("C") and character not in {"\n", "\t"})
        or (
            not _is_expected_letter_or_digit(character)
            and character not in _ALLOWED_PUNCTUATION
            and not character.isdigit()
        )
        or (
            unicodedata.category(character).startswith("L")
            and not _is_expected_letter_or_digit(character)
        )
        for character in visible
    )
    return TextQuality(visible_characters=len(visible), garbled_characters=garbled)


def indexability_gate(content: str, quality: TextQuality | None = None) -> str | None:
    """返回禁止进入下游的原因；返回 ``None`` 才表示可继续处理。"""
    text = content.strip()
    effective_quality = quality or assess_text_quality(text)
    if effective_quality.is_garbled:
        return "GARBLED_TEXT"
    if _CONTENTS_TITLE.match(text):
        return "CONTENTS_PAGE"
    dot_leader_characters = sum(len(match.group(0)) for match in _DOT_LEADER.finditer(text))
    if dot_leader_characters >= 60 and dot_leader_characters / max(len(text), 1) >= 0.12:
        return "CONTENTS_PAGE"
    if effective_quality.visible_characters > MAX_INDEXABLE_CHUNK_CHARS:
        return "OVERSIZED_CHUNK"
    return None
