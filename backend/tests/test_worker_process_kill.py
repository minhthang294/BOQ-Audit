"""Kill a real worker process mid-batch and verify restart recovery.

The worker subprocess, its SQLite database, data directory, and fake model command all live
inside one pytest tmp directory. The test only signals PIDs it created itself.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import AuditInventoryApproval, AuditRun, AuditRunBatchResult, AuditRunCheckpoint, Job, JobStatus, User, UserRole
from app.services import audit_runner

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
PINNED_SKILL = REPO / ".orchestration" / "skill" / "SKILL.md"

FAKE_CODEX = '''
import json
import os
import sys
import time
from pathlib import Path

work = Path.cwd() / "work"
context = json.loads((work / "batch_context.json").read_text(encoding="utf-8"))
sys.stdout.write(json.dumps({"type": "agent_message", "text": "synthetic batch"}) + "\\n")
sys.stdout.flush()
if context.get("check_ids") == ["child"]:
    (work / "child.started").write_text("1", encoding="utf-8")
    (work / "child.pid").write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(120)
    raise SystemExit(1)
patch = {"items": [{"check_id": "root", "result_status": "CHECKED", "evidence_ids": ["ev-root"]}]}
evidence = {"evidence": [{
    "evidence_id": "ev-root", "check_id": "root", "source_file": "source/input.pdf",
    "location": "PDF page 1 / synthetic", "observation": "synthetic observed value",
    "method": "read source",
}]}
records = {"records": [
    {"record_type": "page_ledger", "record_id": "pdf-P00001", "check_id": "root",
     "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": [], "target_ids": []},
     "payload": {"page_id": "pdf-P00001", "source_pdf": "source/input.pdf", "pdf_page": 1,
                 "status": "VERIFIED", "evidence_anchors": ["PDF page 1"]}},
    {"record_type": "run_metadata", "record_id": "META-root", "check_id": "root",
     "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": [], "target_ids": []},
     "payload": {"metadata_id": "META-root", "stage": "pass1", "references_read": [],
                 "memory_cases_consulted": [], "completed_checks": []}},
]}
for name, value in (
    ("batch_patch.json", patch),
    ("batch_evidence.json", evidence),
    ("batch_ledger_records.json", records),
):
    (work / name).write_text(json.dumps(value), encoding="utf-8")
'''


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="requires POSIX process signals")
def test_worker_kill_mid_batch_preserves_committed_batch_and_recovers_active_batch(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    jobs_root = data_dir / "jobs"
    source = tmp_path / "source.pdf"
    source.write_bytes(b"synthetic drawing source")
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(FAKE_CODEX, encoding="utf-8")
    database_url = f"sqlite:///{tmp_path / 'worker-kill.db'}"
    job_code = f"BOQ-KILL-{os.getpid()}-{int(time.time())}"

    engine = create_engine(database_url, connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    inventory = {
        "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
        "pages": [
            {"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1},
            {"page_id": "pdf-P00002", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 2},
        ],
        "sheets": [],
        "targets": [],
        "expected_work_items": [
            {"check_id": "root", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["pdf-P00001"],
             "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
            {"check_id": "child", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["pdf-P00002"],
             "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": ["root"]},
        ],
    }
    with sessions() as db:
        reviewer = User(name="Reviewer", email="reviewer@example.com", password_hash="synthetic", role=UserRole.ADMIN)
        db.add(reviewer)
        db.flush()
        job = Job(
            user_id=reviewer.id, job_code=job_code, project_name="Synthetic kill test",
            original_filename="source.pdf", input_file_path=str(source), status=JobStatus.SUBMITTED,
        )
        db.add(job)
        db.flush()
        db.add(AuditInventoryApproval(
            job_id=job.id, reviewer_user_id=reviewer.id,
            source_digest=audit_runner._source_digest(job),
            inventory_json=json.dumps(inventory, sort_keys=True),
        ))
        db.commit()
        job_id = job.id

    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "DATA_DIR": str(data_dir),
        "BOQ_AUDIT_SKILL_PATH": str(PINNED_SKILL),
        "CODEX_COMMAND": f"{sys.executable} {fake_codex}",
        "CODEX_TIMEOUT_SECONDS": "300",
        "AUDIT_CHECKPOINT_WORKER_ENABLED": "true",
        "AUDIT_QUALITY_REVIEWER_EMAILS": "reviewer@example.com",
        "PYTHONPATH": str(BACKEND),
        "PYTHONUNBUFFERED": "1",
    }
    stderr_path = tmp_path / "worker.stderr.log"
    worker_one = worker_two = None
    fake_child_pid: list[int] = []

    def is_our_fake_child(pid: int) -> bool:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return False
        return str(fake_codex).encode() in cmdline and str(sys.executable).encode() in cmdline

    def kill_fake_child_once() -> None:
        if not fake_child_pid:
            return
        pid = fake_child_pid.pop()
        if not is_our_fake_child(pid):
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def spawn_worker():
        return subprocess.Popen(
            [sys.executable, "-m", "app.worker"],
            cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=stderr_path.open("ab"),
        )

    def wait_for(predicate, *, timeout=60.0, message):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                value = predicate()
            except Exception as exc:  # pragma: no cover - diagnostic path
                last_error = exc
                value = None
            if value:
                return value
            time.sleep(0.1)
        tail = stderr_path.read_text(errors="replace")[-4000:] if stderr_path.exists() else ""
        raise AssertionError(f"timed out waiting for {message}; last_error={last_error}; worker stderr tail:\n{tail}")

    def checkpoint_state():
        with sessions() as db:
            run = db.scalar(select(AuditRun).where(AuditRun.job_id == job_id))
            if run is None:
                return None
            checkpoint = db.get(AuditRunCheckpoint, run.id)
            if checkpoint is None:
                return None
            ledger = {item["check_id"]: item for item in json.loads(checkpoint.ledger_json)}
            results = list(db.scalars(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == run.id)))
            return {
                "run_id": run.id,
                "run_status": run.status,
                "attempt": run.attempt,
                "job_status": db.get(Job, job_id).status,
                "status": checkpoint.status,
                "pass_no": checkpoint.pass_no,
                "version": checkpoint.version,
                "active": json.loads(checkpoint.active_batch_ids_json),
                "derived_from_run_id": checkpoint.derived_from_run_id,
                "ledger": {check_id: item["execution_status"] for check_id, item in ledger.items()},
                "results": [
                    {
                        "batch_key": row.batch_key,
                        "pass_no": row.pass_no,
                        "check_ids": json.loads(row.check_ids_json),
                        "patch_json": row.patch_json,
                        "evidence_json": row.evidence_json,
                        "checkpoint_version": row.checkpoint_version,
                        "derived_from_run_id": row.derived_from_run_id,
                    }
                    for row in results
                ],
            }

    try:
        worker_one = spawn_worker()
        wait_for(
            lambda: (state := checkpoint_state()) and state["status"] == "RUNNING" and state["active"] == ["child"] and state,
            message="worker to start the child batch",
        )

        def child_pid():
            markers = list(jobs_root.glob(f"{job_code}/attempts/*/batches/pass-1-*/work/child.pid"))
            if not markers:
                return None
            return int(markers[0].read_text())

        fake_child_pid.append(wait_for(child_pid, timeout=30.0, message="fake model child to start"))

        before_kill = checkpoint_state()
        assert before_kill["ledger"] == {"root": "DONE", "child": "RUNNING"}
        assert before_kill["job_status"] == JobStatus.PROCESSING

        worker_one.kill()
        worker_one.wait(timeout=15)
        kill_fake_child_once()

        after_kill = checkpoint_state()
        assert after_kill["status"] == "RUNNING"
        assert after_kill["active"] == ["child"]
        assert after_kill["ledger"] == {"root": "DONE", "child": "RUNNING"}
        assert len(after_kill["results"]) == 1
        committed = after_kill["results"][0]
        assert committed["check_ids"] == ["root"]

        worker_two = spawn_worker()
        recovered = wait_for(
            lambda: (state := checkpoint_state()) and state["job_status"] == JobStatus.WAITING_FOR_INFO and state["status"] == "INTERRUPTED" and state,
            message="second worker to recover the interrupted batch",
        )
        assert recovered["attempt"] == 1
        assert recovered["ledger"] == {"root": "DONE", "child": "BLOCKED"}
        assert recovered["active"] == []
        assert recovered["version"] == after_kill["version"] + 1
        assert recovered["derived_from_run_id"] is None
        assert len(recovered["results"]) == 1
        assert recovered["results"][0] == committed

        worker_two.send_signal(signal.SIGTERM)
        assert worker_two.wait(timeout=30) in {0, -signal.SIGTERM}
    finally:
        for process in (worker_one, worker_two):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=15)
        kill_fake_child_once()
        engine.dispose()
