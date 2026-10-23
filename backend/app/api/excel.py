import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import PersistentRateLimiter
from app.models.entities import ExcelConversation, ExcelWorkbook, User
from app.schemas.api import (
    ExcelExplainRequest,
    ExcelExplainResponse,
    ExcelSheetResponse,
    ExcelWorkbookResponse,
)
from app.services import excel_explain
from app.storage.files import EXCEL_MIMES, save_upload, validate_upload

router = APIRouter(prefix="/excel", tags=["excel"])
logger = logging.getLogger("boq-audit.excel")
settings = get_settings()
explain_limiter = PersistentRateLimiter(
    "excel-explain",
    settings.excel_explain_rate_limit,
    settings.excel_explain_rate_window_seconds,
)


def _workbook_response(workbook: ExcelWorkbook, meta: dict) -> ExcelWorkbookResponse:
    return ExcelWorkbookResponse(
        code=workbook.code,
        filename=workbook.original_filename,
        file_size=workbook.file_size,
        file_type=Path(workbook.file_path).suffix.lstrip(".").lower(),
        sheets=meta["sheets"],
        active_sheet=meta["active_sheet"],
        created_at=workbook.created_at,
    )


def _created_at_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _expired(workbook: ExcelWorkbook) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.excel_explain_retention_hours)
    return _created_at_utc(workbook.created_at) < cutoff


def _remove_folder(workbook: ExcelWorkbook) -> None:
    root = settings.excel_dir.resolve()
    parent = Path(workbook.file_path).resolve().parent
    if parent.is_relative_to(root):
        shutil.rmtree(parent, ignore_errors=True)


def _delete_workbook(db: Session, workbook: ExcelWorkbook) -> None:
    _remove_folder(workbook)
    db.delete(workbook)
    db.commit()


def _cleanup_expired(db: Session, user_id: int) -> None:
    items = db.scalars(select(ExcelWorkbook).where(ExcelWorkbook.user_id == user_id)).all()
    for workbook in items:
        if _expired(workbook):
            _delete_workbook(db, workbook)


def _owned_workbook(db: Session, code: str, user: User) -> ExcelWorkbook:
    workbook = db.scalar(select(ExcelWorkbook).where(ExcelWorkbook.code == code, ExcelWorkbook.user_id == user.id))
    if not workbook:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy file Excel đã tải lên.")
    if _expired(workbook):
        _delete_workbook(db, workbook)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File Excel đã hết thời gian lưu tạm. Vui lòng tải lại.")
    return workbook


@router.post("/upload", response_model=ExcelWorkbookResponse, status_code=status.HTTP_201_CREATED)
async def upload_excel(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    filename, extension = validate_upload(file, {".xlsx", ".xlsm"}, EXCEL_MIMES)
    _cleanup_expired(db, user.id)
    code = "XL-" + uuid4().hex[:16].upper()
    folder = settings.excel_dir / code
    path = folder / f"input{extension}"
    try:
        kind = "xlsx" if extension in {".xlsx", ".xlsm"} else extension[1:]
        size = await save_upload(file, path, kind, max_mb=settings.excel_explain_max_mb)
        meta = await run_in_threadpool(excel_explain.inspect_workbook, path)
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except excel_explain.ExcelExplainError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        logger.exception("excel_upload_failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Không xử lý được file Excel. Vui lòng thử lại.") from None

    workbook = ExcelWorkbook(
        code=code,
        user_id=user.id,
        original_filename=filename,
        stored_filename=path.name,
        file_path=str(path.resolve()),
        file_size=size,
        sheet_count=len(meta["sheets"]),
    )
    db.add(workbook)
    db.commit()
    db.refresh(workbook)
    logger.info("excel_uploaded code=%s user_id=%s bytes=%s type=%s", code, user.id, size, extension)
    return _workbook_response(workbook, meta)


@router.get("/{code}", response_model=ExcelWorkbookResponse)
def get_excel(
    code: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    workbook = _owned_workbook(db, code, user)
    try:
        path = excel_explain.excel_path(workbook)
        meta = excel_explain.inspect_workbook(path)
    except excel_explain.ExcelExplainError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _workbook_response(workbook, meta)


@router.get("/{code}/sheet", response_model=ExcelSheetResponse)
def get_sheet(
    code: str,
    name: str = Query(min_length=1, max_length=100),
    max_rows: int = Query(default=60, ge=1, le=200),
    max_cols: int = Query(default=20, ge=1, le=100),
    row_start: int = Query(default=1, ge=1),
    col_start: int = Query(default=1, ge=1),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    workbook = _owned_workbook(db, code, user)
    try:
        return excel_explain.preview_sheet(
            excel_explain.excel_path(workbook),
            name,
            max_rows=max_rows,
            max_cols=max_cols,
            row_start=row_start,
            col_start=col_start,
        )
    except excel_explain.ExcelExplainError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/{code}/explain", response_model=ExcelExplainResponse)
async def explain_excel(
    code: str,
    payload: ExcelExplainRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    workbook = _owned_workbook(db, code, user)
    conversation = db.get(ExcelConversation, workbook.id)
    if conversation is None:
        conversation = ExcelConversation(workbook_id=workbook.id, user_id=user.id)
    elif conversation.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy hội thoại Excel.")
    if not explain_limiter.consume(db, str(user.id)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Bạn đã gửi quá nhiều câu hỏi. Vui lòng thử lại sau.",
            headers={"Retry-After": str(explain_limiter.window_seconds)},
        )
    try:
        result = await run_in_threadpool(
            excel_explain.explain,
            workbook,
            user.id,
            payload.sheet,
            payload.scope,
            payload.selection,
            payload.question,
            [turn.model_dump() for turn in payload.history],
            conversation.thread_id,
        )
    except excel_explain.ExcelAIBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except excel_explain.ExcelAIUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except excel_explain.ExcelExplainError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except Exception:
        logger.exception("excel_explain_failed code=%s user_id=%s", code, user.id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Không tạo được giải thích. Vui lòng thử lại.") from None
    conversation.thread_id = result.pop("thread_id")
    db.add(conversation)
    db.commit()
    return ExcelExplainResponse(**result)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_excel(
    code: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    workbook = _owned_workbook(db, code, user)
    _delete_workbook(db, workbook)
    return None
