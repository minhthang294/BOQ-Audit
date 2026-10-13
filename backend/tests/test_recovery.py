from pathlib import Path
from threading import Event
import json
import os
import time
import fcntl
from threading import Thread
from types import SimpleNamespace

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.config import get_settings
from app.core.migrations import migrate_and_recover, recover_interrupted_runs
from app.models.entities import AuditRun, Job, JobStatus, OutputType
from app.services import audit_runner
from app import worker
from test_jobs import create_job


def _job(code):
    with SessionLocal() as db:
        return db.scalar(select(Job.id).where(Job.job_code == code))


def test_pending_upload_is_not_claimed_and_ready_queue_survives_api_restart(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    job_id = _job(code)
    with SessionLocal() as db:
        db.get(Job, job_id).input_file_path = "pending"
        db.commit()
    assert audit_runner.claim_job() is None
    with SessionLocal() as db:
        db.get(Job, job_id).input_file_path = str(Path("/tmp/boq-audit-tests/jobs") / code / "input" / "missing.pdf")
        db.commit()
    assert audit_runner.claim_job() is None
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.FAILED
        assert job.completed_at is not None
        assert "tải lại" in job.admin_notes
        path = next((Path("/tmp/boq-audit-tests/jobs") / code / "input").iterdir())
        job.input_file_path = str(path)
        job.status = JobStatus.SUBMITTED
        job.completed_at = None
        job.admin_notes = None
        db.commit()
    migrate_and_recover()
    assert audit_runner.claim_job() == (job_id, 1)
    assert audit_runner.claim_job() is None


def test_interruption_requeues_once_preserves_files_and_rejects_stale_finish(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    job_id, first_id = audit_runner.claim_job()
    old_output = get_settings().jobs_dir / code / "attempts" / "1" / "output" / "BOQ_Audit_Report.xlsx"
    old_output.parent.mkdir(parents=True)
    old_output.write_bytes(b"old")
    recover_interrupted_runs()
    recover_interrupted_runs()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.SUBMITTED
        assert job.audit_runs[0].status == "INTERRUPTED"
        assert job.audit_runs[0].duration_seconds is None
    assert old_output.read_bytes() == b"old"
    assert audit_runner.claim_job() == (job_id, first_id + 1)
    assert not audit_runner._finish(job_id, first_id, 0, 0, "", [(old_output, OutputType.EXCEL_REPORT)])
    with SessionLocal() as db:
        assert db.get(Job, job_id).outputs == []
    recover_interrupted_runs()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.FAILED
        assert [run.status for run in job.audit_runs] == ["INTERRUPTED", "INTERRUPTED"]
    assert owner_client.post(f"/api/jobs/{code}/retry").status_code == 200
    assert owner_client.post(f"/api/jobs/{code}/retry").status_code == 409
    assert old_output.read_bytes() == b"old"
    assert audit_runner.claim_job() == (job_id, first_id + 2)


def test_interrupted_execution_keeps_uncommitted_attempt_private(owner_client, pdf_bytes, monkeypatch):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    job_id, run_id = audit_runner.claim_job()
    monkeypatch.setattr(audit_runner, "_execute", lambda *_: (None, True, "", []))
    audit_runner.run_audit_job(job_id, run_id, stop=Event())
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.PROCESSING
        assert job.outputs == []
        assert job.audit_runs[0].status == "RUNNING"
    recover_interrupted_runs()
    assert owner_client.get(f"/api/jobs/{code}").json()["outputs"] == []


def test_worker_health_rejects_stale_or_other_instance(tmp_path):
    lock = tmp_path / "audit-worker.lock"
    heartbeat = tmp_path / "audit-worker-heartbeat.json"
    lock.write_text("current")
    heartbeat.write_text(json.dumps({"instance": "current", "pid": os.getpid(), "updated": time.time()}))
    with lock.open("r") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        assert worker._health(heartbeat, lock) == 0
        heartbeat.write_text(json.dumps({"instance": "previous", "pid": os.getpid(), "updated": time.time()}))
        assert worker._health(heartbeat, lock) == 1
        heartbeat.write_text(json.dumps({"instance": "current", "pid": os.getpid(), "updated": time.time() - 11}))
        assert worker._health(heartbeat, lock) == 1


def test_stop_terminates_fake_codex_process_group(tmp_path, monkeypatch):
    fake = tmp_path / "fake-codex"
    fake.write_text("""#!/usr/bin/env python3
import os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path(os.environ['FAKE_CHILD_PID']).write_text(str(child.pid))
def stop(*_):
    child.wait(timeout=5)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
while True: time.sleep(0.1)
""")
    fake.chmod(0o755)
    child_pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_CHILD_PID", str(child_pid_file))
    settings = get_settings()
    skill = tmp_path / "SKILL.md"
    skill.write_text("fake skill")
    monkeypatch.setattr(settings, "boq_audit_skill_path", skill)
    monkeypatch.setattr(settings, "codex_command", str(fake))
    stop = Event()
    results = []
    job = SimpleNamespace(job_code="FAKE", input_file_path="/tmp/fake.pdf", estimate_input=None, narrative_input=None)
    thread = Thread(target=lambda: results.append(audit_runner._execute(job, 1, stop, lambda: None)))
    thread.start()
    try:
        for _ in range(100):
            if child_pid_file.exists():
                break
            time.sleep(0.05)
        assert child_pid_file.exists()
    finally:
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert results[0][1] is True
    child_pid = int(child_pid_file.read_text())
    assert not Path(f"/proc/{child_pid}").exists()
