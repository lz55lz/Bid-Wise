"""上传文件内容级校验；后缀只决定允许类型，真实内容仍需最小签名验证。"""

from pathlib import Path
from zipfile import BadZipFile, ZipFile

from app.core.errors import DomainError

_OOXML_REQUIRED_ROOT = {
    ".docx": "word/",
    ".xlsx": "xl/",
    ".pptx": "ppt/",
}


def validate_uploaded_file_content(path: Path, suffix: str) -> None:
    """校验 PDF 头或 OOXML ZIP 结构，拒绝仅伪造扩展名的上传。"""
    if suffix == ".pdf":
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise DomainError("INVALID_FILE_CONTENT", "PDF 文件内容与扩展名不匹配", 422)
        return

    required_root = _OOXML_REQUIRED_ROOT.get(suffix)
    if required_root is None:
        raise DomainError("UNSUPPORTED_FILE_TYPE", "不支持的文件类型", 422)
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            if "[Content_Types].xml" not in names or not any(
                name.startswith(required_root) for name in names
            ):
                raise DomainError(
                    "INVALID_FILE_CONTENT", "Office 文件内容与扩展名不匹配", 422
                )
    except BadZipFile as exc:
        raise DomainError("INVALID_FILE_CONTENT", "Office 文件不是有效的 OOXML 文件", 422) from exc
