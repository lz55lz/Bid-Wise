"""Multi-Tier adaptive chunking - port from WeKnora internal/infrastructure/chunker/.

策略链：
1. Heading Tier: Markdown 标题感知分块
2. Heuristic Tier: 启发式边界驱动分块
3. Legacy Tier: WeKnora TextSplitter（保留分隔符）

自适应选择：auto 策略通过文档画像（DocProfile）选择最优 tier 链。
"""

import re
import statistics
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# WeKnora 标准分块参数
DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 80

# WeKnora TextSplitter（保留分隔符的分块实现）
_weknora_splitter_path = Path(__file__).resolve().parents[2] / ".." / ".." / "WeKnora"
if _weknora_splitter_path.exists() and str(_weknora_splitter_path) not in sys.path:
    sys.path.insert(0, str(_weknora_splitter_path))

try:
    from docreader.splitter.splitter import TextSplitter as WeKnoraTextSplitter
except ImportError:
    WeKnoraTextSplitter = None


class StrategyTier(StrEnum):
    HEADING = "heading"
    HEURISTIC = "heuristic"
    LEGACY = "legacy"


# --- 文档画像 ---
@dataclass
class DocProfile:
    total_chars: int = 0
    total_lines: int = 0
    avg_line_len: float = 0.0
    std_line_len: float = 0.0
    md_heading_counts: dict[int, int] = field(default_factory=dict)
    md_heading_total: int = 0
    numbered_section_count: int = 0
    all_caps_short_line_count: int = 0
    blank_paragraph_breaks: int = 0
    form_feed_count: int = 0
    visual_sep_count: int = 0
    german_chapter_count: int = 0
    english_chapter_count: int = 0
    chinese_chapter_count: int = 0
    repeated_footer_count: int = 0
    has_tables: bool = False
    has_code: bool = False
    code_ratio: float = 0.0
    detected_langs: list[str] = field(default_factory=list)

    def heading_density(self) -> float:
        if self.total_lines == 0:
            return 0.0
        return self.md_heading_total / self.total_lines

    def dominant_heading_level(self) -> int:
        if self.md_heading_total == 0:
            return 0
        for level in range(1, 7):
            if self.md_heading_counts.get(level, 0) >= 3:
                return level
        for level in range(6, 0, -1):
            if self.md_heading_counts.get(level, 0) > 0:
                return level
        return 0

    def heuristic_marker_total(self) -> int:
        return (
            self.numbered_section_count
            + self.german_chapter_count
            + self.english_chapter_count
            + self.chinese_chapter_count
            + self.all_caps_short_line_count
            + self.visual_sep_count
            + self.form_feed_count
        )


# --- 正则模式 ---
_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_NUMBERED_SECTION_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百千零\d]+[章节篇部分条款项]|"
    r"\d+\.\d+(?:\.\d+)*|"
    r"[A-Z]\.\d+(?:\.\d+)*|"
    r"\(\d+\)\s+\S|"
    r"\d+\s+[A-Z][a-z])"
)
_GERMAN_CHAPTER_RE = re.compile(r"^(?:Kapitel|Abschnitt|Teil)\s+\d+", re.I)
_ENGLISH_CHAPTER_RE = re.compile(r"^(?:Chapter|Section|Article|Part)\s+\d+", re.I)
_CHINESE_CHAPTER_RE = re.compile(r"^(?:第[一二三四五六七八九十]+[章篇节条])")
_ALL_CAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9\s]{5,30}$")
_VISUAL_SEP_RE = re.compile(r"^[_\-=]{3,}$")
_PAGE_FOOTER_RE = re.compile(r"^\s*[-−–—=·]+\s*\d+\s*[-−–—=·]+\s*$")
_EXCESSIVE_BLANKS_RE = re.compile(r"\n{3,}")
_FORM_FEED_RE = re.compile(r"\f")


