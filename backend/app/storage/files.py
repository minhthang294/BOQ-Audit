import re
import unicodedata
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.config import get_settings

PDF_MIMES = {"application/pdf", "application/x-pdf", "application/octet-stream"}
EXCEL_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",
}


def safe_display_filename(name: str | None) -> str:
    raw = Path((name or "file").replace("\\", "/")).name
    normalized = unicodedata.normalize("NFKC", raw)
    cleaned = re.sub(r"[^\w.() -]", "_", normalized, flags=re.UNICODE).strip(" .")
    return cleaned[:240] or "file"


def validate_upload(file: UploadFile, allowed_extensions: set[str], allowed_mimes: set[str]) -> tuple[str, str]:
    name = safe_display_filename(file.filename)
    ext = Path(name).suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Định dạng tệp không được hỗ trợ")
    content_type = (file.content_type or "").lower()
    if content_type and content_type not in allowed_mimes:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "MIME type của tệp không hợp lệ")
    return name, ext


async def save_upload(file: UploadFile, destination: Path, expected_kind: str) -> int:
    settings = get_settings()
    destination.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    size = 0
    header = b""
    try:
        with destination.open("xb") as target:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Tệp vượt quá {settings.max_upload_mb} MB")
                if len(header) < 8:
                    header += chunk[: 8 - len(header)]
                target.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tệp rỗng")
        if expected_kind == "pdf" and not header.startswith(b"%PDF-"):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Nội dung không phải tệp PDF hợp lệ")
        if expected_kind == "xlsx" and not header.startswith(b"PK"):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Nội dung không phải tệp XLSX hợp lệ")
        if expected_kind == "xls" and not header.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Nội dung không phải tệp XLS hợp lệ")
        return size
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


def random_stored_name(extension: str) -> str:
    return f"{uuid4().hex}{extension}"


def stream_file(path: Path, filename: str, media_type: str = "application/octet-stream", inline: bool = False) -> StreamingResponse:
    async def chunks():
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                yield chunk

    safe_name = safe_display_filename(filename)
    disposition_type = "inline" if inline else "attachment"
    disposition = f"{disposition_type}; filename*=UTF-8''{quote(safe_name)}"
    return StreamingResponse(chunks(), media_type=media_type, headers={"Content-Disposition": disposition})
