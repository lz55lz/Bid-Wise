"""本地 PDF 解析器 - 使用 pypdfium2 进行文本/扫描页面分类和布局重建。

Port from WeKnora docreader/parser/pdf_parser.py

主要功能：
- 页面级分类：text（原生文本页）vs scanned（扫描页）
- 扫描页渲染为 JPEG，标记给 OCR 处理
- 原生文本页进行布局重建（XY-cut 多列排序、标题检测）
- 向量图表区域提取
- 跨页眉脚过滤
"""

import base64
import io
import logging
import os
import re
import statistics
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# pypdfium2 及相关依赖在首次导入时检查
_pdfium_imported = False
_pdfium = None
_pdfium_raw = None


def _ensure_pdfium():
    global _pdfium_imported, _pdfium, _pdfium_raw
    if _pdfium_imported:
        return _pdfium, _pdfium_raw
    try:
        import pypdfium2 as pf
        import pypdfium2.raw as raw
        _pdfium = pf
        _pdfium_raw = raw
        _pdfium_imported = True
    except ImportError:
        raise ImportError("pypdfium2 未安装，请运行: pip install pypdfium2") from None
    return _pdfium, _pdfium_raw


# pdfium C 库在进程级不是线程安全的，所有 pdfium 操作必须串行化
_PDFIUM_LOCK = threading.Lock()

# --- 配置常量 ---
SCAN_IMAGE_AREA_RATIO = 0.5  # 图像区域/页面面积 ≥ 0.5 → 扫描页
SCAN_MIN_CHARS_PER_PAGE = 10  # 字符数少于此值且 image_ratio ≥ 0.1 → 扫描页
_LOW_TEXT_IMAGE_RATIO = 0.1  # 低文本页面的图像占比阈值

# 嵌入图片提取
EXTRACT_EMBEDDED_IMAGES = True
EMBED_MIN_PIXELS = 80
EMBED_MIN_AREA_RATIO = 0.01
EMBED_REPEAT_PAGE_FRAC = 0.5
EMBED_MAX_IMAGES = 50

# 布局重建
LAYOUT_ORDERING = True
FILTER_HIDDEN_TEXT = True
DETECT_HEADINGS = True
MIN_HEADING_LINE_CHARS = 3

# 向量图表渲染
RENDER_VECTOR_FIGURES = True
MIN_CHART_REGION_CHARS = 10
MIN_CHART_REGION_AREA_RATIO = 0.005
MAX_CHART_REGION_AREA_RATIO = 0.65
MAX_FIGURE_HEIGHT_RATIO = 0.35

# 图像渲染
PDF_RENDER_DPI = 150
PDF_JPEG_QUALITY = 85
PDF_RENDER_MAX_EDGE = 2048
PDF_RENDER_PARALLELISM = 4
PDF_RENDER_MAX_WORKERS = 2

# 页面宽度比例阈值（识别页边距/水印列）
MARGIN_COL_WIDTH_RATIO = 0.25
# 单词间隔阈值
WORD_GAP_WIDTH_RATIO = 0.4

# --- 正则表达式 ---
# WeKnora 标准：移除 PDF 占位符（软连字符、零宽字符、BOM、FFFE/FFFF）
_PDF_ARTIFACT_RE = re.compile(r"[­​-‏﻿￾￿]")
_PDF_ARTIFACT_JOIN_RE = re.compile(r"(\w)[­￾](\w)")
_CHART_DEBRIS_LINE_RE = re.compile(
    r"^\s*[-+*|=~▔▁▂▃▄▅▆▇█]+|^\s*\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s*$"
)
_CHART_LAYER_RE = re.compile(r"^\s*[▁▂▃▄▅▆▇█▓▒░]+\s*$")
_ARXIV_LINE_RE = re.compile(r"^\s*arXiv:\d+\.\d+\s*(\[[^\]]+\])?\s*$", re.I)
_PAGE_NUM_LINE_RE = re.compile(r"^\s*[-−–—=·]+\s*\d+\s*[-−–—=·]+\s*$")
_FIGURE_CAPTION_SEARCH_RE = re.compile(r"(?i)\bfig(?:ure)?\s+\d+", re.A)
_FIGURE_CAPTION_RE = re.compile(r"(?i)^(fig(?:ure)?\s+\d+[\s.:]|(表|图表|图)\s*\d+)", re.A)


# --- 数据结构 ---
@dataclass
class PdfPageResult:
    """单个页面的解析结果"""
    page_index: int
    cls: str  # "text" | "scanned"
    text: str
    images: dict[str, str]  # ref_path -> base64


@dataclass
class PdfParseResult:
    """PDF 解析完整结果"""
    nodes: list[dict[str, Any]]
    text: str
    images: dict[str, str]
    metadata: dict[str, Any]


# --- 工具函数 ---
def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None or not str(v).strip():
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or not str(v).strip():
        return default
    try:
        return int(str(v).strip())
    except ValueError:
        return default


def _close_pdfium_resource(obj):
    """安全关闭 pdfium 对象"""
    try:
        if obj is not None:
            close = getattr(obj, "close", None)
            if close is not None:
                close()
    except Exception:
        pass


def _normalize_image_quality(quality: int) -> int:
    return max(30, min(100, quality))


