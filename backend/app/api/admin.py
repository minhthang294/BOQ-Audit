from pathlib import Path
import logging
import shutil
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, selectinload

from app.api.deps import admin_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import Job, JobOutput, JobStatus, OutputType, User
from app.schemas.api import AdminJobListResponse, AdminJobResponse, AdminUpdateJob, AttemptArtifactsResponse, MessageResponse, OutputResponse
from app.storage.files import EXCEL_MIMES, PDF_MIMES, random_stored_name, save_upload, stored_job_file, stream_file, validate_upload

router = APIRouter(prefix="/admin/jobs", tags=["admin"])
logger = logging.getLogger("boq-audit.admin")
BUSY_STATUSES = {JobStatus.SUBMITTED, JobStatus.PROCESSING}
ARTIFACT_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".json", ".md"}
PRIVATE_NAMES = ("log", "draft", "stdout", "stderr", "codex")


def _lock_job(db: Session, job_code: str) -> Job:
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    return find_job(db, job_code)


def _artifacts(job_code: str, output_dir: Path) -> list[dict]:
    if not output_dir.is_dir() or not output_dir.resolve().is_relative_to((get_settings().jobs_dir / job_code).resolve()):
        return []
    root = output_dir.resolve()
    files = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        relative = path.relative_to(output_dir)
        if path.suffix.lower() not in ARTIFACT_EXTENSIONS or any(any(word in part.lower() for word in PRIVATE_NAMES) for part in relative.parts):
            continue
        files.append({"name": relative.as_posix(), "size": path.stat().st_size})
    return files