# --- 画像计算 ---
def profile_document(text: str) -> DocProfile:
    if not text:
        return DocProfile()

    runes = list(text)
    profile = DocProfile(md_heading_counts={level: 0 for level in range(1, 7)})
    profile.total_chars = len(runes)
    profile.form_feed_count = text.count("\f")
    lines = text.split("\n")
    profile.total_lines = len(lines)

    lengths: list[float] = []
    in_fence = False
    code_chars = 0

    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
            profile.has_code = True
            continue
        if in_fence:
            code_chars += len(trimmed)
            continue

        rune_len = len(trimmed)
        lengths.append(float(rune_len))

        m = _MARKDOWN_HEADING_RE.match(trimmed)
        if m:
            level = len(m.group(1))
            profile.md_heading_counts[level] = profile.md_heading_counts.get(level, 0) + 1
            profile.md_heading_total += 1
        elif _NUMBERED_SECTION_RE.match(trimmed):
            profile.numbered_section_count += 1
            profile.md_heading_total += 1
            profile.md_heading_counts[2] = profile.md_heading_counts.get(2, 0) + 1
        elif _GERMAN_CHAPTER_RE.match(trimmed):
            profile.german_chapter_count += 1
            profile.md_heading_total += 1
            profile.md_heading_counts[2] = profile.md_heading_counts.get(2, 0) + 1
        elif _ENGLISH_CHAPTER_RE.match(trimmed):
            profile.english_chapter_count += 1
            profile.md_heading_total += 1
            profile.md_heading_counts[2] = profile.md_heading_counts.get(2, 0) + 1
        elif _CHINESE_CHAPTER_RE.match(trimmed):
            profile.chinese_chapter_count += 1
            profile.md_heading_total += 1
            profile.md_heading_counts[2] = profile.md_heading_counts.get(2, 0) + 1
        elif _ALL_CAPS_HEADING_RE.match(trimmed):
            profile.all_caps_short_line_count += 1
            profile.md_heading_total += 1
            profile.md_heading_counts[2] = profile.md_heading_counts.get(2, 0) + 1
        elif _VISUAL_SEP_RE.match(trimmed):
            profile.visual_sep_count += 1
        elif _PAGE_FOOTER_RE.match(trimmed):
            profile.repeated_footer_count += 1
        elif trimmed.startswith("|") and trimmed.endswith("|"):
            profile.has_tables = True

    if lengths:
        profile.avg_line_len = statistics.mean(lengths)
        if len(lengths) > 1:
            profile.std_line_len = statistics.stdev(lengths)

    if profile.total_chars > 0:
        profile.code_ratio = code_chars / profile.total_chars

    return profile


# --- 策略选择 ---
def select_strategy(profile: DocProfile) -> list[StrategyTier]:
    dom_level = profile.dominant_heading_level()
    if dom_level > 0 and profile.heading_density() >= 0.02:
        return [StrategyTier.HEADING, StrategyTier.LEGACY]
    if profile.heuristic_marker_total() >= 3:
        return [StrategyTier.HEURISTIC, StrategyTier.LEGACY]
    return [StrategyTier.LEGACY]




# --- Chunk 数据结构 ---
@dataclass
class Chunk:
    content: str
    context_header: str = ""
    seq: int = 0
    start: int = 0
    end: int = 0
    # 父子块体系：child chunk 指向所属 parent 的下标，-1 表示无独立父块
    parent_index: int = -1

    def embedding_content(self) -> str:
        body = self.content.strip()
        if not self.context_header:
            return body
        return f"{self.context_header}\n\n{body}"


# ==================== Tier 1: 标题分块 ====================
class HeadingHierarchy:
    def __init__(self) -> None:
        self._stack: list[str] = []

    def observe(self, line: str) -> None:
        stripped = line.strip()
        m = _MARKDOWN_HEADING_RE.match(stripped)
        if m:
            level = len(m.group(1))
            content = m.group(2).strip()
            while len(self._stack) >= level:
                self._stack.pop()
            self._stack.append(content)
            return
        # 中文章节号、英文章节号等作为 level=2 heading 入栈
        for pattern in (
            _CHINESE_CHAPTER_RE, _ENGLISH_CHAPTER_RE,
            _GERMAN_CHAPTER_RE, _NUMBERED_SECTION_RE,
        ):
            if pattern.match(stripped):
                while len(self._stack) >= 2:
                    self._stack.pop()
                self._stack.append(stripped)
                return

    def breadcrumb_with_hashes(self) -> str:
        if not self._stack:
            return ""
        return "#" * len(self._stack) + " " + self._stack[-1]


