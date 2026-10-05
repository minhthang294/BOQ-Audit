"""Additive startup migration for the single-instance SQLite deployment."""
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from app.core.database import Base, SessionLocal, engine
from app.models.entities import AuditRun, ChatMessage, CustomerActiveSession, Job, JobStatus, User, UserRole, UserSession, utcnow


def migrate_and_recover() -> None:
    # Only new tables are introduced; existing columns and stored timestamps stay intact.
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for user in db.scalars(select(User).where(User.role == UserRole.CUSTOMER)):
            latest = db.scalar(select(UserSession).where(UserSession.user_id == user.id, UserSession.expires_at > utcnow()).order_by(UserSession.created_at.desc(), UserSession.session_id.desc()))
            if latest:
                db.execute(insert(CustomerActiveSession).values(user_id=user.id, session_id=latest.session_id).on_conflict_do_nothing())
        for run in db.scalars(select(AuditRun).where(AuditRun.status == "RUNNING")):
            run.status = "INTERRUPTED"
            run.ended_at = utcnow()
            run.duration_seconds = None
        for job in db.scalars(select(Job).where(Job.status.in_((JobStatus.SUBMITTED, JobStatus.PROCESSING)))):
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            job.admin_notes = "Tiến trình bị gián đoạn khi backend khởi động lại; cần thử lại. Thời gian AI chưa đo đủ."
        for message in db.scalars(select(ChatMessage).where(ChatMessage.status == "PENDING")):
            message.status = "FAILED"
        db.commit()
