"""Admin APIs for private, reviewer-assigned audit lessons."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import admin_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import User, UserRole
from app.schemas.api import (
    AdminLessonAssignmentRequest,
    AdminLessonCandidateRequest,
    AdminLessonRetireRequest,
    AdminLessonReviewRequest,
)
from app.services.audit_lessons import (
    LessonAuthorizationError,
    LessonConflict,
    LessonCorrupt,
    LessonNotFound,
    LessonStateError,
    LessonStore,
    LessonStoreError,
)

router = APIRouter(prefix="/admin/lessons", tags=["admin lessons"])


def _store() -> LessonStore:
    return LessonStore()


def _reviewer_allowlist() -> set[str]:
    return get_settings().audit_lesson_reviewer_email_set


def _configured_admin(db: Session, email: str) -> bool:
    reviewer = email.strip().lower()
    return bool(
        reviewer in _reviewer_allowlist()
        and db.scalar(select(User.id).where(
            func.lower(User.email) == reviewer,
            User.role == UserRole.ADMIN,
            User.is_active.is_(True),
        )) is not None
    )


def _load_private_lesson(store: LessonStore, lesson_id: str, version: int, actor_email: str) -> dict[str, Any]:
    try:
        record = store.get_version(lesson_id, version)
        events = store.history(lesson_id, version)
    except LessonNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lesson không tồn tại") from exc
    except (LessonStoreError, OSError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Lesson lưu trữ lỗi hoặc không hợp lệ") from exc
    email = actor_email.strip().lower()
    creator = str(events[0].get("actor_id", "")).strip().lower() if events else ""
    designated = str(record.get("designated_reviewer_id") or "").strip().lower()
    if record.get("visibility_scope") != "private" or email not in {creator, designated}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lesson không tồn tại")
    return record


def _handle_store_error(exc: LessonStoreError) -> HTTPException:
    if isinstance(exc, LessonNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Lesson không tồn tại")
    if isinstance(exc, LessonAuthorizationError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, (LessonConflict, LessonStateError)):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("")
async def list_admin_lessons(
    lesson_status: Literal["CANDIDATE", "VERIFIED", "RETIRED", "REJECTED"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(admin_user),
):
    store = _store()
    visible = []
    try:
        for lesson_id in store.lesson_ids():
            for record in reversed(store.list_versions(lesson_id)):
                try:
                    visible.append(_load_private_lesson(store, lesson_id, record["version"], admin.email))
                except HTTPException as exc:
                    if exc.status_code != status.HTTP_404_NOT_FOUND:
                        raise
    except (LessonStoreError, OSError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Lesson store không đọc được") from exc
    if lesson_status:
        visible = [item for item in visible if item["status"] == lesson_status]
    visible.sort(key=lambda item: (item["lesson_id"], item["version"]), reverse=True)
    return {"items": visible[:limit], "total": len(visible)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_admin_lesson_candidate(
    body: AdminLessonCandidateRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    designated = body.designated_reviewer_email.strip().lower() if body.designated_reviewer_email else None
    if designated and not _configured_admin(db, designated):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Người duyệt phải là admin đang hoạt động trong AUDIT_LESSON_REVIEWER_EMAILS")
    try:
        return _store().create_candidate(
            error_class=body.error_class,
            applicability_check_kinds=body.applicability_check_kinds,
            applicability_target_ids=body.applicability_target_ids,
            trigger_conditions=body.trigger_conditions,
            required_checks=body.required_checks,
            counterexamples=body.counterexamples,
            source_case_ids=body.source_case_ids,
            evidence_refs=body.evidence_refs,
            project_scope=body.project_scope,
            visibility_scope="private",
            actor_id=admin.email.strip().lower(),
            designated_reviewer_id=designated,
        )
    except (LessonStoreError, OSError) as exc:
        if isinstance(exc, LessonStoreError):
            raise _handle_store_error(exc) from exc
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Không lưu được lesson candidate") from exc


@router.get("/{lesson_id}/versions/{version}")
async def get_admin_lesson_version(lesson_id: str, version: int, admin: User = Depends(admin_user)):
    if version < 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lesson không tồn tại")
    return _load_private_lesson(_store(), lesson_id, version, admin.email)


@router.post("/{lesson_id}/versions/{version}/assignment")
async def assign_admin_lesson_reviewer(
    lesson_id: str,
    version: int,
    body: AdminLessonAssignmentRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    if version < 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lesson không tồn tại")
    store = _store()
    _load_private_lesson(store, lesson_id, version, admin.email)
    designated = body.designated_reviewer_email.strip().lower()
    if not _configured_admin(db, designated):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Người duyệt phải là admin đang hoạt động trong AUDIT_LESSON_REVIEWER_EMAILS")
    try:
        return store.assign_reviewer(lesson_id, version, designated_reviewer_id=designated, assigned_by=admin.email.strip().lower())
    except (LessonStoreError, OSError) as exc:
        if isinstance(exc, LessonStoreError):
            raise _handle_store_error(exc) from exc
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Không lưu được assignment") from exc


@router.post("/{lesson_id}/versions/{version}/review")
async def review_admin_lesson(
    lesson_id: str,
    version: int,
    body: AdminLessonReviewRequest,
    admin: User = Depends(admin_user),
):
    if version < 1 or admin.email.strip().lower() not in _reviewer_allowlist():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Lesson review chỉ dành cho reviewer được cấu hình")
    store = _store()
    record = _load_private_lesson(store, lesson_id, version, admin.email)
    try:
        action = store.verify if body.result == "VERIFIED" else store.reject
        return action(
            lesson_id,
            version,
            reviewer_id=admin.email.strip().lower(),
            designated_reviewer_id=str(record.get("designated_reviewer_id") or ""),
            evidence_refs=body.evidence_refs,
            reason=body.reason,
        )
    except (LessonStoreError, OSError) as exc:
        if isinstance(exc, LessonStoreError):
            raise _handle_store_error(exc) from exc
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Không lưu được quyết định review") from exc


@router.post("/{lesson_id}/versions/{version}/retire")
async def retire_admin_lesson(
    lesson_id: str,
    version: int,
    body: AdminLessonRetireRequest,
    admin: User = Depends(admin_user),
):
    if version < 1 or admin.email.strip().lower() not in _reviewer_allowlist():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Thu hồi lesson chỉ dành cho reviewer được cấu hình")
    store = _store()
    record = _load_private_lesson(store, lesson_id, version, admin.email)
    try:
        return store.retire(lesson_id, version, actor_id=admin.email.strip().lower(), reason=body.reason)
    except (LessonStoreError, OSError) as exc:
        if isinstance(exc, LessonStoreError):
            raise _handle_store_error(exc) from exc
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Không lưu được quyết định thu hồi") from exc
