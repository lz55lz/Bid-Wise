"""MinIO 私有对象存储适配器。"""

import logging

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error

from app.core.config import Settings

logger = logging.getLogger(__name__)


class ObjectStorageUnavailable(Exception):
    """对象存储未配置或临时不可用；由 API 转换为稳定的 503 业务错误。"""


class MinioObjectStorage:
    """只接受服务端生成的对象键，不能把客户端对象键作为任何授权依据。"""

    def __init__(self, settings: Settings) -> None:
        if not (
            settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key
        ):
            raise ObjectStorageUnavailable("MinIO 未配置")
        endpoint = urlparse(settings.minio_endpoint)
        if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
            raise ObjectStorageUnavailable("MINIO_ENDPOINT 必须是 http(s) 地址")
        self._bucket = settings.minio_bucket
        self._client = Minio(
            endpoint.netloc,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=endpoint.scheme == "https",
        )

    def put_file(self, object_key: str, source_path: Path, mime_type: str) -> None:
        """上传暂存文件；Bucket 不存在时仅创建当前应用专用的目标 Bucket。"""
        self._ensure_bucket()
        try:
            with source_path.open("rb") as source:
                self._client.put_object(
                    self._bucket,
                    object_key,
                    source,
                    source_path.stat().st_size,
                    content_type=mime_type,
                )
        except (OSError, S3Error) as exc:
            raise ObjectStorageUnavailable("文件上传到对象存储失败") from exc

    def delete_object(self, object_key: str) -> None:
        """数据库写入失败时尽力补偿已上传对象，不掩盖原始异常。"""
        try:
            self._client.remove_object(self._bucket, object_key)
        except S3Error:
            # 补偿失败会由后续对象巡检处理，不能覆盖更重要的原始持久化错误。
            logger.warning("对象存储补偿删除失败 key=%s", object_key, exc_info=True)

    def download_to_path(self, object_key: str, destination: Path) -> None:
        """仅由 Worker 使用，下载到其私有临时目录后立即解析和清理。"""
        try:
            response = self._client.get_object(self._bucket, object_key)
            try:
                with destination.open("wb") as output:
                    for chunk in response.stream(amt=1024 * 1024):
                        output.write(chunk)
            finally:
                response.close()
                response.release_conn()
        except (OSError, S3Error) as exc:
            raise ObjectStorageUnavailable("文件下载失败") from exc

    def stream_object(self, object_key: str) -> Iterator[bytes]:
        """以服务端生成的对象键流式读取私有文件，供已授权 HTTP 下载使用。"""
        try:
            response = self._client.get_object(self._bucket, object_key)
        except S3Error as exc:
            raise ObjectStorageUnavailable("文件下载失败") from exc
        try:
            yield from response.stream(amt=1024 * 1024)
        except S3Error as exc:
            raise ObjectStorageUnavailable("文件下载失败") from exc
        finally:
            response.close()
            response.release_conn()

    def put_bytes(self, object_key: str, content: bytes, mime_type: str) -> None:
        """保存 MinerU 原始结果包，以支持后续人工追溯和重新归一化。"""
        from io import BytesIO

        self._ensure_bucket()
        try:
            self._client.put_object(
                self._bucket,
                object_key,
                BytesIO(content),
                len(content),
                content_type=mime_type,
            )
        except S3Error as exc:
            raise ObjectStorageUnavailable("解析结果保存失败") from exc

    def _ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
        except S3Error as exc:
            raise ObjectStorageUnavailable("对象存储不可用") from exc
