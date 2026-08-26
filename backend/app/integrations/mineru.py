import json
import time
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.core.config import Settings


class ParserUnavailable(Exception):
    """The configured MinerU service cannot parse the requested document."""


@dataclass(frozen=True, slots=True)
class ParsedNode:
    node_type: str
    content: str
    page_number: int | None = None
    section_path: str | None = None
    bbox: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MinerUParseResult:
    nodes: tuple[ParsedNode, ...]
    raw_output: bytes
    raw_content_type: str = "application/json"


class MinerUClient(Protocol):
    def parse(self, source_path: Path, source_mime_type: str) -> MinerUParseResult: ...


class UnavailableMinerUClient:
    """Safe default when MinerU has not been configured for the worker."""

    def parse(self, source_path: Path, source_mime_type: str) -> MinerUParseResult:
        del source_path, source_mime_type
        raise ParserUnavailable("A MinerU service endpoint is required")


class HttpMinerUClient:
    """Private-worker client for the official authenticated MinerU v4 API."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.mineru_base_url.rstrip("/") if settings.mineru_base_url else None
        self._api_key = (
            settings.mineru_api_key.get_secret_value() if settings.mineru_api_key else None
        )
        self._timeout_seconds = 900

    def parse(self, source_path: Path, source_mime_type: str) -> MinerUParseResult:
        del source_mime_type
        if not self._base_url or not self._api_key:
            raise ParserUnavailable("MinerU API address or credential is not configured")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            with httpx.Client(timeout=httpx.Timeout(self._timeout_seconds, connect=15)) as client:
                submission = self._api_data(
                    client.post(
                        f"{self._base_url}/file-urls/batch",
                        headers=headers,
                        json={
                            "model_version": "pipeline",
                            "enable_formula": True,
                            "enable_table": True,
                            "files": [{"name": source_path.name}],
                        },
                    )
                )
                batch_id = submission.get("batch_id")
                file_urls = submission.get("file_urls")
                if (
                    not isinstance(batch_id, str)
                    or not isinstance(file_urls, list)
                    or len(file_urls) != 1
                    or not isinstance(file_urls[0], str)
                ):
                    raise ValueError("MinerU returned an invalid upload response")
                with source_path.open("rb") as source:
                    uploaded = client.put(file_urls[0], content=source)
                    uploaded.raise_for_status()
                result_payload = self._wait_for_completion(client, batch_id, headers)
                zip_url = result_payload.get("full_zip_url")
                if not isinstance(zip_url, str) or not zip_url.startswith("https://"):
                    raise ValueError("MinerU returned an invalid result archive URL")
                raw_output = self._download_result_archive(client, zip_url)
        except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile) as exc:
            raise ParserUnavailable("MinerU request failed") from exc

        nodes = self._result_nodes_from_zip(raw_output)
        return MinerUParseResult(
            nodes=tuple(nodes),
            raw_output=raw_output,
            raw_content_type="application/zip",
        )

    def _download_result_archive(self, client: httpx.Client, zip_url: str) -> bytes:
        """下载 MinerU 的预签名结果包。

        结果 URL 通常属于对象存储域名，可能和 API 域名走不同 TLS/代理路径。
        先复用 API client；连接错误后仅对该短生命周期的预签名 URL 直连重试，
        避免一个代理异常让已完成的解析任务白白降级。
        """
        last_error: httpx.HTTPError | None = None
        for _attempt in range(2):
            try:
                response = client.get(zip_url, follow_redirects=True)
                response.raise_for_status()
                return response.content
            except httpx.HTTPError as exc:
                last_error = exc
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._timeout_seconds, connect=15),
                follow_redirects=True,
                trust_env=False,
            ) as direct_client:
                response = direct_client.get(zip_url)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise exc from last_error

    def _wait_for_completion(
        self, client: httpx.Client, batch_id: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            payload = self._api_data(
                client.get(f"{self._base_url}/extract-results/batch/{batch_id}", headers=headers)
            )
            rows = payload.get("extract_result")
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError("MinerU returned an invalid task result")
            result = rows[0]
            state = result.get("state")
            if state == "done":
                return result
            if state == "failed":
                # MinerU 官方 API 在失败时会返回 err_msg（如页数超限、文件损坏等），
                # 必须带入异常，否则排查时无法得知真实失败原因
                err_msg = result.get("err_msg")
                detail = err_msg.strip() if isinstance(err_msg, str) and err_msg.strip() else "unknown reason"
                raise ParserUnavailable(f"MinerU parsing task failed: {detail}")
            time.sleep(2)
        raise ParserUnavailable("MinerU parsing timed out")

    @staticmethod
    def _api_data(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ValueError("MinerU API rejected the request")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("MinerU API returned invalid data")
        return data

    @staticmethod
    def _result_nodes_from_zip(archive: bytes) -> list[ParsedNode]:
        with zipfile.ZipFile(BytesIO(archive)) as result_zip:
            content_list: list[object] | None = None
            content_list_v2: list[object] | None = None
            markdown: str | None = None
            for member in result_zip.infolist():
                if member.is_dir():
                    continue
                name = Path(member.filename).name.lower()
                with result_zip.open(member) as result_file:
                    data = result_file.read()
                if name.endswith("_content_list_v2.json"):
                    try:
                        parsed = json.loads(data)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        parsed = None
                    if isinstance(parsed, list):
                        content_list_v2 = parsed
                elif name == "content_list.json" or name.endswith("_content_list.json"):
                    try:
                        parsed = json.loads(data)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        parsed = None
                    if isinstance(parsed, list):
                        content_list = parsed
                elif name.endswith(".md") and markdown is None:
                    markdown = data.decode("utf-8", errors="replace")
            # Prefer v2 format which has title/paragraph types
            if content_list_v2 is not None:
                nodes = HttpMinerUClient._content_list_v2_nodes(content_list_v2)
                if nodes:
                    return nodes
            if content_list is not None:
                nodes = HttpMinerUClient._content_list_nodes(content_list)
                if nodes:
                    return nodes
            if markdown:
                return HttpMinerUClient._markdown_nodes(markdown)
        raise ParserUnavailable("MinerU returned no usable content")

    @staticmethod
    def _content_list_nodes(content_list: list[object]) -> list[ParsedNode]:
        nodes: list[ParsedNode] = []
        section_path: list[str] = []
        type_mapping = {
            "title": "SECTION",
            "text": "PARAGRAPH",
            "table": "TABLE",
            "image": "IMAGE",
            "list": "LIST",
        }
        for item in content_list:
            if not isinstance(item, dict):
                continue
            raw_type = str(item.get("type", "text")).lower()
            node_type = type_mapping.get(raw_type, "PARAGRAPH")
            content = next(
                (
                    value
                    for value in (item.get("text"), item.get("content"), item.get("table_body"))
                    if isinstance(value, str) and value.strip()
                ),
                "",
            )
            if not content:
                continue
            if node_type == "SECTION":
                section_path = [content.strip()]
            page_index = item.get("page_idx")
            page_number = (
                page_index + 1 if isinstance(page_index, int) and page_index >= 0 else None
            )
            bbox = HttpMinerUClient._bbox_object(item.get("bbox"))
            nodes.append(
                ParsedNode(
                    node_type=node_type,
                    content=content,
                    page_number=page_number,
                    section_path=" / ".join(section_path) or None,
                    bbox=bbox,
                    metadata={"mineru_type": raw_type},
                )
            )
        return nodes

    @staticmethod
    def _content_list_v2_nodes(content_list_v2: list[object]) -> list[ParsedNode]:
        """Parse content_list_v2.json which has title/paragraph types per page."""
        nodes: list[ParsedNode] = []
        section_path: list[str] = []
        for page_offset, page_items in enumerate(content_list_v2):
            if not isinstance(page_items, list):
                continue
            for item in page_items:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "")).lower()
                # Content 嵌套在 item["content"]["title_content"] 或 ["paragraph_content"] 中
                content_obj = item.get("content")
                content = ""
                if item_type == "title" and isinstance(content_obj, dict):
                    title_content = content_obj.get("title_content")
                    if isinstance(title_content, list):
                        content = "".join(
                            c.get("content", "")
                            for c in title_content
                            if isinstance(c, dict) and isinstance(c.get("content"), str)
                        )
                elif item_type == "paragraph" and isinstance(content_obj, dict):
                    para_content = content_obj.get("paragraph_content")
                    if isinstance(para_content, list):
                        content = "".join(
                            c.get("content", "")
                            for c in para_content
                            if isinstance(c, dict) and isinstance(c.get("content"), str)
                        )
                elif item_type == "table":
                    content = content_obj.get("table_body") if isinstance(content_obj, dict) else ""
                if content is None:
                    content = ""
                content = content.strip()
                if not content:
                    continue
                node_type = {
                    "title": "SECTION", "table": "TABLE", "list": "LIST", "image": "IMAGE",
                }.get(item_type, "PARAGRAPH")
                # MinerU v2 may record the page on item, otherwise its outer list is page ordered.
                page_index = item.get("page_idx")
                page_number = (
                    page_index + 1
                    if isinstance(page_index, int) and page_index >= 0
                    else page_offset + 1
                )
                # 从 content.content 中提取 heading level（MinerU v2 格式）。
                heading_level = 1
                if isinstance(content_obj, dict):
                    lvl = content_obj.get("level")
                    if isinstance(lvl, int) and 1 <= lvl <= 6:
                        heading_level = lvl
                if node_type == "SECTION":
                    if heading_level <= len(section_path):
                        section_path = section_path[: heading_level - 1]
                    while len(section_path) < heading_level - 1:
                        section_path.append("未命名层级")
                    section_path.append(content.strip())
                bbox = HttpMinerUClient._bbox_object(item.get("bbox"))
                nodes.append(
                    ParsedNode(
                        node_type=node_type,
                        content=content,
                        page_number=page_number,
                        section_path=" / ".join(section_path) or None,
                        bbox=bbox,
                        metadata={
                            "mineru_type": item_type,
                            "heading_level": heading_level,
                            "source": "mineru_content_list_v2",
                        },
                    )
                )
        return nodes

    @staticmethod
    def _markdown_nodes(markdown: str) -> list[ParsedNode]:
        nodes: list[ParsedNode] = []
        section_path: str | None = None
        for line in (line.strip() for line in markdown.splitlines()):
            if not line:
                continue
            if line.startswith("#"):
                section_path = line.lstrip("#").strip()
                if section_path:
                    nodes.append(ParsedNode("SECTION", section_path, section_path=section_path))
            else:
                nodes.append(ParsedNode("PARAGRAPH", line, section_path=section_path))
        return nodes

    @staticmethod
    def _bbox_object(value: object) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if (
            isinstance(value, list)
            and len(value) == 4
            and all(isinstance(item, int | float) for item in value)
        ):
            return {"x0": value[0], "y0": value[1], "x1": value[2], "y1": value[3]}
        return None
