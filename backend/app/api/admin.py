import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import admin_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import AuditAnnotationDraft, AuditAnnotationReviewEvent, AuditExcelReportDraft, AuditExcelReportReviewEvent, AuditInventoryApproval, AuditIssueClassification, AuditReleaseSignoffEvent, AuditRun, AuditRunCheckpoint, AuditRunGateRepairRequest, AuditRunStageGate, Job, JobOutput, JobStatus, OutputType, User
from app.schemas.api import AdminAnnotationDraftRequest, AdminAnnotationDraftResponse, AdminAnnotationMarkReviewRequest, AdminExcelReportReviewRequest, AdminExcelReportReviewResponse, AdminGateRepairRequest, AdminGateRepairResponse, AdminInventoryApprovalRequest, AdminInventoryApprovalResponse, AdminInventoryResponse, AdminIssueClassificationRequest, AdminIssueClassificationResponse, AdminIssueListResponse, AdminJobListResponse, AdminJobResponse, AdminQualityDetails, AdminQualityResponse, AdminReleaseSignoffRequest, AdminReleaseSignoffResponse, AdminSanityResponse, AdminStageGateResponse, AdminUpdateJob, AttemptArtifactsResponse, MessageResponse, OutputResponse
from app.services import audit_quality as quality
from app.services.audit_annotation_draft import AnnotationDraftError, render_annotation_draft, render_annotation_review_images, render_source_page, summarize_annotation_reviews
from app.services.audit_checkpoint import CheckpointError, load_run_checkpoint, recover_checkpoint, retry_checkpoint_batch, validate_repair_dependency_scope
from app.services.audit_coverage import CoverageInputError, calculate_coverage, validate_inventory, validate_pass1_source_coverage
from app.services.audit_excel_report import ReportExportError, build_excel_report_draft, validate_excel_report_draft
from app.services.audit_inventory_scaffold import SourceScaffoldError, build_source_inventory
from app.services.audit_quality_invalidation import has_quality_review, latest_event, latest_review, record_mutation_invalidation
from app.services.audit_report_source import build_classification_state, build_report_source
from app.services.audit_runner import _source_digest
from app.services.audit_sanity import check_detail_rows
from app.services.audit_stage_gate import assess_pass3_closure
from app.storage.files import EXCEL_MIMES, PDF_MIMES, random_stored_name, save_upload, stored_job_file, stream_file, validate_upload

router = APIRouter(prefix="/admin/jobs", tags=["admin"])
logger = logging.getLogger("boq-audit.admin")
BUSY_STATUSES = {JobStatus.SUBMITTED, JobStatus.PROCESSING}
ARTIFACT_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".json", ".md"}
PRIVATE_NAMES = ("log", "draft", "stdout", "stderr", "codex")


def _lock_job(db: Session, job_code: str) -> Job:
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    return find_job(db, job_code)


def _artifacts(job_code: str, output_dir: Path) -> list[dict]:
    if not output_dir.is_dir() or not output_dir.resolve().is_relative_to((get_settings().jobs_dir / job_code).resolve()):
        return []
    root = output_dir.resolve()
    files = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        relative = path.relative_to(output_dir)
        if path.suffix.lower() not in ARTIFACT_EXTENSIONS or any(any(word in part.lower() for word in PRIVATE_NAMES) for part in relative.parts):
            continue
        files.append({"name": relative.as_posix(), "size": path.stat().st_size})
    return files