def _artifact_path(job_code: str, output_dir: Path, name: str) -> Path:
    root = output_dir.resolve()
    if not root.is_relative_to((get_settings().jobs_dir / job_code).resolve()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tệp không tồn tại")
    path = stored_job_file(job_code, str(output_dir / name))
    if not path.is_relative_to(root) or path.suffix.lower() not in ARTIFACT_EXTENSIONS or any(any(word in part.lower() for word in PRIVATE_NAMES) for part in Path(name).parts):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tệp không tồn tại")
    return path


def find_job(db: Session, job_code: str) -> Job:
    job = db.scalar(
        select(Job)
        .options(selectinload(Job.outputs), selectinload(Job.estimate_input), selectinload(Job.narrative_input), selectinload(Job.user))
        .where(Job.job_code == job_code)
    )
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy hồ sơ")
    return job


@router.get("", response_model=AdminJobListResponse)
async def list_admin_jobs(
    search: str | None = Query(default=None, max_length=200),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    query = select(Job).join(Job.user).options(
        selectinload(Job.outputs), selectinload(Job.estimate_input), selectinload(Job.narrative_input), selectinload(Job.user)
    )
    if search:
        needle = f"%{search.strip()}%"
        query = query.where(or_(Job.job_code.ilike(needle), Job.project_name.ilike(needle), User.name.ilike(needle), User.email.ilike(needle)))
    if job_status:
        query = query.where(Job.status == job_status)
    items = list(db.scalars(query.order_by(Job.created_at.desc())).all())
    return {"items": items, "total": len(items)}


@router.get("/{job_code}", response_model=AdminJobResponse)
async def get_admin_job(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    return find_job(db, job_code)


@router.get("/{job_code}/attempts", response_model=AttemptArtifactsResponse)
async def list_attempts(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    root = get_settings().jobs_dir / job.job_code
    return {
        "attempts": [{"attempt": run.attempt, "status": run.status, "artifacts": _artifacts(job.job_code, root / "attempts" / str(run.attempt) / "output")} for run in job.audit_runs],
        "legacy": _artifacts(job.job_code, root / "output"),
    }


@router.get("/{job_code}/attempts/{attempt}/artifacts/{name:path}")
async def download_attempt_artifact(job_code: str, attempt: int, name: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    if attempt < 1 or not any(run.attempt == attempt for run in job.audit_runs):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy lượt xử lý")
    path = _artifact_path(job.job_code, get_settings().jobs_dir / job.job_code / "attempts" / str(attempt) / "output", name)
    return stream_file(path, path.name)


@router.get("/{job_code}/legacy/artifacts/{name:path}")
async def download_legacy_artifact(job_code: str, name: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    path = _artifact_path(job.job_code, get_settings().jobs_dir / job.job_code / "output", name)
    return stream_file(path, path.name)


@router.get("/{job_code}/input/download")
async def download_input(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    path = stored_job_file(job.job_code, job.input_file_path)
    return stream_file(path, job.original_filename, "application/pdf")


@router.get("/{job_code}/estimate/download")
async def download_estimate(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    if not job.estimate_input:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hồ sơ không có tệp dự toán")
    path = stored_job_file(job.job_code, job.estimate_input.file_path)
    return stream_file(path, job.estimate_input.original_filename)


@router.get("/{job_code}/narrative/download")
async def download_narrative(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    if not job.narrative_input:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hồ sơ không có tệp thuyết minh")
    path = stored_job_file(job.job_code, job.narrative_input.file_path)
    return stream_file(path, job.narrative_input.original_filename)


@router.patch("/{job_code}", response_model=AdminJobResponse)
async def update_job(job_code: str, payload: AdminUpdateJob, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = _lock_job(db, job_code)
    values = payload.model_dump(exclude_unset=True)
    if values.get("status") in {JobStatus.SUBMITTED, JobStatus.PROCESSING}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Trạng thái này do worker quản lý")
    if job.status == JobStatus.PROCESSING and "status" in values:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể đổi trạng thái khi đang xử lý")
    if values.get("status") == JobStatus.COMPLETED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dùng thao tác hoàn thành hồ sơ")
    for key, value in values.items():
        setattr(job, key, value)
    if payload.status is not None:
        from app.models.entities import utcnow
        job.completed_at = utcnow() if payload.status == JobStatus.FAILED else None
    db.commit()
    return find_job(db, job_code)


@router.post("/{job_code}/outputs", response_model=OutputResponse, status_code=status.HTTP_201_CREATED)
async def upload_output(
    job_code: str,
    file_type: OutputType = Form(),
    file: UploadFile = File(),
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    job = find_job(db, job_code)
    if job.status in BUSY_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể sửa kết quả khi hồ sơ đang chờ hoặc xử lý")
    job_id, job_code_value = job.id, job.job_code
    has_estimate = job.estimate_input is not None
    db.rollback()
    if file_type == OutputType.ANNOTATED_PDF:
        display_name, ext = validate_upload(file, {".pdf"}, PDF_MIMES)
        kind = "pdf"
    elif file_type in {OutputType.EXCEL_REPORT, OutputType.ESTIMATE_REPORT}:
        if file_type == OutputType.ESTIMATE_REPORT and not has_estimate:
            file.file.close()
            raise HTTPException(status.HTTP_409_CONFLICT, "Khách hàng không gửi kèm dự toán")
        display_name, ext = validate_upload(file, {".xlsx", ".xls"}, EXCEL_MIMES)
        kind = ext.removeprefix(".")
    else:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Chỉ hỗ trợ báo cáo Excel và PDF đánh dấu")
    stored_name = random_stored_name(ext)
    destination = get_settings().jobs_dir / job_code_value / "output" / stored_name
    size = await save_upload(file, destination, kind)
    try:
        job = _lock_job(db, job_code_value)
        if job.status in BUSY_STATUSES:
            raise HTTPException(status.HTTP_409_CONFLICT, "Không thể sửa kết quả khi hồ sơ đang chờ hoặc xử lý")
        old_outputs = list(db.scalars(select(JobOutput).where(JobOutput.job_id == job_id, JobOutput.file_type == file_type)).all())
        output = JobOutput(job_id=job_id, file_type=file_type, original_filename=display_name, stored_filename=stored_name, file_path=str(destination), file_size=size)
        db.add(output)
        for old in old_outputs:
            db.delete(old)
        db.commit()
        db.refresh(output)
        for old in old_outputs:
            if "attempts" not in Path(old.file_path).parts:
                Path(old.file_path).unlink(missing_ok=True)
        return output
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise


@router.delete("/{job_code}/outputs/{output_id}", response_model=MessageResponse)
async def delete_output(job_code: str, output_id: int, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = _lock_job(db, job_code)
    if job.status in BUSY_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể sửa kết quả khi hồ sơ đang chờ hoặc xử lý")
    output = next((item for item in job.outputs if item.id == output_id), None)
    if not output:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tệp kết quả")
    path = Path(output.file_path)
    db.delete(output)
    db.commit()
    if "attempts" not in path.parts:
        path.unlink(missing_ok=True)
    return {"message": "Đã xóa tệp kết quả"}


@router.delete("/{job_code}", response_model=MessageResponse)
async def delete_job(job_code: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = _lock_job(db, job_code)
    if job.status == JobStatus.PROCESSING or job.input_file_path == "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể xóa hồ sơ đang lưu hoặc xử lý")
    jobs_root = get_settings().jobs_dir.resolve()
    job_directory = (jobs_root / job.job_code).resolve()
    if job_directory.parent != jobs_root:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Đường dẫn hồ sơ không hợp lệ")

    quarantined_directory = jobs_root / f".deleting-{job.job_code}-{uuid4().hex}"
    moved = False
    try:
        if job_directory.exists():
            job_directory.rename(quarantined_directory)
            moved = True
        db.delete(job)
        db.commit()
    except Exception:
        db.rollback()
        if moved and quarantined_directory.exists():
            quarantined_directory.rename(job_directory)
        raise

    if moved:
        try:
            shutil.rmtree(quarantined_directory)
        except OSError:
            logger.exception("job_files_cleanup_failed admin_user_id=%s job_code=%s", admin.id, job_code)
    logger.info("job_deleted admin_user_id=%s job_code=%s", admin.id, job_code)
    return {"message": "Đã xóa hồ sơ"}


@router.post("/{job_code}/complete", response_model=AdminJobResponse)
async def complete_job(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    from app.models.entities import utcnow
    job = _lock_job(db, job_code)
    if job.status in BUSY_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể công bố hồ sơ đang chờ hoặc xử lý")
    types = {output.file_type for output in job.outputs}
    required = {OutputType.EXCEL_REPORT, OutputType.ANNOTATED_PDF}
    if job.estimate_input:
        required.add(OutputType.ESTIMATE_REPORT)
    if not required.issubset(types):
        message = (
            "Cần đủ báo cáo BOQ, báo cáo dự toán và PDF đánh dấu trước khi hoàn thành"
            if job.estimate_input
            else "Cần đủ báo cáo Excel và PDF đánh dấu trước khi hoàn thành"
        )
        raise HTTPException(status.HTTP_409_CONFLICT, message)
    if job.status != JobStatus.COMPLETED:
        job.completed_at = utcnow()
    job.status = JobStatus.COMPLETED
    db.commit()
    return find_job(db, job_code)