def _find_heading_boundaries(text: str, primary_level: int) -> list[tuple[int, str]]:
    bounds = [(0, "")]
    pos = 0
    in_fence = False
    for line in text.split("\n"):
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
            pos += len(line) + 1
            continue
        if not in_fence:
            m = _MARKDOWN_HEADING_RE.match(trimmed)
            if m:
                level = len(m.group(1))
                if 1 <= level <= primary_level:
                    if pos > 0:
                        bounds.append((pos, line))
                    else:
                        bounds[0] = (0, line)
            elif (
                _CHINESE_CHAPTER_RE.match(trimmed) or _ENGLISH_CHAPTER_RE.match(trimmed)
                or _GERMAN_CHAPTER_RE.match(trimmed) or _NUMBERED_SECTION_RE.match(trimmed)
            ):
                if pos > 0:
                    bounds.append((pos, line))
                else:
                    bounds[0] = (0, line)
        pos += len(line) + 1
    return bounds


def split_by_headings(text: str, chunk_size: int) -> list[Chunk]:
    if not text:
        return []
    profile = profile_document(text)
    primary_level = profile.dominant_heading_level()
    if primary_level == 0:
        return split_by_legacy(text, chunk_size)

    bounds = _find_heading_boundaries(text, primary_level)
    if len(bounds) <= 1:
        return split_by_legacy(text, chunk_size)

    runes = list(text)
    hierarchy = HeadingHierarchy()
    out: list[Chunk] = []
    seq = 0
    pending: tuple[int, int, str] | None = None  # (rune_start, end_rune, breadcrumb)

    for i, (rune_start, heading_line) in enumerate(bounds):
        end_rune = len(runes)
        if i + 1 < len(bounds):
            end_rune = bounds[i + 1][0]

        if heading_line:
            hierarchy.observe(heading_line)

        breadcrumb = hierarchy.breadcrumb_with_hashes()
        section_runes = runes[rune_start:end_rune]
        section_content = "".join(section_runes)
        sec_len = len(section_runes)
        if sec_len == 0:
            continue

        # 过滤目录页：breadcrumb 内容含"目录"
        breadcrumb_text = breadcrumb.lstrip("#").strip()
        if "目录" in breadcrumb_text:
            continue

        # 过滤碎片 section：内容太短（< 30字符）直接跳过
        if sec_len < 30:
            continue

        # 密集 heading 场景（每段 < 150 char）：先暂存，与下一段合并后再输出
        if sec_len < 150:
            if pending is None:
                pending = (rune_start, end_rune, breadcrumb)
            else:
                # 合并到 pending
                prev_start, prev_end, prev_breadcrumb = pending
                merged_content = "".join(runes[prev_start:end_rune])
                merged_len = len(merged_content)
                if merged_len < 150:
                    # 继续累积
                    pending = (prev_start, end_rune, prev_breadcrumb)
                    continue
                # 达到阈值，输出合并后的 chunk
                out.append(Chunk(
                    content=merged_content,
                    context_header=prev_breadcrumb,
                    seq=seq,
                    start=prev_start,
                    end=end_rune,
                ))
                seq += 1
                pending = None
            continue

        # 常规大小 section：先输出 pending 合并块
        if pending is not None:
            prev_start, prev_end, prev_breadcrumb = pending
            merged_content = "".join(runes[prev_start:end_rune])
            out.append(Chunk(
                content=merged_content,
                context_header=prev_breadcrumb,
                seq=seq,
                start=prev_start,
                end=end_rune,
            ))
            seq += 1
            pending = None

        if sec_len + len(breadcrumb) + 2 <= chunk_size:
            out.append(Chunk(
                content=section_content,
                context_header=breadcrumb,
                seq=seq,
                start=rune_start,
                end=end_rune,
            ))
            seq += 1
        else:
            sub_chunks = split_by_legacy(section_content, chunk_size)
            for sub_idx, sub in enumerate(sub_chunks):
                if sub_idx == 0 and len(sub.content) < 80 and len(sub_chunks) > 1:
                    continue
                out.append(Chunk(
                    content=sub.content,
                    context_header=breadcrumb,
                    seq=seq,
                    start=rune_start + sub.start,
                    end=rune_start + sub.end,
                ))
                seq += 1

    # 输出最后的 pending 块
    if pending is not None:
        prev_start, prev_end, prev_breadcrumb = pending
        merged_content = "".join(runes[prev_start:len(runes)])
        out.append(Chunk(
            content=merged_content,
            context_header=prev_breadcrumb,
            seq=seq,
            start=prev_start,
            end=len(runes),
        ))
        seq += 1

    out = _coalesce_tiny_chunks(out, chunk_size)
    for i, c in enumerate(out):
        c.seq = i
    return out


