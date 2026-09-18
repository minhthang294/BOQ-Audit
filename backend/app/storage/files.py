import hashlib
import re
import unicodedata
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, Request, Response, UploadFile, status
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
            while chunk := await file.read(1024 * 1024):
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


def stored_job_file(job_code: str, stored_path: str) -> Path:
    job_root = (get_settings().jobs_dir / job_code).resolve()
    path = Path(stored_path).resolve()
    if not path.is_relative_to(job_root) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tệp không tồn tại")
    return path


def stream_file(
    path: Path,
    filename: str,
    media_type: str = "application/octet-stream",
    inline: bool = False,
    request: Request | None = None,
) -> Response:
    stat_result = path.stat()
    safe_name = safe_display_filename(filename)
    disposition_type = "inline" if inline else "attachment"
    disposition = f"{disposition_type}; filename*=UTF-8''{quote(safe_name)}"
    cache_control = "private, no-cache" if inline else "private, no-store"
    etag_source = f"{stat_result.st_mtime_ns}-{stat_result.st_size}".encode()
    etag = f'"{hashlib.md5(etag_source, usedforsecurity=False).hexdigest()}"'
    last_modified = formatdate(stat_result.st_mtime, usegmt=True)
    common_headers = {
        "Content-Disposition": disposition,
        "Cache-Control": cache_control,
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": last_modified,
    }
    # Authorization has already run before this helper. Revalidation therefore
    # saves the PDF transfer without allowing a cached draft to bypass access
    # checks after logout or after COMPLETED is moved back to REVIEW.
    if inline and request and request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=common_headers)

    start = 0
    end = stat_result.st_size
    response_status = status.HTTP_200_OK
    range_header = request.headers.get("range") if inline and request else None
    if range_header and (not request.headers.get("if-range") or request.headers["if-range"] in {etag, last_modified}):
        try:
            unit, value = range_header.split("=", 1)
            if unit.strip().lower() != "bytes" or "," in value:
                raise ValueError
            first, last = (part.strip() for part in value.split("-", 1))
            if first:
                start = int(first)
                end = min(int(last) + 1, stat_result.st_size) if last else stat_result.st_size
            else:
                suffix_length = int(last)
                if suffix_length <= 0:
                    raise ValueError
                start = max(stat_result.st_size - suffix_length, 0)
            if start < 0 or start >= end or start >= stat_result.st_size:
                raise ValueError
            response_status = status.HTTP_206_PARTIAL_CONTENT
            common_headers["Content-Range"] = f"bytes {start}-{end - 1}/{stat_result.st_size}"
        except (TypeError, ValueError):
            return Response(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                headers={**common_headers, "Content-Range": f"bytes */{stat_result.st_size}"},
            )

    content_length = end - start
    common_headers["Content-Length"] = str(content_length)

    async def chunks():
        remaining = content_length
        with path.open("rb") as source:
            source.seek(start)
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(chunks(), status_code=response_status, media_type=media_type, headers=common_headers)
