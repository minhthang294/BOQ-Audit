from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import Job, JobOutput, JobStatus, User, utcnow
from app.schemas.api import JobListResponse, JobResponse, OutputResponse
from app.storage.files import PDF_MIMES, random_stored_name, save_upload, stream_file, validate_upload

router = APIRouter(prefix="/jobs", tags=["jobs"])


def owned_job(db: Session, job_code: str, user: User) -> Job:
    job = db.scalar(
        select(Job).options(selectinload(Job.outputs)).where(Job.job_code == job_code, Job.user_id == user.id)
    )
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy hồ sơ")
    return job


@router.get("", response_model=JobListResponse)
async def list_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Job).options(selectinload(Job.outputs)).where(Job.user_id == user.id).order_by(Job.created_at.desc())
    items = list(db.scalars(query).all())
    return {"items": items, "total": len(items)}


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    project_name: str = Form(min_length=1, max_length=240),
    file: UploadFile = File(),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    display_name, ext = validate_upload(file, {".pdf"}, PDF_MIMES)
    job = Job(
        user_id=user.id,
        project_name=project_name.strip(),
        description=None,
        original_filename=display_name,
        input_file_path="pending",
        status=JobStatus.PROCESSING,
        started_at=utcnow(),
    )
    if not job.project_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Tên công trình không được để trống")
    db.add(job)
    db.flush()
    job.job_code = f"BOQ-{job.id:06d}"
    destination = get_settings().jobs_dir / job.job_code / "input" / random_stored_name(ext)
    try:
        await save_upload(file, destination, "pdf")
        job.input_file_path = str(destination)
        db.commit()
        db.refresh(job)
        return job
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise


@router.get("/{job_code}", response_model=JobResponse)
async def get_job(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return owned_job(db, job_code, user)


@router.get("/{job_code}/input/download")
async def download_input(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = owned_job(db, job_code, user)
    path = Path(job.input_file_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tệp hồ sơ không tồn tại")
    return stream_file(path, job.original_filename, "application/pdf")


@router.get("/{job_code}/input/view")
async def view_input(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = owned_job(db, job_code, user)
    path = Path(job.input_file_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tệp hồ sơ không tồn tại")
    return stream_file(path, job.original_filename, "application/pdf", inline=True)


@router.get("/{job_code}/outputs", response_model=list[OutputResponse])
async def list_outputs(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return owned_job(db, job_code, user).outputs


@router.get("/{job_code}/outputs/{output_id}/download")
async def download_output(job_code: str, output_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = owned_job(db, job_code, user)
    output = next((item for item in job.outputs if item.id == output_id), None)
    if not output:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tệp kết quả")
    path = Path(output.file_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tệp kết quả không tồn tại")
    return stream_file(path, output.original_filename)
