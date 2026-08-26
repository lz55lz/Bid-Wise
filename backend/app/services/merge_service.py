"""WeKnora merge 流水线核心逻辑 — 按原样 port，不做优化。

来源：
- searchutil/chunkmerge.go：JoinChunkContent / AppendWithOverlap / AppendWithExactOverlap
- chat_pipeline/merge.go：mergeSequentialChunks / classifyMerge / chunkTrusted
- chat_pipeline/merge.go：groupAndMergeCurrentContent（简化版，无 KnowledgeID 分组）

这是 Lei 的 RAG 8 步合并流水线的第 5/7.5 步。
"""

import logging
from dataclasses import dataclass

from app.db.models import SearchChunk

logger = logging.getLogger(__name__)

# 重叠检测常量（与 WeKnora 保持一致）
_MIN_OVERLAP_RUNES = 12
_DEFAULT_SEARCH_SPAN = 400


# ─── searchutil/chunkmerge.go 工具函数 ────────────────────────────────────────

def _rune_len(s: str) -> int:
    return len(s)


def _join_chunk_content(acc: str, next_chunk: str, separator: str) -> str:
    """Join two chunk bodies without relying on parser offsets.

    Exact containment is collapsed, a real suffix/prefix overlap is removed,
    and otherwise both bodies are retained with separator between them.
    Conservative fallback prefers small duplication over silently dropping content.
    """
    if not acc:
        return next_chunk
    if not next_chunk:
        return acc
    if _contains_chunk_content(acc, next_chunk):
        return acc
    if _contains_chunk_content(next_chunk, acc):
        return next_chunk

    acc_runes = list(acc)
    next_runes = list(next_chunk)
    max_overlap = min(len(acc_runes), len(next_runes))
    # Bound suffix matching to prevent adversarial quadratic behavior
    if max_overlap > _DEFAULT_SEARCH_SPAN:
        max_overlap = _DEFAULT_SEARCH_SPAN
    for overlap in range(max_overlap, _MIN_OVERLAP_RUNES - 1, -1):
        if acc_runes[len(acc_runes) - overlap :] == next_runes[:overlap]:
            return acc + "".join(next_runes[overlap:])
    return acc + separator + next_chunk


def _contains_chunk_content(container: str, contained: str) -> bool:
    """Report whether the complete current body is safely represented by another."""
    if not container or not contained:
        return False
    if container == contained:
        return True
    return len(list(contained)) >= _MIN_OVERLAP_RUNES and contained in container


def _append_with_exact_overlap(
    acc: str, next_chunk: str, position_overlap: int
) -> tuple[str, bool]:
    """Append with exact overlap verified by position coordinates.

    Returns (merged, ok). If ok=False, caller falls back to AppendWithOverlap.
    """
    if not acc:
        return next_chunk, True
    if not next_chunk:
        return acc, True
    if position_overlap < 0:
        return "", False
    if position_overlap == 0:
        return acc + next_chunk, True

    acc_runes = list(acc)
    next_runes = list(next_chunk)
    if position_overlap > len(acc_runes) or position_overlap > len(next_runes):
        return "", False
    if acc_runes[len(acc_runes) - position_overlap :] != next_runes[:position_overlap]:
        return "", False
    return acc + "".join(next_runes[position_overlap:]), True


def _append_with_overlap(acc: str, next_chunk: str, position_overlap: int) -> str:
    """Append next to acc, removing overlap detected by text matching.

    position_overlap is estimated from StartAt/EndAt, used only to bound the
    search window; actual overlap is found by text matching.
    Falls back to plain concat if no text overlap found.
    """
    if not acc:
        return next_chunk
    if not next_chunk:
        return acc

    acc_runes = list(acc)
    next_runes = list(next_chunk)

    span = max(position_overlap, 0)
    max_k = min(len(acc_runes), len(next_runes))
    cap = max(span * 3, _DEFAULT_SEARCH_SPAN)
    if max_k > cap:
        max_k = cap
    # Head slack: max prefix to skip (for synthesized content like table headers)
    head_slack = max(span * 2, 320)

    for k in range(max_k, _MIN_OVERLAP_RUNES - 1, -1):
        needle = acc_runes[len(acc_runes) - k :]
        pos = _index_runes(next_runes, needle, head_slack)
        if pos >= 0:
            return acc + "".join(next_runes[pos + k :])
    return acc + next_chunk


def _index_runes(haystack: list[str], needle: list[str], max_start: int) -> int:
    """Find first occurrence of needle in haystack with rune-level equality.

    max_start caps the search starting position (skips synthesized prefix content).
    Returns -1 if not found.
    """
    if not needle or len(needle) > len(haystack):
        return -1
    limit = len(haystack) - len(needle)
    if max_start < limit:
        limit = max_start
    for i in range(limit + 1):
        match = True
        for j, char in enumerate(needle):
            if haystack[i + j] != char:
                match = False
                break
        if match:
            return i
    return -1


# ─── merge.go mergeSituation 分类 ─────────────────────────────────────────────

