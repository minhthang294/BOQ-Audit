"""Additive startup migration and worker-owned audit recovery for SQLite."""
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.sqlite import insert

from app.core.database import Base, SessionLocal, engine
from app.models.entities import AuditRun, AuditRunCheckpoint, AuditRunGateRepairRequest, ChatMessage, CustomerActiveSession, Job, JobStatus, User, UserRole, UserSession, utcnow


def migrate_and_recover() -> None:
    Base.metadata.create_all(engine)
    revision_columns = {column["name"] for column in inspect(engine).get_columns("audit_run_batch_revisions")}
    checkpoint_columns = {column["name"] for column in inspect(engine).get_columns("audit_run_checkpoints")}
    batch_result_columns = {column["name"] for column in inspect(engine).get_columns("audit_run_batch_results")}
    with engine.begin() as connection:
        if "invalidated_check_ids_json" not in revision_columns:
            connection.execute(text("ALTER TABLE audit_run_batch_revisions ADD COLUMN invalidated_check_ids_json TEXT NOT NULL DEFAULT '[]'"))
        if "invalidation_digest" not in revision_columns:
            connection.execute(text("ALTER TABLE audit_run_batch_revisions ADD COLUMN invalidation_digest VARCHAR(64)"))
        if "lesson_snapshot_json" not in checkpoint_columns:
            connection.execute(text("ALTER TABLE audit_run_checkpoints ADD COLUMN lesson_snapshot_json TEXT"))
        if "lesson_snapshot_digest" not in checkpoint_columns:
            connection.execute(text("ALTER TABLE audit_run_checkpoints ADD COLUMN lesson_snapshot_digest VARCHAR(64)"))
        if "checker_digest" not in checkpoint_columns:
            connection.execute(text("ALTER TABLE audit_run_checkpoints ADD COLUMN checker_digest VARCHAR(64)"))
        if "contract_version" not in checkpoint_columns:
            connection.execute(text("ALTER TABLE audit_run_checkpoints ADD COLUMN contract_version INTEGER NOT NULL DEFAULT 3"))
        if "derived_from_run_id" not in checkpoint_columns:
            connection.execute(text("ALTER TABLE audit_run_checkpoints ADD COLUMN derived_from_run_id INTEGER REFERENCES audit_runs(id)"))
        if "derived_from_run_id" not in batch_result_columns:
            connection.execute(text("ALTER TABLE audit_run_batch_results ADD COLUMN derived_from_run_id INTEGER REFERENCES audit_runs(id)"))
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
        for request in db.scalars(select(AuditRunGateRepairRequest).where(AuditRunGateRepairRequest.status == "RUNNING")):
            request.status = "FAILED"
            request.error_code = "WORKER_INTERRUPTED"
            request.ended_at = utcnow()
            run = db.get(AuditRun, request.run_id)
            if run is not None and run.status == "RUNNING" and run.job.status == JobStatus.PROCESSING:
                run.status = JobStatus.WAITING_FOR_INFO.value
                run.ended_at = utcnow()
                run.job.status = JobStatus.WAITING_FOR_INFO
                run.job.admin_notes = "Lượt sửa gate bị gián đoạn; reviewer có thể thử lại nếu còn budget."
        db.flush()
        for job in db.scalars(select(Job).where(Job.status == JobStatus.PROCESSING)):
            latest = max(job.audit_runs, key=lambda run: run.attempt, default=None)
            if latest is not None and latest.status == "RUNNING" and db.get(AuditRunCheckpoint, latest.id) is not None:
                continue
            if latest is None or latest.status != "RUNNING":
                job.status = JobStatus.FAILED
                job.completed_at = utcnow()
                job.admin_notes = "Không tìm thấy lượt audit đang chạy; cần admin kiểm tra và thử lại."
        for run in db.scalars(select(AuditRun).where(AuditRun.status == "RUNNING")):
            if db.get(AuditRunCheckpoint, run.id) is not None:
                continue
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