# --- 页面分类 ---
def _page_image_area_ratio(page, raw) -> float:
    """计算页面上图像对象覆盖面积与页面面积之比"""
    page_w, page_h = page.get_size()
    page_area = float(page_w) * float(page_h)
    if page_area <= 0:
        return 0.0

    total_image_area = 0.0
    try:
        for obj in page.get_objects():
            if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
                continue
            try:
                left, bottom, right, top = obj.get_bounds()
            except Exception:
                continue
            total_image_area += abs((right - left) * (top - bottom))
    except Exception:
        return 0.0

    return total_image_area / page_area


def _classify_page(image_area_ratio: float, text_len: int) -> str:
    """根据图像面积比和文本长度分类页面

    规则（来自 WeKnora）：
    1. image_area_ratio ≥ 0.5 → scanned
    2. text_len < 10 且 image_area_ratio ≥ 0.1 → scanned
    3. 其他 → text
    """
    if image_area_ratio >= SCAN_IMAGE_AREA_RATIO:
        return "scanned"
    if text_len < SCAN_MIN_CHARS_PER_PAGE and image_area_ratio >= _LOW_TEXT_IMAGE_RATIO:
        return "scanned"
    return "text"


# --- 文本提取 ---
def _extract_page_text(page) -> str:
    """纯文本提取（从上到下）"""
    textpage = None
    try:
        textpage = page.get_textpage()
        # 直接调用 get_text_bounded()，避免 get_text_range() 默认参数的弃用警告
        return textpage.get_text_bounded()
    finally:
        _close_pdfium_resource(textpage)


def _sanitize_pdf_text(text: str) -> str:
    """移除 PDF 占位符并修复断开的长单词"""
    if not text:
        return text
    text = _PDF_ARTIFACT_RE.sub("", text)
    text = _PDF_ARTIFACT_JOIN_RE.sub(r"\1\2", text)
    return text


def _is_chart_debris_line(line: str) -> bool:
    t = line.strip()
    if not t:
        return False
    if _CHART_DEBRIS_LINE_RE.match(t):
        return True
    if _CHART_LAYER_RE.match(t):
        return True
    # 刻度标签如 "0 1 2 3 4 5 6 0"
    if re.fullmatch(r"[\d\s.()-]+", t) and len(t) <= 24 and sum(c.isdigit() for c in t) >= 3:
        return True
    return False


