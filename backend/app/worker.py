"""Single-process audit worker. Run with python -m app.worker."""
import fcntl
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.migrations import recover_interrupted_runs
from app.models.entities import AuditRun, AuditRunCheckpoint, AuditRunGateRepairRequest, Job, JobStatus, utcnow
from app.services.audit_runner import claim_job, run_audit_job, run_checkpoint_repair_request

logger = logging.getLogger("boq-audit.worker")
STOP = Event()


def _signal(_number, _frame):
    STOP.set()


def _health(heartbeat_path: Path, lock_path: Path) -> int:
    try:
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        instance = lock_path.read_text(encoding="utf-8").strip()
        if heartbeat["instance"] != instance or abs(time.time() - heartbeat["updated"]) > 10:
            return 1
        with lock_path.open("r") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                return 1
        os.kill(heartbeat["pid"], 0)
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        return 1


def _next_checkpoint_run() -> tuple[int, int] | None:
    if not get_settings().audit_checkpoint_worker_enabled:
        return None
    with SessionLocal() as db:
        jobs = db.scalars(select(Job).where(Job.status == JobStatus.PROCESSING).order_by(Job.created_at, Job.id))
        for job in jobs:
            latest = max(job.audit_runs, key=lambda run: run.attempt, default=None)
            checkpoint = db.get(AuditRunCheckpoint, latest.id) if latest and latest.status == "RUNNING" else None
            if checkpoint is not None and checkpoint.status in {"READY", "RUNNING"}:
                return job.id, latest.id
    return None


def _claim_repair_request() -> int | None:
    if not get_settings().audit_checkpoint_worker_enabled:
        return None
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        requests = db.scalars(select(AuditRunGateRepairRequest).where(AuditRunGateRepairRequest.status == "REQUESTED").order_by(AuditRunGateRepairRequest.id)).all()
        for request in requests:
            run = db.get(AuditRun, request.run_id)
            latest = db.scalar(select(AuditRun.id).where(AuditRun.job_id == run.job_id).order_by(AuditRun.attempt.desc()).limit(1)) if run else None
            if run is None or latest != run.id or run.status != JobStatus.WAITING_FOR_INFO.value or run.job.status != JobStatus.WAITING_FOR_INFO:
                request.status = "FAILED"
                request.error_code = "STALE_JOB"
                request.ended_at = utcnow()
                continue
            request.status = "RUNNING"
            request.started_at = utcnow()
            run.status = "RUNNING"
            run.ended_at = None
            run.duration_seconds = None
            run.job.status = JobStatus.PROCESSING
            run.job.admin_notes = "Đang sửa batch theo blocker stage gate trên cùng lượt audit."
            db.commit()
            return request.id
        db.commit()
    return None


def main() -> int:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = settings.data_dir / "audit-worker.lock"
    heartbeat_path = settings.data_dir / "audit-worker-heartbeat.json"
    if len(sys.argv) > 1 and sys.argv[1] == "--health":
        return _health(heartbeat_path, lock_path)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.error("audit worker lock already held: %s", lock_path)
            return 1
        instance = uuid4().hex
        lock.seek(0)
        lock.truncate()
        lock.write(instance)
        lock.flush()
        os.fsync(lock.fileno())
        signal.signal(signal.SIGTERM, _signal)
        signal.signal(signal.SIGINT, _signal)

        def heartbeat():
            temporary = heartbeat_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"instance": instance, "pid": os.getpid(), "updated": time.time()}), encoding="utf-8")
            temporary.replace(heartbeat_path)

        heartbeat()
        recover_interrupted_runs()
        while not STOP.is_set():
            heartbeat()
            repair_id = _claim_repair_request()
            if repair_id is not None:
                run_checkpoint_repair_request(repair_id, stop=STOP, heartbeat=heartbeat)
                continue
            claim = _next_checkpoint_run() or claim_job()
            if claim:
                run_audit_job(*claim, stop=STOP, heartbeat=heartbeat)
            else:
                STOP.wait(2)
        return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