MERGE_SEPARATE = "separate"
MERGE_EXTEND = "extend"
MERGE_SUBSUME = "subsume"
MERGE_JOIN_DISTINCT = "join_distinct"
MERGE_JOIN_TEXT = "join_text"


def _chunk_trusted(chunk: "SearchChunk") -> bool:
    """Check if chunk StartAt/EndAt can be trusted for position-based merging.

    Trusted means: unedited (ContentRevision=0), not rewritten, valid range,
    and consistent length (runeLen(Content) == EndAt-StartAt).
    """
    content_len = _rune_len(chunk.content or "")
    start = chunk.start_at or 0
    end = chunk.end_at or 0
    # ContentRevision / ContentRewritten: Lei has no equivalent fields yet,
    # so we trust all chunks that pass the geometric check
    return end > start and content_len == end - start


def _classify_merge(
    last_chunk: "SearchChunk", last_index: int, current: "SearchChunk"
) -> str:
    """Classify how current chunk relates to the group's last chunk in document order."""
    last_start = last_chunk.start_at or 0
    last_end = last_chunk.end_at or 0
    curr_start = current.start_at or 0
    curr_end = current.end_at or 0

    if _chunk_trusted(last_chunk) and _chunk_trusted(current) and curr_start >= last_start:
        if curr_start > last_end:
            return MERGE_SEPARATE
        if curr_end > last_end:
            return MERGE_EXTEND
        if _contains_chunk_content(last_chunk.content or "", current.content or ""):
            return MERGE_SUBSUME
        return MERGE_JOIN_DISTINCT

    text_contained = (
        _contains_chunk_content(last_chunk.content or "", current.content or "")
        or _contains_chunk_content(current.content or "", last_chunk.content or "")
    )
    sequential = current.chunk_index == last_index + 1
    if not text_contained and not sequential:
        return MERGE_SEPARATE
    return MERGE_JOIN_TEXT


# ─── merge.go mergeSequentialChunks 核心逻辑 ───────────────────────────────────

@dataclass
class _MergedGroup:
    """Track a group of chunks being merged sequentially."""
    chunk: "SearchChunk"
    last_index: int


def merge_sequential_chunks(chunks: list["SearchChunk"]) -> list["SearchChunk"]:
    """Join sequential chunks within the same document version.

    - 只在同一 source_document_version_id 内合并：chunk_index 是版本内计数器，
      跨文档/跨版本的同序号块绝不拼接（否则会把无关文档交错成一段）。
    - Trusted pairs (verified StartAt/EndAt + consistent rune length) use
      position-aware merging with exact overlap removal.
    - Untrusted pairs fall back to text-based joining.
    - Contained chunks are subsumed without content duplication.
    - SubChunkID tracking is appended (for traceability, even if not used yet).
    """
    if not chunks:
        return []
    chunks = sorted(
        chunks,
        key=lambda c: (str(c.source_document_version_id), c.chunk_index),
    )

    result: list[SearchChunk] = []
    groups: list[_MergedGroup] = [_MergedGroup(chunks[0], chunks[0].chunk_index)]
    for i in range(1, len(chunks)):
        current = chunks[i]
        last_group = groups[-1]
        last_chunk = last_group.chunk

        if current.source_document_version_id != last_chunk.source_document_version_id:
            groups.append(_MergedGroup(current, current.chunk_index))
            continue

        situation = _classify_merge(last_chunk, last_group.last_index, current)

        if situation == MERGE_SEPARATE:
            groups.append(_MergedGroup(current, current.chunk_index))
            continue

        if situation == MERGE_EXTEND:
            position_overlap = (last_chunk.end_at or 0) - (current.start_at or 0)
            merged, ok = _append_with_exact_overlap(
                last_chunk.content or "", current.content or "", position_overlap
            )
            if ok:
                last_chunk.content = merged
            else:
                last_chunk.content = _append_with_overlap(
                    last_chunk.content or "", current.content or "", position_overlap
                )
            last_chunk.end_at = current.end_at
            # Track SubChunkID (Lei: stored in metadata for now)
            _record_merged_child(last_chunk, current)

        elif situation == MERGE_SUBSUME:
            _record_merged_child(last_chunk, current)

        elif situation == MERGE_JOIN_DISTINCT:
            last_chunk.content = _join_chunk_content(
                last_chunk.content or "", current.content or "", "\n\n"
            )
            _record_merged_child(last_chunk, current)

        elif situation == MERGE_JOIN_TEXT:
            last_chunk.content = _join_chunk_content(
                last_chunk.content or "", current.content or "", "\n\n"
            )
            _record_merged_child(last_chunk, current)

        # Extend group's span, keep highest score (Lei: no score field on chunk)
        if current.chunk_index > last_group.last_index:
            last_group.last_index = current.chunk_index

    for g in groups:
        result.append(g.chunk)
    return result


def _record_merged_child(target: "SearchChunk", source: "SearchChunk") -> None:
    """Append source chunk ID to target's SubChunkID metadata."""
    sub_ids: list[str] = list(target.metadata_.get("sub_chunk_ids") or [])
    source_id = str(source.id)
    if source_id not in sub_ids:
        sub_ids.append(source_id)
        target.metadata_["sub_chunk_ids"] = sub_ids