def _strip_chart_text_debris(text: str) -> str:
    """删除从矢量图渗入文本层的坐标轴/图例线"""
    if not text:
        return text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list = []
    i = 0
    while i < len(lines):
        if _is_chart_debris_line(lines[i]):
            j = i
            while j < len(lines) and (
                _is_chart_debris_line(lines[j]) or not lines[j].strip()
            ):
                j += 1
            if j - i >= 3:
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _strip_arxiv_and_page_num_lines(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list = []
    for ln in lines:
        t = ln.strip()
        if _ARXIV_LINE_RE.match(t):
            continue
        if _PAGE_NUM_LINE_RE.match(t):
            continue
        if "arXiv:" in ln:
            ln = re.sub(r"\s*arXiv:\s*\S+\s*(?:\[[^\]]+\])?\s*[^\n]*", "", ln).strip()
            if not ln:
                continue
        kept.append(ln)
    return "\n".join(kept)


def _strip_lines_above_figure_captions(text: str) -> str:
    """删除紧跟在图表标题上方的图表标签行"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list = []
    for ln in lines:
        if _line_has_figure_caption(ln):
            while out and _is_figure_interior_line(out[-1]):
                out.pop()
            out.append(ln)
        else:
            out.append(ln)
    return "\n".join(out)


def _is_body_paragraph_line(text: str) -> bool:
    t = text.strip()
    if len(t) < 48:
        return False
    return len(t.split()) >= 8


def _is_figure_interior_line(text: str) -> bool:
    """紧贴在图表标题上方、单个字符很短的行（图例、刻度标签）"""
    t = text.strip()
    if not t or _FIGURE_CAPTION_RE.match(t):
        return False
    if _ARXIV_LINE_RE.match(t) or _PAGE_NUM_LINE_RE.match(t):
        return True
    if _is_body_paragraph_line(t):
        return False
    if _is_chart_debris_line(t):
        return True
    if t.endswith((".", "。", "!", "?", "！")) and len(t) >= 15:
        return False
    if len(t.split()) >= 7:
        return False
    if len(t) <= 40:
        return True
    return False


def _line_has_figure_caption(text: str) -> bool:
    return bool(_FIGURE_CAPTION_SEARCH_RE.search((text or "").strip()))


def _postprocess_pdf_text(text: str) -> str:
    text = _sanitize_pdf_text(text)
    text = _strip_arxiv_and_page_num_lines(text)
    text = _strip_lines_above_figure_captions(text)
    text = _strip_chart_text_debris(text)
    return text


# --- 布局感知文本提取 ---
def _chars_bbox(char_list: list) -> tuple:
    return (
        min(c["x0"] for c in char_list),
        min(c["y0"] for c in char_list),
        max(c["x1"] for c in char_list),
        max(c["y1"] for c in char_list),
    )


def _bbox_area_ratio(bbox, page_w: float, page_h: float) -> float:
    page_area = float(page_w) * float(page_h)
    if page_area <= 0:
        return 0.0
    x0, y0, x1, y1 = bbox
    return max(0.0, (x1 - x0) * (y1 - y0) / page_area)


def _char_looks_chart_axis_tick(ch: str) -> bool:
    t = ch.strip()
    if not t:
        return False
    if len(t) == 1 and t in "0123456789.%()-":
        return True
    if _CHART_LAYER_RE.match(t):
        return True
    if re.fullmatch(r"iter\.\s*\(1e4\)", t, re.I):
        return True
    if re.fullmatch(r"(?:training|test)\s+error\s*\(%\)", t, re.I):
        return True
    return False


def _chart_region_bbox(chars: list, page_w: float, page_h: float):
    """数值图表坐标轴标签的边界框（当标题遍历失效时的后备）"""
    chart = [c for c in chars if _char_looks_chart_axis_tick(c["ch"])]
    if len(chart) < MIN_CHART_REGION_CHARS:
        return None
    bbox = _chars_bbox(chart)
    ratio = _bbox_area_ratio(bbox, page_w, page_h)
    if ratio < MIN_CHART_REGION_AREA_RATIO or ratio > MAX_CHART_REGION_AREA_RATIO:
        return None
    x0, y0, x1, y1 = bbox
    pad_x = max(8.0, (x1 - x0) * 0.08)
    pad_y = max(8.0, (y1 - y0) * 0.08)
    return (
        max(0.0, x0 - pad_x),
        max(0.0, y0 - pad_y),
        min(page_w, x1 + pad_x),
        min(page_h, y1 + pad_y),
    )


def _expand_chart_bbox(bbox, page_w: float, page_h: float, margin_frac: float = 0.18):
    x0, y0, x1, y1 = bbox
    dx = (x1 - x0) * margin_frac
    dy = (y1 - y0) * margin_frac
    return (
        max(0.0, x0 - dx),
        max(0.0, y0 - dy),
        min(page_w, x1 + dx),
        min(page_h, y1 + dy),
    )


def _collect_invisible_boxes(page, raw) -> list:
    """收集页面中不可见（渲染模式 3）文本对象的边界框"""
    boxes: list = []
    try:
        for obj in page.get_objects():
            if obj.type != raw.FPDF_PAGEOBJ_TEXT:
                continue
            try:
                mode = raw.FPDFTextObj_GetTextRenderMode(obj.raw)
            except Exception:
                continue
            if mode != raw.FPDF_TEXTRENDERMODE_INVISIBLE:
                continue
            try:
                left, bottom, right, top = obj.get_bounds()
            except Exception:
                continue
            boxes.append(
                (min(left, right), min(bottom, top), max(left, right), max(bottom, top))
            )
    except Exception:
        return []
    return boxes


def _point_in_boxes(x: float, y: float, boxes: list) -> bool:
    for x0, y0, x1, y1 in boxes:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
    return False


def _page_chars(textpage, page, raw) -> tuple:
    """返回 (chars, page_width)，过滤隐藏/越界字形"""
    n = textpage.count_chars()
    if n <= 0:
        return [], 0.0
    width, height = page.get_size()
    invisible = _collect_invisible_boxes(page, raw) if FILTER_HIDDEN_TEXT else []

    chars: list = []
    for i in range(n):
        try:
            left, bottom, right, top = textpage.get_charbox(i)
        except Exception:
            continue
        ch = textpage.get_text_range(i, 1)
        if ch in ("\r", "\n"):
            continue
        x0, x1 = (left, right) if left <= right else (right, left)
        y0, y1 = (bottom, top) if bottom <= top else (top, bottom)
        if FILTER_HIDDEN_TEXT:
            if x1 < 0 or x0 > width or y1 < 0 or y0 > height:
                continue
            if invisible and _point_in_boxes((x0 + x1) / 2, (y0 + y1) / 2, invisible):
                continue
        chars.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "ch": ch})
    return chars, width


def _find_split(items: list, axis: str, min_gap: float):
    """在指定轴上找到最宽的干净间隔，用于检测多列布局"""
    lo, hi = ("x0", "x1") if axis == "x" else ("y0", "y1")
    intervals = sorted(((s[lo], s[hi]) for s in items), key=lambda iv: iv[0])
    cur_end = intervals[0][1]
    best_gap, best_cut = 0.0, None
    for a, b in intervals[1:]:
        gap = a - cur_end
        if gap >= min_gap and gap > best_gap:
            best_gap, best_cut = gap, cur_end + gap / 2
        if b > cur_end:
            cur_end = b
    return best_cut


def _split_columns(chars: list, scale: float, width: float, depth: int = 0) -> list:
    """在完整高度 gutter 处分割为阅读顺序列"""
    if len(chars) <= 1 or depth > 10:
        return [chars]
    min_gap = max(scale * 2.5, width * 0.04)
    cut = _find_split(chars, "x", min_gap)
    if cut is None:
        return [chars]
    left = [c for c in chars if (c["x0"] + c["x1"]) / 2 < cut]
    right = [c for c in chars if (c["x0"] + c["x1"]) / 2 >= cut]
    if not left or not right:
        return [chars]
    return _split_columns(left, scale, width, depth + 1) + _split_columns(
        right, scale, width, depth + 1
    )


def _column_x_span(chars: list) -> float:
    if not chars:
        return 0.0
    return max(c["x1"] for c in chars) - min(c["x0"] for c in chars)


def _column_single_line_fraction(lines: list) -> float:
    if not lines:
        return 0.0
    single = sum(1 for ln in lines if len(ln["text"]) <= 2)
    return single / len(lines)


def _is_artifact_column(chars: list, width: float) -> bool:
    """检测页边距条和垂直水印（如 arXiv 侧边栏）"""
    if not chars or width <= 0:
        return True
    span = _column_x_span(chars)
    if span <= 0:
        return True
    lines = _group_lines(chars)
    single_frac = _column_single_line_fraction(lines)
    narrow = span / width < MARGIN_COL_WIDTH_RATIO
    if narrow and single_frac >= 0.45:
        return True
    ys = [(c["y0"] + c["y1"]) / 2 for c in chars]
    y_span = max(ys) - min(ys)
    if y_span > span * 3.5 and len(chars) >= 8 and single_frac >= 0.35:
        return True
    return False


def _filter_reading_columns(chars: list, scale: float, width: float) -> list:
    """分割为列并删除页边距/水印条"""
    cols = _split_columns(chars, scale, width)
    kept = [c for c in cols if not _is_artifact_column(c, width)]
    if kept:
        return kept
    if len(cols) > 1:
        return [max(cols, key=_column_x_span)]
    return cols


def _merge_orphan_punctuation_lines(lines: list) -> list:
    """将纯标点符号行附加到前一个可见行"""
    if not lines:
        return []
    merged: list = []
    for ln in lines:
        t = ln["text"].strip()
        if (
            merged
            and t
            and len(t) <= 4
            and all(c in ".,;:!?…·" or c.isspace() for c in t)
        ):
            suffix = "".join(t.split())
            prev = merged[-1]["text"]
            if suffix and prev and not prev.endswith((" ", "-")):
                merged[-1]["text"] = prev + suffix
            else:
                merged[-1]["text"] = (prev + " " + t).strip()
            continue
        merged.append(dict(ln))
    return merged


def _join_line_glyphs(ln_sorted: list) -> str:
    """连接可见行的字形，从水平间隙推断单词空格"""
    if not ln_sorted:
        return ""
    widths = [c["x1"] - c["x0"] for c in ln_sorted if c["x1"] > c["x0"]]
    med_w = statistics.median(widths) if widths else 1.0
    gap_threshold = med_w * WORD_GAP_WIDTH_RATIO

    parts: list[str] = []
    for i, cur in enumerate(ln_sorted):
        ch = cur["ch"]
        if i == 0:
            parts.append(ch)
            continue
        prev = ln_sorted[i - 1]
        if ch.isspace() or prev["ch"].isspace():
            if not ch.isspace() or (parts and not parts[-1].endswith(" ")):
                parts.append(ch)
            continue
        if cur["x0"] - prev["x1"] > gap_threshold:
            parts.append(" ")
        parts.append(ch)
    return "".join(parts).strip()


def _group_lines(chars: list) -> list:
    """将列的字形按行分组（从上到下，字形按 x 排序）"""
    if not chars:
        return []
    heights = [c["y1"] - c["y0"] for c in chars if c["y1"] - c["y0"] > 0]
    med_h = statistics.median(heights) if heights else 1.0

    ordered = sorted(chars, key=lambda c: -(c["y0"] + c["y1"]) / 2)
    lines: list = []
    cur: list = []
    ref = None
    for c in ordered:
        yc = (c["y0"] + c["y1"]) / 2
        if ref is None or abs(yc - ref) <= 0.5 * med_h:
            cur.append(c)
            ref = yc if ref is None else ref
        else:
            lines.append(cur)
            cur = [c]
            ref = yc
    if cur:
        lines.append(cur)

    out: list = []
    for ln in lines:
        ln_sorted = sorted(ln, key=lambda c: c["x0"])
        text = _join_line_glyphs(ln_sorted)
        if not text:
            continue
        hs = [c["y1"] - c["y0"] for c in ln_sorted if c["y1"] - c["y0"] > 0]
        out.append({"h": statistics.median(hs) if hs else med_h, "text": text})
    return out


def _segments_to_markdown(lines: list) -> str:
    """将合并的行渲染为文本，将视觉上大号行提升为标题"""
    if not lines:
        return ""
    body = statistics.median([ln["h"] for ln in lines])

    def level(ln) -> int:
        txt = ln["text"]
        if (
            not DETECT_HEADINGS
            or body <= 0
            or len(txt) > 80
            or len(txt) < MIN_HEADING_LINE_CHARS
        ):
            return 0
        if txt[-1:] in ".。!！?？,，;；:：":
            return 0
        r = ln["h"] / body
        if r >= 2.0:
            return 1
        if r >= 1.6:
            return 2
        if r >= 1.35:
            return 3
        return 0

    levels = [level(ln) for ln in lines]
    if sum(1 for x in levels if x) > max(1, int(0.4 * len(lines))):
        levels = [0] * len(lines)

    out = []
    for ln, lv in zip(lines, levels, strict=True):
        out.append(("#" * lv + " " + ln["text"]) if lv else ln["text"])
    return "\n".join(out)


def _chars_to_layout_markdown(chars: list, scale: float, width: float) -> str:
    blocks: list = []
    for col in _filter_reading_columns(chars, scale, width):
        lines = _merge_orphan_punctuation_lines(_group_lines(col))
        md = _segments_to_markdown(lines)
        if md:
            blocks.append(md)
    return "\n".join(blocks)


def _layout_line_stats(text: str) -> tuple:
    """返回 (行数, 单字符行数, 纯标点行数)"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0, 0, 0
    single = sum(1 for ln in lines if len(ln) <= 2)
    punct_only = sum(
        1
        for ln in lines
        if len(ln) <= 4 and re.fullmatch(r"[\s.,;:!?…·\-–—]+", ln)
    )
    return len(lines), single, punct_only


def _layout_garbled_line_fraction(text: str) -> float:
    """看起来像损坏 OCR 的行比例（许多 1-2 字母的 token）"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    garbled = 0
    for ln in lines:
        words = ln.split()
        if len(words) >= 6 and sum(1 for w in words if len(w) <= 2) / len(words) > 0.45:
            garbled += 1
    return garbled / len(lines)


def _plain_is_well_formed(plain: str) -> bool:
    """当 pdfium 纯文本已有可用词汇和标点时返回 True"""
    plain = (plain or "").strip()
    if not plain:
        return False
    if re.search(r"\[\w+,\s", plain):
        return True
    if plain.count(" . . ") >= 2:
        return True
    words = re.findall(r"\S+", plain)
    if len(words) < 30:
        return False
    avg_len = sum(len(w) for w in words) / len(words)
    return avg_len >= 5.0


def _should_prefer_plain(plain: str, layout: str) -> bool:
    """当布局重建看起来损坏时回退到 pdfium 纯文本"""
    layout = (layout or "").strip()
    plain = (plain or "").strip()
    if not layout:
        return True
    if not plain:
        return False
    n, single, punct_only = _layout_line_stats(layout)
    if n == 0:
        return True
    if single / n >= 0.18 or punct_only / n >= 0.12:
        return True
    garbled = _layout_garbled_line_fraction(layout)
    if garbled >= 0.20 and _layout_garbled_line_fraction(plain) < 0.08:
        return True
    if re.search(r"\[\w+,\s", plain) and re.search(
        r"\[\w+\s+\w+\s+\d", layout
    ):
        return True
    for ln in plain.splitlines():
        probe = ln.strip()
        if len(probe) < 24:
            continue
        alnum = "".join(c for c in probe if c.isalnum())[:16]
        if len(alnum) < 12:
            continue
        layout_alnum = "".join(c for c in layout if c.isalnum())
        if alnum not in layout_alnum:
            return True
        break
    return False


def _extract_layout_text(page, raw) -> str:
    """布局感知提取：阅读顺序 + 标题检测 + 隐藏文本过滤"""
    textpage = None
    try:
        textpage = page.get_textpage()
        chars, width = _page_chars(textpage, page, raw)
        if not chars:
            return ""
        heights = [c["y1"] - c["y0"] for c in chars if c["y1"] - c["y0"] > 0]
        scale = (statistics.median(heights) if heights else 1.0) or 1.0
        return _chars_to_layout_markdown(chars, scale, width)
    except Exception:
        logger.debug("布局提取失败，使用纯文本", exc_info=True)
        return _extract_page_text(page)
    finally:
        _close_pdfium_resource(textpage)


# --- 图像渲染 ---
def _effective_scale(page, scale: float, max_edge: int) -> float:
    """减小 scale 以使渲染长边不超过 max_edge px"""
    if max_edge <= 0:
        return scale
    width, height = page.get_size()
    longest_pt = max(float(width), float(height))
    if longest_pt <= 0:
        return scale
    return min(scale, max_edge / longest_pt)


def _pil_to_jpeg_bytes(pil, quality: int) -> bytes:
    buf = io.BytesIO()
    if pil.mode not in ("RGB", "L"):
        pil = pil.convert("RGB")
    pil.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _render_page_to_jpeg(page, scale: float, quality: int, max_edge: int = 0) -> bytes:
    bitmap = None
    try:
        bitmap = page.render(scale=_effective_scale(page, scale, max_edge))
        img_obj = bitmap.to_pil()
        if img_obj.mode != "RGB":
            img_obj = img_obj.convert("RGB")
        buf = io.BytesIO()
        img_obj.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    finally:
        _close_pdfium_resource(bitmap)


def _render_page_clip_jpeg(page, bbox, scale: float, quality: int, max_edge: int) -> bytes:
    """渲染 PDF 页面区域到 JPEG（bbox 为 PDF 点，左下原点）"""
    left, bottom, right, top = bbox
    scale_eff = _effective_scale(page, scale, max_edge)
    bitmap = None
    try:
        bitmap = page.render(scale=scale_eff)
        pil = bitmap.to_pil().convert("RGB")
    finally:
        _close_pdfium_resource(bitmap)
    page_w, page_h = page.get_size()
    x0 = int(left * scale_eff)
    x1 = int(right * scale_eff)
    y0 = int((page_h - top) * scale_eff)
    y1 = int((page_h - bottom) * scale_eff)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("degenerate clip bbox")
    return _pil_to_jpeg_bytes(pil.crop((x0, y0, x1, y1)), quality)


def _bbox_above_caption(lines: list, cap_i: int, page_w: float, page_h: float):
    """图表标题行上方的区域（PDF 坐标，左下原点）"""
    cap_bbox = lines[cap_i]["bbox"]
    cap_top = cap_bbox[3]
    x0, x1 = cap_bbox[0], cap_bbox[2]
    fig_h = page_h * min(MAX_FIGURE_HEIGHT_RATIO, 0.35)
    y_bottom = cap_top
    y_top = min(page_h, cap_top + fig_h)

    for j in range(cap_i - 1, -1, -1):
        t = lines[j]["text"]
        b = lines[j]["bbox"]
        if b[3] < y_bottom - 4:
            continue
        if b[1] > y_top + 4:
            break
        if _is_body_paragraph_line(t) and not _is_figure_interior_line(t):
            break
        if _is_figure_interior_line(t) or _is_chart_debris_line(t) or not t.strip():
            x0 = min(x0, b[0])
            x1 = max(x1, b[2])
            y_top = max(y_top, min(page_h, b[3] + fig_h * 0.15))

    min_h = page_h * 0.08
    if y_top - y_bottom < min_h:
        y_top = min(page_h, y_bottom + min_h)
    margin_x = max(8.0, (x1 - x0) * 0.05)
    return (
        max(0.0, x0 - margin_x),
        y_bottom,
        min(page_w, x1 + margin_x),
        y_top,
    )


def _cap_bbox_height(bbox, page_h: float, cap_y_top: float) -> tuple:
    """限制图表边界框高度"""
    x0, y0, x1, y1 = bbox
    max_top = min(y1, cap_y_top + page_h * MAX_FIGURE_HEIGHT_RATIO)
    if max_top <= y0:
        return bbox
    return (x0, y0, x1, max_top)


def _extract_vector_figure_clips(
    page,
    page_index: int,
    plain_text: str,
    raw,
    base_name: str,
    scale: float,
    quality: int,
    max_edge: int,
) -> list:
    """提取向量图表区域，渲染为 JPEG 返回 [(ref_path, b64, y_sort, caption_line), ...]"""
    if not RENDER_VECTOR_FIGURES or not re.search(r"\bFigure\s+\d+", plain_text, re.I):
        return []
    textpage = None
    try:
        textpage = page.get_textpage()
        chars, page_w = _page_chars(textpage, page, raw)
        if not chars:
            return []
        page_h = page.get_size()[1]
        lines = _merge_orphan_punctuation_lines(_group_lines_with_chars(chars))
        caption_indices = [
            i for i, ln in enumerate(lines) if _line_has_figure_caption(ln["text"])
        ]
        if not caption_indices:
            return []

        results: list = []
        for fig_idx, cap_i in enumerate(caption_indices):
            cap_line = lines[cap_i]["text"].strip()
            m = _FIGURE_CAPTION_SEARCH_RE.search(cap_line)
            if m:
                cap_line = cap_line[m.start() :].split("\n", 1)[0].strip()

            bbox = _bbox_above_caption(lines, cap_i, page_w, page_h)
            if bbox is None:
                bbox = _chart_region_bbox(chars, page_w, page_h)
            if bbox is None:
                continue

            ratio = _bbox_area_ratio(bbox, page_w, page_h)
            if ratio > MAX_CHART_REGION_AREA_RATIO:
                bbox = _cap_bbox_height(bbox, page_h, lines[cap_i]["bbox"][3])
                ratio = _bbox_area_ratio(bbox, page_w, page_h)
                if ratio > MAX_CHART_REGION_AREA_RATIO:
                    continue
            if ratio < MIN_CHART_REGION_AREA_RATIO:
                continue

            bbox = _expand_chart_bbox(bbox, page_w, page_h, margin_frac=0.06)
            jpeg = _render_page_clip_jpeg(page, bbox, scale, quality, max_edge)
            fname = f"{base_name}_p{page_index + 1}_fig{fig_idx + 1}.jpg"
            ref_path = f"images/{fname}"
            results.append(
                (
                    ref_path,
                    base64.b64encode(jpeg).decode("utf-8"),
                    bbox[3],
                    cap_line,
                )
            )
        return results
    except Exception:
        logger.debug("向量图表提取失败 page %d", page_index, exc_info=True)
        return []
    finally:
        _close_pdfium_resource(textpage)


def _group_lines_with_chars(chars: list) -> list:
    """将字形分组为行；每行包含其字形列表和边界框"""
    if not chars:
        return []
    heights = [c["y1"] - c["y0"] for c in chars if c["y1"] > c["y0"]]
    med_h = statistics.median(heights) if heights else 1.0
    ordered = sorted(chars, key=lambda c: -(c["y0"] + c["y1"]) / 2)
    groups: list = []
    cur: list = []
    ref = None
    for c in ordered:
        yc = (c["y0"] + c["y1"]) / 2
        if ref is None or abs(yc - ref) <= 0.5 * med_h:
            cur.append(c)
            ref = yc if ref is None else ref
        else:
            groups.append(cur)
            cur = [c]
            ref = yc
    if cur:
        groups.append(cur)

    lines: list = []
    for grp in groups:
        grp_sorted = sorted(grp, key=lambda c: c["x0"])
        text = _join_line_glyphs(grp_sorted)
        if not text:
            continue
        hs = [c["y1"] - c["y0"] for c in grp_sorted if c["y1"] > c["y0"]]
        lines.append(
            {
                "text": text,
                "h": statistics.median(hs) if hs else med_h,
                "chars": grp_sorted,
                "bbox": _chars_bbox(grp_sorted),
            }
        )
    return lines


def _inject_figure_markdown_before_captions(text: str, clips: list) -> str:
    """在每个图表标题行前放置 ``![...]()``"""
    if not clips:
        return text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    clip_idx = 0
    for i, ln in enumerate(lines):
        if clip_idx >= len(clips):
            break
        if not _line_has_figure_caption(ln):
            continue
        if i > 0 and lines[i - 1].lstrip().startswith("!["):
            continue
        ref_path = clips[clip_idx][0]
        fname = os.path.basename(ref_path)
        img_md = f"![{fname}]({ref_path})"
        lines[i] = f"{img_md}\n\n{ln}"
        clip_idx += 1
    return "\n".join(lines)


# --- 嵌入式图片提取 ---
def _select_embedded_images(
    meta: list,
    num_text_pages: int,
    *,
    min_pixels: int = EMBED_MIN_PIXELS,
    min_area_ratio: float = EMBED_MIN_AREA_RATIO,
    repeat_frac: float = EMBED_REPEAT_PAGE_FRAC,
    max_images: int = EMBED_MAX_IMAGES,
) -> list:
    """决定保留哪些嵌入图片候选"""
    from collections import defaultdict

    hash_pages = defaultdict(set)
    for m in meta:
        hash_pages[m["hash"]].add(m["page"])

    repeat_threshold = max(2, int(num_text_pages * repeat_frac)) if num_text_pages else 2
    banned = {h for h, pages in hash_pages.items() if len(pages) >= repeat_threshold}

    kept: list = []
    seen = set()
    for idx, m in enumerate(meta):
        if m["area_ratio"] < min_area_ratio:
            continue
        if m["width"] < min_pixels or m["height"] < min_pixels:
            continue
        if m["hash"] in banned:
            continue
        key = (m["page"], m["hash"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(idx)
        if len(kept) >= max_images:
            break
    return kept


def _extract_embedded_images(pdf, classes, raw, base_name: str, quality: int) -> dict:
    """从原生文本页提取过滤后的嵌入图表"""
    import hashlib

    text_indices = [i for i, c in enumerate(classes) if c == "text"]
    if not text_indices:
        return {}

    candidates: list = []
    meta: list = []
    for i in text_indices:
        page = pdf[i]
        try:
            width, height = page.get_size()
            page_area = float(width) * float(height)
            if page_area <= 0:
                continue
            for obj in page.get_objects():
                if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
                    continue
                try:
                    left, bottom, right, top = obj.get_bounds()
                except Exception:
                    continue
                area_ratio = abs((right - left) * (top - bottom)) / page_area
                if area_ratio < EMBED_MIN_AREA_RATIO:
                    continue
                try:
                    pil = obj.get_bitmap().to_pil()
                except Exception:
                    continue
                content_hash = hashlib.md5(pil.tobytes()).hexdigest()
                candidates.append((i, top, pil))
                meta.append(
                    {
                        "page": i,
                        "width": pil.width,
                        "height": pil.height,
                        "area_ratio": area_ratio,
                        "hash": content_hash,
                    }
                )
        finally:
            _close_pdfium_resource(page)

    kept_idx = _select_embedded_images(meta, len(text_indices))
    if not kept_idx:
        return {}

    from collections import defaultdict

    result: dict = defaultdict(list)
    per_page_count: dict = defaultdict(int)
    max_edge = PDF_RENDER_MAX_EDGE
    for idx in kept_idx:
        page_i, y_top, pil = candidates[idx]
        if pil.mode not in ("RGB", "L"):
            pil = pil.convert("RGB")
        if max_edge > 0 and max(pil.size) > max_edge:
            ratio = max_edge / max(pil.size)
            pil = pil.resize(
                (max(1, int(pil.width * ratio)), max(1, int(pil.height * ratio)))
            )
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=True)
        per_page_count[page_i] += 1
        fname = f"{base_name}_p{page_i+1}_img{per_page_count[page_i]}.jpg"
        ref_path = f"images/{fname}"
        result[page_i].append(
            (ref_path, base64.b64encode(buf.getvalue()).decode("utf-8"), y_top)
        )

    for page_i in result:
        result[page_i].sort(key=lambda item: item[2], reverse=True)
    return result


def _strip_repeating_lines(texts: list, classes: list) -> list:
    """删除在大多数文本页重复的运行页眉/页脚"""
    from collections import Counter

    text_indices = [i for i, c in enumerate(classes) if c == "text"]
    if len(text_indices) < 4:
        return list(texts)

    counter: Counter = Counter()
    for i in text_indices:
        lines = [ln.strip() for ln in texts[i].splitlines() if ln.strip()]
        if not lines:
            continue
        for edge in {lines[0], lines[-1]}:
            if len(edge) <= 80:
                counter[edge] += 1

    threshold = max(2, int(len(text_indices) * 0.6))
    repeating = {line for line, count in counter.items() if count >= threshold}
    if not repeating:
        return list(texts)

    cleaned = []
    for i, text in enumerate(texts):
        if classes[i] != "text":
            cleaned.append(text)
            continue
        kept = [ln for ln in text.splitlines() if ln.strip() not in repeating]
        cleaned.append("\n".join(kept))
    return cleaned


# --- 扫描页渲染 ---
def _render_scanned_pages(
    pdf, content: bytes, indices: list, scale: float, quality: int, max_edge: int
) -> dict:
    """渲染扫描页到 JPEG 字节"""
    out: dict = {}
    for i in indices:
        page = pdf[i]
        try:
            out[i] = _render_page_to_jpeg(page, scale, quality, max_edge)
        finally:
            _close_pdfium_resource(page)
    return out


# --- 主解析器 ---
class LocalPdfParser:
    """本地 PDF 解析器，使用 pypdfium2 进行页面分类和文本提取。

    解析策略：
    1. 对每页分类：text（原生文本页）或 scanned（扫描页）
    2. text 页：提取文本层，进行布局重建
    3. scanned 页：渲染为 JPEG，标记给下游 OCR 处理
    4. 混合文档：正确交错两种页面类型
    """

    def __init__(self) -> None:
        pass

    def parse(self, source_path: Path) -> dict[str, Any]:
        """解析 PDF 文件

        Returns:
            包含以下键的字典：
            - text: str - 拼接的 markdown 文本
            - images: dict[str, str] - ref_path -> base64 JPEG
            - metadata: dict - 解析元数据
        """
        pdfium, pdfium_r = _ensure_pdfium()

        with _PDFIUM_LOCK:
            return self._parse_locked(source_path, pdfium, pdfium_r)

    def _parse_locked(self, source_path: Path, pdfium, pdfium_r) -> dict[str, Any]:
        base_name = os.path.splitext(source_path.stem)[0]
        scale = max(1, PDF_RENDER_DPI) / 72
        quality = _normalize_image_quality(PDF_JPEG_QUALITY)

        with source_path.open("rb") as f:
            content = f.read()

        pdf = pdfium.PdfDocument(content)
        images: dict = {}
        try:
            page_count = len(pdf)

            # Pass 1: 文本提取 + 页面分类
            texts: list = []
            classes: list = []
            vector_clips: dict = {}
            for i in range(page_count):
                page = pdf[i]
                try:
                    plain = _extract_page_text(page)
                    ratio = _page_image_area_ratio(page, pdfium_r)
                    cls = _classify_page(ratio, len(plain.strip()))

                    # 布局重建仅对原生文本页有价值
                    if cls == "text" and LAYOUT_ORDERING:
                        if _plain_is_well_formed(plain):
                            text = plain
                        else:
                            layout = _extract_layout_text(page, pdfium_r)
                            if layout and not _should_prefer_plain(plain, layout):
                                text = layout
                            else:
                                text = plain
                    else:
                        text = plain

                    if cls == "text":
                        clips = _extract_vector_figure_clips(
                            page,
                            i,
                            plain,
                            pdfium_r,
                            base_name,
                            scale,
                            quality,
                            PDF_RENDER_MAX_EDGE,
                        )
                        if clips:
                            vector_clips[i] = clips
                            for ref_path, b64, _y, _cap in clips:
                                images[ref_path] = b64

                    text = _postprocess_pdf_text(text)
                    if cls == "text" and vector_clips.get(i):
                        text = _inject_figure_markdown_before_captions(text, vector_clips[i])
                finally:
                    _close_pdfium_resource(page)
                texts.append(text)
                classes.append(cls)

            texts = _strip_repeating_lines(texts, classes)
            scanned_indices = [i for i, c in enumerate(classes) if c == "scanned"]

            # Pass 2: 渲染扫描页（仅扫描页）
            if scanned_indices:
                rendered = _render_scanned_pages(
                    pdf,
                    content,
                    scanned_indices,
                    scale,
                    quality,
                    PDF_RENDER_MAX_EDGE,
                )
                for i, img_bytes in rendered.items():
                    ref_path = f"images/{base_name}_page_{i+1}.jpg"
                    images[ref_path] = base64.b64encode(img_bytes).decode("utf-8")

            # Pass 3: 从文本页提取嵌入图片
            embedded: dict = {}
            if EXTRACT_EMBEDDED_IMAGES:
                embedded = _extract_embedded_images(
                    pdf, classes, pdfium_r, base_name, quality
                )
                for refs in embedded.values():
                    for ref_path, b64, _y in refs:
                        images[ref_path] = b64
        finally:
            _close_pdfium_resource(pdf)

        # 组装 markdown（按阅读顺序）
        embedded_count = 0
        vector_figure_count = 0
        blocks = []
        for i in range(page_count):
            if classes[i] == "scanned":
                page_filename = f"{base_name}_page_{i+1}.jpg"
                blocks.append(f"![{page_filename}](images/{page_filename})")
            else:
                stripped = texts[i].strip()
                if stripped:
                    blocks.append(stripped)
                vector_figure_count += len(vector_clips.get(i, []))
                page_images = list(embedded.get(i, []))
                page_images.sort(key=lambda item: item[2], reverse=True)
                for ref_path, _b64, _y in page_images:
                    fname = os.path.basename(ref_path)
                    blocks.append(f"![{fname}]({ref_path})")
                    embedded_count += 1

        content_text = "\n\n".join(blocks).strip()

        metadata = {
            "page_count": page_count,
            "scanned_page_count": len(scanned_indices),
            "text_page_count": page_count - len(scanned_indices),
            "embedded_image_count": embedded_count,
            "vector_figure_count": vector_figure_count,
            "image_source_type": "scanned_pdf" if scanned_indices else "pdf_text_layer",
            "parser": "pypdfium2_local",
        }

        logger.info(
            "LocalPdfParser: %s -> %d pages (%d scanned, %d text), "
            "embedded_images=%d, content_len=%d",
            source_path.name,
            page_count,
            len(scanned_indices),
            page_count - len(scanned_indices),
            embedded_count,
            len(content_text),
        )
        return {
            "text": content_text,
            "pages": [
                {"page_number": index + 1, "text": page_text}
                for index, page_text in enumerate(texts)
                if page_text.strip()
            ],
            "images": images,
            "metadata": metadata,
        }


def create_local_pdf_parser() -> LocalPdfParser:
    """工厂函数：创建本地 PDF 解析器"""
    return LocalPdfParser()
