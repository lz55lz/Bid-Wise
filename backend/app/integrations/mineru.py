"""MinerU 官方批量解析 API 的 Worker 专用客户端。"""

import json
import time
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import httpx

from app.core.config import Settings



_MAX_RESULT_ARCHIVE_BYTES = 200 * 1024 * 1024
_MAX_RESULT_ARCHIVE_FILES = 5_000
_MAX_RESULT_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
_MAX_RESULT_MEMBER_BYTES = 50 * 1024 * 1024

class ParserUnavailable(Exception):
    """MinerU 未配置、超时或返回不可用结果。"""


@dataclass(frozen=True, slots=True)
class ParsedNode:
    """解析器统一输出的最小节点，不包含数据库或授权信息。"""

    node_type: str
    content: str
    page_number: int | None = None
    section_path: str | None = None
    bbox: object | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParseResult:
    """供 Worker 持久化的 MinerU 原始输出和规范化节点。"""

    nodes: tuple[ParsedNode, ...]
    raw_output: bytes


class MinerUClient:
    """使用服务端 API Key 调用官方批量上传、轮询与结果下载接口。"""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.mineru_base_url.rstrip("/") if settings.mineru_base_url else None
        self._api_key = (
            settings.mineru_api_key.get_secret_value() if settings.mineru_api_key else None
        )

    def parse(self, source_path: Path) -> ParseResult:
        """上传一份源文件、等待终态，再从结果压缩包提取 Markdown 节点。"""
        if not self._base_url or not self._api_key:
            raise ParserUnavailable("MinerU 地址或密钥未配置")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(timeout=httpx.Timeout(900, connect=15)) as client:
            try:
                submitted = self._api_data(
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
            except (httpx.HTTPError, ValueError) as exc:
                # 仅保留调用阶段，不能把带签名的上传 URL、Token 或响应正文写入
                # 任务错误信息，避免它们进入数据库、日志与前端接口。
                raise ParserUnavailable("MinerU 申请上传地址失败") from exc
            batch_id = submitted.get("batch_id")
            urls = submitted.get("file_urls")
            if not isinstance(batch_id, str) or not isinstance(urls, list) or len(urls) != 1:
                raise ParserUnavailable("MinerU 返回了无效的上传地址")
            upload_url = urls[0]
            if not isinstance(upload_url, str):
                raise ParserUnavailable("MinerU 返回了无效的上传地址")
            try:
                with source_path.open("rb") as source:
                    client.put(upload_url, content=source).raise_for_status()
            except (httpx.HTTPError, OSError) as exc:
                raise ParserUnavailable("MinerU 上传源文件失败") from exc
            try:
                result_url = self._wait_for_result(client, batch_id, headers)
            except (httpx.HTTPError, ValueError) as exc:
                raise ParserUnavailable("MinerU 查询解析进度失败") from exc
            raw_output = self._download_result_archive(client, result_url)
        try:
            nodes = tuple(self._result_nodes(raw_output))
        except (ValueError, zipfile.BadZipFile) as exc:
            raise ParserUnavailable("MinerU 解析结果包无效") from exc
        return ParseResult(nodes=nodes, raw_output=raw_output)

    @staticmethod
    def _download_result_archive(client: httpx.Client, result_url: str) -> bytes:
        """下载 CDN 结果包，兼容系统代理与直连 TLS 路径差异。

        MinerU API 域名与结果 ZIP 的 CDN 域名不同。旧项目的生产实践表明：
        某些 Windows 网络环境会让继承环境代理的 httpx 客户端在 CDN TLS
        握手阶段提前断开，而直连客户端可以正常下载。因此先按默认网络策略
        重试，再只对这个短生命周期的预签名结果 URL 尝试一次直连。
        """
        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            try:
                with client.stream("GET", result_url, follow_redirects=True) as response:
                    return MinerUClient._read_limited_archive(response)
            except (httpx.HTTPError, ParserUnavailable) as exc:
                last_error = exc if isinstance(exc, httpx.HTTPError) else last_error
                if isinstance(exc, ParserUnavailable):
                    raise
                if attempt < 2:
                    # CDN TLS 握手偶发提前断开时重试即可；不能通过 verify=False
                    # 绕过证书校验，否则投标文件结果将失去传输完整性保障。
                    time.sleep(0.5 * (attempt + 1))

        try:
            with httpx.Client(
                timeout=httpx.Timeout(900, connect=15),
                follow_redirects=True,
                # 不读取 HTTP(S)_PROXY/ALL_PROXY 等进程环境变量，只用于
                # MinerU 返回的单次签名结果地址，避免代理异常影响已完成任务。
                trust_env=False,
            ) as direct_client:
                with direct_client.stream("GET", result_url) as response:
                    return MinerUClient._read_limited_archive(response)
        except ParserUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise ParserUnavailable("MinerU 下载解析结果失败") from (last_error or exc)

    @staticmethod
    def _read_limited_archive(response: httpx.Response) -> bytes:
        """流式读取结果包并限制体积，避免第三方响应直接占满 Worker 内存。"""
        response.raise_for_status()
        raw_length = response.headers.get("content-length")
        if raw_length:
            try:
                if int(raw_length) > _MAX_RESULT_ARCHIVE_BYTES:
                    raise ParserUnavailable("MinerU 结果包超过允许大小")
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > _MAX_RESULT_ARCHIVE_BYTES:
                raise ParserUnavailable("MinerU 结果包超过允许大小")
            chunks.append(chunk)
        return b"".join(chunks)

    def _wait_for_result(
        self,
        client: httpx.Client,
        batch_id: str,
        headers: dict[str, str],
    ) -> str:
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            result = self._api_data(
                client.get(f"{self._base_url}/extract-results/batch/{batch_id}", headers=headers)
            )
            rows = result.get("extract_result")
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ParserUnavailable("MinerU 返回了无效的任务状态")
            state = rows[0].get("state")
            if state == "done":
                url = rows[0].get("full_zip_url")
                if isinstance(url, str) and url.startswith("https://"):
                    return url
                raise ParserUnavailable("MinerU 未返回结果包地址")
            if state == "failed":
                detail = rows[0].get("err_msg")
                raise ParserUnavailable(str(detail or "MinerU 解析失败"))
            time.sleep(2)
        raise ParserUnavailable("MinerU 解析超时")

    @staticmethod
    def _api_data(response: httpx.Response) -> dict[str, object]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ParserUnavailable("MinerU 拒绝请求")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ParserUnavailable("MinerU 返回了无效数据")
        return data

    @staticmethod
    def _result_nodes(archive: bytes) -> list[ParsedNode]:
        """选择结果包中的首个 Markdown，按标题和段落构造可追溯节点。"""
        with zipfile.ZipFile(BytesIO(archive)) as result_zip:
            infos = result_zip.infolist()
            if len(infos) > _MAX_RESULT_ARCHIVE_FILES:
                raise ParserUnavailable("MinerU 结果包文件数量异常")
            if sum(info.file_size for info in infos) > _MAX_RESULT_UNCOMPRESSED_BYTES:
                raise ParserUnavailable("MinerU 结果包解压后体积异常")
            by_name = {info.filename: info for info in infos}
            for member in result_zip.namelist():
                if not member.lower().endswith("_content_list_v2.json"):
                    continue
                if by_name[member].file_size > _MAX_RESULT_MEMBER_BYTES:
                    raise ParserUnavailable("MinerU 内容列表文件过大")
                try:
                    rows = json.loads(result_zip.read(member))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(rows, list):
                    nodes = MinerUClient._v2_nodes(rows)
                    if nodes:
                        return nodes
            markdown = None
            for member in result_zip.namelist():
                if not member.lower().endswith(".md"):
                    continue
                if by_name[member].file_size > _MAX_RESULT_MEMBER_BYTES:
                    raise ParserUnavailable("MinerU Markdown 文件过大")
                markdown = result_zip.read(member).decode("utf-8", errors="replace")
                break
        if not markdown:
            raise ParserUnavailable("MinerU 未返回可用 Markdown")
        nodes: list[ParsedNode] = []
        path: list[str] = []
        for line in (item.strip() for item in markdown.splitlines()):
            if not line:
                continue
            if line.startswith("#"):
                level = min(6, len(line) - len(line.lstrip("#")))
                title = line[level:].strip()
                if not title:
                    continue
                path = path[: level - 1]
                path.append(title)
                nodes.append(
                    ParsedNode(
                        "SECTION",
                        title,
                        None,
                        " / ".join(path),
                        None,
                        {"heading_level": level},
                    )
                )
            else:
                nodes.append(ParsedNode("PARAGRAPH", line, None, " / ".join(path) or None))
        if not nodes:
            raise ParserUnavailable("MinerU 返回的 Markdown 为空")
        return nodes

    @staticmethod
    def _v2_nodes(rows: list[object]) -> list[ParsedNode]:
        """解析 MinerU content_list_v2，保留结构、页码与版面定位。

        MinerU 的 v2 ``content`` 字段会随节点类型使用不同载荷：标题/正文通常是
        span 列表，表格优先提供 ``html``，列表提供 ``list_items``。这里做兼容式
        归一化，避免用旧版 ``paragraph_content/table_body`` 字段导致表格和列表在
        进入清洗/RAG 前就被静默丢失。
        """

        def text_of(value: object) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            if isinstance(value, (int, float)):
                return str(value)
            if isinstance(value, list):
                return "".join(text_of(item) for item in value)
            if isinstance(value, dict):
                for key in ("content", "text", "value"):
                    if key in value:
                        text = text_of(value.get(key))
                        if text:
                            return text
                return "".join(text_of(item) for item in value.values())
            return ""

        def body_text(kind: str, body: dict[str, object]) -> str:
            if kind == "table":
                # 某些 MinerU 结果同时给出只有表头/空单元格的 html 与内容更完整的
                # table_body 或 markdown。不能因字段优先级而静默选中空表骨架。
                candidates = [
                    text_of(body.get(key)).strip()
                    for key in ("html", "table_body", "markdown", "text")
                ]
                candidates = [value for value in candidates if value]
                if not candidates:
                    return ""
                return max(
                    candidates,
                    key=lambda value: len(re.sub(r"<[^>]+>", "", value)),
                )
            if kind == "list":
                items = body.get("list_items") or body.get("items")
                if isinstance(items, list):
                    lines = [text_of(item).strip() for item in items]
                    return "\n".join(line for line in lines if line)
            preferred = (
                "title_content"
                if kind == "title"
                else "paragraph_content"
            )
            return text_of(body.get(preferred) or body.get("text") or body.get("content")).strip()

        nodes: list[ParsedNode] = []
        heading_by_level: dict[int, str] = {}
        for page_offset, items in enumerate(rows):
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("type", "")).lower().strip()
                raw_body = item.get("content")
                body = raw_body if isinstance(raw_body, dict) else {"content": raw_body}
                content = body_text(kind, body)
                if not content:
                    continue

                raw_level = body.get("level", item.get("level"))
                level = raw_level if isinstance(raw_level, int) and 1 <= raw_level <= 6 else None
                node_type = {
                    "title": "SECTION",
                    "table": "TABLE",
                    "list": "LIST",
                    "image": "IMAGE",
                }.get(kind, "PARAGRAPH")
                if node_type == "SECTION":
                    heading_level = level or 1
                    heading_by_level[heading_level] = content
                    for deeper in [key for key in heading_by_level if key > heading_level]:
                        del heading_by_level[deeper]
                section_path = " / ".join(
                    heading_by_level[key] for key in sorted(heading_by_level)
                ) or None

                index = item.get("page_idx")
                page = index + 1 if isinstance(index, int) and index >= 0 else page_offset + 1
                bbox = item.get("bbox")
                if not isinstance(bbox, (dict, list)):
                    bbox = None
                metadata: dict[str, object] = {"mineru_type": kind}
                if level is not None:
                    metadata["heading_level"] = level
                if kind in {"page_header", "page_footer", "page_number", "page_aside_text"}:
                    metadata["layout_artifact"] = True
                nodes.append(
                    ParsedNode(
                        node_type=node_type,
                        content=content,
                        page_number=page,
                        section_path=section_path,
                        bbox=bbox,
                        metadata=metadata,
                    )
                )
        return nodes
