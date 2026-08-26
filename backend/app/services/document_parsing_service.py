import hashlib
import logging
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import DocumentNode
from app.db.repositories.document_repository import DocumentRepository
from app.integrations.markitdown_parser import MarkItdownClient
from app.integrations.mineru import (
    MinerUClient,
    MinerUParseResult,
    ParsedNode,
    ParserUnavailable,
)
from app.integrations.object_storage import (
    MinioObjectStorage,
    ObjectStorageUnavailable,
)
from app.integrations.pdf_parser import LocalPdfParser
from app.integrations.task_publisher import TaskPublisher
from app.services.evidence_service import EvidenceService
from app.services.task_service import RetryableDocumentTaskError, TaskService

logger = logging.getLogger(__name__)

_ALLOWED_NODE_TYPES = frozenset(
    {"SECTION", "PARAGRAPH", "TABLE", "CELL", "IMAGE", "LIST"}
)

_MAX_SECTION_PATH_LEN = 1024
_MAX_HEADING_LEVEL = 6
_MIN_MEANINGFUL_CHARS = 4
_LOCAL_PDF_PARSER = "local_pdf"
_MAX_REPLACEMENT_CHARACTER_RATIO = 0.002

# 节点过滤：本地解析器 fallback 时排除噪音行（页码等）
_PDF_PAGE_ARTIFACT_RE = re.compile(
    r"^(?:第\s*\d{1,4}\s*页(?:\s*/\s*共?\s*\d{1,4}\s*页)?|"
    r"page\s*\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?|\d{1,4})$",
    re.IGNORECASE,
)



def _count_meaningful_chars(text: str) -> int:
    """统计字母数字与 CJK 汉字数量，用于过滤页码等噪音行。"""
    return sum(c.isalnum() or "一" <= c <= "鿿" for c in text)


