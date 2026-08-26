from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error

from app.core.config import Settings


class ObjectStorageUnavailable(Exception):
    """Raised when a private object-storage operation cannot be completed."""


class MinioObjectStorage:
    def __init__(self, settings: Settings) -> None:
        if not (
            settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key
        ):
            raise ObjectStorageUnavailable("MinIO is not configured")

        endpoint = urlparse(settings.minio_endpoint)
        if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
            raise ObjectStorageUnavailable("MINIO_ENDPOINT must be an http(s) URL")

        self._bucket = settings.minio_bucket
        self._client = Minio(
            endpoint.netloc,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=endpoint.scheme == "https",
        )

    def put_file(self, object_key: str, path: Path, content_type: str) -> None:
        self._ensure_bucket()
        try:
            with path.open("rb") as file_handle:
                self._client.put_object(
                    self._bucket,
                    object_key,
                    file_handle,
                    path.stat().st_size,
                    content_type=content_type,
                )
        except (OSError, S3Error) as exc:
            raise ObjectStorageUnavailable("Unable to store the private object") from exc

    def put_bytes(self, object_key: str, content: bytes, content_type: str) -> None:
        self._ensure_bucket()
        try:
            from io import BytesIO

            self._client.put_object(
                self._bucket,
                object_key,
                BytesIO(content),
                len(content),
                content_type=content_type,
            )
        except S3Error as exc:
            raise ObjectStorageUnavailable("Unable to store the parser output") from exc

    def delete_object(self, object_key: str) -> None:
        try:
            self._client.remove_object(self._bucket, object_key)
        except S3Error as exc:
            raise ObjectStorageUnavailable("Unable to compensate the private object") from exc

    def check_available(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                raise ObjectStorageUnavailable("The private bucket does not exist")
        except ObjectStorageUnavailable:
            raise
        except S3Error as exc:
            raise ObjectStorageUnavailable("Unable to access the private bucket") from exc

    def download_to_path(self, object_key: str, destination: Path) -> None:
        try:
            response = self._client.get_object(self._bucket, object_key)
            try:
                with destination.open("wb") as file_handle:
                    for chunk in response.stream(amt=1024 * 1024):
                        file_handle.write(chunk)
            finally:
                response.close()
                response.release_conn()
        except (OSError, S3Error) as exc:
            raise ObjectStorageUnavailable("Unable to read the private object") from exc

    @contextmanager
    def open_object(self, object_key: str) -> Iterator[object]:
        try:
            response = self._client.get_object(self._bucket, object_key)
        except S3Error as exc:
            raise ObjectStorageUnavailable("Unable to read the private object") from exc
        try:
            yield response
        finally:
            response.close()
            response.release_conn()

    def _ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
        except S3Error as exc:
            # Another API worker can win the creation race. A second existence
            # check avoids treating that expected condition as data loss.
            try:
                if self._client.bucket_exists(self._bucket):
                    return
            except S3Error:
                pass
            raise ObjectStorageUnavailable("Unable to access the private bucket") from exc
