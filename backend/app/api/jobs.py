import logging
import shutil
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import Job, JobStatus, User, UserRole, utcnow
from app.schemas.api import CustomerUpdateJob, JobListResponse, JobResponse, MessageResponse, OutputResponse
from app.storage.files import PDF_MIMES, random_stored_name, save_upload, stored_job_file, stream_file, validate_upload

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger("boq-audit.jobs")
MAX_ACTIVE_JOBS_PER_USER = 2
ACTIVE_JOB_STATUSES = (
    JobStatus.SUBMITTED,
    JobStatus.PROCESSING,
    JobStatus.WAITING_FOR_INFO,
    JobStatus.REVIEW,
)


def owned_job(db: Session, job_code: str, user: User) -> Job:
    job = db.scalar(
        select(Job).options(selectinload(Job.outputs)).where(Job.job_code == job_code, Job.user_id == user.id)
    )
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy hồ sơ")
    return job


def visible_customer_job(job: Job) -> JobResponse:
    response = JobResponse.model_validate(job)
    if job.status != JobStatus.COMPLETED:
        return response.model_copy(update={"outputs": []})
    return response


def output_access_job(db: Session, job_code: str, user: User) -> Job:
    if user.role == UserRole.ADMIN:
        job = db.scalar(select(Job).options(selectinload(Job.outputs)).where(Job.job_code == job_code))
        if not job:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy hồ sơ")
        return job
    job = owned_job(db, job_code, user)
    if job.status != JobStatus.COMPLETED:
        # Do not disclose whether draft output identifiers exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tệp kết quả")
    return job


@router.get("", response_model=JobListResponse)
async def list_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Job).options(selectinload(Job.outputs)).where(Job.user_id == user.id).order_by(Job.created_at.desc())
    items = list(db.scalars(query).all())
    return {"items": [visible_customer_job(item) for item in items], "total": len(items)}


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    project_name: str = Form(min_length=1, max_length=240),
    file: UploadFile = File(),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    display_name, ext = validate_upload(file, {".pdf"}, PDF_MIMES)
    clean_project_name = project_name.strip()
    if not clean_project_name:
        file.file.close()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Tên công trình không được để trống")
    # SQLite không có row-level lock. BEGIN IMMEDIATE tuần tự hóa đoạn kiểm tra
    # quota + tạo job, ngăn hai upload đồng thời cùng vượt giới hạn.
    if db.bind and db.bind.dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    active_jobs = db.scalar(
        select(func.count(Job.id)).where(
            Job.user_id == user.id,
            Job.status.in_(ACTIVE_JOB_STATUSES),
        )
    ) or 0
    if active_jobs >= MAX_ACTIVE_JOBS_PER_USER:
        db.rollback()
        file.file.close()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Bạn đang có 2 hồ sơ được xử lý. Vui lòng chờ một hồ sơ hoàn thành trước khi tạo hồ sơ mới.",
        )
    job = Job(
        user_id=user.id,
        project_name=clean_project_name,
        description=None,
        original_filename=display_name,
        input_file_path="pending",
        status=JobStatus.PROCESSING,
        started_at=utcnow(),
    )
    db.add(job)
    db.flush()
    job.job_code = f"BOQ-{job.id:06d}"
    job_id = job.id
    job_code = job.job_code
    destination = get_settings().jobs_dir / job_code / "input" / random_stored_name(ext)
    # End the quota/create transaction before copying a potentially 500 MB file.
    db.commit()
    try:
        await save_upload(file, destination, "pdf")
        persisted_job = db.get(Job, job_id)
        if not persisted_job:
            raise RuntimeError("Job disappeared while storing its input file")
        persisted_job.input_file_path = str(destination)
        db.commit()
        db.refresh(persisted_job)
        return persisted_job
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        incomplete_job = db.get(Job, job_id)
        if incomplete_job:
            db.delete(incomplete_job)
            db.commit()
        raise


@router.patch("/{job_code}", response_model=JobResponse)
async def rename_job(
    job_code: str,
    payload: CustomerUpdateJob,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    job = owned_job(db, job_code, user)
    job.project_name = payload.project_name
    db.commit()
    return visible_customer_job(owned_job(db, job_code, user))


@router.delete("/{job_code}", response_model=MessageResponse)
async def delete_job(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = owned_job(db, job_code, user)
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
            logger.exception("customer_job_files_cleanup_failed user_id=%s job_code=%s", user.id, job_code)
    logger.info("customer_job_deleted user_id=%s job_code=%s", user.id, job_code)
    return {"message": "Đã xóa hồ sơ"}


@router.get("/{job_code}", response_model=JobResponse)
async def get_job(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return visible_customer_job(owned_job(db, job_code, user))


@router.get("/{job_code}/input/download")
async def download_input(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = owned_job(db, job_code, user)
    path = stored_job_file(job.job_code, job.input_file_path)
    return stream_file(path, job.original_filename, "application/pdf")


@router.get("/{job_code}/input/view")
async def view_input(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = owned_job(db, job_code, user)
    path = stored_job_file(job.job_code, job.input_file_path)
    return stream_file(path, job.original_filename, "application/pdf", inline=True)


@router.get("/{job_code}/outputs", response_model=list[OutputResponse])
async def list_outputs(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != UserRole.ADMIN:
        job = owned_job(db, job_code, user)
        return job.outputs if job.status == JobStatus.COMPLETED else []
    return output_access_job(db, job_code, user).outputs


@router.get("/{job_code}/outputs/{output_id}/download")
async def download_output(job_code: str, output_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = output_access_job(db, job_code, user)
    output = next((item for item in job.outputs if item.id == output_id), None)
    if not output:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tệp kết quả")
    path = stored_job_file(job.job_code, output.file_path)
    return stream_file(path, output.original_filename)


@router.get("/{job_code}/outputs/{output_id}/view")
async def view_output(job_code: str, output_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = output_access_job(db, job_code, user)
    output = next((item for item in job.outputs if item.id == output_id), None)
    if not output or output.file_type.value != "ANNOTATED_PDF":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy PDF đánh dấu")
    path = stored_job_file(job.job_code, output.file_path)
    return stream_file(path, output.original_filename, "application/pdf", inline=True)
