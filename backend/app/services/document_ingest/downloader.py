"""document_ingest.downloader — MinIO/Path 文件下载

从 bid_pipeline/graph.py 的 _parse_node 抽出下载逻辑。
支持三种来源：
  1. 本地 raw_text_path（已下载好的文本文件）
  2. minio://bucket/object_name URL（MinIO 对象存储）
  3. 本地 Path（直接使用）

下载到临时文件，调用方负责清理（return 的 path 可能是 tempdir 路径）。
"""
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def download_document(
    *,
    raw_text_path: str | None = None,
    doc_url: str | None = None,
    file_name: str | None = None,
    settings: Any = None,
) -> str | None:
    """从 MinIO 或本地路径下载/解析文档，返回本地文件路径。

    优先级：raw_text_path (本地已下载) > doc_url。

    Args:
        raw_text_path: 已存在的本地文件路径（首选）
        doc_url: URL，支持 minio://bucket/object 或本地 Path
        settings: 配置对象（doc_url 为 minio:// 时必传，提供 minio_endpoint/credentials）

    Returns:
        本地文件路径；失败返回 None。
        调用方负责 unlink(tempfile.gettempdir() 下的临时文件)。
    """
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()

    # 优先级 1：raw_text_path 存在则直接用
    if raw_text_path and Path(raw_text_path).exists():
        return raw_text_path

    # 优先级 2：minio:// URL → 下载到临时文件
    if doc_url and doc_url.startswith("minio://"):
        try:
            from minio import Minio

            minio_client = Minio(
                settings.minio_endpoint.replace("http://", "").replace(
                    "https://", ""
                ),
                access_key=settings.minio_access_key.get_secret_value(),
                secret_key=settings.minio_secret_key.get_secret_value(),
                secure=False,
            )
            bucket, object_name = doc_url.replace("minio://", "", 1).split("/", 1)
            suffix = Path(file_name or "").suffix.lower() or ".bin"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.close()
            minio_client.fget_object(bucket, object_name, tmp.name)
            logger.info(
                f"[document_ingest] Downloaded from MinIO: {bucket}/{object_name}"
            )
            return tmp.name
        except Exception as e:
            logger.error(f"[document_ingest] MinIO download failed: {e}")
            return None

    # 优先级 3：本地 Path
    if doc_url and Path(doc_url).exists():
        return doc_url

    return None


def cleanup_temp_file(file_path: str | None) -> None:
    """清理临时目录下的下载文件（安全 unlink，OSError 兜底）。"""
    if not file_path:
        return
    try:
        if file_path.startswith(tempfile.gettempdir()):
            os.unlink(file_path)
    except OSError:
        pass
