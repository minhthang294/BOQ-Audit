import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

from conftest import ASGIClient, login
from app.core.database import SessionLocal
from app.models.entities import AuditInventoryApproval, AuditQualityReview, AuditQualityReviewEvent, AuditRun, AuditRunCheckpoint, AuditRunStageGate, Job, JobStatus, User, utcnow
from app.schemas.api import AdminStageGateResponse
from test_jobs import create_job


def _user_client(username):
    client = ASGIClient()
    password = "owner-pass-123" if username == "owner" else "admin-pass-123"
    assert login(client, username, password).status_code == 200
    return client


def _add_review(code, *, attempt=1, aggregate=None, event_type=None):
    with SessionLocal() as db:
        job = db.query(Job).filter_by(job_code=code).one()
        run = AuditRun(job_id=job.id, attempt=attempt, status="COMPLETED", started_at=utcnow(), ended_at=utcnow(), duration_seconds=1)
        db.add(run)
        db.flush()
        review = AuditQualityReview(
            run_id=run.id,
            reviewer_user_id=None,
            snapshot_id=f"snapshot-{attempt}",
            stage="release",
            controller_status="PASS",
            candidate_release_status="READY_FOR_ENGINEER_REVIEW",
            quality_aggregate=json.dumps(aggregate if aggregate is not None else {}),
            source_digest="a" * 64,
            skill_digest=None,
            input_digest="b" * 64,
            receipt_digests="{}",
            output_manifest_digest="c" * 64,
            customer_fields_digest="d" * 64,
            output_manifest="[]",
        )
        db.add(review)
        db.flush()
        if event_type:
            db.add(AuditQualityReviewEvent(review_id=review.id, event_type=event_type, actor_id="system", reason="test"))
        db.commit()
        return review.id


def test_admin_quality_api_requires_admin_and_reports_no_review(pdf_bytes):
    owner = _user_client("owner")
    admin = _user_client("admin")
    code = create_job(owner, pdf_bytes).json()["job_code"]

    assert ASGIClient().get(f"/api/admin/jobs/{code}/quality").status_code == 401
    forbidden = owner.get(f"/api/admin/jobs/{code}/quality")
    assert forbidden.status_code == 403

    response = admin.get(f"/api/admin/jobs/{code}/quality")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["freshness_status"] == "NOT_REVIEWED"
    assert response.json()["quality"] is None
    assert response.json()["coverage_status"] == "UNAVAILABLE"


def test_admin_quality_api_returns_latest_safe_summary_and_invalidation(pdf_bytes):
    owner = _user_client("owner")
    admin = _user_client("admin")
    code = create_job(owner, pdf_bytes).json()["job_code"]
    _add_review(code, aggregate={
        "validator_exit_code": 0,
        "open_record_count": 2,
        "open_issue_count": 1,
        "blocking_checks": ["PASS2_INCOMPLETE"],
        "critical_errors": None,
        "warnings": None,
        "error_code": None,
        "missing_outputs": [],
        "raw_evidence": "must not be exposed",
    }, event_type="QUALITY_INVALIDATED")

    response = admin.get(f"/api/admin/jobs/{code}/quality")

    assert response.status_code == 200
    body = response.json()
    assert body["attempt"] == 1
    assert body["freshness_status"] == "INVALIDATED"
    assert body["invalidation_event_type"] == "QUALITY_INVALIDATED"
    assert body["quality"]["advisory"] is True
    assert body["quality"]["enforcing"] is False
    assert body["quality"]["open_record_count"] == 2
    assert body["quality"]["blocking_checks"] == ["PASS2_INCOMPLETE"]
    assert body["coverage"] is None
    assert "raw_evidence" not in response.text
    assert "source_digest" not in response.text


def test_admin_quality_api_uses_latest_attempt_and_tolerates_invalid_aggregate(pdf_bytes):
    owner = _user_client("owner")
    admin = _user_client("admin")
    code = create_job(owner, pdf_bytes).json()["job_code"]
    _add_review(code, attempt=1, aggregate={"open_record_count": 7})
    _add_review(code, attempt=2, aggregate="malformed")

    response = admin.get(f"/api/admin/jobs/{code}/quality")

    assert response.status_code == 200
    assert response.json()["attempt"] == 2
    assert response.json()["quality"]["open_record_count"] is None
    assert response.json()["freshness_status"] == "NOT_VERIFIED"


