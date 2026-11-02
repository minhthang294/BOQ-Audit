from pathlib import Path
from threading import Event
import json
import os
import time
import fcntl
from threading import Thread
from types import SimpleNamespace

from sqlalchemy import inspect, select

from app.core.database import SessionLocal, engine
from app.core.config import get_settings
from app.core.migrations import migrate_and_recover, recover_interrupted_runs
from app.models.entities import AuditInventoryApproval, AuditIssueClassification, AuditRun, AuditRunBatchResult, AuditRunBatchRevision, AuditRunCheckpoint, AuditRunGateRepairRequest, AuditRunStageGate, Job, JobOutput, JobStatus, OutputType, User
from app.services import audit_checkpoint, audit_runner
from app.services.audit_quality import ValidationResult, ValidatorReceipt
from app import worker
from test_jobs import create_job


def _job(code):
    with SessionLocal() as db:
        return db.scalar(select(Job.id).where(Job.job_code == code))


def test_additive_migration_creates_batch_revision_table_without_replacing_jobs(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    AuditRunBatchRevision.__table__.drop(engine)
    assert not inspect(engine).has_table("audit_run_batch_revisions")
    migrate_and_recover()
    assert inspect(engine).has_table("audit_run_batch_revisions")
    assert _job(code) is not None


def test_additive_migration_creates_issue_classification_table(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    AuditIssueClassification.__table__.drop(engine)
    assert not inspect(engine).has_table("audit_issue_classifications")
    migrate_and_recover()
    assert inspect(engine).has_table("audit_issue_classifications")
    assert _job(code) is not None


def test_pending_upload_is_not_claimed_and_ready_queue_survives_api_restart(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    job_id = _job(code)
    with SessionLocal() as db:
        db.get(Job, job_id).input_file_path = "pending"
        db.commit()
    assert audit_runner.claim_job() is None
    with SessionLocal() as db:
        db.get(Job, job_id).input_file_path = str(get_settings().jobs_dir / code / "input" / "missing.pdf")
        db.commit()
    assert audit_runner.claim_job() is None
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.FAILED
        assert job.completed_at is not None
        assert "tải lại" in job.admin_notes
        path = next((get_settings().jobs_dir / code / "input").iterdir())
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
    sandbox_env = audit_runner.codex_exec_environment
    monkeypatch.setattr(audit_runner, "codex_exec_environment", lambda workspace, source_env: {
        **sandbox_env(workspace, source_env), "FAKE_CHILD_PID": str(child_pid_file),
    })
    settings = get_settings()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    skill = skill_dir / "SKILL.md"
    skill.write_text("fake skill")
    monkeypatch.setattr(settings, "boq_audit_skill_path", skill)
    monkeypatch.setattr(settings, "codex_command", str(fake))
    stop = Event()
    results = []
    source = tmp_path.parent / f"{tmp_path.name}-source.pdf"
    source.write_bytes(b"fake")
    job = SimpleNamespace(job_code="FAKE", input_file_path=str(source), estimate_input=None, narrative_input=None)
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


def _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    skill = tmp_path / "SKILL.md"
    skill.write_text("synthetic skill", encoding="utf-8")
    settings = SimpleNamespace(audit_checkpoint_worker_enabled=True, boq_audit_skill_path=skill)
    monkeypatch.setattr(audit_runner, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(audit_checkpoint, "get_settings", lambda: SimpleNamespace(audit_quality_reviewer_email_set={"admin"}))
    monkeypatch.setattr(audit_runner.quality, "verify_pinned_bundle", lambda *_args, **_kwargs: SimpleNamespace(digest=audit_runner.quality.TRUSTED_SKILL_DIGEST))
    assert audit_runner.claim_job() is None
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        source_digest = audit_runner._source_digest(job)
        inventory = {
            "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
            "pages": [{"page_id": "p1", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1}],
            "sheets": [], "targets": [],
            "expected_work_items": [{"check_id": "c1", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []}],
        }
        admin_id = db.scalar(select(User.id).where(User.email == "admin"))
        db.add(AuditInventoryApproval(job_id=job.id, reviewer_user_id=admin_id, source_digest=source_digest, inventory_json=json.dumps(inventory)))
        db.commit()
        job_id = job.id
    claim = audit_runner.claim_job()
    assert claim is not None and claim[0] == job_id
    return code, claim


def test_checkpoint_worker_keeps_same_run_after_restart_and_parks_gate_blocker(owner_client, pdf_bytes, tmp_path, monkeypatch):
    code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    with SessionLocal() as db:
        assert db.get(AuditRunCheckpoint, claim[1]).status == "READY"
    recover_interrupted_runs()
    assert worker._next_checkpoint_run() == claim
    results = iter([{"status": "BATCH_DONE"}, {"status": "PASS_GATE_BLOCKED"}])
    monkeypatch.setattr(audit_runner, "execute_next_checkpoint_batch", lambda *_args, **_kwargs: next(results))
    monkeypatch.setattr(audit_runner, "_execute", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner called")))
    audit_runner.run_audit_job(*claim)
    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        assert job.status == JobStatus.WAITING_FOR_INFO
        assert db.get(AuditRun, claim[1]).status == JobStatus.WAITING_FOR_INFO.value
        assert len(job.audit_runs) == 1
        assert db.get(AuditRunCheckpoint, claim[1]).status == "READY"
    assert worker._next_checkpoint_run() is None


def test_checkpoint_worker_feature_gate_pauses_resume_without_mutating_run(owner_client, pdf_bytes, tmp_path, monkeypatch):
    _code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    settings = worker.get_settings()
    settings.audit_checkpoint_worker_enabled = False

    assert worker._next_checkpoint_run() is None
    with SessionLocal() as db:
        assert db.get(Job, claim[0]).status == JobStatus.PROCESSING
        assert db.get(AuditRun, claim[1]).status == "RUNNING"
        assert db.get(AuditRunCheckpoint, claim[1]).status == "READY"

    settings.audit_checkpoint_worker_enabled = True
    assert worker._next_checkpoint_run() == claim


def test_approved_job_never_falls_back_to_legacy_claim_or_disabled_retry(owner_client, pdf_bytes, tmp_path, monkeypatch):
    from app.api import jobs as jobs_api

    code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    settings = worker.get_settings()
    settings.audit_checkpoint_worker_enabled = False
    monkeypatch.setattr(jobs_api, "get_settings", lambda: settings)
    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        run = db.get(AuditRun, claim[1])
        job.status = JobStatus.FAILED
        run.status = JobStatus.FAILED.value
        db.commit()
    retry = owner_client.post(f"/api/jobs/{code}/retry")
    assert retry.status_code == 409
    assert "checkpoint worker" in retry.json()["detail"]
    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        job.status = JobStatus.SUBMITTED
        db.commit()
    assert audit_runner.claim_job(claim[0]) is None
    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        assert job.status == JobStatus.SUBMITTED
        assert len(job.audit_runs) == 1
    settings.audit_checkpoint_worker_enabled = True
    assert audit_runner.claim_job(claim[0]) == (claim[0], claim[1] + 1)


def test_checkpoint_run_cannot_publish_manual_outputs_before_ledger_export_and_engineer_review(owner_client, pdf_bytes, tmp_path, monkeypatch):
    from conftest import ASGIClient, login

    code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        job.status = JobStatus.REVIEW
        for kind in (OutputType.EXCEL_REPORT, OutputType.ANNOTATED_PDF):
            db.add(JobOutput(job_id=job.id, file_type=kind, original_filename=f"{kind.value}.bin", stored_filename=f"{kind.value}.bin", file_path=str(tmp_path / f"{kind.value}.bin"), file_size=1))
        db.commit()
    admin = ASGIClient()
    assert login(admin, "admin", "admin-pass-123").status_code == 200
    response = admin.post(f"/api/admin/jobs/{code}/complete")
    assert response.status_code == 409
    assert "signoff" in response.json()["detail"].lower()
    with SessionLocal() as db:
        assert db.get(Job, claim[0]).status == JobStatus.REVIEW


def test_checkpoint_worker_gate_blocker_is_visible_and_cannot_be_published(owner_client, pdf_bytes, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from conftest import ASGIClient, login

    code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    with SessionLocal() as db:
        source_digest = audit_runner._source_digest(db.get(Job, claim[0]))
    result = (
        {"items": [{"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-1"]}]},
        {"evidence": [{
            "evidence_id": "ev-1", "check_id": "c1", "source_file": "source/input.pdf",
            "location": "PDF page 1 / synthetic detail", "observation": "dimension label present", "method": "read source",
        }]},
        {"records": [
            {"record_type": "page_ledger", "record_id": "p1", "check_id": "c1",
             "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
             "payload": {"page_id": "p1", "source_pdf": "source/input.pdf", "pdf_page": 1,
                         "status": "VERIFIED", "evidence_anchors": ["PDF page 1"]}},
            {"record_type": "run_metadata", "record_id": "META-c1", "check_id": "c1",
             "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
             "payload": {"metadata_id": "META-c1", "stage": "pass1", "references_read": [],
                         "memory_cases_consulted": [], "completed_checks": []}},
        ]},
    )
    monkeypatch.setattr(audit_runner, "_execute_codex_batch", lambda *_args, **_kwargs: (0, False, "synthetic", result))

    receipt = ValidatorReceipt(
        schema_version=1, run_id=claim[1], snapshot_id="synthetic-blocked-gate", stage="pass1",
        source_digest=source_digest, skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
        input_digest="c" * 64, validator_exit_code=2, candidate_release_status="BLOCKED",
        blocking_checks=("Mandatory reference files were not recorded as read",), open_record_count=1,
        validated_at=datetime.now(timezone.utc).isoformat(), controller_status="BLOCKED",
    )
    receipt_path = tmp_path / "blocked-gate.json"
    receipt_path.write_text(json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
    validation = ValidationResult(receipt=receipt, receipt_path=receipt_path)
    monkeypatch.setattr(audit_runner, "_validate_checkpoint_stage", lambda *_args, **_kwargs: validation)

    audit_runner.run_audit_job(*claim)
    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        checkpoint = db.get(AuditRunCheckpoint, claim[1])
        gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == claim[1]))
        assert job.status == JobStatus.WAITING_FOR_INFO
        assert checkpoint.pass_no == 1 and checkpoint.status == "READY"
        assert gate is not None and not gate.advanced

    admin = ASGIClient()
    assert login(admin, "admin", "admin-pass-123").status_code == 200
    quality = admin.get(f"/api/admin/jobs/{code}/quality")
    assert quality.status_code == 200, quality.text
    assert quality.json()["stage_gates"][0]["controller_status"] == "BLOCKED"
    refused = admin.post(f"/api/admin/jobs/{code}/complete")
    assert refused.status_code == 409
    assert "signoff" in refused.json()["detail"].lower()
    with SessionLocal() as db:
        assert db.get(Job, claim[0]).status == JobStatus.WAITING_FOR_INFO


def test_recovery_parks_interrupted_gate_repair_without_new_attempt(owner_client, pdf_bytes, tmp_path, monkeypatch):
    _code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    with SessionLocal() as db:
        base = AuditRunBatchResult(
            run_id=claim[1], pass_no=1, batch_key="a" * 64, check_ids_json='["c1"]',
            source_files_json='["source/input.pdf"]', patch_json='{"items":[]}', evidence_json='{"evidence":[]}',
            patch_digest="b" * 64, evidence_digest="c" * 64, checkpoint_version=1,
        )
        db.add(base); db.flush()
        gate = AuditRunStageGate(
            run_id=claim[1], pass_no=1, checkpoint_version=1, snapshot_id="restart-gate", stage="pass1",
            receipt_json="{}", receipt_digest="d" * 64, advanced=False,
        )
        db.add(gate); db.flush()
        request = AuditRunGateRepairRequest(
            run_id=claim[1], base_result_id=base.id, gate_id=gate.id, batch_ids_json='["c1"]',
            source_digest="e" * 64, skill_digest="f" * 64, checkpoint_version=1,
            attempt_no=1, status="RUNNING",
        )
        db.add(request); db.commit()
        request_id = request.id
    recover_interrupted_runs()
    with SessionLocal() as db:
        request = db.get(AuditRunGateRepairRequest, request_id)
        job = db.get(Job, claim[0])
        assert request.status == "FAILED"
        assert request.error_code == "WORKER_INTERRUPTED"
        assert job.status == JobStatus.WAITING_FOR_INFO
        assert db.get(AuditRun, claim[1]).status == JobStatus.WAITING_FOR_INFO.value
        assert len(job.audit_runs) == 1


def test_admin_retries_interrupted_checkpoint_on_same_run(owner_client, pdf_bytes, tmp_path, monkeypatch):
    from app.api import admin as admin_api
    from conftest import ASGIClient, login

    code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    admin_client = ASGIClient()
    login(admin_client, "admin", "admin-pass-123")
    monkeypatch.setattr(admin_api, "_allowed_inventory_reviewers", lambda: {"admin"})
    admin_settings = SimpleNamespace(audit_checkpoint_worker_enabled=True, boq_audit_skill_path=tmp_path / "SKILL.md")
    monkeypatch.setattr(admin_api, "get_settings", lambda: admin_settings)
    with SessionLocal() as db:
        db.execute(__import__("sqlalchemy").text("BEGIN IMMEDIATE"))
        job = db.get(Job, claim[0])
        checkpoint = audit_checkpoint.load_run_checkpoint(db, run_id=claim[1], current_source_digest=audit_runner._source_digest(job), current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST)
        audit_checkpoint.start_checkpoint_batch(db, checkpoint=checkpoint, expected_version=checkpoint["version"], batch_ids=["c1"])
        checkpoint = audit_checkpoint.load_run_checkpoint(db, run_id=claim[1], current_source_digest=audit_runner._source_digest(job), current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST)
        audit_checkpoint.recover_checkpoint(db, checkpoint=checkpoint, expected_version=checkpoint["version"])
        job.status = JobStatus.WAITING_FOR_INFO
        db.get(AuditRun, claim[1]).status = JobStatus.WAITING_FOR_INFO.value
        db.commit()
    assert owner_client.post(f"/api/admin/jobs/{code}/checkpoint/retry").status_code == 403
    admin_settings.audit_checkpoint_worker_enabled = False
    assert admin_client.post(f"/api/admin/jobs/{code}/checkpoint/retry").status_code == 409
    with SessionLocal() as db:
        assert db.get(Job, claim[0]).status == JobStatus.WAITING_FOR_INFO
        assert db.get(AuditRun, claim[1]).status == JobStatus.WAITING_FOR_INFO.value
    admin_settings.audit_checkpoint_worker_enabled = True
    response = admin_client.post(f"/api/admin/jobs/{code}/checkpoint/retry")
    assert response.status_code == 200
    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        assert job.status == JobStatus.PROCESSING
        assert db.get(AuditRun, claim[1]).status == "RUNNING"
        assert len(job.audit_runs) == 1
        assert db.get(AuditRunCheckpoint, claim[1]).status == "READY"


def test_reviewer_requests_only_one_scoped_gate_repair(owner_client, pdf_bytes, tmp_path, monkeypatch):
    import hashlib
    from app.api import admin as admin_api
    from conftest import ASGIClient, login

    code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    admin_client = ASGIClient()
    login(admin_client, "admin", "admin-pass-123")
    monkeypatch.setattr(admin_api, "_allowed_inventory_reviewers", lambda: {"admin"})
    admin_settings = SimpleNamespace(audit_checkpoint_worker_enabled=True, boq_audit_skill_path=tmp_path / "SKILL.md")
    monkeypatch.setattr(admin_api, "get_settings", lambda: admin_settings)
    monkeypatch.setattr(admin_api, "_source_digest", lambda _job: "a" * 64)
    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        job.status = JobStatus.WAITING_FOR_INFO
        db.get(AuditRun, claim[1]).status = JobStatus.WAITING_FOR_INFO.value
        base = AuditRunBatchResult(
            run_id=claim[1], pass_no=1, batch_key="b" * 64, check_ids_json='["c1"]',
            source_files_json='["source/input.pdf"]', patch_json='{"items":[]}', evidence_json='{"evidence":[]}',
            patch_digest="c" * 64, evidence_digest="d" * 64, checkpoint_version=1,
        )
        db.add(base); db.flush()
        receipt = {
            "schema_version": 1, "run_id": claim[1], "snapshot_id": "repair-gate", "stage": "pass1",
            "controller_status": "BLOCKED", "candidate_release_status": "BLOCKED",
            "blocking_checks": ["Missing mandatory reference"], "validator_exit_code": 2,
            "source_digest": "a" * 64, "skill_digest": audit_runner.quality.TRUSTED_SKILL_DIGEST,
        }
        receipt_json = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        db.add(AuditRunStageGate(
            run_id=claim[1], pass_no=1, checkpoint_version=1, snapshot_id="repair-gate", stage="pass1",
            receipt_json=receipt_json, receipt_digest=hashlib.sha256(receipt_json.encode()).hexdigest(), advanced=False,
        ))
        db.commit()
        base_id = base.id
    monkeypatch.setattr(admin_api, "load_run_checkpoint", lambda *_args, **_kwargs: {
        "pass_no": 1, "version": 1, "status": "READY", "source_digest": "a" * 64,
        "skill_digest": audit_runner.quality.TRUSTED_SKILL_DIGEST,
        "inventory": {"expected_work_items": [{"check_id": "c1", "dependencies": []}]},
        "batch_plan": {"1": [["c1"]]},
        "ledger": [{"check_id": "c1", "pass_no": 1, "execution_status": "DONE"}],
        "batch_results": [{"base_result_id": base_id, "revision_no": 0, "pass_no": 1, "check_ids": ["c1"]}],
    })
    endpoint = f"/api/admin/jobs/{code}/checkpoint/repair"
    assert owner_client.post(endpoint, json={"batch_ids": ["c1"]}).status_code == 403
    admin_settings.audit_checkpoint_worker_enabled = False
    assert admin_client.post(endpoint, json={"batch_ids": ["c1"]}).status_code == 409
    admin_settings.audit_checkpoint_worker_enabled = True
    response = admin_client.post(endpoint, json={"batch_ids": ["c1"]})
    assert response.status_code == 200
    assert response.json()["status"] == "REQUESTED"
    assert admin_client.post(endpoint, json={"batch_ids": ["c1"]}).status_code == 409
