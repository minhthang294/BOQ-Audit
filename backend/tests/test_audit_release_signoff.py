import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pymupdf
from sqlalchemy import select

from app.api import admin as admin_api
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import (
    AuditAnnotationDraft,
    AuditExcelReportDraft,
    AuditExcelReportReviewEvent,
    AuditInventoryApproval,
    AuditReleaseSignoffEvent,
    AuditRun,
    AuditRunCheckpoint,
    AuditRunStageGate,
    Job,
    JobStatus,
    JobOutput,
    OutputType,
    User,
)
from app.services import audit_checkpoint, audit_runner
from app.services.audit_quality import ValidationResult, ValidatorReceipt
from conftest import ASGIClient, login
from test_recovery import _approved_checkpoint_job
from test_jobs import create_job


def test_new_legacy_attempt_cannot_bypass_approved_checkpoint_signoff(owner_client, pdf_bytes, tmp_path, monkeypatch):
    code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        job.status = JobStatus.REVIEW
        db.add(AuditRun(job_id=job.id, attempt=2, status=JobStatus.REVIEW.value))
        for kind in (OutputType.EXCEL_REPORT, OutputType.ANNOTATED_PDF):
            db.add(JobOutput(job_id=job.id, file_type=kind, original_filename=f"{kind.value}.bin",
                             stored_filename=f"{kind.value}.bin", file_path=str(tmp_path / f"{kind.value}.bin"), file_size=1))
        db.commit()
        output_id = db.scalar(select(JobOutput.id).where(JobOutput.job_id == job.id).limit(1))
    admin = ASGIClient()
    assert login(admin, "admin", "admin-pass-123").status_code == 200
    assert admin.patch(f"/api/admin/jobs/{code}", json={"warnings": 1}).status_code == 409
    assert admin.post(f"/api/admin/jobs/{code}/outputs", data={"file_type": "EXCEL_REPORT"},
                      files={"file": ("report.xlsx", b"PK\x03\x04fake", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}).status_code == 409
    assert admin.delete(f"/api/admin/jobs/{code}/outputs/{output_id}").status_code == 409
    response = admin.post(f"/api/admin/jobs/{code}/complete")
    assert response.status_code == 409
    assert "checkpoint audit" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.get(Job, claim[0]).status == JobStatus.REVIEW


def test_approved_inventory_without_checkpoint_cannot_use_legacy_complete(owner_client, pdf_bytes, tmp_path):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        reviewer = db.scalar(select(User).where(User.email == "admin"))
        job.status = JobStatus.REVIEW
        db.add(AuditInventoryApproval(job_id=job.id, reviewer_user_id=reviewer.id,
                                      source_digest="a" * 64, inventory_json="{}"))
        for kind in (OutputType.EXCEL_REPORT, OutputType.ANNOTATED_PDF):
            db.add(JobOutput(job_id=job.id, file_type=kind, original_filename=f"{kind.value}.bin",
                             stored_filename=f"{kind.value}.bin", file_path=str(tmp_path / f"{kind.value}.bin"), file_size=1))
        db.commit()
    admin = ASGIClient()
    assert login(admin, "admin", "admin-pass-123").status_code == 200
    quality = admin.get(f"/api/admin/jobs/{code}/quality")
    assert quality.status_code == 200
    assert quality.json()["controlled_audit_history"] is True
    assert quality.json()["coverage"] is None
    response = admin.post(f"/api/admin/jobs/{code}/complete")
    assert response.status_code == 409
    assert "checkpoint audit" in response.json()["detail"]


def test_current_engineer_signoff_publishes_exact_immutable_ledger_artifacts(owner_client, pdf_bytes, tmp_path, monkeypatch):
    code, claim = _approved_checkpoint_job(owner_client, pdf_bytes, tmp_path, monkeypatch)
    settings = get_settings()
    root = settings.jobs_dir / code
    pdf_path = root / "annotation_drafts" / "approved.pdf"
    excel_path = root / "excel_drafts" / "approved.xlsx"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_bytes, excel_bytes = b"%PDF-signed", b"PK\x03\x04-signed-xlsx"
    pdf_path.write_bytes(pdf_bytes)
    excel_path.write_bytes(excel_bytes)
    pdf_sha, excel_sha = hashlib.sha256(pdf_bytes).hexdigest(), hashlib.sha256(excel_bytes).hexdigest()

    with SessionLocal() as db:
        job = db.get(Job, claim[0])
        run = db.get(AuditRun, claim[1])
        checkpoint = db.get(AuditRunCheckpoint, run.id)
        checkpoint.pass_no = 3
        checkpoint.version = 7
        checkpoint.status = "READY"
        job.status = JobStatus.REVIEW
        run.status = JobStatus.REVIEW.value
        admin = db.scalar(select(User).where(User.email == "admin"))
        approval = db.get(AuditInventoryApproval, checkpoint.approval_id)
        pdf_draft = AuditAnnotationDraft(
            run_id=run.id, reviewer_user_id=admin.id, snapshot_digest="a" * 64,
            classification_digest="b" * 64, source_pdf_sha256="c" * 64,
            output_pdf_sha256=pdf_sha, file_path=str(pdf_path), marks_json="[]", qa_json="{}",
        )
        db.add(pdf_draft)
        db.flush()
        excel_draft = AuditExcelReportDraft(
            run_id=run.id, reviewer_user_id=admin.id, snapshot_digest="a" * 64,
            classification_digest="b" * 64, annotation_pdf_sha256=pdf_sha,
            workbook_sha256=excel_sha, file_path=str(excel_path), qa_json='{"status":"PASS"}',
        )
        db.add(excel_draft)
        db.flush()
        excel_review = AuditExcelReportReviewEvent(
            draft_id=excel_draft.id, revision_no=1, reviewer_user_id=admin.id,
            result="PASS", reason="Reviewed all workbook sections.",
            reviewed_sections_json='["ledger_rows"]', snapshot_digest="a" * 64,
            classification_digest="b" * 64, annotation_pdf_sha256=pdf_sha,
            workbook_sha256=excel_sha,
        )
        db.add(excel_review)
        db.flush()
        event = AuditReleaseSignoffEvent(
            run_id=run.id, revision_no=1, reviewer_user_id=admin.id,
            result="APPROVE_FULL", reason="Approved synthetic artifacts for release.",
            accepted_partial=False, snapshot_digest="a" * 64, classification_digest="b" * 64,
            checkpoint_version=7, approval_id=approval.id, pdf_draft_id=pdf_draft.id,
            pdf_sha256=pdf_sha, excel_draft_id=excel_draft.id,
            excel_review_event_id=excel_review.id, excel_sha256=excel_sha,
            unresolved_scope_json="[]",
        )
        db.add(event)
        db.commit()
        context = {
            "run": SimpleNamespace(id=run.id),
            "checkpoint": {"version": 7},
            "source": {"snapshot_digest": "a" * 64},
            "classifications": {"classification_digest": "b" * 64},
            "approval": SimpleNamespace(id=approval.id),
            "pdf_draft": SimpleNamespace(id=pdf_draft.id, output_pdf_sha256=pdf_sha, file_path=str(pdf_path)),
            "excel_draft": SimpleNamespace(id=excel_draft.id, workbook_sha256=excel_sha, file_path=str(excel_path)),
            "excel_review_event": SimpleNamespace(id=excel_review.id),
            "unresolved_scope": [],
            "requires_unmarked_pdf_attestation": False,
        }
    monkeypatch.setattr(admin_api, "_release_signoff_context", lambda *_args: context)

    admin_client = ASGIClient()
    assert login(admin_client, "admin", "admin-pass-123").status_code == 200
    signoff_url = f"/api/admin/jobs/{code}/checkpoint/release-signoff"
    signoff_body = {
        "snapshot_digest": "a" * 64, "classification_digest": "b" * 64,
        "pdf_draft_id": context["pdf_draft"].id, "pdf_sha256": pdf_sha,
        "excel_draft_id": context["excel_draft"].id, "excel_sha256": excel_sha,
        "result": "APPROVE_FULL", "accepted_partial": False,
        "reason": "Kỹ sư đã xem đủ nguồn và phạm vi kỹ thuật.",
    }
    monkeypatch.setattr(admin_api, "get_settings", lambda: SimpleNamespace(audit_engineer_signoff_email_set=set()))
    assert admin_client.post(signoff_url, json=signoff_body).status_code == 403
    monkeypatch.setattr(admin_api, "get_settings", lambda: SimpleNamespace(audit_engineer_signoff_email_set={"admin"}))
    partial = admin_client.post(signoff_url, json={**signoff_body, "result": "APPROVE_PARTIAL", "accepted_partial": True})
    assert partial.status_code == 409
    context["unresolved_scope"] = ["issue:I-02"]
    assert admin_client.post(signoff_url, json=signoff_body).status_code == 409
    partial = admin_client.post(signoff_url, json={**signoff_body, "result": "APPROVE_PARTIAL", "accepted_partial": True})
    assert partial.status_code == 201, partial.text
    assert partial.json()["unresolved_scope"] == ["issue:I-02"]
    context["unresolved_scope"] = []
    signoff = admin_client.post(signoff_url, json=signoff_body)
    assert signoff.status_code == 201, signoff.text
    assert signoff.json()["is_current"] is True
    monkeypatch.setattr(admin_api, "get_settings", lambda: SimpleNamespace(audit_engineer_signoff_email_set=set()))
    assert admin_client.get(signoff_url).json()["is_current"] is False
    monkeypatch.setattr(admin_api, "get_settings", lambda: SimpleNamespace(audit_engineer_signoff_email_set={"admin"}))
    assert admin_client.get(signoff_url).json()["result"] == "APPROVE_FULL"
    published = admin_client.post(f"/api/admin/jobs/{code}/complete")
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "COMPLETED"
    with SessionLocal() as db:
        outputs = db.query(JobOutput).filter_by(job_id=claim[0]).all()
        assert {output.file_type for output in outputs} == {OutputType.EXCEL_REPORT, OutputType.ANNOTATED_PDF}
        assert {Path(output.file_path).read_bytes() for output in outputs} == {pdf_bytes, excel_bytes}
    output_ids = [item["id"] for item in published.json()["outputs"]]
    for output_id in output_ids:
        customer_download = owner_client.get(f"/api/jobs/{code}/outputs/{output_id}/download")
        assert customer_download.status_code == 200
    assert admin_client.delete(f"/api/admin/jobs/{code}/outputs/{output_ids[0]}").status_code == 409


def test_synthetic_worker_to_real_report_context_signoff_and_publish(owner_client, tmp_path, monkeypatch):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    pdf_bytes = doc.tobytes()
    doc.close()
    code = create_job(owner_client, pdf_bytes).json()["job_code"]

    settings = get_settings()
    skill = tmp_path / "skills" / "boq-audit" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("synthetic pinned skill", encoding="utf-8")
    test_settings = settings.model_copy(update={
        "audit_checkpoint_worker_enabled": True,
        "audit_quality_reviewer_emails": "admin",
        "audit_engineer_signoff_emails": "admin",
        "boq_audit_skill_path": skill,
    })
    monkeypatch.setattr(audit_runner, "get_settings", lambda: test_settings)
    monkeypatch.setattr(audit_checkpoint, "get_settings", lambda: test_settings)
    monkeypatch.setattr(admin_api, "get_settings", lambda: test_settings)
    monkeypatch.setattr(audit_runner.quality, "verify_pinned_bundle", lambda *_args, **_kwargs: SimpleNamespace(digest=audit_runner.quality.TRUSTED_SKILL_DIGEST))

    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        admin = db.scalar(select(User).where(User.email == "admin"))
        inventory = {
            "documents": [{"document_id": "pdf", "revision_digest": f"sha256:{pdf_sha}"}],
            "pages": [{"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": f"sha256:{pdf_sha}", "pdf_page": 1}],
            "sheets": [], "targets": [],
            "expected_work_items": [
                {"check_id": "p1-check", "document_id": "pdf", "revision_digest": f"sha256:{pdf_sha}", "page_ids": ["pdf-P00001"], "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
                {"check_id": "p2-check", "document_id": "pdf", "revision_digest": f"sha256:{pdf_sha}", "page_ids": ["pdf-P00001"], "target_ids": [], "check_kind": "independent_review", "pass_no": 2, "dependencies": ["p1-check"]},
                {"check_id": "p3-check", "document_id": "pdf", "revision_digest": f"sha256:{pdf_sha}", "page_ids": ["pdf-P00001"], "target_ids": [], "check_kind": "closure_review", "pass_no": 3, "dependencies": ["p2-check"]},
            ],
        }
        db.add(AuditInventoryApproval(
            job_id=job.id, reviewer_user_id=admin.id, source_digest=audit_runner._source_digest(job),
            inventory_json=json.dumps(inventory, sort_keys=True),
        ))
        db.commit()
        job_id = job.id
    claim = audit_runner.claim_job(job_id)
    assert claim is not None

    def fake_batch(_job, _attempt, *, pass_no, batch_ids, **_kwargs):
        check_id = batch_ids[0]
        evidence_id = f"EV-P{pass_no}"
        patch_doc = {"items": [{"check_id": check_id, "result_status": "CHECKED", "evidence_ids": [evidence_id]}]}
        evidence = {"evidence": [{
            "evidence_id": evidence_id, "check_id": check_id, "source_file": "source/input.pdf",
            "location": "PDF page 1", "observation": f"Synthetic pass {pass_no} review", "method": "visual source review",
        }]}
        refs = {"page_ids": ["pdf-P00001"], "sheet_ids": [], "target_ids": []}
        records = []
        if pass_no == 1:
            records.extend([
                {"record_type": "page_ledger", "record_id": "pdf-P00001", "check_id": check_id, "source_refs": refs,
                 "payload": {"page_id": "pdf-P00001", "source_pdf": "source/input.pdf", "pdf_page": 1, "status": "VERIFIED", "evidence_anchors": ["PDF page 1"]}},
                {"record_type": "detail_rows", "record_id": "ROW-001", "check_id": check_id, "source_refs": refs,
                 "payload": {"row_id": "ROW-001", "source_anchor": "PDF page 1, synthetic row 1", "spec_status": "VERIFIED", "qty_status": "VERIFIED", "status": "VERIFIED", "section_id": "S1", "stt": 1, "unit": "m", "reported_quantity": 10}},
                {"record_type": "run_metadata", "record_id": "META-P1", "check_id": check_id, "source_refs": refs,
                 "payload": {"metadata_id": "META-P1", "stage": "pass1", "references_read": [], "memory_cases_consulted": [], "completed_checks": []}},
            ])
        else:
            kind = "pass2_review" if pass_no == 2 else "pass3_review"
            review_id = f"REVIEW-P{pass_no}"
            records.append({"record_type": kind, "record_id": review_id, "check_id": check_id, "source_refs": refs,
                            "payload": {"review_id": review_id, "pass_no": pass_no, "target": "all inventoried PDF pages", "result": "VERIFIED", "method": "independent synthetic review", "evidence": [evidence_id], "page_ids": ["pdf-P00001"]}})
        return 0, False, "synthetic model adapter", (patch_doc, evidence, {"records": records})

    monkeypatch.setattr(audit_runner, "_execute_codex_batch", fake_batch)

    def pass_gate(_job_code, _attempt, run_id, checkpoint, _sources):
        pass_no = checkpoint["pass_no"]
        receipt = ValidatorReceipt(
            schema_version=1, run_id=run_id, snapshot_id=f"synthetic-pass-{pass_no}", stage=f"pass{pass_no}",
            source_digest=checkpoint["source_digest"], skill_digest=checkpoint["skill_digest"],
            input_digest=hashlib.sha256(f"pass-{pass_no}".encode()).hexdigest(), validator_exit_code=0,
            candidate_release_status=audit_runner.quality.STAGE_COMPLETE_STATUS, blocking_checks=(), open_record_count=0,
            validated_at=datetime.now(timezone.utc).isoformat(), controller_status="PASS",
        )
        path = tmp_path / f"receipt-pass-{pass_no}.json"
        path.write_text(json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return ValidationResult(receipt=receipt, receipt_path=path)

    monkeypatch.setattr(audit_runner, "_validate_checkpoint_stage", pass_gate)
    audit_runner.run_audit_job(*claim)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        run = db.get(AuditRun, claim[1])
        checkpoint = db.get(AuditRunCheckpoint, claim[1])
        gates = list(db.scalars(select(admin_api.AuditRunStageGate).where(admin_api.AuditRunStageGate.run_id == claim[1])))
        assert job.status == JobStatus.REVIEW and run.status == JobStatus.REVIEW.value
        assert checkpoint.pass_no == 3 and checkpoint.status == "READY"
        assert len(gates) == 3 and [gate.advanced for gate in gates] == [True, True, False]

    admin = ASGIClient()
    assert login(admin, "admin", "admin-pass-123").status_code == 200
    annotation = admin.post(f"/api/admin/jobs/{code}/checkpoint/annotation-drafts", json={"marks": []})
    assert annotation.status_code == 201, annotation.text
    excel = admin.get(f"/api/admin/jobs/{code}/checkpoint/report-draft.xlsx")
    assert excel.status_code == 200, excel.text
    assert excel.headers["x-audit-excel-qa"] == "PASS"
    review_url = f"/api/admin/jobs/{code}/checkpoint/report-drafts/{excel.headers['x-audit-excel-draft-id']}/review"
    sections = ["summary_counts", "ledger_rows", "source_formatting", "severity_colors", "pdf_alignment"]
    reviewed = admin.post(review_url, json={
        "workbook_sha256": excel.headers["x-audit-excel-sha256"], "result": "PASS",
        "reason": "Đã đối chiếu workbook synthetic với ledger và nguồn.", "reviewed_sections": sections,
    })
    assert reviewed.status_code == 200, reviewed.text

    signoff_url = f"/api/admin/jobs/{code}/checkpoint/release-signoff"
    signoff_body = {
        "snapshot_digest": excel.headers["x-audit-snapshot-digest"],
        "classification_digest": excel.headers["x-audit-classification-digest"],
        "pdf_draft_id": annotation.json()["draft_id"], "pdf_sha256": annotation.json()["output_pdf_sha256"],
        "excel_draft_id": int(excel.headers["x-audit-excel-draft-id"]), "excel_sha256": excel.headers["x-audit-excel-sha256"],
        "result": "APPROVE_FULL", "accepted_partial": False, "reviewed_unmarked_pdf": True,
        "reason": "Kỹ sư test đã xác nhận toàn bộ hồ sơ synthetic.",
    }
    with SessionLocal() as db:
        passed_gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == claim[1]).order_by(AuditRunStageGate.id.desc()).limit(1))
        assert passed_gate is not None and passed_gate.pass_no == 3
        blocked_receipt = json.loads(passed_gate.receipt_json)
        blocked_receipt.update(snapshot_id="synthetic-pass-3-blocked", controller_status="BLOCKED", validator_exit_code=2,
                               candidate_release_status="BLOCKED", blocking_checks=["synthetic Pass 3 blocker"])
        blocked_json = json.dumps(blocked_receipt, sort_keys=True, separators=(",", ":"))
        blocked_gate = AuditRunStageGate(
            run_id=claim[1], pass_no=3, checkpoint_version=passed_gate.checkpoint_version,
            snapshot_id=blocked_receipt["snapshot_id"], stage="pass3", receipt_json=blocked_json,
            receipt_digest=hashlib.sha256(blocked_json.encode()).hexdigest(), advanced=False,
        )
        db.add(blocked_gate)
        db.commit()
        blocked_gate_id = blocked_gate.id
    blocked_signoff = admin.post(signoff_url, json=signoff_body)
    assert blocked_signoff.status_code == 409, blocked_signoff.text
    with SessionLocal() as db:
        db.delete(db.get(AuditRunStageGate, blocked_gate_id))
        db.commit()
    signoff = admin.post(signoff_url, json=signoff_body)
    assert signoff.status_code == 201, signoff.text
    assert signoff.json()["is_current"] is True
    published = admin.post(f"/api/admin/jobs/{code}/complete")
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "COMPLETED"
    with SessionLocal() as db:
        outputs = list(db.scalars(select(JobOutput).where(JobOutput.job_id == job_id)))
        assert {row.file_type for row in outputs} == {OutputType.EXCEL_REPORT, OutputType.ANNOTATED_PDF}
        published_by_type = {row.file_type: Path(row.file_path).read_bytes() for row in outputs}
        assert published_by_type[OutputType.ANNOTATED_PDF] and hashlib.sha256(published_by_type[OutputType.ANNOTATED_PDF]).hexdigest() == annotation.json()["output_pdf_sha256"]
        assert published_by_type[OutputType.EXCEL_REPORT] == excel.content
    for output in published.json()["outputs"]:
        assert owner_client.get(f"/api/jobs/{code}/outputs/{output['id']}/download").status_code == 200