class DocumentParsingService:
    """Worker-only orchestration for MinerU parsing and normalized node persistence."""

    def __init__(
        self,
        session: Session,
        object_storage: MinioObjectStorage,
        mineru: MinerUClient,
        task_publisher: TaskPublisher | None = None,
    ) -> None:
        self._session = session
        self._documents = DocumentRepository(session)
        self._storage = object_storage
        self._mineru = mineru
        self._tasks = TaskService(session)
        self._evidences = EvidenceService(session)
        self._publisher = task_publisher
        self._markitdown = MarkItdownClient()
        self._local_pdf = LocalPdfParser()

    # ============================================================ process flow

    def process(self, task_id: UUID, document_version_id: UUID) -> None:
        task = self._tasks.start_parse(task_id, document_version_id)
        if task is None:
            return
        try:
            nodes_count = self.do_parse(document_version_id)
            version = self._documents.get_version(document_version_id)
            self._tasks.mark_structuring(document_version_id)
            clean_task = self._tasks.create_clean_task(version)
            self._tasks.complete_parse(
                task_id, document_version_id, None, nodes_count, True
            )
            # 后续 clean/index/extract 阶段已并入 ARQ bid_pipeline，
            # 单阶段 follow-up 派发已废弃（clean_task 仅作任务记录）。
            _ = clean_task
        except ParserUnavailable:
            self._retry_or_fail_parse(
                task_id,
                document_version_id,
                "MINERU_UNAVAILABLE",
                "MinerU 解析服务未配置或暂不可用。",
            )
        except ObjectStorageUnavailable:
            self._retry_or_fail_parse(
                task_id,
                document_version_id,
                "OBJECT_STORAGE_UNAVAILABLE",
                "对象存储暂不可用。",
            )
        except ValueError as e:
            self._fail_parse(task_id, document_version_id, "PARSE_FAILED", str(e))
        except Exception:
            self._fail_parse(
                task_id, document_version_id, "PARSE_FAILED", "文档解析失败。"
            )

    # ============================================================ parse logic

    def do_parse(self, document_version_id: UUID) -> int:
        """核心解析逻辑，不含 TaskService 管理。返回节点数。"""
        version = self._documents.get_version(document_version_id)
        if version is None:
            raise ValueError("文档版本不存在")
        document = self._documents.get_document(version.document_id)
        if document is None:
            raise ValueError("文档不存在")

        output_key: str | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="ai-bid-parse-") as tmpdir:
                source_path = Path(tmpdir) / Path(version.file_name).name
                self._storage.download_to_path(version.object_key, source_path)

                result = self._parse_document(source_path, version.mime_type)
                nodes = self._normalize(result.nodes, document_version_id)

                output_key = self._build_output_key(
                    document_version_id, result.raw_content_type
                )
                self._storage.put_bytes(
                    output_key, result.raw_output, result.raw_content_type
                )
                self._documents.add_nodes(nodes)
                self._session.flush()
                self._evidences.create_document_evidences(nodes, version.created_by)
                self._session.commit()
                return len(nodes)
        except Exception:
            if output_key:
                self._safe_delete_object(output_key)
            raise

    def _parse_document(
        self, source_path: Path, mime_type: str
    ) -> MinerUParseResult:
        """先尝试 MinerU；失败时按 mime_type 选取本地 fallback。"""
        try:
            return self._mineru.parse(source_path, mime_type)
        except Exception as exc:
            logger.warning("MinerU 解析失败，fallback: %s", exc)
            return self._fallback_parse(source_path, mime_type)

    def _fallback_parse(
        self, source_path: Path, mime_type: str
    ) -> MinerUParseResult:
        if mime_type == "application/pdf":
            try:
                return self._parse_with_local_pdf(source_path, mime_type)
            except Exception as pdf_exc:
                logger.warning("本地 PDF 解析失败: %s", pdf_exc)
        return self._markitdown.parse(source_path, mime_type)

    def _parse_with_local_pdf(
        self, source_path: Path, mime_type: str
    ) -> MinerUParseResult:
        """使用本地 pypdfium2 解析器解析 PDF。"""
        parsed = self._local_pdf.parse(source_path)
        text = parsed["text"]
        replacement_ratio = text.count("�") / max(len(text), 1)
        if replacement_ratio > _MAX_REPLACEMENT_CHARACTER_RATIO:
            raise ParserUnavailable(
                "本地 PDF 文本编码损坏，需使用 MinerU 或 OCR 解析，已拒绝写入乱码节点。"
            )
        nodes = self._text_to_nodes(parsed["pages"])
        if not nodes:
            raise ParserUnavailable("本地 PDF 解析器返回空内容")
        return MinerUParseResult(
            nodes=tuple(nodes),
            raw_output=text.encode("utf-8"),
            raw_content_type="text/plain",
        )

    @staticmethod
    def _build_output_key(document_version_id: UUID, content_type: str) -> str:
        suffix = ".zip" if content_type == "application/zip" else ".json"
        return f"parse-output/{document_version_id}/{uuid4()}{suffix}"

    def _safe_delete_object(self, key: str) -> None:
        try:
            self._storage.delete_object(key)
        except ObjectStorageUnavailable:
            pass

    # ============================================================ task helpers

    def _fail_parse(
        self,
        task_id: UUID,
        document_version_id: UUID,
        error_code: str,
        message: str,
    ) -> None:
        # A parser-output write or node flush can fail before TaskService gets a
        # chance to update state. Clear that failed transaction first, then use
        # a new transaction to make the failure durable.
        self._session.rollback()
        self._tasks.fail_parse(task_id, document_version_id, error_code, message)

    def _retry_or_fail_parse(
        self,
        task_id: UUID,
        document_version_id: UUID,
        error_code: str,
        message: str,
    ) -> None:
        self._session.rollback()
        if self._tasks.retry_parse_or_fail(
            task_id, document_version_id, error_code, message
        ):
            raise RetryableDocumentTaskError(error_code, message)

    # ============================================================ normalize

    @staticmethod
    def _normalize(
        parsed_nodes: tuple[ParsedNode, ...], document_version_id: UUID
    ) -> list[DocumentNode]:
        normalized = [
            DocumentParsingService._build_node(parsed, document_version_id, order_no)
            for order_no, parsed in enumerate(parsed_nodes, start=1)
        ]
        if not normalized:
            raise ValueError("MinerU returned no referenceable nodes")
        return normalized

    @staticmethod
    def _build_node(
        parsed: ParsedNode, document_version_id: UUID, order_no: int
    ) -> DocumentNode:
        node_type = parsed.node_type.upper()
        content = parsed.content.strip()
        source_section_path = parsed.section_path

        # SECTION 节点注入 markdown 标题标记，便于 heading tier 检测
        if node_type == "SECTION":
            level = min(
                _MAX_HEADING_LEVEL,
                max(1, parsed.metadata.get("heading_level", 1)),
            )
            content = "#" * level + " " + content

        if node_type not in _ALLOWED_NODE_TYPES or not content:
            raise ValueError("invalid MinerU node")
        if parsed.page_number is not None and parsed.page_number <= 0:
            raise ValueError("invalid page number")
        if parsed.bbox is not None and not isinstance(parsed.bbox, dict):
            raise ValueError("invalid bounding box")

        return DocumentNode(
            id=uuid4(),
            document_version_id=document_version_id,
            node_type=node_type,
            page_number=parsed.page_number,
            section_path=DocumentParsingService._truncate_section_path(source_section_path),
            order_no=order_no,
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            cleaned_content=None,
            cleaning_metadata={},
            bbox=parsed.bbox,
            metadata_={
                **dict(parsed.metadata),
                # ``section_path`` is varchar(1024) for query/display.  Keep
                # the complete MinerU hierarchy for downstream clause context.
                "source_section_path": source_section_path or "",
            },
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _truncate_section_path(section_path: str | None) -> str | None:
        """截断超长 section_path，保留尾部路径段并维持原始顺序。"""
        if section_path is None or len(section_path) <= _MAX_SECTION_PATH_LEN:
            return section_path
        parts = section_path.split(" / ")
        truncated = ""
        for part in reversed(parts):
            candidate = f"{part} / {truncated}" if truncated else part
            if len(candidate) > _MAX_SECTION_PATH_LEN:
                break
            truncated = candidate
        return truncated

    # ============================================================ local PDF text

    @staticmethod
    def _text_to_nodes(pages: list[dict[str, object]]) -> list[ParsedNode]:
        """将纯文本转换为 ParsedNode 列表。

        - 以 ``#`` 开头的行识别为 markdown 标题。
        - MarkItdownClient 启发式判定为标题的行识别为二级标题。
        - 其余有效行合并为同章节下的段落，减少节点总数。
        """
        nodes: list[ParsedNode] = []
        section_path: list[str] = []
        para_buffer: list[str] = []

        current_page_number: int | None = None

        def flush_paragraph() -> None:
            if not para_buffer:
                return
            content = "\n".join(para_buffer)
            sec_path = " / ".join(section_path) if section_path else None
            nodes.append(
                ParsedNode(
                    node_type="PARAGRAPH",
                    content=content,
                    page_number=current_page_number,
                    section_path=sec_path,
                    metadata={
                        "parser": _LOCAL_PDF_PARSER,
                        "merged_lines": len(para_buffer),
                    },
                )
            )
            para_buffer.clear()

        for page in pages:
            flush_paragraph()
            current_page_number = int(page["page_number"])
            for raw_line in str(page["text"]).split("\n"):
                line = raw_line.strip()
                if not line or _PDF_PAGE_ARTIFACT_RE.fullmatch(line):
                    continue
                if _count_meaningful_chars(line) < _MIN_MEANINGFUL_CHARS:
                    continue
                if line.startswith("#"):
                    flush_paragraph()
                    level = len(line) - len(line.lstrip("#"))
                    title = line.lstrip("#").strip()
                    if not title:
                        continue
                    if level == 1:
                        section_path = [title]
                    else:
                        section_path.append(title)
                    nodes.append(ParsedNode(
                        node_type="SECTION", content=title,
                        page_number=current_page_number,
                        section_path=" / ".join(section_path[-3:]),
                        metadata={"parser": _LOCAL_PDF_PARSER, "heading_level": level},
                    ))
                elif MarkItdownClient._is_likely_heading(line):
                    flush_paragraph()
                    section_path.append(line)
                    nodes.append(ParsedNode(
                        node_type="SECTION", content=line,
                        page_number=current_page_number,
                        section_path=" / ".join(section_path[-3:]),
                        metadata={"parser": _LOCAL_PDF_PARSER, "heading_level": 2},
                    ))
                else:
                    para_buffer.append(line)

        flush_paragraph()
        return nodes