def test_admin_quality_api_exposes_validated_checkpoint_coverage(pdf_bytes, monkeypatch, tmp_path):
    from app.api import admin as admin_api

    owner = _user_client("owner")
    admin = _user_client("admin")
    code = create_job(owner, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job = db.query(Job).filter_by(job_code=code).one()
        run = AuditRun(job_id=job.id, attempt=1, status="RUNNING", started_at=utcnow())
        db.add(run)
        db.flush()
        approval = AuditInventoryApproval(job_id=job.id, reviewer_user_id=db.query(User.id).filter_by(email="admin").scalar(), source_digest="a" * 64, inventory_json="{}")
        db.add(approval)
        db.flush()
        db.add(AuditRunCheckpoint(run_id=run.id, approval_id=approval.id, source_digest="a" * 64, skill_digest="b" * 64, inventory_digest="c" * 64, batch_plan_digest="d" * 64, inventory_json="{}", batch_plan_json="{}", ledger_json="[]", gate_reports_json="{}", pass_no=1, active_batch_ids_json="[]", status="READY", version=2))
        db.commit()
        run_id, approval_id = run.id, approval.id
    inventory = {
        "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
        "pages": [{"page_id": "p1", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1}],
        "sheets": [], "targets": [],
        "expected_work_items": [{"check_id": "c1", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []}],
    }
    ledger = [{**inventory["expected_work_items"][0], "execution_status": "PENDING", "result_status": "NOT_CHECKED", "evidence_ids": [], "applicability_reason": None}]
    detail_record = {"record_type": "detail_rows", "record_id": "r1", "payload": {"row_id": "r1", "unit": "cái", "reported_quantity": 1.5}}
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("test", encoding="utf-8")
    monkeypatch.setattr(admin_api, "get_settings", lambda: SimpleNamespace(boq_audit_skill_path=skill_path))
    monkeypatch.setattr(admin_api, "_source_digest", lambda _job: "a" * 64)
    monkeypatch.setattr(admin_api.quality, "verify_pinned_bundle", lambda *_args, **_kwargs: type("Pinned", (), {"digest": "b" * 64})())
    monkeypatch.setattr(admin_api, "load_run_checkpoint", lambda *_args, **_kwargs: {"run_id": run_id, "approval_id": approval_id, "source_digest": "a" * 64, "skill_digest": "b" * 64, "inventory_digest": "c" * 64, "batch_plan_digest": "d" * 64, "gate_reports": {}, "inventory": inventory, "ledger": ledger, "ledger_projection": {"detail_rows": [detail_record["payload"]]}, "ledger_records": [detail_record], "batch_results": [], "batch_plan": {"1": [["c1"]]}, "pass_no": 1, "status": "READY", "version": 2})

    response = admin.get(f"/api/admin/jobs/{code}/quality")

    assert response.status_code == 200
    body = response.json()
    assert body["coverage_status"] == "INCOMPLETE"
    assert body["coverage"]["not_checked_count"] == 1
    assert body["coverage"]["checkpoint_version"] == 2
    assert body["coverage"]["work_items"] == [{"check_id": "c1", "pass_no": 1, "execution_status": "PENDING", "result_status": "NOT_CHECKED"}]
    assert body["report_source"]["checkpoint_version"] == 2
    assert body["report_source"]["issue_candidate_count"] == 0
    assert body["report_source"]["ledger_record_counts"]["detail_rows"] == 1
    assert body["sanity"]["status"] == "REVIEW_REQUIRED"
    assert body["sanity"]["anomalies"][0]["anomaly"] == "DISCRETE_FRACTION"
    assert "evidence_ids" not in response.text


def test_admin_quality_api_returns_404_for_unknown_job():
    admin = _user_client("admin")
    assert admin.get("/api/admin/jobs/BOQ-999999/quality").status_code == 404


def test_gate_repair_classifier_separates_contract_gap_from_missing_review():
    from app.api.admin import _gate_repair_status
    from app.services.audit_lesson_selection import select_lessons

    gate = AdminStageGateResponse(
        attempt=1, pass_no=3, checkpoint_version=4, snapshot_id="gate-1",
        controller_status="BLOCKED", blocking_checks=["pass3 closure incomplete"],
        advanced=False, integrity_status="VERIFIED", created_at=utcnow(),
    )
    checkpoint = {
        "pass_no": 3, "version": 4, "status": "READY",
        "inventory": {"pages": [{"page_id": "p1"}], "targets": []},
        "ledger": [{"pass_no": 3, "execution_status": "DONE"}],
        "ledger_records": [],
        "lesson_snapshot": select_lessons(
            project_scope="JOB-TEST",
            expected_work_items=[{"check_id": "c1", "check_kind": "DRAWING_REVIEW", "target_ids": []}],
            current_lessons=[],
        ),
    }
    job = SimpleNamespace(status=JobStatus.WAITING_FOR_INFO)
    assert _gate_repair_status(job, checkpoint, [gate]) == "UNMAPPED_BLOCKER"
    checkpoint["ledger_records"].append({"record_type": "issue_candidates", "payload": {}})
    assert _gate_repair_status(job, checkpoint, [gate]) == "CONTROLLER_CONTRACT"


def test_admin_quality_api_exposes_gate_history_and_flags_tampering(pdf_bytes):
    owner = _user_client("owner")
    admin = _user_client("admin")
    code = create_job(owner, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job = db.query(Job).filter_by(job_code=code).one()
        run = AuditRun(job_id=job.id, attempt=1, status="RUNNING", started_at=utcnow())
        db.add(run)
        db.flush()
        receipt = {
            "schema_version": 1, "run_id": run.id, "snapshot_id": "gate-snapshot", "stage": "pass1",
            "controller_status": "BLOCKED", "candidate_release_status": "BLOCKED",
            "blocking_checks": ["Missing mandatory reference"],
        }
        receipt_json = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        gate = AuditRunStageGate(
            run_id=run.id, pass_no=1, checkpoint_version=3, snapshot_id="gate-snapshot", stage="pass1",
            receipt_json=receipt_json, receipt_digest=hashlib.sha256(receipt_json.encode()).hexdigest(),
            advanced=False,
        )
        db.add(gate)
        db.commit()
        gate_id = gate.id

    body = admin.get(f"/api/admin/jobs/{code}/quality").json()
    assert body["stage_gates"][0]["integrity_status"] == "VERIFIED"
    assert body["stage_gates"][0]["blocking_checks"] == ["Missing mandatory reference"]
    assert body["gate_repair_status"] == "CHECKPOINT_UNAVAILABLE"
    assert "receipt_json" not in json.dumps(body)

    with SessionLocal() as db:
        db.get(AuditRunStageGate, gate_id).receipt_json = receipt_json.replace("BLOCKED", "PASS")
        db.commit()
    body = admin.get(f"/api/admin/jobs/{code}/quality").json()
    assert body["stage_gates"][0]["integrity_status"] == "INVALID"
    assert body["stage_gates"][0]["advanced"] is False
    assert body["gate_repair_status"] == "GATE_INVALID"

    with SessionLocal() as db:
        run = db.query(AuditRun).filter_by(job_id=db.query(Job).filter_by(job_code=code).one().id).one()
        error_receipt = {
            "schema_version": 1, "run_id": run.id, "snapshot_id": "source-binding-error", "stage": "pass1",
            "source_digest": "a" * 64, "skill_digest": None, "input_digest": "b" * 64,
            "validator_exit_code": None, "candidate_release_status": None,
            "blocking_checks": [], "open_record_count": None, "controller_status": "ERROR",
        }
        error_json = json.dumps(error_receipt, sort_keys=True, separators=(",", ":"))
        db.add(AuditRunStageGate(
            run_id=run.id, pass_no=1, checkpoint_version=4, snapshot_id="source-binding-error", stage="pass1",
            receipt_json=error_json, receipt_digest=hashlib.sha256(error_json.encode()).hexdigest(),
            error_code="STAGE_SOURCE_BINDING", advanced=False,
        ))
        db.commit()
    body = admin.get(f"/api/admin/jobs/{code}/quality").json()
    assert body["stage_gates"][0]["controller_status"] == "ERROR"
    assert body["stage_gates"][0]["error_code"] == "STAGE_SOURCE_BINDING"
    assert body["stage_gates"][0]["integrity_status"] == "VERIFIED"