def _coalesce_tiny_chunks(chunks: list[Chunk], chunk_size: int) -> list[Chunk]:
    if len(chunks) <= 1 or chunk_size <= 0:
        return chunks
    target = max(200, chunk_size // 2)
    result: list[Chunk] = []
    cur = chunks[0]
    cur_len = len(cur.content)

    for i in range(1, len(chunks)):
        nxt = chunks[i]
        nxt_len = len(nxt.content)
        shared = _common_heading_prefix(cur.context_header, nxt.context_header)
        if shared and cur.end == nxt.start and cur_len < target and cur_len + nxt_len <= chunk_size:
            cur.content += nxt.content
            cur.context_header = shared
            cur.end = nxt.end
            cur_len += nxt_len
        else:
            result.append(cur)
            cur = nxt
            cur_len = nxt_len
    result.append(cur)
    return result


def _common_heading_prefix(a: str, b: str) -> str:
    if a == b:
        return a
    la = a.split("\n")
    lb = b.split("\n")
    n = min(len(la), len(lb))
    for i in range(n):
        if la[i] != lb[i]:
            return "\n".join(la[:i]) if i > 0 else ""
    return a


# ==================== Tier 2: 启发式分块 ====================
class Priority:
    FORM_FEED = 50
    CHAPTER_MARKER = 40
    NUMBERED_HEAD = 35
    ALL_CAPS_HEADING = 30
    VISUAL_SEP = 25
    PAGE_FOOTER = 20
    BLANK_BLOCK = 10


@dataclass
class Boundary:
    rune_start: int
    priority: int


def split_by_heuristics(text: str, chunk_size: int) -> list[Chunk]:
    if not text:
        return []
    runes = list(text)
    total = len(runes)
    if total <= chunk_size:
        return split_by_legacy(text, chunk_size)

    bounds = _find_heuristic_boundaries(text)
    if not bounds:
        return split_by_legacy(text, chunk_size)

    bounds.sort(key=lambda b: (b.rune_start, -b.priority))
    deduped: list[Boundary] = []
    prev = -1
    for b in bounds:
        if b.rune_start != prev:
            deduped.append(b)
            prev = b.rune_start
    bounds = deduped

    bounds.append(Boundary(rune_start=total, priority=0))
    if bounds[0].rune_start != 0:
        bounds.insert(0, Boundary(rune_start=0, priority=0))

    out: list[Chunk] = []
    seq = 0
    chunk_start = bounds[0].rune_start
    cur_end = chunk_start
    min_chunk = max(50, chunk_size // 4)

    i = 1
    while i < len(bounds):
        next_end = bounds[i].rune_start
        block_len = next_end - cur_end

        if block_len > chunk_size:
            if cur_end - chunk_start > 0:
                out = _append_chunk(out, runes, chunk_start, cur_end, seq)
                seq = len(out)
                chunk_start = cur_end
            out = _append_oversize_block(out, runes, cur_end, next_end, chunk_size, seq)
            seq = len(out) + 1 if out else 0
            cur_end = next_end
            chunk_start = next_end
        else:
            accumulated = next_end - chunk_start
            if accumulated > chunk_size and cur_end - chunk_start >= min_chunk:
                out = _append_chunk(out, runes, chunk_start, cur_end, seq)
                seq = len(out) + 1 if out else 0
                chunk_start = _apply_overlap_aligned(runes, cur_end, 80, bounds)
            cur_end = next_end
        i += 1

    if cur_end > chunk_start:
        out = _append_chunk(out, runes, chunk_start, cur_end, len(out))

    for i, c in enumerate(out):
        c.seq = i
    return out


def _find_heuristic_boundaries(text: str) -> list[Boundary]:
    bounds: list[Boundary] = []
    for m in _FORM_FEED_RE.finditer(text):
        bounds.append(Boundary(rune_start=m.start(), priority=Priority.FORM_FEED))

    pos = 0
    in_fence = False
    for line in text.split("\n"):
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            if _NUMBERED_SECTION_RE.match(trimmed):
                bounds.append(Boundary(rune_start=pos, priority=Priority.NUMBERED_HEAD))
            elif _ENGLISH_CHAPTER_RE.match(trimmed) or _GERMAN_CHAPTER_RE.match(trimmed):
                bounds.append(Boundary(rune_start=pos, priority=Priority.CHAPTER_MARKER))
            elif _CHINESE_CHAPTER_RE.match(trimmed):
                bounds.append(Boundary(rune_start=pos, priority=Priority.CHAPTER_MARKER))
            elif _ALL_CAPS_HEADING_RE.match(trimmed):
                bounds.append(Boundary(rune_start=pos, priority=Priority.ALL_CAPS_HEADING))
            elif _VISUAL_SEP_RE.match(trimmed):
                bounds.append(Boundary(rune_start=pos, priority=Priority.VISUAL_SEP))
            elif _PAGE_FOOTER_RE.match(trimmed):
                bounds.append(Boundary(rune_start=pos, priority=Priority.PAGE_FOOTER))
        pos += len(line) + 1

    for m in _EXCESSIVE_BLANKS_RE.finditer(text):
        rune_start = len(list(text[:m.start()]))
        bounds.append(Boundary(rune_start=rune_start, priority=Priority.BLANK_BLOCK))

    return bounds


def _append_chunk(
    out: list[Chunk], runes: list[str], start: int, end: int, seq: int
) -> list[Chunk]:
    if end <= start:
        return out
    raw = "".join(runes[start:end])
    if not raw.strip():
        return out
    out.append(Chunk(content=raw, seq=seq, start=start, end=end))
    return out


def _append_oversize_block(
    out: list[Chunk], runes: list[str], start: int, end: int,
    chunk_size: int, seq: int,
) -> list[Chunk]:
    if end <= start:
        return out
    sub_text = "".join(runes[start:end])
    subs = split_by_legacy(sub_text, chunk_size)
    for s in subs:
        out.append(Chunk(content=s.content, seq=seq, start=start + s.start, end=start + s.end))
        seq += 1
    return out


def _apply_overlap_aligned(
    runes: list[str], cur_end: int, overlap: int, bounds: list[Boundary],
) -> int:
    if overlap <= 0:
        return cur_end
    target = max(0, cur_end - overlap)
    window_start = max(0, cur_end - 2 * overlap)
    best = -1
    for b in bounds:
        if window_start <= b.rune_start < cur_end and b.rune_start > best:
            best = b.rune_start
    if best >= 0:
        return best
    for i in range(target, max(window_start, 0), -1):
        if i < len(runes) and runes[i] == "\n":
            return i + 1
    return target


# ==================== Tier 3: Legacy（WeKnora TextSplitter）====================
def split_by_legacy(
    text: str, chunk_size: int, overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """WeKnora TextSplitter，保留分隔符的分块实现。"""
    if not text:
        return []

    if WeKnoraTextSplitter is None:
        # Fallback：递归分隔符（无分隔符保留，仅保底用）
        return _split_by_legacy_fallback(text, chunk_size, overlap)

    splitter = WeKnoraTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", " "],
    )
    raw_chunks = splitter.split_text(text)
    return [
        Chunk(content=content, seq=seq, start=start, end=end)
        for seq, (start, end, content) in enumerate(raw_chunks)
    ]


def _split_by_legacy_fallback(
    text: str, chunk_size: int, overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """简单滑动窗口 fallback（WeKnora TextSplitter 不可用时）。"""
    if not text:
        return []
    total = len(text)
    chunks: list[Chunk] = []
    pos = 0
    seq = 0
    while pos < total:
        end = min(pos + chunk_size, total)
        chunks.append(Chunk(content=text[pos:end], seq=seq, start=pos, end=end))
        seq += 1
        pos += chunk_size - overlap
        if pos >= total:
            break
        if pos <= chunks[-1].start:
            pos = chunks[-1].start + 1
    for i, c in enumerate(chunks):
        c.seq = i
    return chunks


# ==================== 验证 ====================
@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""


def validate_chunks(chunks: list[Chunk], total_chars: int, chunk_size: int) -> ValidationResult:
    if not chunks:
        return ValidationResult(ok=False, reason="no chunks produced")
    if len(chunks) > total_chars / 50:
        return ValidationResult(ok=False, reason="too many tiny chunks")
    # 过滤掉 < 50 字符的碎片后检查碎片率
    valid = [c for c in chunks if len(c.content) >= 50]
    tiny = [c for c in chunks if len(c.content) < 50]
    if len(tiny) > 0:
        chunks = valid  # 用过滤后的有效 chunk
        if not chunks:
            return ValidationResult(ok=False, reason="all chunks < 50 chars")
    too_small = sum(1 for c in chunks if len(c.content) < 80)
    if too_small / len(chunks) > 0.3:
        return ValidationResult(ok=False, reason="too many tiny chunks")
    return ValidationResult(ok=True)


# ==================== 主入口 ====================
def split_text(
    text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, strategy: str = "auto",
) -> list[Chunk]:
    """自适应文本分块主入口。"""
    if not text:
        return []

    if strategy == "legacy" or strategy == "":
        return split_by_legacy(text, chunk_size)

    if strategy == "heading":
        chain = [StrategyTier.HEADING, StrategyTier.LEGACY]
    elif strategy == "heuristic":
        chain = [StrategyTier.HEURISTIC, StrategyTier.LEGACY]
    else:
        # auto → 根据文档画像选择最优 tier 链（WeKnora 方案）
        chain = select_strategy(profile_document(text))

    total_chars = len(text)
    last_out: list[Chunk] = []

    for tier in chain:
        if tier == StrategyTier.HEADING:
            out = split_by_headings(text, chunk_size)
        elif tier == StrategyTier.HEURISTIC:
            out = split_by_heuristics(text, chunk_size)
        else:
            out = split_by_legacy(text, chunk_size)

        validated = validate_chunks(out, total_chars, chunk_size)
        if validated.ok:
            # 内部过滤了碎片，检查是否真的过滤了
            valid = [c for c in out if len(c.content) >= 50]
            if len(valid) == len(out):
                return out  # 无碎片，直接返回
            # 有碎片但全部有效：过滤后返回
            too_small = sum(1 for c in valid if len(c.content) < 80)
            if not too_small or too_small / len(valid) <= 0.3:
                return valid
            # 碎片率仍高，fallback 到下一 tier
        last_out = out

    return last_out if last_out else split_by_legacy(text, chunk_size)


# ==================== 父子块（WeKnora SplitParentChild）====================
# 检索用 child（精准命中），LLM 上下文用 parent（语义完整）
DEFAULT_PARENT_CHUNK_SIZE = 2048
DEFAULT_CHILD_CHUNK_SIZE = 384


def split_parent_child(
    text: str,
    parent_size: int = DEFAULT_PARENT_CHUNK_SIZE,
    child_size: int = DEFAULT_CHILD_CHUNK_SIZE,
    strategy: str = "auto",
) -> tuple[list[Chunk], list[Chunk]]:
    """父子块切块：先按 parent_size 切父块，再把每个父块细分为 child_size 子块。

    Returns:
        (parents, children)。children[i].parent_index >= 0 时指向 parents 下标；
        父块内容与子块完全一致（单段无需再分）时不创建独立父块，parent_index=-1。
        child 的 start/end 已换算为整篇文本的 rune 偏移。
    """
    if not text:
        return [], []

    parents_raw = split_text(text, chunk_size=parent_size, strategy=strategy)
    if not parents_raw:
        return [], []

    parents: list[Chunk] = []
    children: list[Chunk] = []
    for parent in parents_raw:
        subs = split_text(
            parent.content, chunk_size=child_size, strategy=strategy
        )
        # 父块无需再分（内容与子块一致）时不建独立父块
        if len(subs) > 1 or (len(subs) == 1 and subs[0].content != parent.content):
            parent_index = len(parents)
            parents.append(parent)
        else:
            parent_index = -1
        for sub in subs:
            sub.start += parent.start
            sub.end += parent.start
            sub.context_header = _merge_breadcrumbs(
                parent.context_header, sub.context_header
            )
            sub.parent_index = parent_index
            children.append(sub)

    for i, c in enumerate(children):
        c.seq = i
    return parents, children


def _merge_breadcrumbs(parent: str, child: str) -> str:
    """合并父子面包屑：child 的首行通常重复 parent 的末行，去掉避免冗余。"""
    if not parent:
        return child
    if not child:
        return parent
    parent_lines = parent.split("\n")
    child_lines = child.split("\n")
    if parent_lines and child_lines and parent_lines[-1].strip() == child_lines[0].strip():
        child_lines = child_lines[1:]
    if not child_lines:
        return parent
    return parent + "\n" + "\n".join(child_lines)
