"""Shared, deterministic quality checks for text emitted by document parsers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

GARBLED_RATIO_THRESHOLD = 0.35
MAX_INDEXABLE_CHUNK_CHARS = 1_200
# 工程、金额、表格文本中的常用符号不应被误当成解析乱码；真正影响中文
# 语义的替换字符、控制字符和异常文字体系仍会被严格拦截。
_ALLOWED_PUNCTUATION = frozenset(
    "，。；：、（）()【】[]《》〈〉“”‘’—–-+/%.:;,_!！?？#&=|~<>≤≥×√℃㎡"
)
_CONTENTS_TITLE = re.compile(r"^\s*(?:目\s*录|contents?)\b", re.IGNORECASE)
_DOT_LEADER = re.compile(r"(?:[.．·…]{4,}|_{4,})")


@dataclass(frozen=True, slots=True)
class TextQuality:
    visible_characters: int
    garbled_characters: int
    unexpected_script_characters: int

    @property
    def garbled_ratio(self) -> float:
        if not self.visible_characters:
            return 1.0
        return self.garbled_characters / self.visible_characters

    @property
    def is_garbled(self) -> bool:
        return self.garbled_ratio > GARBLED_RATIO_THRESHOLD


def _is_expected_letter_or_digit(character: str) -> bool:
    return (
        character.isascii() and character.isalnum()
    ) or "一" <= character <= "鿿"


def _is_unexpected_script(character: str) -> bool:
    """Chinese tender text may include ASCII IDs, but not a dominant foreign script.

    Common PDF mojibake turns UTF-8 bytes into Greek/Cyrillic-like letters.
    ``str.isalnum`` considers those valid, so a generic control-character-only
    check misses exactly the corruption that harms Chinese tag matching.
    """
    return unicodedata.category(character).startswith("L") and not _is_expected_letter_or_digit(
        character
    )


def assess_text_quality(content: str) -> TextQuality:
    visible = [character for character in content if not character.isspace()]
    unexpected_script = sum(_is_unexpected_script(character) for character in visible)
    garbled = sum(
        character == "�"
        or (unicodedata.category(character).startswith("C") and character not in {"\n", "\t"})
        or (
            not _is_expected_letter_or_digit(character)
            and character not in _ALLOWED_PUNCTUATION
            and not character.isdigit()
        )
        or _is_unexpected_script(character)
        for character in visible
    )
    return TextQuality(
        visible_characters=len(visible),
        garbled_characters=garbled,
        unexpected_script_characters=unexpected_script,
    )


def garbled_character_count(characters: list[str]) -> int:
    """Compatibility helper for callers that already split visible characters."""
    return assess_text_quality("".join(characters)).garbled_characters


def indexability_gate(content: str, quality: TextQuality | None = None) -> str | None:
    """Return why a parser node must not enter indexing, tags or an LLM.

    This is a quality gate, not a relevance classifier: a contents page or an
    unresolved 10k-character fallback block cannot yield trustworthy tender
    evidence even if it happens to contain requirement keywords.
    """
    text = content.strip()
    effective_quality = quality or assess_text_quality(text)
    if effective_quality.is_garbled:
        return "GARBLED_TEXT"
    if _CONTENTS_TITLE.match(text):
        return "CONTENTS_PAGE"
    dot_leader_characters = sum(
        len(match.group(0)) for match in _DOT_LEADER.finditer(text)
    )
    if dot_leader_characters >= 60 and dot_leader_characters / max(len(text), 1) >= 0.12:
        return "CONTENTS_PAGE"
    if effective_quality.visible_characters > MAX_INDEXABLE_CHUNK_CHARS:
        return "OVERSIZED_CHUNK"
    return None
