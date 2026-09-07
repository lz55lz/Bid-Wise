"""基于 MiniMax-M3 的招标标签结构化提取服务。"""

import asyncio
import json
import re
from datetime import UTC, datetime
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.constants import LLM_MODEL_ID
from app.db.session import get_session_factory
from app.integrations.model_gateway import ModelProfile, create_chat_model
from app.modules.documents.models import DocumentNode
from app.modules.evidence.models import Evidence, EvidenceSourceNode
from app.modules.tender_analysis.models import TenderPipelineStage, TenderTag
from app.modules.tender_analysis.workflow import TenderPipelineState

_MAX_EVIDENCES_PER_MODEL_REQUEST = 6
_MAX_BATCH_CHARS = 9_000
# 字符串结束引号后面允许出现的 JSON 结构字符；用于区分内容引号与真正的结束引号。
_STRUCTURE_FOLLOWERS = frozenset(",}]:]")
_EXTRACTION_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["code", "value", "confidence", "source_evidence_id", "source_text"],
                "properties": {
                    "code": {"type": "string"},
                    "value": {},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "source_evidence_id": {"type": "string"},
                    "source_text": {"type": "string"},
                },
            },
        }
    },
}


class TenderExtractionService:
    """仅从当前文档版本的节点中提取候选标签，输出由后续校验和人工复核把关。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def extract(self, state: TenderPipelineState) -> dict[str, object]:
        """从结构化 Evidence 提取标签，并把命中回落到正式 Node 血缘。"""
        if not self._settings.llm_is_configured:
            raise RuntimeError("MiniMax LLM 服务未配置，不能执行招标标签提取")
        version_id = UUID(state["document_version_id"])
        candidate_codes = state.get("candidate_tag_codes", [])
        if not candidate_codes:
            return {"extracted_tags": {}}
        tags = list(
            (
                await self._session.scalars(
                    select(TenderTag).where(
                        TenderTag.code.in_(candidate_codes), TenderTag.is_active.is_(True)
                    )
                )
            ).all()
        )
        evidence_candidates = state.get("evidence_candidate_tags", {})
        candidate_ids = [UUID(item) for item in evidence_candidates]
        if not candidate_ids:
            return {"extracted_tags": {}}
        evidences = list(
            (
                await self._session.scalars(
                    select(Evidence)
                    .where(
                        Evidence.id.in_(candidate_ids),
                        Evidence.document_version_id == version_id,
                        Evidence.quoted_text.is_not(None),
                    )
                )
            ).all()
        )
        evidences.sort(
            key=lambda item: (
                int((item.locator or {}).get("order_start") or 0),
                str(item.id),
            )
        )
        allowed_evidences = {str(item.id): item for item in evidences}
        source_nodes = await self._source_nodes(candidate_ids)
        model = create_chat_model(self._settings, ModelProfile.EXTRACTION)
        extracted: dict[str, dict[str, object]] = {}
        allowed_codes = {tag.code for tag in tags}
        tags_by_code = {tag.code: tag for tag in tags}
        batches = self._build_batches(evidences)
        llm_concurrency = self._settings.llm_batch_concurrency
        semaphore = asyncio.Semaphore(llm_concurrency)
        await self._set_extract_progress(
            UUID(state["pipeline_run_id"]), total_batches=len(batches), completed_batches=0,
            failed_batches=0, llm_concurrency=llm_concurrency,
        )

        async def run_batch(
            batch_index: int, batch: list[Evidence]
        ) -> tuple[int, list[object], dict[str, object] | None]:
            batch_codes = sorted(
                {
                    code
                    for evidence in batch
                    for code in evidence_candidates.get(str(evidence.id), candidate_codes)
                    if code in allowed_codes
                }
            )
            if not batch_codes:
                return batch_index, [], None
            tag_spec = [
                {
                    "code": code,
                    "name": tags_by_code[code].name,
                    "prompt": tags_by_code[code].extraction_prompt or "",
                }
                for code in batch_codes
            ]
            evidence_spec = [
                {
                    "evidence_id": str(evidence.id),
                    "page_start": (evidence.locator or {}).get("page_start"),
                    "page_end": (evidence.locator or {}).get("page_end"),
                    "section_path": (evidence.locator or {}).get("section_path"),
                    "candidate_codes": evidence_candidates.get(
                        str(evidence.id), candidate_codes
                    ),
                    "text": evidence.quoted_text or "",
                }
                for evidence in batch
            ]
            messages = [
                SystemMessage(
                    content=(
                        "你是招标文件字段提取器。仅依据给定 Evidence 提取标签；不得臆造。"
                        "每条 Evidence 只能提取其 candidate_codes 中的标签。"
                        '仅返回 JSON 对象：{"items": [..]}；每项为 code、value、'
                        "confidence、source_evidence_id、source_text。confidence 是 0 到 1 "
                        "数值；source_text 必须是对应 Evidence 原文中的连续片段；找不到值则不要输出。"
                        "输出必须是严格合法的 JSON：字符串值内部出现的半角双引号必须转义，"
                        "或改写为中文引号，不得换行打断字符串。"
                        "返回结构必须严格符合以下 JSON Schema："
                        f"{json.dumps(_EXTRACTION_SCHEMA, ensure_ascii=False, separators=(',', ':'))}"
                    )
                ),
                HumanMessage(
                    content=json.dumps(
                        {"tags": tag_spec, "evidences": evidence_spec}, ensure_ascii=False
                    )
                ),
            ]
            async with semaphore:
                for format_attempt in range(2):
                    try:
                        response = await model.ainvoke(messages)
                        return batch_index, self._parse_json_items(
                            self._text_content(response.content)
                        ), None
                    except (ValueError, RuntimeError) as exc:
                        if format_attempt == 1:
                            return batch_index, [], {
                                "batch_index": batch_index,
                                "evidence_count": len(batch),
                                "reason": self._failure_reason(exc),
                            }
                        messages = [
                            *messages,
                            HumanMessage(
                                content=(
                                    "上一次输出未通过 JSON 校验（"
                                    f"{self._failure_reason(exc)}）。请重新只返回符合 Schema 的 JSON 对象，"
                                    "不要包含 Markdown、解释或代码围栏。"
                                )
                            ),
                        ]
                    except Exception as exc:  # 单批 Provider 故障不能拖死其他批次。
                        if format_attempt == 1:
                            return batch_index, [], {
                                "batch_index": batch_index,
                                "evidence_count": len(batch),
                                "reason": f"模型调用失败：{type(exc).__name__}",
                            }
                        messages = [
                            *messages,
                            HumanMessage(content="上一次调用失败，请仅返回符合 Schema 的 JSON 对象。"),
                        ]
            return batch_index, [], None

        batch_results: list[tuple[int, list[object], dict[str, object] | None]] = []
        tasks = [
            asyncio.create_task(run_batch(index, batch))
            for index, batch in enumerate(batches)
        ]
        progress_interval = max(1, len(tasks) // 20)
        for completed_batches, task in enumerate(asyncio.as_completed(tasks), start=1):
            result = await task
            batch_results.append(result)
            failed_batches = sum(1 for _, _, failure in batch_results if failure is not None)
            if completed_batches % progress_interval == 0 or completed_batches == len(tasks):
                await self._set_extract_progress(
                    UUID(state["pipeline_run_id"]),
                    total_batches=len(tasks),
                    completed_batches=completed_batches,
                    failed_batches=failed_batches,
                    llm_concurrency=llm_concurrency,
                )
        extraction_failures = [
            failure for _, _, failure in batch_results if failure is not None
        ]
        for _, values, _ in sorted(batch_results, key=lambda item: item[0]):
            for item in values:
                if not isinstance(item, dict):
                    continue
                code, source_evidence_id, confidence = (
                    item.get("code"),
                    item.get("source_evidence_id"),
                    item.get("confidence"),
                )
                if (
                    code not in allowed_codes
                    or source_evidence_id not in allowed_evidences
                    or code not in evidence_candidates.get(
                        source_evidence_id, candidate_codes
                    )
                    or not isinstance(confidence, (int, float))
                    or not 0 <= confidence <= 1
                ):
                    continue
                evidence = allowed_evidences[source_evidence_id]
                source_text = str(item.get("source_text") or "").strip()
                evidence_text = evidence.quoted_text or ""
                if source_text and source_text not in evidence_text:
                    source_text = ""
                value = item.get("value")
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                source_node = self._resolve_source_node(
                    source_nodes.get(evidence.id, []), source_text
                )
                locator = evidence.locator or {}
                candidate = {
                    "value": value,
                    "confidence": float(confidence),
                    "source_evidence_id": source_evidence_id,
                    "source_node_id": (
                        str(source_node.id)
                        if source_node is not None
                        else (str(evidence.document_node_id) if evidence.document_node_id else None)
                    ),
                    "source_page_number": (
                        source_node.page_number
                        if source_node is not None
                        else locator.get("page_start")
                    ),
                    "source_section_path": str(locator.get("section_path") or ""),
                    "source_text": source_text[:4000],
                    "extract_method": "LLM",
                    "model_id": LLM_MODEL_ID,
                }
                if tags_by_code[code].is_multi_value:
                    self._merge_multi_value_candidate(extracted, code, candidate)
                elif (
                    code not in extracted
                    or candidate["confidence"] > extracted[code]["confidence"]
                ):
                    extracted[code] = candidate
        stage = await self._session.scalar(
            select(TenderPipelineStage)
            .where(
                TenderPipelineStage.pipeline_run_id == UUID(state["pipeline_run_id"]),
                TenderPipelineStage.stage_name == "extract",
            )
            .with_for_update()
        )
        if stage is not None:
            stage.status = "SUCCEEDED"
            stage.output_summary = {
                "candidate_evidences": len(evidences),
                "extracted_tags": len(extracted),
                "batch_count": len(batches),
                "llm_concurrency": llm_concurrency,
                "failed_batch_count": len(extraction_failures),
            }
        return {
            "extracted_tags": extracted,
            "extraction_failures": extraction_failures,
        }

    @staticmethod
    async def _set_extract_progress(
        pipeline_run_id: UUID,
        *,
        total_batches: int,
        completed_batches: int,
        failed_batches: int,
        llm_concurrency: int,
    ) -> None:
        """抽取调用可能持续数分钟，进度单独提交供轮询页面读取。"""
        async with get_session_factory()() as session:
            stage = await session.scalar(
                select(TenderPipelineStage).where(
                    TenderPipelineStage.pipeline_run_id == pipeline_run_id,
                    TenderPipelineStage.stage_name == "extract",
                )
            )
            if stage is None:
                return
            stage.status = "RUNNING"
            stage.started_at = stage.started_at or datetime.now(UTC)
            stage.output_summary = {
                "batch_count": total_batches,
                "completed_batch_count": completed_batches,
                "failed_batch_count": failed_batches,
                "llm_concurrency": llm_concurrency,
            }
            await session.commit()

    async def _source_nodes(
        self, evidence_ids: list[UUID]
    ) -> dict[UUID, list[DocumentNode]]:
        rows = await self._session.execute(
            select(EvidenceSourceNode.evidence_id, DocumentNode)
            .join(DocumentNode, DocumentNode.id == EvidenceSourceNode.document_node_id)
            .where(EvidenceSourceNode.evidence_id.in_(evidence_ids))
            .order_by(EvidenceSourceNode.evidence_id, EvidenceSourceNode.ordinal)
        )
        output: dict[UUID, list[DocumentNode]] = {}
        for evidence_id, node in rows.all():
            output.setdefault(evidence_id, []).append(node)
        return output

    @staticmethod
    def _resolve_source_node(
        nodes: list[DocumentNode], source_text: str
    ) -> DocumentNode | None:
        if source_text:
            for node in nodes:
                if source_text in (node.cleaned_content or ""):
                    return node
        return nodes[0] if nodes else None


    @staticmethod
    def _merge_multi_value_candidate(
        extracted: dict[str, dict[str, object]],
        code: str,
        candidate: dict[str, object],
    ) -> None:
        """多值标签聚合不同节点命中，同时保留最高置信来源作为主引用锚点。"""
        raw_value = candidate.get("value")
        incoming = raw_value if isinstance(raw_value, list) else [raw_value]
        incoming = [item for item in incoming if item is not None and item != ""]
        if not incoming:
            return
        existing = extracted.get(code)
        if existing is None:
            stored = dict(candidate)
            stored["value"] = []
            stored["sources"] = []
            extracted[code] = stored
            existing = stored
        values = existing.get("value")
        if not isinstance(values, list):
            values = [values] if values is not None else []
        signatures = {
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in values
        }
        for item in incoming:
            signature = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if signature not in signatures:
                values.append(item)
                signatures.add(signature)
        existing["value"] = values
        sources = existing.get("sources")
        if not isinstance(sources, list):
            sources = []
        sources.append(
            {
                "source_evidence_id": candidate.get("source_evidence_id"),
                "source_node_id": candidate.get("source_node_id"),
                "source_page_number": candidate.get("source_page_number"),
                "source_text": candidate.get("source_text"),
                "confidence": candidate.get("confidence"),
            }
        )
        existing["sources"] = sources
        if float(candidate.get("confidence", 0)) > float(existing.get("confidence", 0)):
            existing.update(
                {
                    "confidence": candidate.get("confidence", 0),
                    "source_evidence_id": candidate.get("source_evidence_id"),
                    "source_node_id": candidate.get("source_node_id"),
                    "source_page_number": candidate.get("source_page_number"),
                    "source_text": candidate.get("source_text"),
                }
            )

    @staticmethod
    def _build_batches(evidences: list[Evidence]) -> list[list[Evidence]]:
        """Evidence 已在 ingestion 阶段受控切块；这里仅按模型请求预算组批。"""
        batches: list[list[Evidence]] = []
        current: list[Evidence] = []
        current_chars = 0
        for evidence in evidences:
            text_chars = len(evidence.quoted_text or "")
            would_overflow = (
                current
                and (
                    len(current) >= _MAX_EVIDENCES_PER_MODEL_REQUEST
                    or current_chars + text_chars > _MAX_BATCH_CHARS
                )
            )
            if would_overflow:
                batches.append(current)
                current = []
                current_chars = 0
            current.append(evidence)
            current_chars += text_chars
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _text_content(content: object) -> str:
        """兼容 LangChain 的字符串和文本块响应，不接受工具调用等非文本内容。

        MiniMax 的 OpenAI 兼容层可能同时返回中间文本块和最终文本块；拼接两段 JSON
        会得到 ``[]\n[...]`` 这类无效载荷。结构化提取只接受最后一个输出文本块，
        并仍由下游严格 ``json.loads`` 校验。
        """
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
        ]
        return parts[-1] if parts else ""

    @staticmethod
    def _parse_json_items(raw: str) -> list[object]:
        """解析 JSON 模式对象，且只接受其中明确的 ``items`` 数组。

        除了剥离模型常见的 ```json 展示包装外，这里还会修复“字符串值内部未转义的
        半角双引号”这一最高频的非法输出形态：招标原文里 ``"信用中国"`` 这类引号会被
        模型原样写进 JSON 字符串，导致整个批次解析失败。仍然不尝试修复被截断的
        JSON，也不从任意文本里猜取字段；无法恢复时明确失败并附上原始输出片段。
        """
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as first_error:
            repaired = _escape_unescaped_quotes(cleaned)
            try:
                payload = json.loads(repaired)
            except json.JSONDecodeError:
                raise ValueError(
                    "模型未返回合法标签 JSON 对象："
                    f"{first_error.msg}（位置 {first_error.pos}）；"
                    f"原始输出片段 {raw.strip()[:300]!r}"
                ) from first_error
        values = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise ValueError("模型标签结果必须包含 items 数组")
        return values

    @staticmethod
    def _failure_reason(exc: BaseException) -> str:
        """反馈给模型/人工复核的简短校验原因，不保存原始模型输出。"""
        return " ".join(str(exc).split())[:180] or type(exc).__name__


def _escape_unescaped_quotes(text: str) -> str:
    """把 JSON 字符串内部未转义的半角双引号转成 ``\\"``。

    只在字符串内部生效：一个引号后面若紧跟 ``,`` ``}`` ``]`` ``:`` 或文本结尾，
    才被认定为该字符串的结束引号，否则视为原文内容。处理过程保留已有的转义序列，
    也不改动字符串之外的任何字符。
    """
    out: list[str] = []
    in_string = False
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if not in_string:
            out.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == "\\" and index + 1 < length:
            out.append(char)
            out.append(text[index + 1])
            index += 2
            continue
        if char != '"':
            out.append(char)
            index += 1
            continue
        cursor = index + 1
        while cursor < length and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= length or text[cursor] in _STRUCTURE_FOLLOWERS:
            in_string = False
            out.append(char)
        else:
            out.append('\\"')
        index += 1
    return "".join(out)