def _artifact_path(job_code: str, output_dir: Path, name: str) -> Path:
    root = output_dir.resolve()
    if not root.is_relative_to((get_settings().jobs_dir / job_code).resolve()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tệp không tồn tại")
    path = stored_job_file(job_code, str(output_dir / name))
    if not path.is_relative_to(root) or path.suffix.lower() not in ARTIFACT_EXTENSIONS or any(any(word in part.lower() for word in PRIVATE_NAMES) for part in Path(name).parts):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tệp không tồn tại")
    return path


def find_job(db: Session, job_code: str) -> Job:
    job = db.scalar(
        select(Job)
        .options(selectinload(Job.outputs), selectinload(Job.estimate_input), selectinload(Job.narrative_input), selectinload(Job.user))
        .where(Job.job_code == job_code)
    )
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy hồ sơ")
    return job


def _has_controlled_audit_history(db: Session, job_id: int) -> bool:
    return (
        db.scalar(select(AuditInventoryApproval.id).where(AuditInventoryApproval.job_id == job_id).limit(1)) is not None
        or db.scalar(select(AuditRunCheckpoint.run_id).join(AuditRun, AuditRun.id == AuditRunCheckpoint.run_id)
                     .where(AuditRun.job_id == job_id).limit(1)) is not None
    )


@router.get("", response_model=AdminJobListResponse)
async def list_admin_jobs(
    search: str | None = Query(default=None, max_length=200),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    query = select(Job).join(Job.user).options(
        selectinload(Job.outputs), selectinload(Job.estimate_input), selectinload(Job.narrative_input), selectinload(Job.user)
    )
    if search:
        needle = f"%{search.strip()}%"
        query = query.where(or_(Job.job_code.ilike(needle), Job.project_name.ilike(needle), User.name.ilike(needle), User.email.ilike(needle)))
    if job_status:
        query = query.where(Job.status == job_status)
    items = list(db.scalars(query.order_by(Job.created_at.desc())).all())
    return {"items": items, "total": len(items)}


@router.get("/{job_code}", response_model=AdminJobResponse)
async def get_admin_job(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    return find_job(db, job_code)


def _quality_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _quality_strings(value: object, *, max_items: int = 100, max_length: int = 200) -> list[str] | None:
    if not isinstance(value, list) or len(value) > max_items or any(not isinstance(item, str) or len(item) > max_length for item in value):
        return None
    return value


def _stage_gate_history(db: Session, run: AuditRun | None) -> list[AdminStageGateResponse]:
    if run is None:
        return []
    rows = db.scalars(select(AuditRunStageGate).where(AuditRunStageGate.run_id == run.id).order_by(AuditRunStageGate.id.desc()).limit(20))
    history = []
    for row in rows:
        try:
            raw = row.receipt_json.encode("utf-8")
            receipt = quality.strict_json_loads(raw, label="stored stage gate receipt")
            blockers = _quality_strings(receipt.get("blocking_checks"), max_items=100, max_length=1000) if isinstance(receipt, dict) else None
            valid = (
                hashlib.sha256(raw).hexdigest() == row.receipt_digest
                and receipt.get("schema_version") == 1
                and receipt.get("run_id") == row.run_id
                and receipt.get("stage") == row.stage == f"pass{row.pass_no}"
                and receipt.get("snapshot_id") == row.snapshot_id
                and receipt.get("controller_status") in {"PASS", "BLOCKED", "ERROR"}
                and (receipt.get("candidate_release_status") is None or isinstance(receipt.get("candidate_release_status"), str))
                and blockers is not None
                and (not row.advanced or (
                    receipt.get("controller_status") == "PASS"
                    and receipt.get("validator_exit_code") == 0
                    and receipt.get("candidate_release_status") == quality.STAGE_COMPLETE_STATUS
                    and receipt.get("open_record_count") == 0
                ))
            )
        except (AttributeError, TypeError, UnicodeError, quality.StrictJsonError):
            valid = False
        history.append(AdminStageGateResponse(
            attempt=run.attempt, pass_no=row.pass_no, checkpoint_version=row.checkpoint_version,
            snapshot_id=row.snapshot_id, controller_status=receipt["controller_status"] if valid else "ERROR",
            candidate_release_status=receipt.get("candidate_release_status") if valid else None,
            blocking_checks=blockers if valid else ["Bản ghi gate lưu trữ không qua kiểm tra toàn vẹn."],
            error_code=row.error_code if valid else "GATE_HISTORY_INVALID",
            advanced=row.advanced if valid else False, integrity_status="VERIFIED" if valid else "INVALID",
            created_at=row.created_at,
        ))
    return history


def _gate_repair_status(job: Job, checkpoint: dict | None, gates: list[AdminStageGateResponse]) -> str:
    if not gates or gates[0].advanced or gates[0].controller_status == "PASS":
        return "NOT_APPLICABLE"
    gate = gates[0]
    if gate.integrity_status != "VERIFIED":
        return "GATE_INVALID"
    if checkpoint is None:
        return "CHECKPOINT_UNAVAILABLE"
    if gate.pass_no != checkpoint["pass_no"] or gate.checkpoint_version != checkpoint["version"] or job.status != JobStatus.WAITING_FOR_INFO or checkpoint["status"] != "READY":
        return "NOT_READY"
    if gate.controller_status == "ERROR":
        return "VALIDATOR_ERROR"
    if gate.pass_no == 3 and assess_pass3_closure(checkpoint)["controller_contract_blocked"]:
        return "CONTROLLER_CONTRACT"
    return "UNMAPPED_BLOCKER"


def _current_report_source(db: Session, job: Job):
    run = max(job.audit_runs, key=lambda item: item.attempt, default=None)
    if run is None or db.get(AuditRunCheckpoint, run.id) is None:
        raise CheckpointError("latest audit run has no checkpoint")
    settings = get_settings()
    pinned = quality.verify_pinned_bundle(
        settings.boq_audit_skill_path.resolve(strict=True).parent,
        Path(__file__).resolve().parents[1] / "boq_audit_skill_manifest.json",
        expected_digest=quality.TRUSTED_SKILL_DIGEST,
    )
    checkpoint = load_run_checkpoint(db, run_id=run.id, current_source_digest=_source_digest(job), current_skill_digest=pinned.digest)
    return run, checkpoint, build_report_source(checkpoint)


def _report_classifications(db: Session, run_id: int, source: dict):
    rows = db.scalars(select(AuditIssueClassification).where(
        AuditIssueClassification.run_id == run_id,
        AuditIssueClassification.snapshot_digest == source["snapshot_digest"],
    )).all()
    return build_classification_state(source, [{
        "issue_id": row.issue_id, "revision_no": row.revision_no,
        "final_status": row.final_status, "severity": row.severity,
        "reviewer_user_id": row.reviewer_user_id, "reason": row.reason,
    } for row in rows])


def _annotation_review_events(db: Session, draft_id: int) -> list[dict]:
    rows = db.scalars(select(AuditAnnotationReviewEvent).where(AuditAnnotationReviewEvent.draft_id == draft_id)).all()
    return [{"mark_id": row.mark_id, "revision_no": row.revision_no, "result": row.result, "output_pdf_sha256": row.output_pdf_sha256} for row in rows]


def _current_annotation_report_qa(db: Session, job: Job, run_id: int, source: dict, classifications: dict) -> dict | None:
    draft = db.scalar(select(AuditAnnotationDraft).where(
        AuditAnnotationDraft.run_id == run_id,
        AuditAnnotationDraft.snapshot_digest == source["snapshot_digest"],
        AuditAnnotationDraft.classification_digest == classifications["classification_digest"],
    ).order_by(AuditAnnotationDraft.id.desc()).limit(1))
    if draft is None:
        return None
    try:
        pdf = stored_job_file(job.job_code, draft.file_path).read_bytes()
        if hashlib.sha256(pdf).hexdigest() != draft.output_pdf_sha256:
            return None
    except (HTTPException, OSError):
        return None
    annotation_qa = json.loads(draft.qa_json)
    review = summarize_annotation_reviews(annotation_qa, _annotation_review_events(db, draft.id))
    return {
        **annotation_qa,
        "annotation_qa_status": review["annotation_qa_status"],
        "marks": [{
            **mark,
            "annotation_qa_status": review["latest_reviews"].get(mark["mark_id"], {}).get("result", "UNVERIFIED"),
        } for mark in annotation_qa["marks"]],
    }


def _annotation_draft_response(db: Session, row: AuditAnnotationDraft, source: dict | None, classifications: dict | None) -> AdminAnnotationDraftResponse:
    qa = json.loads(row.qa_json)
    review = summarize_annotation_reviews(qa, _annotation_review_events(db, row.id))
    try:
        output_matches = hashlib.sha256(Path(row.file_path).read_bytes()).hexdigest() == row.output_pdf_sha256
    except OSError:
        output_matches = False
    current = bool(
        output_matches and source is not None and classifications is not None
        and row.snapshot_digest == source["snapshot_digest"]
        and row.classification_digest == classifications["classification_digest"]
    )
    return AdminAnnotationDraftResponse(
        draft_id=row.id, snapshot_digest=row.snapshot_digest,
        classification_digest=row.classification_digest,
        output_pdf_sha256=row.output_pdf_sha256,
        annotation_qa_status=review["annotation_qa_status"] if current else "UNVERIFIED",
        missing_active_issue_ids=qa["missing_active_issue_ids"],
        review_counts=review["counts"],
        marks=[{
            "mark_id": mark["mark_id"], "issue_id": mark["issue_id"], "severity": mark["severity"],
            "lineage_ids": mark.get("lineage_ids", []),
            "pdf_page": mark["pdf_page"], "note": mark["note"],
            "review_status": review["latest_reviews"].get(mark["mark_id"], {}).get("result", "UNVERIFIED"),
        } for mark in qa["marks"]],
        is_current=current,
    )


@router.get("/{job_code}/quality", response_model=AdminQualityResponse)
async def get_admin_job_quality(job_code: str, response: Response, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    job = find_job(db, job_code)
    controlled_audit_history = _has_controlled_audit_history(db, job.id)
    review = latest_review(db, job.id)
    coverage = None
    sanity = AdminSanityResponse(status="UNAVAILABLE")
    coverage_status = "UNAVAILABLE"
    coverage_limitation = "Chưa có checkpoint hợp lệ gắn với inventory đã duyệt và nguồn hiện tại."
    run = db.scalar(select(AuditRun).where(AuditRun.job_id == job.id).order_by(AuditRun.attempt.desc()).limit(1))
    stage_gates = _stage_gate_history(db, run)
    repair_requests = list(db.scalars(select(AuditRunGateRepairRequest).where(AuditRunGateRepairRequest.run_id == run.id).order_by(AuditRunGateRepairRequest.id.desc()))) if run else []
    repair_attempts = 0
    repair_request_status = None
    report_source = None
    checkpoint = None
    checkpoint_row = db.get(AuditRunCheckpoint, run.id) if run else None
    if checkpoint_row is not None:
        settings = get_settings()
        try:
            pinned = quality.verify_pinned_bundle(
                settings.boq_audit_skill_path.resolve(strict=True).parent,
                Path(__file__).resolve().parents[1] / "boq_audit_skill_manifest.json",
                expected_digest=quality.TRUSTED_SKILL_DIGEST,
            )
            checkpoint = load_run_checkpoint(
                db,
                run_id=run.id,
                current_source_digest=_source_digest(job),
                current_skill_digest=pinned.digest,
            )
            source = build_report_source(checkpoint)
            report_source = {key: source[key] for key in ("snapshot_digest", "checkpoint_version", "contract_version", "ledger_record_counts", "issue_final_status_counts", "issue_candidate_count", "pdf_mark_required_issue_count")}
            classification = _report_classifications(db, run.id, source)
            report_source.update({key: classification[key] for key in ("classification_digest", "classified_issue_count", "unclassified_issue_count")})
            current_bases = {item["base_result_id"] for item in checkpoint["batch_results"] if item["pass_no"] == checkpoint["pass_no"]}
            current_requests = [item for item in repair_requests if item.base_result_id in current_bases]
            repair_attempts = len(current_requests)
            repair_request_status = current_requests[0].status if current_requests else None
            sanity_result = check_detail_rows(checkpoint["ledger_projection"].get("detail_rows"))
            sanity = AdminSanityResponse(**sanity_result)
            coverage = calculate_coverage(checkpoint["inventory"], checkpoint["ledger"])
            coverage.update({
                "pass_no": checkpoint["pass_no"],
                "checkpoint_status": checkpoint["status"],
                "checkpoint_version": checkpoint["version"],
                "contract_version": checkpoint.get("contract_version", 3),
                "work_items": [
                    {key: item[key] for key in ("check_id", "pass_no", "execution_status", "result_status")}
                    for item in checkpoint["ledger"]
                ],
                "repair_batches": checkpoint["batch_plan"][str(checkpoint["pass_no"])],
            })
            lesson_snapshot = checkpoint.get("lesson_snapshot")
            dispositions = lesson_snapshot.get("dispositions", []) if isinstance(lesson_snapshot, dict) else []
            coverage["lesson_selection"] = {
                "status": "MISSING" if lesson_snapshot is None else "BLOCKED" if lesson_snapshot.get("blocking_lesson_ids") else "PINNED",
                "selection_digest": checkpoint.get("lesson_snapshot_digest"),
                "selected_count": sum(item.get("disposition") == "SELECTED" for item in dispositions if isinstance(item, dict)),
                "pending_review_count": sum(item.get("disposition") == "PENDING_REVIEW" for item in dispositions if isinstance(item, dict)),
                "unresolved_count": sum(item.get("disposition") == "UNRESOLVED" for item in dispositions if isinstance(item, dict)),
            }
            if checkpoint["pass_no"] == 3:
                coverage["pass3_closure"] = assess_pass3_closure(checkpoint)
            coverage_status = coverage["coverage_status"]
            coverage_limitation = "Chỉ phản ánh các scope/work item đã khai báo; chưa chứng minh inventory bao quát mọi chi tiết bản vẽ."
        except (CheckpointError, quality.QualityAdapterError, OSError, ValueError, CoverageInputError) as exc:
            if isinstance(exc, CheckpointError) and str(exc).startswith("LEGACY_V3_UNVERIFIED:"):
                coverage_status = "LEGACY_V3_UNVERIFIED"
                coverage_limitation = "Checkpoint v3 được giữ làm lịch sử; không diễn giải lại hoặc resume theo contract v4. Cần tạo attempt mới."
    if review is None:
        return AdminQualityResponse(job_code=job_code, controlled_audit_history=controlled_audit_history, freshness_status="NOT_REVIEWED", coverage_status=coverage_status, coverage=coverage, coverage_limitation=coverage_limitation, sanity=sanity, stage_gates=stage_gates, gate_repair_status=_gate_repair_status(job, checkpoint, stage_gates), gate_repair_attempts=repair_attempts, gate_repair_request_status=repair_request_status, report_source=report_source)

    event = latest_event(db, review.id)
    try:
        aggregate = json.loads(review.quality_aggregate)
    except (TypeError, json.JSONDecodeError):
        aggregate = None
    if not isinstance(aggregate, dict):
        aggregate = {}
    quality_details = AdminQualityDetails(
        controller_status=review.controller_status,
        candidate_release_status=review.candidate_release_status,
        validator_exit_code=aggregate.get("validator_exit_code") if isinstance(aggregate.get("validator_exit_code"), int) and not isinstance(aggregate.get("validator_exit_code"), bool) else None,
        open_record_count=_quality_int(aggregate.get("open_record_count")),
        open_issue_count=_quality_int(aggregate.get("open_issue_count")),
        blocking_checks=_quality_strings(aggregate.get("blocking_checks")),
        critical_errors=_quality_int(aggregate.get("critical_errors")),
        warnings=_quality_int(aggregate.get("warnings")),
        error_code=aggregate.get("error_code") if isinstance(aggregate.get("error_code"), str) and len(aggregate["error_code"]) <= 100 else None,
        missing_outputs=_quality_strings(aggregate.get("missing_outputs"), max_items=20, max_length=64),
    )
    review_run = db.get(AuditRun, review.run_id)
    invalidated = event is not None and event.event_type in {"REVOKED", "QUALITY_INVALIDATED"}
    return AdminQualityResponse(
        job_code=job_code,
        controlled_audit_history=controlled_audit_history,
        attempt=review_run.attempt if review_run else None,
        stage=review.stage,
        freshness_status="INVALIDATED" if invalidated else "NOT_VERIFIED",
        invalidation_event_type=event.event_type if invalidated else None,
        quality=quality_details,
        coverage_status=coverage_status,
        coverage=coverage,
        coverage_limitation=coverage_limitation,
        sanity=sanity,
        stage_gates=stage_gates,
        gate_repair_status=_gate_repair_status(job, checkpoint, stage_gates),
        gate_repair_attempts=repair_attempts,
        gate_repair_request_status=repair_request_status,
        report_source=report_source,
    )



def _allowed_inventory_reviewers() -> set[str]:
    return get_settings().audit_quality_reviewer_email_set


def _job_source_scaffold(job: Job):
    pdf_path = job.input_file_path
    xlsx_path = job.estimate_input.file_path if job.estimate_input else None
    return build_source_inventory(pdf_path=pdf_path, xlsx_path=xlsx_path)


def _current_job_inventory_context(job: Job):
    source_digest = _source_digest(job)
    scaffold = _job_source_scaffold(job)
    if _source_digest(job) != source_digest:
        raise SourceScaffoldError("source changed while deriving inventory")
    return source_digest, scaffold


@router.get("/{job_code}/checkpoint/issues", response_model=AdminIssueListResponse)
async def list_admin_checkpoint_issues(
    job_code: str, response: Response,
    offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
    _: User = Depends(admin_user), db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    job = find_job(db, job_code)
    try:
        run, _checkpoint, source = _current_report_source(db, job)
    except (CheckpointError, quality.QualityAdapterError, OSError, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    records = sorted((item for item in source["ledger_records"] if item["record_type"] == "issues"), key=lambda item: item["record_id"])
    classification = _report_classifications(db, run.id, source)
    decisions = classification["decisions"]
    issues = [{
        "issue_id": item["record_id"], "claim": item["payload"]["claim"],
        "final_status": item["payload"]["final_status"], "source_refs": item["source_refs"],
        "lineage_ids": item["payload"].get("lineage_ids", []),
        "severity": decisions[item["record_id"]]["severity"] if item["record_id"] in decisions else None,
        "review_reason": decisions[item["record_id"]]["reason"] if item["record_id"] in decisions else None,
        "classified": item["record_id"] in decisions,
    } for item in records[offset:offset + limit]]
    return AdminIssueListResponse(snapshot_digest=source["snapshot_digest"], classification_digest=classification["classification_digest"], issues=issues, total=len(records), offset=offset, limit=limit)


@router.post("/{job_code}/checkpoint/issues/{issue_id}/classification", response_model=AdminIssueClassificationResponse)
async def classify_admin_checkpoint_issue(
    job_code: str, issue_id: str, body: AdminIssueClassificationRequest,
    admin: User = Depends(admin_user), db: Session = Depends(get_db),
):
    if admin.email.strip().lower() not in _allowed_inventory_reviewers():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Issue classification requires a configured quality reviewer")
    job = _lock_job(db, job_code)
    if job.status not in {JobStatus.WAITING_FOR_INFO, JobStatus.REVIEW}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Issue classification requires a paused audit")
    try:
        run, checkpoint, source = _current_report_source(db, job)
        if checkpoint["pass_no"] != 3 or body.snapshot_digest != source["snapshot_digest"]:
            raise CheckpointError("issue classification snapshot is stale or Pass 3 is unavailable")
        issue = next((item for item in source["ledger_records"] if item["record_type"] == "issues" and item["record_id"] == issue_id), None)
        if issue is None or body.final_status != issue["payload"]["final_status"]:
            raise CheckpointError("issue is absent or its final status changed")
        if (body.final_status in {"FALSE_POSITIVE", "CLOSED_WITH_EXPLANATION"}) != (body.severity is None):
            raise CheckpointError("closed/false-positive issues have no severity; open issues require one")
        reason = body.reason.strip()
        if len(reason) < 12:
            raise CheckpointError("review reason is too short")
        previous = db.scalar(select(AuditIssueClassification).where(
            AuditIssueClassification.run_id == run.id,
            AuditIssueClassification.snapshot_digest == source["snapshot_digest"],
            AuditIssueClassification.issue_id == issue_id,
        ).order_by(AuditIssueClassification.revision_no.desc()).limit(1))
        if previous is not None and (previous.final_status, previous.severity, previous.reason) == (body.final_status, body.severity, reason):
            raise CheckpointError("issue classification is unchanged")
        decision = AuditIssueClassification(
            run_id=run.id, snapshot_digest=source["snapshot_digest"], issue_id=issue_id,
            revision_no=previous.revision_no + 1 if previous else 1,
            final_status=body.final_status, severity=body.severity,
            reviewer_user_id=admin.id, reason=reason,
        )
        db.add(decision)
        record_mutation_invalidation(db, run.job_id, admin.id, f"issue classification changed: {issue_id}")
        db.commit()
    except (CheckpointError, quality.QualityAdapterError, OSError, ValueError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AdminIssueClassificationResponse(issue_id=issue_id, snapshot_digest=decision.snapshot_digest, final_status=decision.final_status, severity=decision.severity, reviewer_user_id=admin.id)


@router.post("/{job_code}/checkpoint/annotation-drafts", response_model=AdminAnnotationDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_admin_annotation_draft(
    job_code: str, body: AdminAnnotationDraftRequest,
    admin: User = Depends(admin_user), db: Session = Depends(get_db),
):
    if admin.email.strip().lower() not in _allowed_inventory_reviewers():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Annotation drafts require a configured quality reviewer")
    job = _lock_job(db, job_code)
    if job.status not in {JobStatus.WAITING_FOR_INFO, JobStatus.REVIEW}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Annotation drafts require a paused audit")
    destination = None
    try:
        run, checkpoint, source = _current_report_source(db, job)
        if checkpoint["pass_no"] != 3:
            raise AnnotationDraftError("annotation drafts require Pass 3")
        classifications = _report_classifications(db, run.id, source)
        font = next((path for path in (
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/noto/NotoSans-Regular.ttf"),
        ) if path.is_file()), None)
        if font is None:
            raise AnnotationDraftError("Vietnamese annotation font is unavailable")
        pdf, qa = render_annotation_draft(
            pdf_path=stored_job_file(job.job_code, job.input_file_path),
            inventory=checkpoint["inventory"], source=source,
            classifications=classifications, marks=body.marks, font_path=font,
        )
        destination = get_settings().jobs_dir / job.job_code / "annotation_drafts" / f"{uuid4().hex}.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as output:
            output.write(pdf)
            output.flush()
            os.fsync(output.fileno())
        row = AuditAnnotationDraft(
            run_id=run.id, reviewer_user_id=admin.id,
            snapshot_digest=source["snapshot_digest"],
            classification_digest=classifications["classification_digest"],
            source_pdf_sha256=qa["source_pdf_sha256"],
            output_pdf_sha256=qa["output_pdf_sha256"],
            file_path=str(destination), marks_json=json.dumps(body.marks, ensure_ascii=False),
            qa_json=json.dumps(qa, ensure_ascii=False),
        )
        db.add(row)
        db.flush()
        response = _annotation_draft_response(db, row, source, classifications)
        db.commit()
        return response
    except (CheckpointError, quality.QualityAdapterError, AnnotationDraftError, OSError, ValueError, TypeError, KeyError, IntegrityError) as exc:
        db.rollback()
        if destination is not None:
            destination.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/{job_code}/checkpoint/annotation-source/{page_id}")
async def preview_admin_annotation_source(
    job_code: str, page_id: str, dpi: int = Query(default=300, ge=72, le=600),
    _: User = Depends(admin_user), db: Session = Depends(get_db),
):
    job = find_job(db, job_code)
    try:
        run, checkpoint, source = _current_report_source(db, job)
        if checkpoint["pass_no"] != 3:
            raise AnnotationDraftError("annotation source preview requires Pass 3")
        pages = [item for item in checkpoint["inventory"]["pages"] if item["page_id"] == page_id and item["document_id"] == "pdf"]
        documents = [item for item in checkpoint["inventory"]["documents"] if item["document_id"] == "pdf"]
        if len(pages) != 1 or len(documents) != 1:
            raise AnnotationDraftError("source page is absent from the approved inventory")
        pdf = stored_job_file(job.job_code, job.input_file_path).read_bytes()
        pdf_sha = hashlib.sha256(pdf).hexdigest()
        if f"sha256:{pdf_sha}" != documents[0]["revision_digest"] or pages[0]["revision_digest"] != f"sha256:{pdf_sha}":
            raise AnnotationDraftError("source PDF differs from the approved inventory revision")
        image, (width, height) = render_source_page(pdf, page_number=pages[0]["pdf_page"], dpi=dpi)
        classification = _report_classifications(db, run.id, source)
    except (CheckpointError, quality.QualityAdapterError, AnnotationDraftError, OSError, ValueError, KeyError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return Response(content=image, media_type="image/png", headers={
        "Cache-Control": "private, no-store", "X-Render-DPI": str(dpi),
        "X-Render-Width": str(width), "X-Render-Height": str(height),
        "X-Source-PDF-SHA256": pdf_sha,
        "X-Audit-Snapshot-Digest": source["snapshot_digest"],
        "X-Classification-Digest": classification["classification_digest"],
    })


@router.get("/{job_code}/checkpoint/annotation-drafts/latest", response_model=AdminAnnotationDraftResponse)
async def get_latest_admin_annotation_draft(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    run = max(job.audit_runs, key=lambda item: item.attempt, default=None)
    row = db.scalar(select(AuditAnnotationDraft).where(AuditAnnotationDraft.run_id == run.id).order_by(AuditAnnotationDraft.id.desc()).limit(1)) if run else None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No annotation draft exists for the latest run")
    try:
        _, _, source = _current_report_source(db, job)
        classifications = _report_classifications(db, run.id, source)
    except (CheckpointError, quality.QualityAdapterError, OSError, ValueError):
        source, classifications = None, None
    try:
        return _annotation_draft_response(db, row, source, classifications)
    except (AnnotationDraftError, ValueError, KeyError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Annotation draft QA ledger is invalid") from exc


@router.get("/{job_code}/checkpoint/annotation-drafts/{draft_id}/download")
async def download_admin_annotation_draft(job_code: str, draft_id: int, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    run_ids = {run.id for run in job.audit_runs}
    row = db.get(AuditAnnotationDraft, draft_id)
    if row is None or row.run_id not in run_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation draft not found")
    path = stored_job_file(job.job_code, row.file_path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != row.output_pdf_sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "Annotation draft PDF no longer matches its recorded digest")
    return stream_file(path, f"annotation-draft-{draft_id}.pdf", "application/pdf")


def _annotation_mark_material(db: Session, job: Job, draft_id: int, mark_id: str):
    row = db.get(AuditAnnotationDraft, draft_id)
    if row is None or row.run_id not in {run.id for run in job.audit_runs}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation draft not found")
    qa = json.loads(row.qa_json)
    mark = next((item for item in qa["marks"] if item["mark_id"] == mark_id), None)
    if mark is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation mark not found")
    pdf = stored_job_file(job.job_code, row.file_path).read_bytes()
    if hashlib.sha256(pdf).hexdigest() != row.output_pdf_sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "Annotation draft PDF no longer matches its recorded digest")
    return row, mark, pdf


@router.get("/{job_code}/checkpoint/annotation-drafts/{draft_id}/marks/{mark_id}/preview/{kind}")
async def preview_admin_annotation_mark(
    job_code: str, draft_id: int, mark_id: str, kind: str,
    _: User = Depends(admin_user), db: Session = Depends(get_db),
):
    if kind not in {"target", "note", "page"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation review image not found")
    job = find_job(db, job_code)
    row, mark, pdf = _annotation_mark_material(db, job, draft_id, mark_id)
    try:
        image = render_annotation_review_images(pdf, mark)[kind]
    except (AnnotationDraftError, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return Response(content=image, media_type="image/png", headers={
        "Cache-Control": "private, no-store", "X-Review-Image-SHA256": hashlib.sha256(image).hexdigest(),
        "X-Annotation-PDF-SHA256": row.output_pdf_sha256,
    })


@router.post("/{job_code}/checkpoint/annotation-drafts/{draft_id}/marks/{mark_id}/review", response_model=AdminAnnotationDraftResponse)
async def review_admin_annotation_mark(
    job_code: str, draft_id: int, mark_id: str, body: AdminAnnotationMarkReviewRequest,
    admin: User = Depends(admin_user), db: Session = Depends(get_db),
):
    if admin.email.strip().lower() not in _allowed_inventory_reviewers():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Annotation review requires a configured quality reviewer")
    job = _lock_job(db, job_code)
    if job.status not in {JobStatus.WAITING_FOR_INFO, JobStatus.REVIEW}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Annotation review requires a paused audit")
    try:
        run, checkpoint, source = _current_report_source(db, job)
        if checkpoint["pass_no"] != 3:
            raise AnnotationDraftError("annotation review requires Pass 3")
        classifications = _report_classifications(db, run.id, source)
        row, mark, pdf = _annotation_mark_material(db, job, draft_id, mark_id)
        if row.run_id != run.id or row.snapshot_digest != source["snapshot_digest"] or row.classification_digest != classifications["classification_digest"] or body.output_pdf_sha256 != row.output_pdf_sha256:
            raise AnnotationDraftError("annotation review draft is stale")
        images = render_annotation_review_images(pdf, mark)
        expected = {kind: hashlib.sha256(image).hexdigest() for kind, image in images.items()}
        if (body.target_crop_sha256, body.note_crop_sha256, body.page_render_sha256) != (expected["target"], expected["note"], expected["page"]):
            raise AnnotationDraftError("reviewed images differ from the final PDF render")
        reason = body.reason.strip()
        if len(reason) < 12:
            raise AnnotationDraftError("annotation review reason is too short")
        previous = db.scalar(select(AuditAnnotationReviewEvent).where(
            AuditAnnotationReviewEvent.draft_id == row.id, AuditAnnotationReviewEvent.mark_id == mark_id,
        ).order_by(AuditAnnotationReviewEvent.revision_no.desc()).limit(1))
        if previous is not None and (previous.result, previous.reason) == (body.result, reason):
            raise AnnotationDraftError("annotation review is unchanged")
        db.add(AuditAnnotationReviewEvent(
            draft_id=row.id, mark_id=mark_id, revision_no=previous.revision_no + 1 if previous else 1,
            reviewer_user_id=admin.id, result=body.result, reason=reason,
            output_pdf_sha256=row.output_pdf_sha256,
            target_crop_sha256=expected["target"], note_crop_sha256=expected["note"],
            page_render_sha256=expected["page"],
        ))
        record_mutation_invalidation(db, run.job_id, admin.id, f"annotation mark review changed: {mark_id}")
        db.flush()
        response = _annotation_draft_response(db, row, source, classifications)
        db.commit()
        return response
    except (CheckpointError, quality.QualityAdapterError, AnnotationDraftError, OSError, ValueError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/{job_code}/checkpoint/report-draft.xlsx")
async def download_admin_checkpoint_report_draft(job_code: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    destination = None
    try:
        run, checkpoint, source = _current_report_source(db, job)
        if checkpoint["pass_no"] != 3:
            raise ReportExportError("Excel report draft requires Pass 3")
        classifications = _report_classifications(db, run.id, source)
        annotation_qa = _current_annotation_report_qa(db, job, run.id, source, classifications)
        source_workbook = stored_job_file(job.job_code, job.estimate_input.file_path) if job.estimate_input else None
        prior = db.scalars(select(AuditExcelReportDraft).where(
            AuditExcelReportDraft.run_id == run.id,
            AuditExcelReportDraft.snapshot_digest == source["snapshot_digest"],
            AuditExcelReportDraft.classification_digest == classifications["classification_digest"],
        ).order_by(AuditExcelReportDraft.id.desc())).all()
        annotation_qa_digest = hashlib.sha256(
            json.dumps(annotation_qa, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest() if annotation_qa else None
        for candidate in prior:
            try:
                qa = json.loads(candidate.qa_json)
                path = stored_job_file(job.job_code, candidate.file_path)
                actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if (candidate.annotation_pdf_sha256 == (annotation_qa or {}).get("output_pdf_sha256")
                    and actual_sha == candidate.workbook_sha256 and qa.get("status") == "PASS"
                    and qa.get("annotation_qa_digest") == annotation_qa_digest):
                    workbook, qa_row = path.read_bytes(), candidate
                    break
            except (HTTPException, OSError, ValueError):
                continue
        else:
            coverage = calculate_coverage(checkpoint["inventory"], checkpoint["ledger"])
            workbook = build_excel_report_draft(
                source=source, classifications=classifications, source_workbook=source_workbook,
                annotation_qa=annotation_qa, coverage=coverage,
            )
            qa = validate_excel_report_draft(
                workbook, source=source, classifications=classifications,
                source_workbook=source_workbook, annotation_qa=annotation_qa, coverage=coverage,
            )
            if qa["status"] != "PASS":
                raise ReportExportError("Excel draft failed structural QA")
            destination = get_settings().jobs_dir / job.job_code / "excel_drafts" / f"{uuid4().hex}.xlsx"
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                output.write(workbook)
                output.flush()
                os.fsync(output.fileno())
            qa_row = AuditExcelReportDraft(
                run_id=run.id, reviewer_user_id=admin.id,
                snapshot_digest=source["snapshot_digest"],
                classification_digest=classifications["classification_digest"],
                annotation_pdf_sha256=qa["annotation_pdf_sha256"],
                workbook_sha256=qa["workbook_sha256"], file_path=str(destination),
                qa_json=json.dumps(qa, ensure_ascii=False),
            )
            db.add(qa_row)
            db.commit()
    except (CheckpointError, quality.QualityAdapterError, ReportExportError, CoverageInputError, OSError, ValueError, KeyError, BadZipFile, IntegrityError) as exc:
        db.rollback()
        if destination is not None:
            destination.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="BOQ_Audit_Report_Draft.xlsx"',
            "Cache-Control": "private, no-store",
            "X-Audit-Snapshot-Digest": source["snapshot_digest"],
            "X-Audit-Classification-Digest": classifications["classification_digest"],
            "X-Audit-Excel-SHA256": qa_row.workbook_sha256,
            "X-Audit-Excel-QA": "PASS",
            "X-Audit-Excel-Draft-ID": str(qa_row.id),
        },
    )


def _excel_report_review_state(db: Session, job: Job, draft_id: int) -> AdminExcelReportReviewResponse:
    run, checkpoint, source = _current_report_source(db, job)
    if checkpoint["pass_no"] != 3:
        raise ReportExportError("Excel report review requires Pass 3")
    classifications = _report_classifications(db, run.id, source)
    row = db.get(AuditExcelReportDraft, draft_id)
    if row is None or row.run_id != run.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Excel report draft not found")
    path = stored_job_file(job.job_code, row.file_path)
    workbook = path.read_bytes()
    actual_sha = hashlib.sha256(workbook).hexdigest()
    annotation_qa = _current_annotation_report_qa(db, job, run.id, source, classifications)
    annotation_sha = annotation_qa.get("output_pdf_sha256") if annotation_qa else None
    annotation_digest = hashlib.sha256(
        json.dumps(annotation_qa, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest() if annotation_qa else None
    source_workbook = stored_job_file(job.job_code, job.estimate_input.file_path) if job.estimate_input else None
    coverage = calculate_coverage(checkpoint["inventory"], checkpoint["ledger"])
    current_qa = validate_excel_report_draft(
        workbook, source=source, classifications=classifications,
        source_workbook=source_workbook, annotation_qa=annotation_qa, coverage=coverage,
    )
    stored_qa = json.loads(row.qa_json)
    is_current = bool(
        row.snapshot_digest == source["snapshot_digest"]
        and row.classification_digest == classifications["classification_digest"]
        and row.annotation_pdf_sha256 == annotation_sha
        and row.workbook_sha256 == actual_sha
        and stored_qa.get("status") == "PASS"
        and stored_qa.get("annotation_qa_digest") == annotation_digest
        and current_qa["status"] == "PASS"
    )
    latest = db.scalar(select(AuditExcelReportReviewEvent).where(
        AuditExcelReportReviewEvent.draft_id == row.id,
    ).order_by(AuditExcelReportReviewEvent.revision_no.desc()).limit(1))
    event_current = bool(
        is_current and latest is not None
        and latest.snapshot_digest == source["snapshot_digest"]
        and latest.classification_digest == classifications["classification_digest"]
        and latest.annotation_pdf_sha256 == annotation_sha
        and latest.workbook_sha256 == actual_sha
    )
    return AdminExcelReportReviewResponse(
        draft_id=row.id, snapshot_digest=row.snapshot_digest,
        classification_digest=row.classification_digest,
        annotation_pdf_sha256=row.annotation_pdf_sha256,
        workbook_sha256=row.workbook_sha256,
        structural_qa=current_qa["status"],
        latest_result=latest.result if event_current else "UNREVIEWED",
        latest_reason=latest.reason if event_current else None,
        reviewer_user_id=latest.reviewer_user_id if event_current else None,
        revision_no=latest.revision_no if event_current else None,
        is_current=is_current,
        reviewed_sections=json.loads(latest.reviewed_sections_json) if event_current else [],
    )


def _release_signoff_context(db: Session, job: Job, pdf_draft_id: int, excel_draft_id: int) -> dict:
    run, checkpoint, source = _current_report_source(db, job)
    if job.status not in {JobStatus.WAITING_FOR_INFO, JobStatus.REVIEW} or checkpoint["pass_no"] != 3 or checkpoint["status"] != "READY":
        raise ReportExportError("release signoff requires a paused, ready Pass 3 checkpoint")
    gate = db.scalar(select(AuditRunStageGate).where(
        AuditRunStageGate.run_id == run.id,
    ).order_by(AuditRunStageGate.id.desc()).limit(1))
    try:
        raw_receipt = gate.receipt_json.encode("utf-8") if gate else b""
        receipt = quality.strict_json_loads(raw_receipt, label="Pass 3 gate receipt")
    except (UnicodeError, quality.StrictJsonError) as exc:
        raise ReportExportError("current Pass 3 gate receipt is unavailable or invalid") from exc
    if (
        gate is None or not isinstance(receipt, dict)
        or hashlib.sha256(raw_receipt).hexdigest() != gate.receipt_digest
        or gate.run_id != run.id or gate.pass_no != 3 or gate.stage != "pass3"
        or gate.checkpoint_version != checkpoint["version"] or gate.advanced or gate.error_code is not None
        or receipt.get("schema_version") != 1 or receipt.get("run_id") != run.id
        or receipt.get("snapshot_id") != gate.snapshot_id or receipt.get("stage") != "pass3"
        or receipt.get("source_digest") != checkpoint["source_digest"]
        or receipt.get("skill_digest") != checkpoint["skill_digest"]
        or receipt.get("controller_status") != "PASS" or receipt.get("validator_exit_code") != 0
        or receipt.get("candidate_release_status") != quality.STAGE_COMPLETE_STATUS
        or receipt.get("blocking_checks") != []
        or not isinstance(receipt.get("open_record_count"), int)
        or isinstance(receipt["open_record_count"], bool) or receipt["open_record_count"] < 0
    ):
        raise ReportExportError("current Pass 3 gate has not passed for this checkpoint")
    classifications = _report_classifications(db, run.id, source)
    approval = db.get(AuditInventoryApproval, checkpoint["approval_id"])
    if approval is None or approval.source_digest != checkpoint["source_digest"]:
        raise ReportExportError("approved source inventory is missing or stale")
    coverage = calculate_coverage(checkpoint["inventory"], checkpoint["ledger"])
    closure = assess_pass3_closure(checkpoint)
    if coverage["coverage_status"] not in {"COMPLETE", "COVERED_WITH_UNRESOLVED"}:
        raise ReportExportError("coverage is incomplete or blocked")
    if closure["status"] != "METADATA_COMPLETE" or closure["controller_contract_blocked"]:
        raise ReportExportError("Pass 3 closure requirements are not met")
    if classifications["unclassified_issue_count"]:
        raise ReportExportError("all issues must have reviewer classification before signoff")
    sanity = check_detail_rows(checkpoint["ledger_projection"].get("detail_rows"))
    if sanity["status"] == "UNAVAILABLE":
        raise ReportExportError("deterministic detail-row checks are unavailable")
    unresolved = [
        f"work_item:{item['check_id']}" for item in checkpoint["ledger"]
        if item["execution_status"] != "DONE" or item["result_status"] in {"NOT_CHECKED", "UNRESOLVED"}
    ]
    unresolved.extend(
        f"issue:{issue['issue_id']}" for issue in classifications["decisions"].values()
        if issue["final_status"] == "UNRESOLVED"
    )
    unresolved.extend(f"sanity:{item['row_id']}:{item['anomaly']}" for item in sanity["anomalies"])
    if sanity["status"] == "NO_DETAIL_ROWS":
        unresolved.append("sanity:NO_DETAIL_ROWS")
    unresolved = sorted(set(unresolved))

    pdf_row = db.get(AuditAnnotationDraft, pdf_draft_id)
    pdf_qa = _current_annotation_report_qa(db, job, run.id, source, classifications)
    if pdf_row is None or pdf_row.run_id != run.id or pdf_qa is None:
        raise ReportExportError("current PDF annotation draft is unavailable")
    pdf_review = _annotation_draft_response(db, pdf_row, source, classifications)
    unmarked_pdf = not pdf_qa["marks"] and not pdf_qa["missing_active_issue_ids"]
    if not pdf_review.is_current or (pdf_review.annotation_qa_status != "PASS" and not unmarked_pdf):
        raise ReportExportError("every active PDF annotation must have current PASS review")

    excel_review = _excel_report_review_state(db, job, excel_draft_id)
    if not excel_review.is_current or excel_review.structural_qa != "PASS" or excel_review.latest_result != "PASS":
        raise ReportExportError("Excel draft must have current structural QA and reviewer PASS")
    excel_row = db.get(AuditExcelReportDraft, excel_draft_id)
    excel_event = db.scalar(select(AuditExcelReportReviewEvent).where(
        AuditExcelReportReviewEvent.draft_id == excel_draft_id,
    ).order_by(AuditExcelReportReviewEvent.revision_no.desc()).limit(1))
    if excel_row is None or excel_event is None or excel_event.result != "PASS":
        raise ReportExportError("current Excel review event is missing")
    if excel_row.snapshot_digest != source["snapshot_digest"] or excel_row.classification_digest != classifications["classification_digest"]:
        raise ReportExportError("Excel draft digests do not match the current report snapshot")
    return {
        "run": run, "checkpoint": checkpoint, "source": source, "classifications": classifications,
        "approval": approval, "coverage": coverage, "closure": closure, "sanity": sanity,
        "unresolved_scope": unresolved, "pdf_draft": pdf_row, "pdf_qa": pdf_qa,
        "requires_unmarked_pdf_attestation": unmarked_pdf,
        "excel_draft": excel_row, "excel_review_event": excel_event,
    }


def _release_signoff_response(event: AuditReleaseSignoffEvent, context: dict, *, engineer_allowed: bool = True) -> AdminReleaseSignoffResponse:
    checkpoint = context["checkpoint"]
    current = bool(
        engineer_allowed
        and
        event.run_id == context["run"].id
        and event.snapshot_digest == context["source"]["snapshot_digest"]
        and event.classification_digest == context["classifications"]["classification_digest"]
        and event.checkpoint_version == checkpoint["version"]
        and event.approval_id == context["approval"].id
        and event.pdf_draft_id == context["pdf_draft"].id
        and event.pdf_sha256 == context["pdf_draft"].output_pdf_sha256
        and event.excel_draft_id == context["excel_draft"].id
        and event.excel_review_event_id == context["excel_review_event"].id
        and event.excel_sha256 == context["excel_draft"].workbook_sha256
        and event.reviewed_unmarked_pdf == context["requires_unmarked_pdf_attestation"]
        and json.loads(event.unresolved_scope_json) == context["unresolved_scope"]
    )
    return AdminReleaseSignoffResponse(
        signoff_id=event.id, revision_no=event.revision_no, result=event.result,
        snapshot_digest=event.snapshot_digest, classification_digest=event.classification_digest,
        checkpoint_version=event.checkpoint_version, pdf_draft_id=event.pdf_draft_id,
        pdf_sha256=event.pdf_sha256, excel_draft_id=event.excel_draft_id,
        excel_sha256=event.excel_sha256, unresolved_scope=context["unresolved_scope"], is_current=current,
        reviewed_unmarked_pdf=event.reviewed_unmarked_pdf,
    )


@router.get("/{job_code}/checkpoint/report-drafts/{draft_id}/review", response_model=AdminExcelReportReviewResponse)
async def get_admin_excel_report_review(job_code: str, draft_id: int, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    try:
        return _excel_report_review_state(db, job, draft_id)
    except (CheckpointError, quality.QualityAdapterError, ReportExportError, OSError, ValueError, KeyError, BadZipFile) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{job_code}/checkpoint/report-drafts/{draft_id}/review", response_model=AdminExcelReportReviewResponse)
async def review_admin_excel_report(
    job_code: str, draft_id: int, body: AdminExcelReportReviewRequest,
    admin: User = Depends(admin_user), db: Session = Depends(get_db),
):
    if admin.email.strip().lower() not in _allowed_inventory_reviewers():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Excel report review requires a configured quality reviewer")
    job = _lock_job(db, job_code)
    if job.status not in {JobStatus.WAITING_FOR_INFO, JobStatus.REVIEW}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Excel report review requires a paused audit")
    try:
        state = _excel_report_review_state(db, job, draft_id)
        if not state.is_current or state.structural_qa != "PASS" or body.workbook_sha256 != state.workbook_sha256:
            raise ReportExportError("Excel report draft is stale or failed structural QA")
        previous = db.scalar(select(AuditExcelReportReviewEvent).where(
            AuditExcelReportReviewEvent.draft_id == draft_id,
        ).order_by(AuditExcelReportReviewEvent.revision_no.desc()).limit(1))
        reason = body.reason.strip()
        sections_json = json.dumps(sorted(body.reviewed_sections), ensure_ascii=False)
        if previous is not None and (previous.result, previous.reason, previous.reviewed_sections_json) == (body.result, reason, sections_json):
            raise ReportExportError("Excel report review is unchanged")
        db.add(AuditExcelReportReviewEvent(
            draft_id=draft_id, revision_no=previous.revision_no + 1 if previous else 1,
            reviewer_user_id=admin.id, result=body.result, reason=reason,
            reviewed_sections_json=sections_json, snapshot_digest=state.snapshot_digest,
            classification_digest=state.classification_digest,
            annotation_pdf_sha256=state.annotation_pdf_sha256,
            workbook_sha256=state.workbook_sha256,
        ))
        record_mutation_invalidation(db, job.id, admin.id, f"Excel report QA review changed: {draft_id}")
        db.flush()
        response = _excel_report_review_state(db, job, draft_id)
        db.commit()
        return response
    except (CheckpointError, quality.QualityAdapterError, ReportExportError, OSError, ValueError, KeyError, BadZipFile, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/{job_code}/checkpoint/release-signoff", response_model=AdminReleaseSignoffResponse)
async def get_admin_release_signoff(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    run = max(job.audit_runs, key=lambda item: item.attempt, default=None)
    event = db.scalar(select(AuditReleaseSignoffEvent).where(
        AuditReleaseSignoffEvent.run_id == run.id,
    ).order_by(AuditReleaseSignoffEvent.revision_no.desc()).limit(1)) if run else None
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No engineer signoff exists for the latest audit")
    try:
        context = _release_signoff_context(db, job, event.pdf_draft_id, event.excel_draft_id)
        engineer = db.get(User, event.reviewer_user_id)
        engineer_allowed = bool(engineer and engineer.email.strip().lower() in get_settings().audit_engineer_signoff_email_set)
        return _release_signoff_response(event, context, engineer_allowed=engineer_allowed)
    except (CheckpointError, quality.QualityAdapterError, ReportExportError, CoverageInputError, OSError, ValueError, KeyError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{job_code}/checkpoint/release-signoff", response_model=AdminReleaseSignoffResponse, status_code=status.HTTP_201_CREATED)
async def create_admin_release_signoff(
    job_code: str, body: AdminReleaseSignoffRequest,
    admin: User = Depends(admin_user), db: Session = Depends(get_db),
):
    if admin.email.strip().lower() not in get_settings().audit_engineer_signoff_email_set:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Engineer signoff requires a configured engineer email")
    job = _lock_job(db, job_code)
    try:
        context = _release_signoff_context(db, job, body.pdf_draft_id, body.excel_draft_id)
        if (body.snapshot_digest, body.classification_digest, body.pdf_sha256, body.excel_sha256) != (
            context["source"]["snapshot_digest"], context["classifications"]["classification_digest"],
            context["pdf_draft"].output_pdf_sha256, context["excel_draft"].workbook_sha256,
        ):
            raise ReportExportError("signoff evidence digest differs from the current artifacts")
        if body.reviewed_unmarked_pdf != context["requires_unmarked_pdf_attestation"]:
            raise ReportExportError("signoff must explicitly review the unmarked PDF when no active issue exists")
        unresolved = context["unresolved_scope"]
        if body.result == "APPROVE_FULL" and (unresolved or body.accepted_partial):
            raise ReportExportError("full approval is allowed only when no unresolved scope remains")
        if body.result == "APPROVE_PARTIAL" and (not unresolved or not body.accepted_partial):
            raise ReportExportError("partial approval requires unresolved scope and explicit acknowledgement")
        previous = db.scalar(select(AuditReleaseSignoffEvent).where(
            AuditReleaseSignoffEvent.run_id == context["run"].id,
        ).order_by(AuditReleaseSignoffEvent.revision_no.desc()).limit(1))
        if previous is not None and (
            previous.result, previous.reason, previous.pdf_draft_id, previous.excel_draft_id,
            previous.pdf_sha256, previous.excel_sha256,
        ) == (body.result, body.reason.strip(), body.pdf_draft_id, body.excel_draft_id, body.pdf_sha256, body.excel_sha256):
            raise ReportExportError("engineer signoff is unchanged")
        event = AuditReleaseSignoffEvent(
            run_id=context["run"].id, revision_no=previous.revision_no + 1 if previous else 1,
            reviewer_user_id=admin.id, result=body.result, reason=body.reason.strip(),
            accepted_partial=body.accepted_partial, reviewed_unmarked_pdf=body.reviewed_unmarked_pdf,
            snapshot_digest=context["source"]["snapshot_digest"],
            classification_digest=context["classifications"]["classification_digest"],
            checkpoint_version=context["checkpoint"]["version"], approval_id=context["approval"].id,
            pdf_draft_id=context["pdf_draft"].id, pdf_sha256=context["pdf_draft"].output_pdf_sha256,
            excel_draft_id=context["excel_draft"].id,
            excel_review_event_id=context["excel_review_event"].id,
            excel_sha256=context["excel_draft"].workbook_sha256,
            unresolved_scope_json=json.dumps(unresolved, ensure_ascii=False),
        )
        db.add(event)
        db.flush()
        response = _release_signoff_response(event, context)
        db.commit()
        return response
    except (CheckpointError, quality.QualityAdapterError, ReportExportError, CoverageInputError, OSError, ValueError, KeyError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{job_code}/checkpoint/repair", response_model=AdminGateRepairResponse)
async def request_admin_gate_repair(job_code: str, body: AdminGateRepairRequest, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    if admin.email.strip().lower() not in _allowed_inventory_reviewers():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Gate repair requires a configured quality reviewer")
    if not get_settings().audit_checkpoint_worker_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Checkpoint worker is disabled")
    job = _lock_job(db, job_code)
    run = max(job.audit_runs, key=lambda item: item.attempt, default=None)
    if job.status != JobStatus.WAITING_FOR_INFO or run is None or run.status != JobStatus.WAITING_FOR_INFO.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "No paused checkpoint audit is available")
    try:
        settings = get_settings()
        pinned = quality.verify_pinned_bundle(
            settings.boq_audit_skill_path.resolve(strict=True).parent,
            Path(__file__).resolve().parents[1] / "boq_audit_skill_manifest.json",
            expected_digest=quality.TRUSTED_SKILL_DIGEST,
        )
        checkpoint = load_run_checkpoint(db, run_id=run.id, current_source_digest=_source_digest(job), current_skill_digest=pinned.digest)
        gates = _stage_gate_history(db, run)
        if _gate_repair_status(job, checkpoint, gates) != "UNMAPPED_BLOCKER":
            raise CheckpointError("current gate is not eligible for reviewer-scoped batch repair")
        if body.batch_ids not in checkpoint["batch_plan"][str(checkpoint["pass_no"])] or any(
            item["execution_status"] != "DONE" for item in checkpoint["ledger"] if item["pass_no"] == checkpoint["pass_no"]
        ):
            raise CheckpointError("repair batch is outside the completed current pass")
        effective = next((item for item in checkpoint["batch_results"] if item["pass_no"] == checkpoint["pass_no"] and item["check_ids"] == body.batch_ids), None)
        if effective is None or effective["revision_no"] >= 2:
            raise CheckpointError("batch repair budget is exhausted or result is missing")
        validate_repair_dependency_scope(checkpoint, body.batch_ids)
        attempts = list(db.scalars(select(AuditRunGateRepairRequest).where(AuditRunGateRepairRequest.base_result_id == effective["base_result_id"])))
        if len(attempts) >= 2 or any(item.status in {"REQUESTED", "RUNNING"} for item in attempts):
            raise CheckpointError("repair request is active or its two-attempt budget is exhausted")
        gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == run.id).order_by(AuditRunStageGate.id.desc()).limit(1))
        request = AuditRunGateRepairRequest(
            run_id=run.id, base_result_id=effective["base_result_id"], gate_id=gate.id,
            batch_ids_json=json.dumps(body.batch_ids, ensure_ascii=False, separators=(",", ":")),
            source_digest=checkpoint["source_digest"], skill_digest=checkpoint["skill_digest"],
            checkpoint_version=checkpoint["version"], attempt_no=len(attempts) + 1,
            status="REQUESTED",
        )
        db.add(request)
        db.commit()
    except (CheckpointError, quality.QualityAdapterError, OSError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AdminGateRepairResponse(request_id=request.id, status="REQUESTED")


@router.post("/{job_code}/checkpoint/retry", response_model=AdminJobResponse)
async def retry_admin_checkpoint(job_code: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    if admin.email.strip().lower() not in _allowed_inventory_reviewers():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Checkpoint retry requires a configured quality reviewer")
    if not get_settings().audit_checkpoint_worker_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Checkpoint worker is disabled")
    job = _lock_job(db, job_code)
    run = max(job.audit_runs, key=lambda item: item.attempt, default=None)
    if job.status != JobStatus.WAITING_FOR_INFO or run is None or run.status != JobStatus.WAITING_FOR_INFO.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "No paused checkpoint audit is available")
    try:
        settings = get_settings()
        pinned = quality.verify_pinned_bundle(
            settings.boq_audit_skill_path.resolve(strict=True).parent,
            Path(__file__).resolve().parents[1] / "boq_audit_skill_manifest.json",
            expected_digest=quality.TRUSTED_SKILL_DIGEST,
        )
        checkpoint = load_run_checkpoint(
            db, run_id=run.id, current_source_digest=_source_digest(job), current_skill_digest=pinned.digest,
        )
        if checkpoint["status"] == "RUNNING":
            recover_checkpoint(db, checkpoint=checkpoint, expected_version=checkpoint["version"])
            checkpoint = load_run_checkpoint(db, run_id=run.id, current_source_digest=_source_digest(job), current_skill_digest=pinned.digest)
        if checkpoint["status"] != "INTERRUPTED":
            raise CheckpointError("only an interrupted batch can be retried")
        blocked = {item["check_id"] for item in checkpoint["ledger"] if item["pass_no"] == checkpoint["pass_no"] and item["execution_status"] == "BLOCKED"}
        batch_ids = next((batch for batch in checkpoint["batch_plan"][str(checkpoint["pass_no"])] if set(batch) == blocked), None)
        if batch_ids is None:
            raise CheckpointError("interrupted batch scope is not recoverable")
        retry_checkpoint_batch(db, checkpoint=checkpoint, expected_version=checkpoint["version"], batch_ids=batch_ids)
    except (CheckpointError, quality.QualityAdapterError, OSError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    job.status = JobStatus.PROCESSING
    job.admin_notes = "Đang kiểm lại batch bị gián đoạn trên cùng lượt audit."
    run.status = "RUNNING"
    run.ended_at = None
    run.duration_seconds = None
    db.commit()
    return find_job(db, job_code)


@router.get("/{job_code}/inventory", response_model=AdminInventoryResponse)
async def get_admin_job_inventory(job_code: str, response: Response, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    job = find_job(db, job_code)
    try:
        current_digest, scaffold = _current_job_inventory_context(job)
    except Exception as exc:
        logger.warning("admin_inventory_source_unavailable job_code=%s error=%s", job_code, type(exc).__name__)
        raise HTTPException(status.HTTP_409_CONFLICT, "Source files are unavailable for inventory approval")
    approval_row = db.scalar(
        select(AuditInventoryApproval)
        .where(AuditInventoryApproval.job_id == job.id)
        .order_by(AuditInventoryApproval.approved_at.desc(), AuditInventoryApproval.id.desc())
        .limit(1)
    )
    approval = None
    if approval_row is not None:
        reviewer = db.get(User, approval_row.reviewer_user_id)
        try:
            inventory = json.loads(approval_row.inventory_json)
        except (TypeError, json.JSONDecodeError):
            inventory = {}
        approval = AdminInventoryApprovalResponse(
            job_code=job_code,
            source_digest=approval_row.source_digest,
            reviewer_user_id=approval_row.reviewer_user_id,
            reviewer_email=reviewer.email if reviewer is not None else None,
            approved_at=approval_row.approved_at,
            is_current=approval_row.source_digest == current_digest,
            inventory=inventory,
        )
    return AdminInventoryResponse(job_code=job_code, source_digest=current_digest, scaffold=scaffold, approval=approval)


@router.post("/{job_code}/inventory", response_model=AdminInventoryApprovalResponse, status_code=status.HTTP_201_CREATED)
async def approve_admin_job_inventory(job_code: str, payload: AdminInventoryApprovalRequest, response: Response, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    job = find_job(db, job_code)
    if job.status == JobStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Completed legacy jobs cannot receive retrospective inventory approval")
    try:
        current_digest, scaffold = _current_job_inventory_context(job)
    except Exception as exc:
        logger.warning("admin_inventory_source_unavailable job_code=%s error=%s", job_code, type(exc).__name__)
        raise HTTPException(status.HTTP_409_CONFLICT, "Source files are unavailable for inventory approval")

    try:
        validated = validate_inventory(payload.inventory)
    except CoverageInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    if validated.get("documents") != scaffold.get("documents") or validated.get("pages") != scaffold.get("pages") or validated.get("sheets") != scaffold.get("sheets"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Inventory source units must exactly match the controller scaffold")

    if admin.email.strip().lower() not in _allowed_inventory_reviewers():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Inventory approval requires a configured quality reviewer")

    declared_passes = {item["pass_no"] for item in validated["expected_work_items"]}
    if declared_passes != {1, 2, 3}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Inventory approval requires at least one expected work item in each audit pass")

    try:
        validated = validate_pass1_source_coverage(validated)
    except CoverageInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    if _source_digest(job) != current_digest:
        raise HTTPException(status.HTTP_409_CONFLICT, "Source files changed; reload the inventory before approval")

    canonical = json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    approval = AuditInventoryApproval(
        job_id=job.id,
        reviewer_user_id=admin.id,
        source_digest=current_digest,
        inventory_json=canonical,
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return AdminInventoryApprovalResponse(
        job_code=job_code,
        source_digest=current_digest,
        reviewer_user_id=admin.id,
        reviewer_email=admin.email,
        approved_at=approval.approved_at,
        is_current=True,
        inventory=validated,
    )


@router.get("/{job_code}/attempts", response_model=AttemptArtifactsResponse)
async def list_attempts(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    root = get_settings().jobs_dir / job.job_code
    return {
        "attempts": [{"attempt": run.attempt, "status": run.status, "artifacts": _artifacts(job.job_code, root / "attempts" / str(run.attempt) / "output")} for run in job.audit_runs],
        "legacy": _artifacts(job.job_code, root / "output"),
    }


@router.get("/{job_code}/attempts/{attempt}/artifacts/{name:path}")
async def download_attempt_artifact(job_code: str, attempt: int, name: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    if attempt < 1 or not any(run.attempt == attempt for run in job.audit_runs):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy lượt xử lý")
    path = _artifact_path(job.job_code, get_settings().jobs_dir / job.job_code / "attempts" / str(attempt) / "output", name)
    return stream_file(path, path.name)


@router.get("/{job_code}/legacy/artifacts/{name:path}")
async def download_legacy_artifact(job_code: str, name: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    path = _artifact_path(job.job_code, get_settings().jobs_dir / job.job_code / "output", name)
    return stream_file(path, path.name)


@router.get("/{job_code}/input/download")
async def download_input(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    path = stored_job_file(job.job_code, job.input_file_path)
    return stream_file(path, job.original_filename, "application/pdf")


@router.get("/{job_code}/estimate/download")
async def download_estimate(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    if not job.estimate_input:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hồ sơ không có tệp dự toán")
    path = stored_job_file(job.job_code, job.estimate_input.file_path)
    return stream_file(path, job.estimate_input.original_filename)


@router.get("/{job_code}/narrative/download")
async def download_narrative(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = find_job(db, job_code)
    if not job.narrative_input:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hồ sơ không có tệp thuyết minh")
    path = stored_job_file(job.job_code, job.narrative_input.file_path)
    return stream_file(path, job.narrative_input.original_filename)


@router.patch("/{job_code}", response_model=AdminJobResponse)
async def update_job(job_code: str, payload: AdminUpdateJob, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = _lock_job(db, job_code)
    values = payload.model_dump(exclude_unset=True)
    if values.get("status") in {JobStatus.SUBMITTED, JobStatus.PROCESSING}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Trạng thái này do worker quản lý")
    if job.status == JobStatus.PROCESSING and "status" in values:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể đổi trạng thái khi đang xử lý")
    if values.get("status") == JobStatus.COMPLETED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dùng thao tác hoàn thành hồ sơ")
    changed = [
        field
        for field in ("status", "critical_errors", "warnings", "customer_notes")
        if field in values and values[field] != getattr(job, field)
    ]
    if _has_controlled_audit_history(db, job.id) and ("status" in values or set(changed) & {"critical_errors", "warnings"}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Checkpoint lifecycle and counters are derived from the current ledger")
    for key, value in values.items():
        setattr(job, key, value)
    if payload.status is not None:
        from app.models.entities import utcnow
        job.completed_at = utcnow() if payload.status == JobStatus.FAILED else None
    if changed:
        record_mutation_invalidation(db, job.id, admin.id, f"admin PATCH changed: {', '.join(changed)}")
    db.commit()
    return find_job(db, job_code)


@router.post("/{job_code}/outputs", response_model=OutputResponse, status_code=status.HTTP_201_CREATED)
async def upload_output(
    job_code: str,
    file_type: OutputType = Form(),
    file: UploadFile = File(),
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    job = find_job(db, job_code)
    if job.status in BUSY_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể sửa kết quả khi hồ sơ đang chờ hoặc xử lý")
    if _has_controlled_audit_history(db, job.id):
        file.file.close()
        raise HTTPException(status.HTTP_409_CONFLICT, "Checkpoint report artifacts are generated from the verified ledger")
    job_id, job_code_value = job.id, job.job_code
    has_estimate = job.estimate_input is not None
    db.rollback()
    if file_type == OutputType.ANNOTATED_PDF:
        display_name, ext = validate_upload(file, {".pdf"}, PDF_MIMES)
        kind = "pdf"
    elif file_type in {OutputType.EXCEL_REPORT, OutputType.ESTIMATE_REPORT}:
        if file_type == OutputType.ESTIMATE_REPORT and not has_estimate:
            file.file.close()
            raise HTTPException(status.HTTP_409_CONFLICT, "Khách hàng không gửi kèm dự toán")
        display_name, ext = validate_upload(file, {".xlsx", ".xls"}, EXCEL_MIMES)
        kind = ext.removeprefix(".")
    else:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Chỉ hỗ trợ báo cáo Excel và PDF đánh dấu")
    stored_name = random_stored_name(ext)
    destination = get_settings().jobs_dir / job_code_value / "output" / stored_name
    size = await save_upload(file, destination, kind)
    try:
        job = _lock_job(db, job_code_value)
        if job.status in BUSY_STATUSES:
            raise HTTPException(status.HTTP_409_CONFLICT, "Không thể sửa kết quả khi hồ sơ đang chờ hoặc xử lý")
        if _has_controlled_audit_history(db, job.id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Checkpoint report artifacts are generated from the verified ledger")
        old_outputs = list(db.scalars(select(JobOutput).where(JobOutput.job_id == job_id, JobOutput.file_type == file_type)).all())
        output = JobOutput(job_id=job_id, file_type=file_type, original_filename=display_name, stored_filename=stored_name, file_path=str(destination), file_size=size)
        db.add(output)
        for old in old_outputs:
            db.delete(old)
        record_mutation_invalidation(db, job.id, admin.id, f"admin output upload: {file_type.value}")
        db.commit()
        db.refresh(output)
        for old in old_outputs:
            if "attempts" not in Path(old.file_path).parts:
                Path(old.file_path).unlink(missing_ok=True)
        return output
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise


@router.delete("/{job_code}/outputs/{output_id}", response_model=MessageResponse)
async def delete_output(job_code: str, output_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = _lock_job(db, job_code)
    if job.status in BUSY_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể sửa kết quả khi hồ sơ đang chờ hoặc xử lý")
    if _has_controlled_audit_history(db, job.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Published checkpoint artifacts are immutable")
    output = next((item for item in job.outputs if item.id == output_id), None)
    if not output:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tệp kết quả")
    path = Path(output.file_path)
    record_mutation_invalidation(db, job.id, admin.id, f"admin output delete: {output.file_type.value}")
    db.delete(output)
    db.commit()
    if "attempts" not in path.parts:
        path.unlink(missing_ok=True)
    return {"message": "Đã xóa tệp kết quả"}


@router.delete("/{job_code}", response_model=MessageResponse)
async def delete_job(job_code: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    job = _lock_job(db, job_code)
    if job.status == JobStatus.PROCESSING or job.input_file_path == "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể xóa hồ sơ đang lưu hoặc xử lý")
    if has_quality_review(db, job.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Hồ sơ có lịch sử chất lượng, không thể xóa cứng")
    jobs_root = get_settings().jobs_dir.resolve()
    job_directory = (jobs_root / job.job_code).resolve()
    if job_directory.parent != jobs_root:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Đường dẫn hồ sơ không hợp lệ")

    quarantined_directory = jobs_root / f".deleting-{job.job_code}-{uuid4().hex}"
    moved = False
    try:
        if job_directory.exists():
            job_directory.rename(quarantined_directory)
            moved = True
        db.delete(job)
        db.commit()
    except Exception:
        db.rollback()
        if moved and quarantined_directory.exists():
            quarantined_directory.rename(job_directory)
        raise

    if moved:
        try:
            shutil.rmtree(quarantined_directory)
        except OSError:
            logger.exception("job_files_cleanup_failed admin_user_id=%s job_code=%s", admin.id, job_code)
    logger.info("job_deleted admin_user_id=%s job_code=%s", admin.id, job_code)
    return {"message": "Đã xóa hồ sơ"}


@router.post("/{job_code}/complete", response_model=AdminJobResponse)
async def complete_job(job_code: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    from app.models.entities import utcnow
    job = _lock_job(db, job_code)
    if job.status in BUSY_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Không thể công bố hồ sơ đang chờ hoặc xử lý")
    latest_run = max(job.audit_runs, key=lambda run: run.attempt, default=None)
    if latest_run is not None and db.get(AuditRunCheckpoint, latest_run.id) is not None:
        signoff = db.scalar(select(AuditReleaseSignoffEvent).where(
            AuditReleaseSignoffEvent.run_id == latest_run.id,
        ).order_by(AuditReleaseSignoffEvent.revision_no.desc()).limit(1))
        if signoff is None or signoff.result not in {"APPROVE_FULL", "APPROVE_PARTIAL"}:
            raise HTTPException(status.HTTP_409_CONFLICT, "Cần engineer signoff hiện hành trước khi công bố")
        engineer = db.get(User, signoff.reviewer_user_id)
        if engineer is None or engineer.email.strip().lower() not in get_settings().audit_engineer_signoff_email_set:
            raise HTTPException(status.HTTP_409_CONFLICT, "Engineer signoff không còn thuộc email được phép ký")
        try:
            context = _release_signoff_context(db, job, signoff.pdf_draft_id, signoff.excel_draft_id)
            current_signoff = _release_signoff_response(signoff, context)
        except (CheckpointError, quality.QualityAdapterError, ReportExportError, CoverageInputError, OSError, ValueError, KeyError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        if not current_signoff.is_current:
            raise HTTPException(status.HTTP_409_CONFLICT, "Engineer signoff is stale; review and sign again")
        if (signoff.result == "APPROVE_PARTIAL") != bool(signoff.accepted_partial and signoff.unresolved_scope_json != "[]"):
            raise HTTPException(status.HTTP_409_CONFLICT, "Engineer signoff partial acknowledgement is inconsistent")
        artifacts = [
            (OutputType.EXCEL_REPORT, context["excel_draft"].file_path, "BOQ_Audit_Report.xlsx", context["excel_draft"].workbook_sha256),
            (OutputType.ANNOTATED_PDF, context["pdf_draft"].file_path, "BOQ_Audit_Annotated.pdf", context["pdf_draft"].output_pdf_sha256),
        ]
        if job.estimate_input:
            artifacts.append((OutputType.ESTIMATE_REPORT, context["excel_draft"].file_path, "BOQ_Audit_Report.xlsx", context["excel_draft"].workbook_sha256))
        for file_type, file_path, filename, expected_sha in artifacts:
            artifact_path = stored_job_file(job.job_code, file_path)
            artifact_bytes = artifact_path.read_bytes()
            if hashlib.sha256(artifact_bytes).hexdigest() != expected_sha:
                raise HTTPException(status.HTTP_409_CONFLICT, "A signed report artifact changed before publication")
            existing = [item for item in job.outputs if item.file_type == file_type]
            if existing:
                if len(existing) != 1 or hashlib.sha256(stored_job_file(job.job_code, existing[0].file_path).read_bytes()).hexdigest() != expected_sha:
                    raise HTTPException(status.HTTP_409_CONFLICT, f"Existing {file_type.value} output differs from signed artifact")
            else:
                db.add(JobOutput(
                    job_id=job.id, file_type=file_type, original_filename=filename,
                    stored_filename=artifact_path.name, file_path=str(artifact_path), file_size=len(artifact_bytes),
                ))
        if job.status != JobStatus.COMPLETED:
            job.completed_at = utcnow()
        job.status = JobStatus.COMPLETED
        db.commit()
        return find_job(db, job_code)
    if _has_controlled_audit_history(db, job.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Approved inventory requires a checkpoint audit and current engineer signoff")
    types = {output.file_type for output in job.outputs}
    required = {OutputType.EXCEL_REPORT, OutputType.ANNOTATED_PDF}
    if job.estimate_input:
        required.add(OutputType.ESTIMATE_REPORT)
    if not required.issubset(types):
        message = (
            "Cần đủ báo cáo BOQ, báo cáo dự toán và PDF đánh dấu trước khi hoàn thành"
            if job.estimate_input
            else "Cần đủ báo cáo Excel và PDF đánh dấu trước khi hoàn thành"
        )
        raise HTTPException(status.HTTP_409_CONFLICT, message)
    if job.status != JobStatus.COMPLETED:
        job.completed_at = utcnow()
    job.status = JobStatus.COMPLETED
    db.commit()
    return find_job(db, job_code)
