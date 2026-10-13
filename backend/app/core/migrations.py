"""Additive startup migration and worker-owned audit recovery for SQLite."""
from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert

from app.core.database import Base, SessionLocal, engine
from app.models.entities import AuditRun, ChatMessage, CustomerActiveSession, Job, JobStatus, User, UserRole, UserSession, utcnow


def migrate_and_recover() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for user in db.scalars(select(User).where(User.role == UserRole.CUSTOMER)):
            latest = db.scalar(select(UserSession).where(UserSession.user_id == user.id, UserSession.expires_at > utcnow()).order_by(UserSession.created_at.desc(), UserSession.session_id.desc()))
            if latest:
                db.execute(insert(CustomerActiveSession).values(user_id=user.id, session_id=latest.session_id).on_conflict_do_nothing())
        for job in db.scalars(select(Job).where(Job.status == JobStatus.SUBMITTED, Job.input_file_path == "pending")):
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            job.admin_notes = "Tải tệp đầu vào bị gián đoạn trước khi hoàn tất; cần thử lại."
        for message in db.scalars(select(ChatMessage).where(ChatMessage.status == "PENDING")):
            message.status = "FAILED"
        db.commit()


def recover_interrupted_runs() -> None:
    """Call only after acquiring the worker lock and stopping the prior worker."""
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        for job in db.scalars(select(Job).where(Job.status == JobStatus.PROCESSING)):
            latest = max(job.audit_runs, key=lambda run: run.attempt, default=None)
            if latest is None or latest.status != "RUNNING":
                job.status = JobStatus.FAILED
                job.completed_at = utcnow()
                job.admin_notes = "Không tìm thấy lượt audit đang chạy; cần admin kiểm tra và thử lại."
        for run in db.scalars(select(AuditRun).where(AuditRun.status == "RUNNING")):
            job = run.job
            latest = max(job.audit_runs, key=lambda item: item.attempt, default=None)
            matching = job.status == JobStatus.PROCESSING and latest and latest.id == run.id
            run.status = "INTERRUPTED"
            run.ended_at = utcnow()
            run.duration_seconds = None
            if matching:
                interruptions = sum(item.status == "INTERRUPTED" for item in job.audit_runs)
                if interruptions <= 1:
                    job.status = JobStatus.SUBMITTED
                    job.started_at = None
                    job.completed_at = None
                    job.admin_notes = "Audit bị gián đoạn; worker sẽ chạy lại một lần."
                else:
                    job.status = JobStatus.FAILED
                    job.completed_at = utcnow()
                    job.admin_notes = "Audit bị gián đoạn lần thứ hai; cần admin kiểm tra và thử lại."
        db.commit()
