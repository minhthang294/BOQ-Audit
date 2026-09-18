from pathlib import Path
import logging
import shutil
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import admin_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import Job, JobOutput, JobStatus, OutputType, User
from app.schemas.api import AdminJobListResponse, AdminJobResponse, AdminUpdateJob, MessageResponse, OutputResponse
from app.storage.files import EXCEL_MIMES, PDF_MIMES, random_stored_name, save_upload, stored_job_file, stream_file, validate_upload

router = APIRouter(prefix="/admin/jobs", tags=["admin"])
logger = logging.getLogger("boq-audit.admin")


def find_job(db: Session, job_code: str) -> Job:
    job = db.scalar(
        select(Job).options(selectinload(Job.outputs), selectinload(Job.user)).where(Job.job_code == job_code)
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
    query = select(Job).join(Job.user).options(selectinload(Job.outputs), selectinload(Job.user))
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


@router.get("/{job_code}/input/download")
async def download_input(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    path = stored_job_file(job.job_code, job.input_file_path)
    return stream_file(path, job.original_filename, "application/pdf")


@router.patch("/{job_code}", response_model=AdminJobResponse)
async def update_job(job_code: str, payload: AdminUpdateJob, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    values = payload.model_dump(exclude_unset=True)
    if values.get("status") == JobStatus.COMPLETED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dùng thao tác hoàn thành hồ sơ")
    for key, value in values.items():
        setattr(job, key, value)
    if payload.status == JobStatus.PROCESSING and job.started_at is None:
        from app.models.entities import utcnow
        job.started_at = utcnow()
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
    if file_type == OutputType.ANNOTATED_PDF:
        display_name, ext = validate_upload(file, {".pdf"}, PDF_MIMES)
        kind = "pdf"
    elif file_type == OutputType.EXCEL_REPORT:
        display_name, ext = validate_upload(file, {".xlsx", ".xls"}, EXCEL_MIMES)
        kind = ext.removeprefix(".")
    else:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "V1 chỉ hỗ trợ báo cáo Excel và PDF đánh dấu")
    stored_name = random_stored_name(ext)
    destination = get_settings().jobs_dir / job.job_code / "output" / stored_name
    size = await save_upload(file, destination, kind)
    try:
        old_outputs = list(db.scalars(select(JobOutput).where(JobOutput.job_id == job.id, JobOutput.file_type == file_type)).all())
        output = JobOutput(job_id=job.id, file_type=file_type, original_filename=display_name, stored_filename=stored_name, file_path=str(destination), file_size=size)
        db.add(output)
        for old in old_outputs:
            db.delete(old)
        db.commit()
        db.refresh(output)
        for old in old_outputs:
            Path(old.file_path).unlink(missing_ok=True)
        return output
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise


@router.delete("/{job_code}/outputs/{output_id}", response_model=MessageResponse)
async def delete_output(job_code: str, output_id: int, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    output = next((item for item in job.outputs if item.id == output_id), None)
    if not output:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tệp kết quả")
    path = Path(output.file_path)
    db.delete(output)
    db.commit()
    path.unlink(missing_ok=True)
    return {"message": "Đã xóa tệp kết quả"}


@router.delete("/{job_code}", response_model=MessageResponse)
async def delete_job(job_code: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
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
    job = find_job(db, job_code)
    types = {output.file_type for output in job.outputs}
    required = {OutputType.EXCEL_REPORT, OutputType.ANNOTATED_PDF}
    if not required.issubset(types):
        raise HTTPException(status.HTTP_409_CONFLICT, "Cần đủ báo cáo Excel và PDF đánh dấu trước khi hoàn thành")
    job.status = JobStatus.COMPLETED
    job.completed_at = utcnow()
    db.commit()
    return find_job(db, job_code)
