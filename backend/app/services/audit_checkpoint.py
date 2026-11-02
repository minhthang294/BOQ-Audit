"""Durable, source-bound audit checkpoint primitives for a single AuditRun."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import AuditInventoryApproval, AuditRun, AuditRunBatchLedger, AuditRunBatchResult, AuditRunBatchRevision, AuditRunCheckpoint, AuditRunStageGate, JobStatus, User, UserRole, utcnow
from app.services.audit_batch import BatchOutputError, validate_batch_payload
from app.services.audit_batch_ledgers import decode_batch_ledger_manifest, encode_batch_ledger_manifest, project_batch_ledger_records, validate_batch_ledger_records
from app.services.audit_quality import QualityAdapterError, StrictJsonError, ValidationResult, _read_regular_file, strict_json_loads
from app.services.audit_coverage import CoverageInputError, calculate_coverage, validate_inventory, validate_pass1_source_coverage
from app.services.audit_quality_invalidation import record_mutation_invalidation
from app.services.audit_dependencies import DependencyError, affected_nodes
from app.services.audit_lesson_selection import LessonSelectionError, select_lessons, validate_lesson_snapshot
from app.services.audit_workflow import WorkItemInputError, WorkItemTransitionError, apply_ledger_patch, runnable_work_items, transition_work_item, validate_work_items

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024
_STATUSES = {"READY", "RUNNING", "INTERRUPTED"}
_STAGES = {1, 2, 3}
_CHECKPOINT_TRANSITIONS = {"READY": {"RUNNING"}, "RUNNING": {"READY", "INTERRUPTED"}, "INTERRUPTED": {"READY"}}
_CHECKER_FILES = (
    "services/audit_checkpoint.py", "services/audit_batch.py", "services/audit_batch_ledgers.py",
    "services/audit_coverage.py", "services/audit_dependencies.py", "services/audit_lesson_selection.py",
    "services/audit_lineage_v4.py",
    "services/audit_stage_gate.py", "services/audit_workflow.py", "services/audit_quality.py",
    "services/audit_report_source.py", "services/audit_excel_report.py", "services/audit_annotation_draft.py",
    "models/entities.py", "core/migrations.py",
    "schemas/audit_batch_ledger_contract.v3.json", "schemas/audit_batch_ledger_contract.v4.json", "../requirements.txt",
)


def checkpoint_checker_digest() -> str:
    """Fingerprint code, contract schema, and pinned dependencies that interpret batch evidence."""
    app_dir = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256(b"boq-audit-checkpoint-contract-v2\0")
    for relative in _CHECKER_FILES:
        path = (app_dir / relative).resolve(strict=True)
        digest.update(relative.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


class CheckpointError(ValueError):
    """Checkpoint input is stale or inconsistent."""


class CheckpointConflict(CheckpointError):
    """Another writer advanced the checkpoint version."""


def _json_bytes(value: Any, label: str) -> str:
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise CheckpointError(f"{label} must be JSON-compatible") from exc
    if len(serialized.encode("utf-8")) > _MAX_CHECKPOINT_BYTES:
        raise CheckpointError(f"{label} exceeds the checkpoint size limit")
    return serialized


def _check_total_size(*values: str) -> None:
    if sum(len(value.encode("utf-8")) for value in values) > _MAX_CHECKPOINT_BYTES:
        raise CheckpointError("checkpoint state exceeds the total size limit")


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise CheckpointError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_current_reviewer(db: Session, approval: AuditInventoryApproval) -> None:
    reviewer = db.get(User, approval.reviewer_user_id)
    allowlist = get_settings().audit_quality_reviewer_email_set
    if (
        reviewer is None
        or not reviewer.is_active
        or reviewer.role != UserRole.ADMIN
        or reviewer.email.strip().lower() not in allowlist
    ):
        raise CheckpointError("inventory approval reviewer is not currently allowlisted")


def _validate_batches(inventory: dict[str, Any], batch_plan: Any) -> dict[str, list[list[str]]]:
    expected = {item["check_id"]: item for item in inventory["expected_work_items"]}
    if not expected or not isinstance(batch_plan, dict) or set(batch_plan) != {"1", "2", "3"}:
        raise CheckpointError("batch plan must contain pass 1, 2, and 3 for a non-empty inventory")
    normalized: dict[str, list[list[str]]] = {}
    seen: set[str] = set()
    for pass_key, batches in batch_plan.items():
        if not isinstance(batches, list):
            raise CheckpointError("each pass batch plan must be a list")
        normalized[pass_key] = []
        for batch in batches:
            if not isinstance(batch, list) or not batch:
                raise CheckpointError("batches must be non-empty lists")
            if any(not isinstance(check_id, str) or not check_id.strip() for check_id in batch):
                raise CheckpointError("batch IDs must be non-empty strings")
            if len(set(batch)) != len(batch) or any(check_id in seen for check_id in batch):
                raise CheckpointError("batch plan contains duplicate check IDs")
            for check_id in batch:
                if check_id not in expected or expected[check_id]["pass_no"] != int(pass_key):
                    raise CheckpointError("batch check ID is unknown or assigned to the wrong pass")
                seen.add(check_id)
            normalized[pass_key].append(list(batch))
    if seen != set(expected):
        raise CheckpointError("batch plan must cover every expected work item exactly once")
    return normalized


def build_batch_plan(inventory: dict[str, Any], *, max_batch_size: int = 4) -> dict[str, list[list[str]]]:
    """Build deterministic same-pass batches from approved work and dependencies."""
    try:
        approved = validate_inventory(inventory)
    except CoverageInputError as exc:
        raise CheckpointError("cannot plan batches from invalid inventory") from exc
    if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or not 1 <= max_batch_size <= 32:
        raise CheckpointError("max_batch_size must be between 1 and 32")
    expected = {item["check_id"]: item for item in approved["expected_work_items"]}
    if not expected:
        raise CheckpointError("approved inventory has no expected work items")
    batches: dict[str, list[list[str]]] = {str(stage): [] for stage in (1, 2, 3)}
    completed: set[str] = set()
    for stage in (1, 2, 3):
        pending = [item["check_id"] for item in approved["expected_work_items"] if item["pass_no"] == stage]
        while pending:
            ready = [
                check_id for check_id in pending
                if set(expected[check_id]["dependencies"]) <= completed
            ]
            if not ready:
                raise CheckpointError(f"pass {stage} work items contain an unschedulable dependency cycle")
            for offset in range(0, len(ready), max_batch_size):
                batch = ready[offset:offset + max_batch_size]
                batches[str(stage)].append(batch)
                completed.update(batch)
                pending = [check_id for check_id in pending if check_id not in set(batch)]
    return _validate_batches(approved, batches)


def _ledger_from_inventory(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**copy.deepcopy(item), "execution_status": "PENDING", "result_status": "NOT_CHECKED", "evidence_ids": [], "applicability_reason": None}
        for item in inventory["expected_work_items"]
    ]


def _validate_ledger(inventory: dict[str, Any], ledger: Any) -> list[dict[str, Any]]:
    if not isinstance(ledger, list) or len(ledger) != len(inventory["expected_work_items"]):
        raise CheckpointError("ledger must contain every expected work item")
    expected = {item["check_id"]: item for item in inventory["expected_work_items"]}
    seen: set[str] = set()
    for item in ledger:
        if not isinstance(item, dict) or item.get("check_id") not in expected or item["check_id"] in seen:
            raise CheckpointError("ledger work-item IDs are invalid")
        seen.add(item["check_id"])
        source_item = expected[item["check_id"]]
        if any(item.get(key) != value for key, value in source_item.items()):
            raise CheckpointError("ledger changed an approved work-item field")
    try:
        validate_work_items(ledger)
        calculate_coverage(inventory, ledger)
    except (CoverageInputError, WorkItemInputError) as exc:
        raise CheckpointError("ledger is invalid for approved inventory") from exc
    for item in ledger:
        status = item["execution_status"]
        if status in {"PENDING", "RUNNING", "BLOCKED"} and (
            item["result_status"] != "NOT_CHECKED" or item.get("evidence_ids") or item.get("applicability_reason")
        ):
            raise CheckpointError("unfinished work item contains result or evidence")
        if status == "INVALIDATED" and item["result_status"] != "NOT_CHECKED":
            if not item.get("evidence_ids") or (item["result_status"] == "NOT_APPLICABLE" and not item.get("applicability_reason")):
                raise CheckpointError("invalidated result is missing evidence")
    return ledger


def _validate_batch_results(
    db: Session,
    *,
    run_id: int,
    inventory: dict[str, Any],
    ledger: list[dict[str, Any]],
    batch_plan: dict[str, list[list[str]]],
    contract_version: int,
) -> list[dict[str, Any]]:
    batches = {}
    for pass_key, plan in batch_plan.items():
        for check_ids in plan:
            key = hashlib.sha256(_json_bytes({"pass_no": int(pass_key), "check_ids": check_ids}, "batch key").encode("utf-8")).hexdigest()
            batches[key] = (int(pass_key), check_ids)
    ledger_by_id = {item["check_id"]: item for item in ledger}
    evidence_by_check: dict[str, set[str]] = {}
    all_evidence_ids: set[str] = set()
    seen_keys: set[str] = set()
    validated_results: list[dict[str, Any]] = []
    seen_records: list[dict[str, Any]] = []
    result_bytes = 0
    seen_result_ids: set[int] = set()
    seen_batch_ledger_ids: set[int] = set()
    all_revisions = list(db.scalars(select(AuditRunBatchRevision).where(AuditRunBatchRevision.run_id == run_id).order_by(AuditRunBatchRevision.id)))
    revisions_by_base: dict[int, list[AuditRunBatchRevision]] = {}
    for revision in all_revisions:
        revisions_by_base.setdefault(revision.base_result_id, []).append(revision)
    result_rows = list(db.scalars(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == run_id).order_by(AuditRunBatchResult.id)))
    result_by_id = {result.id: result for result in result_rows}
    expected_by_id = {item["check_id"]: item for item in inventory["expected_work_items"]}
    dependencies = {check_id: item["dependencies"] for check_id, item in expected_by_id.items()}
    invalidation_events: list[tuple[int, set[str]]] = []
    try:
        for revision in all_revisions:
            base = result_by_id.get(revision.base_result_id)
            if base is None or revision.run_id != run_id:
                raise CheckpointError("batch invalidation revision is detached")
            invalidated_json = _json_bytes(json.loads(revision.invalidated_check_ids_json), "invalidated check IDs")
            invalidated = json.loads(invalidated_json)
            if (
                invalidated_json != revision.invalidated_check_ids_json
                or not isinstance(invalidated, list)
                or any(not isinstance(check_id, str) or check_id not in expected_by_id for check_id in invalidated)
                or len(set(invalidated)) != len(invalidated)
                or invalidated != sorted(invalidated)
                or (invalidated and revision.invalidation_digest != hashlib.sha256(invalidated_json.encode("utf-8")).hexdigest())
                or (not invalidated and revision.invalidation_digest not in {None, hashlib.sha256(invalidated_json.encode("utf-8")).hexdigest()})
            ):
                raise CheckpointError("batch invalidation manifest is invalid")
            base_check_ids = json.loads(base.check_ids_json)
            downstream = affected_nodes(dependencies, base_check_ids) - set(base_check_ids)
            expected_invalidated = {
                check_id
                for other in result_rows
                if other.id != base.id and other.checkpoint_version < revision.checkpoint_version
                and downstream.intersection(json.loads(other.check_ids_json))
                for check_id in json.loads(other.check_ids_json)
            }
            if set(invalidated) != expected_invalidated:
                raise CheckpointError("batch invalidation manifest does not match the approved dependency graph")
            invalidation_events.append((revision.checkpoint_version, set(invalidated)))
    except (TypeError, ValueError, json.JSONDecodeError, DependencyError, KeyError) as exc:
        raise CheckpointError("persisted batch invalidation state is corrupt") from exc
    stale_result_ids = set()
    for result in result_rows:
        revisions = revisions_by_base.get(result.id, [])
        effective_version = max([result.checkpoint_version, *(revision.checkpoint_version for revision in revisions)])
        check_ids = set(json.loads(result.check_ids_json))
        if any(version > effective_version and check_ids.intersection(invalidated) for version, invalidated in invalidation_events):
            stale_result_ids.add(result.id)
    checkpoint_row = db.get(AuditRunCheckpoint, run_id, populate_existing=True)
    checkpoint_version = checkpoint_row.version
    for result in result_rows:
        expected = batches.get(result.batch_key)
        if expected is None or result.batch_key in seen_keys:
            raise CheckpointError("persisted batch result is outside the trusted plan")
        pass_no, check_ids = expected
        if result.derived_from_run_id is not None and result.derived_from_run_id != checkpoint_row.derived_from_run_id:
            raise CheckpointError("batch import provenance does not match the checkpoint")
        seen_result_ids.add(result.id)
        try:
            patch_json = _json_bytes(json.loads(result.patch_json), "persisted batch patch")
            evidence_json = _json_bytes(json.loads(result.evidence_json), "persisted batch evidence")
            persisted_ids = json.loads(result.check_ids_json)
            source_files = json.loads(result.source_files_json)
            canonical_ids = _json_bytes(persisted_ids, "persisted batch check IDs")
            canonical_source_files = _json_bytes(source_files, "persisted batch source files")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CheckpointError("persisted batch result is malformed") from exc
        if (
            canonical_ids != result.check_ids_json
            or canonical_source_files != result.source_files_json
            or persisted_ids != check_ids
            or result.pass_no != pass_no
            or not isinstance(source_files, list)
            or any(not isinstance(path, str) for path in source_files)
            or len(set(source_files)) != len(source_files)
        ):
            raise CheckpointError("persisted batch result digest or scope mismatch")
        batch_ledger = db.scalar(select(AuditRunBatchLedger).where(AuditRunBatchLedger.batch_result_id == result.id))
        if batch_ledger is None or batch_ledger.run_id != run_id:
            raise CheckpointError("persisted batch ledger manifest is missing or detached")
        seen_batch_ledger_ids.add(batch_ledger.id)
        versions = [(result.patch_json, result.evidence_json, batch_ledger.records_json, result.patch_digest, result.evidence_digest, batch_ledger.records_digest)]
        previous_version = result.checkpoint_version
        revisions = revisions_by_base.pop(result.id, [])
        if len(revisions) > 2:
            raise CheckpointError("batch repair budget exceeded")
        for expected_no, revision in enumerate(revisions, 1):
            gate = db.get(AuditRunStageGate, revision.gate_id)
            try:
                receipt_raw = gate.receipt_json.encode("utf-8") if gate else b""
                receipt = strict_json_loads(receipt_raw, label="repair gate receipt")
            except (UnicodeError, StrictJsonError) as exc:
                raise CheckpointError("repair gate receipt is invalid") from exc
            blockers = receipt.get("blocking_checks") if isinstance(receipt, dict) else None
            fingerprint = hashlib.sha256(_json_bytes({
                "pass_no": pass_no, "batch_key": result.batch_key,
                "blocking_checks": sorted(set(blockers)),
            }, "repair blocker fingerprint").encode("utf-8")).hexdigest() if isinstance(blockers, list) and all(isinstance(value, str) for value in blockers) else None
            if (
                revision.revision_no != expected_no
                or revision.run_id != run_id
                or revision.checkpoint_version <= previous_version or revision.checkpoint_version > checkpoint_version
                or revision.blocker_fingerprint != fingerprint
                or gate is None or gate.run_id != run_id or gate.pass_no != pass_no
                or gate.advanced or gate.checkpoint_version >= revision.checkpoint_version
                or hashlib.sha256(receipt_raw).hexdigest() != gate.receipt_digest
                or not isinstance(receipt, dict) or receipt.get("controller_status") != "BLOCKED"
                or receipt.get("schema_version") != 1 or receipt.get("validator_exit_code") != 2
                or receipt.get("run_id") != run_id or receipt.get("stage") != f"pass{pass_no}"
                or receipt.get("source_digest") != checkpoint_row.source_digest
                or receipt.get("skill_digest") != checkpoint_row.skill_digest
                or receipt.get("snapshot_id") != gate.snapshot_id
            ):
                raise CheckpointError("batch revision is not bound to a valid blocked gate")
            previous_version = revision.checkpoint_version
            versions.append((revision.patch_json, revision.evidence_json, revision.records_json, revision.patch_digest, revision.evidence_digest, revision.records_digest))
        for revision_index, (candidate_patch, candidate_evidence, candidate_records, patch_digest, evidence_digest, records_digest) in enumerate(versions):
            try:
                if (
                    _json_bytes(json.loads(candidate_patch), "persisted batch patch") != candidate_patch
                    or _json_bytes(json.loads(candidate_evidence), "persisted batch evidence") != candidate_evidence
                    or hashlib.sha256(candidate_patch.encode("utf-8")).hexdigest() != patch_digest
                    or hashlib.sha256(candidate_evidence.encode("utf-8")).hexdigest() != evidence_digest
                ):
                    raise CheckpointError("persisted batch payload digest mismatch")
                patch, evidence = validate_batch_payload(
                    json.loads(candidate_patch), json.loads(candidate_evidence),
                    assigned_check_ids=check_ids, allowed_source_files=set(source_files),
                )
                batch_records = decode_batch_ledger_manifest(candidate_records, records_digest)
                validate_batch_ledger_records(
                    {"records": batch_records}, inventory=inventory,
                    assigned_check_ids=check_ids, pass_no=pass_no,
                    existing=seen_records,
                    batch_evidence_ids={row["evidence_id"] for row in evidence["evidence"]},
                    allowed_source_files=set(source_files),
                    contract_version=contract_version,
                )
            except (BatchOutputError, TypeError, ValueError, json.JSONDecodeError) as exc:
                label = "persisted batch result digest or scope mismatch" if revision_index == 0 else "persisted batch revision payload is invalid"
                raise CheckpointError(label) from exc
        if result.id in stale_result_ids:
            batch_records = []
        else:
            try:
                batch_records = validate_batch_ledger_records(
                    {"records": batch_records}, inventory=inventory,
                    assigned_check_ids=check_ids, pass_no=pass_no,
                    existing=seen_records,
                    batch_evidence_ids={row["evidence_id"] for row in evidence["evidence"]},
                    allowed_source_files=set(source_files),
                    contract_version=contract_version,
                )
            except (BatchOutputError, TypeError, ValueError) as exc:
                raise CheckpointError("effective batch ledger records are invalid") from exc
        result_bytes += sum(len(_json_bytes(record, "persisted ledger record").encode("utf-8")) for record in batch_records)
        if result_bytes > _MAX_CHECKPOINT_BYTES:
            raise CheckpointError("persisted batch ledger records exceed the run storage limit")
        if result.id not in stale_result_ids:
            seen_records.extend(batch_records)
        for item in patch["items"]:
            ledger_item = ledger_by_id[item["check_id"]]
            if ledger_item["pass_no"] != pass_no:
                raise CheckpointError("persisted batch pass does not match the approved work item")
            stale = result.id in stale_result_ids
            if stale and ledger_item["execution_status"] == "DONE":
                raise CheckpointError("invalidated downstream batch still has DONE work items")
            if not stale and ledger_item["execution_status"] != "DONE":
                raise CheckpointError("effective batch result has no matching DONE work item")
            if not stale and (
                ledger_item["result_status"] != item["result_status"]
                or ledger_item["evidence_ids"] != item["evidence_ids"]
                or ledger_item.get("applicability_reason") != item.get("applicability_reason")
            ):
                raise CheckpointError("persisted batch result does not match the current ledger")
            if not stale:
                evidence_by_check[item["check_id"]] = set(item["evidence_ids"])
                all_evidence_ids.update(item["evidence_ids"])
        if len(all_evidence_ids) != sum(len(value) for value in evidence_by_check.values()):
            raise CheckpointError("evidence IDs are reused across batch results")
        seen_keys.add(result.batch_key)
        validated_results.append({
            "base_result_id": result.id,
            "revision_no": len(revisions),
            "pass_no": pass_no,
            "check_ids": list(check_ids),
            "patch": patch,
            "evidence": evidence if result.id not in stale_result_ids else {"evidence": []},
            "ledger_records": batch_records if result.id not in stale_result_ids else [],
            "stale": result.id in stale_result_ids,
        })

    for item in ledger:
        if item["execution_status"] == "DONE" and evidence_by_check.get(item["check_id"]) != set(item["evidence_ids"]):
            raise CheckpointError("DONE work item has no durable batch evidence")
    if seen_batch_ledger_ids != {item.id for item in db.scalars(select(AuditRunBatchLedger).where(AuditRunBatchLedger.run_id == run_id))}:
        raise CheckpointError("persisted batch ledger manifest is detached from its result")
    if revisions_by_base:
        raise CheckpointError("batch revision is detached from its base result")
    return validated_results


def create_run_checkpoint(
    db: Session,
    *,
    run_id: int,
    approval_id: int,
    current_source_digest: str,
    skill_digest: str,
    batch_plan: Any,
    lesson_snapshot: dict[str, Any] | None = None,
    reuse_from_run_id: int | None = None,
) -> AuditRunCheckpoint:
    source_digest = _require_digest(current_source_digest, "source_digest")
    skill_digest = _require_digest(skill_digest, "skill_digest")
    run = db.get(AuditRun, run_id, populate_existing=True)
    approval = db.get(AuditInventoryApproval, approval_id, populate_existing=True)
    if run is None or approval is None or approval.job_id != run.job_id:
        raise CheckpointError("run or matching inventory approval was not found")
    if run.status != "RUNNING":
        raise CheckpointError("checkpoint requires the current running audit attempt")
    latest_attempt = db.scalar(select(func.max(AuditRun.attempt)).where(AuditRun.job_id == run.job_id))
    if run.attempt != latest_attempt:
        raise CheckpointError("checkpoint requires the latest audit attempt")
    latest_approval_id = db.scalar(
        select(AuditInventoryApproval.id)
        .where(AuditInventoryApproval.job_id == run.job_id)
        .order_by(AuditInventoryApproval.approved_at.desc(), AuditInventoryApproval.id.desc())
        .limit(1)
    )
    if approval.id != latest_approval_id:
        raise CheckpointError("checkpoint requires the latest inventory approval")
    if approval.source_digest != source_digest:
        raise CheckpointError("inventory approval is stale for the current source")
    _require_current_reviewer(db, approval)
    if db.get(AuditRunCheckpoint, run_id, populate_existing=True) is not None:
        raise CheckpointError("checkpoint already exists for this run")
    try:
        inventory = validate_pass1_source_coverage(json.loads(approval.inventory_json))
    except (json.JSONDecodeError, CoverageInputError) as exc:
        raise CheckpointError("approved inventory is invalid") from exc
    batches = _validate_batches(inventory, batch_plan)
    if lesson_snapshot is None:
        lesson_snapshot = select_lessons(
            project_scope=run.job.job_code,
            expected_work_items=inventory["expected_work_items"],
            current_lessons=[],
        )
    try:
        validate_lesson_snapshot(
            lesson_snapshot, project_scope=run.job.job_code,
            expected_work_items=inventory["expected_work_items"],
        )
    except LessonSelectionError as exc:
        raise CheckpointError("lesson selection snapshot is invalid") from exc
    if lesson_snapshot["blocking_lesson_ids"]:
        raise CheckpointError("applicable lesson needs reviewer decision before audit can start")
    inventory_json = _json_bytes(inventory, "inventory")
    batch_plan_json = _json_bytes(batches, "batch_plan")
    ledger_json = _json_bytes(_ledger_from_inventory(inventory), "ledger")
    lesson_snapshot_json = _json_bytes(lesson_snapshot, "lesson_snapshot")
    _check_total_size(inventory_json, batch_plan_json, ledger_json, lesson_snapshot_json, "{}", "[]")
    inventory_digest = hashlib.sha256(inventory_json.encode("utf-8")).hexdigest()
    row = AuditRunCheckpoint(
        run_id=run_id,
        contract_version=4,
        approval_id=approval.id,
        source_digest=source_digest,
        skill_digest=skill_digest,
        inventory_digest=inventory_digest,
        batch_plan_digest=hashlib.sha256(batch_plan_json.encode("utf-8")).hexdigest(),
        inventory_json=inventory_json,
        batch_plan_json=batch_plan_json,
        ledger_json=ledger_json,
        lesson_snapshot_json=lesson_snapshot_json,
        lesson_snapshot_digest=hashlib.sha256(lesson_snapshot_json.encode("utf-8")).hexdigest(),
        checker_digest=checkpoint_checker_digest(),
        gate_reports_json="{}",
        pass_no=1,
        active_batch_ids_json="[]",
        status="READY",
        version=1,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise CheckpointError("checkpoint already exists for this run") from exc
    if reuse_from_run_id is not None and reuse_from_run_id != run_id:
        _import_prior_pass_one(db, source_run_id=reuse_from_run_id, target_run_id=run_id)
    return row


def load_run_checkpoint(
    db: Session,
    *,
    run_id: int,
    current_source_digest: str,
    current_skill_digest: str,
) -> dict[str, Any]:
    row = db.get(AuditRunCheckpoint, run_id, populate_existing=True)
    if row is None:
        raise CheckpointError("checkpoint not found")
    if row.contract_version != 4:
        raise CheckpointError("LEGACY_V3_UNVERIFIED: persisted v3 checkpoint is history only; start a new v4 attempt")
    if row.source_digest != _require_digest(current_source_digest, "source_digest"):
        raise CheckpointError("source changed; start a new audit run")
    if row.skill_digest != _require_digest(current_skill_digest, "skill_digest"):
        raise CheckpointError("skill changed; start a new audit run")
    if row.checker_digest != checkpoint_checker_digest():
        raise CheckpointError("audit checker or dependency configuration changed; start a new audit run")
    run = db.get(AuditRun, run_id, populate_existing=True)
    if run is None:
        raise CheckpointError("checkpoint run was removed")
    if row.derived_from_run_id is not None:
        parent = db.get(AuditRun, row.derived_from_run_id, populate_existing=True)
        parent_checkpoint = db.get(AuditRunCheckpoint, row.derived_from_run_id, populate_existing=True)
        if (
            parent is None or parent_checkpoint is None or parent.job_id != run.job_id
            or parent.attempt >= run.attempt
            or any(getattr(row, field) != getattr(parent_checkpoint, field) for field in (
                "contract_version", "source_digest", "skill_digest", "checker_digest", "approval_id",
                "inventory_digest", "batch_plan_digest", "lesson_snapshot_digest",
            ))
        ):
            raise CheckpointError("checkpoint import provenance is invalid")
    latest_approval_id = db.scalar(
        select(AuditInventoryApproval.id)
        .where(AuditInventoryApproval.job_id == run.job_id)
        .order_by(AuditInventoryApproval.approved_at.desc(), AuditInventoryApproval.id.desc())
        .limit(1)
    )
    if row.approval_id != latest_approval_id:
        raise CheckpointError("inventory approval changed; start a new audit run")
    approval = db.get(AuditInventoryApproval, latest_approval_id, populate_existing=True)
    if approval is None or approval.source_digest != row.source_digest:
        raise CheckpointError("inventory approval source changed; start a new audit run")
    _require_current_reviewer(db, approval)
    try:
        inventory = validate_pass1_source_coverage(json.loads(row.inventory_json))
        approved_inventory = validate_pass1_source_coverage(json.loads(approval.inventory_json))
        ledger = json.loads(row.ledger_json)
        batch_plan = json.loads(row.batch_plan_json)
        active_batch_ids = json.loads(row.active_batch_ids_json)
        gate_reports = json.loads(row.gate_reports_json)
        lesson_snapshot = json.loads(row.lesson_snapshot_json) if row.lesson_snapshot_json is not None else None
        _validate_batches(inventory, batch_plan)
        _validate_ledger(inventory, ledger)
    except (TypeError, json.JSONDecodeError, CoverageInputError, CheckpointError) as exc:
        raise CheckpointError("checkpoint JSON is corrupt") from exc
    if approved_inventory != inventory:
        raise CheckpointError("approved inventory changed; start a new audit run")
    if (row.lesson_snapshot_json is None) != (row.lesson_snapshot_digest is None):
        raise CheckpointError("lesson snapshot digest is incomplete")
    if lesson_snapshot is not None:
        try:
            validate_lesson_snapshot(
                lesson_snapshot, project_scope=run.job.job_code,
                expected_work_items=inventory["expected_work_items"],
            )
        except LessonSelectionError as exc:
            raise CheckpointError("lesson selection snapshot is invalid") from exc
        if hashlib.sha256(row.lesson_snapshot_json.encode("utf-8")).hexdigest() != row.lesson_snapshot_digest:
            raise CheckpointError("lesson snapshot checkpoint digest mismatch")
        if lesson_snapshot["blocking_lesson_ids"]:
            raise CheckpointError("checkpoint includes applicable lessons awaiting reviewer decision")
    if row.pass_no not in _STAGES or row.status not in _STATUSES or row.version < 1:
        raise CheckpointError("checkpoint state is corrupt")
    if not isinstance(active_batch_ids, list) or any(not isinstance(item, str) or not item.strip() for item in active_batch_ids):
        raise CheckpointError("active batch checkpoint is corrupt")
    if not isinstance(gate_reports, dict) or any(key not in {"1", "2"} or not isinstance(value, str) or not _DIGEST.fullmatch(value) for key, value in gate_reports.items()):
        raise CheckpointError("checkpoint gate history is corrupt")
    if set(gate_reports) != {str(stage) for stage in range(1, row.pass_no)}:
        raise CheckpointError("checkpoint gate history does not match the current pass")
    running_ids = {item["check_id"] for item in ledger if item["execution_status"] == "RUNNING"}
    if set(active_batch_ids) != running_ids or len(active_batch_ids) != len(running_ids):
        raise CheckpointError("active batch checkpoint does not match ledger")
    if (row.status == "RUNNING") != bool(active_batch_ids):
        raise CheckpointError("checkpoint status does not match active batch")
    batch_results = _validate_batch_results(db, run_id=run_id, inventory=inventory, ledger=ledger, batch_plan=batch_plan, contract_version=row.contract_version)
    ledger_records = [record for result in batch_results if not result.get("stale") for record in result["ledger_records"]]
    ledger_projection = project_batch_ledger_records(ledger_records, contract_version=row.contract_version)
    canonical_inventory = _json_bytes(inventory, "inventory")
    if hashlib.sha256(canonical_inventory.encode("utf-8")).hexdigest() != row.inventory_digest:
        raise CheckpointError("inventory checkpoint digest mismatch")
    canonical_plan = _json_bytes(batch_plan, "batch_plan")
    if hashlib.sha256(canonical_plan.encode("utf-8")).hexdigest() != row.batch_plan_digest:
        raise CheckpointError("batch plan checkpoint digest mismatch")
    _check_total_size(row.inventory_json, row.batch_plan_json, row.ledger_json, row.gate_reports_json, row.active_batch_ids_json, row.lesson_snapshot_json or "")
    return {
        "run_id": row.run_id,
        "contract_version": row.contract_version,
        "approval_id": row.approval_id,
        "source_digest": row.source_digest,
        "skill_digest": row.skill_digest,
        "checker_digest": row.checker_digest,
        "derived_from_run_id": row.derived_from_run_id,
        "inventory_digest": row.inventory_digest,
        "batch_plan_digest": row.batch_plan_digest,
        "inventory": inventory,
        "batch_plan": batch_plan,
        "ledger": ledger,
        "lesson_snapshot": lesson_snapshot,
        "lesson_snapshot_digest": row.lesson_snapshot_digest,
        "pass_no": row.pass_no,
        "active_batch_ids": active_batch_ids,
        "gate_reports": gate_reports,
        "batch_results": batch_results,
        "ledger_records": ledger_records,
        "ledger_projection": ledger_projection,
        "status": row.status,
        "version": row.version,
    }


def _store_state(
    db: Session,
    *,
    checkpoint: dict[str, Any],
    expected_version: int,
    ledger: list[dict[str, Any]],
    active_batch_ids: list[str],
    status: str,
) -> int:
    if status not in _STATUSES:
        raise CheckpointError("invalid checkpoint status")
    row = db.get(AuditRunCheckpoint, checkpoint["run_id"], populate_existing=True)
    if row is None:
        raise CheckpointError("checkpoint not found")
    if row.contract_version != checkpoint["contract_version"] or row.version != expected_version or checkpoint["version"] != expected_version or row.status != checkpoint["status"]:
        raise CheckpointConflict("checkpoint changed; reload before writing")
    try:
        inventory = validate_pass1_source_coverage(json.loads(row.inventory_json))
        batch_plan = json.loads(row.batch_plan_json)
        persisted_ledger = json.loads(row.ledger_json)
        persisted_active_ids = json.loads(row.active_batch_ids_json)
        gate_reports = json.loads(row.gate_reports_json)
    except (json.JSONDecodeError, CoverageInputError) as exc:
        raise CheckpointError("persisted checkpoint inputs are invalid") from exc
    if _json_bytes(inventory, "inventory") != _json_bytes(checkpoint["inventory"], "inventory") or batch_plan != checkpoint["batch_plan"]:
        raise CheckpointError("checkpoint input does not match persisted inventory or batch plan")
    if (
        persisted_ledger != checkpoint["ledger"]
        or persisted_active_ids != checkpoint["active_batch_ids"]
        or gate_reports != checkpoint["gate_reports"]
        or _validate_batch_results(db, run_id=checkpoint["run_id"], inventory=inventory, ledger=persisted_ledger, batch_plan=batch_plan, contract_version=checkpoint["contract_version"]) != checkpoint.get("batch_results")
        or row.pass_no != checkpoint["pass_no"]
        or row.source_digest != checkpoint["source_digest"]
        or row.contract_version != checkpoint["contract_version"]
        or row.skill_digest != checkpoint["skill_digest"]
        or row.approval_id != checkpoint["approval_id"]
    ):
        raise CheckpointConflict("loaded checkpoint state is stale or modified")
    if status not in _CHECKPOINT_TRANSITIONS[row.status]:
        raise CheckpointError("checkpoint status transition is not allowed")
    old_ledger = checkpoint["ledger"]
    _validate_ledger(inventory, ledger)
    old_by_id = {item["check_id"]: item for item in old_ledger}
    new_by_id = {item["check_id"]: item for item in ledger}
    for check_id, old in old_by_id.items():
        new = new_by_id[check_id]
        try:
            if old["execution_status"] == new["execution_status"]:
                transitioned = old
            elif new["execution_status"] == "DONE" and old["execution_status"] == "RUNNING":
                kwargs = {"result_status": new.get("result_status"), "evidence_ids": new.get("evidence_ids")}
                if new.get("applicability_reason") is not None:
                    kwargs["applicability_reason"] = new["applicability_reason"]
                transitioned = transition_work_item(old, "DONE", **kwargs)
            elif old["execution_status"] == "DONE" and new["execution_status"] == "PENDING":
                transitioned = transition_work_item(transition_work_item(old, "INVALIDATED"), "PENDING")
            else:
                transitioned = transition_work_item(old, new["execution_status"])
        except (WorkItemInputError, WorkItemTransitionError) as exc:
            raise CheckpointError("ledger transition is invalid") from exc
        if transitioned != new:
            raise CheckpointError("ledger state fields did not follow the allowed transition")
        if any(old.get(key) != new.get(key) for key in ("document_id", "revision_digest", "page_ids", "sheet_ids", "target_ids", "estimate_line_group_ids", "check_kind", "pass_no", "dependencies")):
            raise CheckpointError("ledger scope or dependencies changed")
    running_ids = {item["check_id"] for item in ledger if item["execution_status"] == "RUNNING"}
    if len(set(active_batch_ids)) != len(active_batch_ids) or set(active_batch_ids) != running_ids:
        raise CheckpointError("active batch IDs must exactly match RUNNING ledger items")
    if (status == "RUNNING") != bool(active_batch_ids):
        raise CheckpointError("checkpoint status must match active batch state")

    ledger_json = _json_bytes(ledger, "ledger")
    active_json = _json_bytes(active_batch_ids, "active_batch_ids")
    _check_total_size(row.inventory_json, row.batch_plan_json, ledger_json, row.gate_reports_json, active_json)
    result = db.execute(
        update(AuditRunCheckpoint)
        .where(AuditRunCheckpoint.run_id == checkpoint["run_id"], AuditRunCheckpoint.version == expected_version)
        .values(ledger_json=ledger_json, active_batch_ids_json=active_json, status=status, version=expected_version + 1, updated_at=utcnow())
    )
    if result.rowcount != 1:
        raise CheckpointConflict("checkpoint version changed; reload before writing")
    return expected_version + 1


def start_checkpoint_batch(db: Session, *, checkpoint: dict[str, Any], expected_version: int, batch_ids: list[str]) -> int:
    if checkpoint["status"] != "READY":
        raise CheckpointError("checkpoint is not ready to start a batch")
    inventory = checkpoint["inventory"]
    plan = checkpoint["batch_plan"].get(str(checkpoint["pass_no"]), [])
    if batch_ids not in plan:
        raise CheckpointError("batch is not in the trusted checkpoint plan")
    ledger = copy.deepcopy(checkpoint["ledger"])
    by_id = {item["check_id"]: item for item in ledger}
    if any(by_id.get(check_id, {}).get("execution_status") != "PENDING" for check_id in batch_ids):
        raise CheckpointError("batch work items must all be PENDING")
    if not set(batch_ids) <= set(runnable_work_items(ledger, checkpoint["pass_no"])):
        raise CheckpointError("batch prerequisites are not complete")
    for item in ledger:
        if item["check_id"] in batch_ids:
            item.update(transition_work_item(item, "RUNNING"))
    return _store_state(db, checkpoint=checkpoint, expected_version=expected_version, ledger=ledger, active_batch_ids=batch_ids, status="RUNNING")


def finish_checkpoint_batch(
    db: Session,
    *,
    checkpoint: dict[str, Any],
    expected_version: int,
    patch: Any,
    evidence: Any,
    ledger_records: Any = None,
    allowed_source_files: set[str],
) -> int:
    if checkpoint["status"] != "RUNNING" or not checkpoint["active_batch_ids"]:
        raise CheckpointError("checkpoint has no active batch to finish")
    active = checkpoint["active_batch_ids"]
    try:
        normalized_patch, normalized_evidence = validate_batch_payload(
            patch,
            evidence,
            assigned_check_ids=active,
            allowed_source_files=allowed_source_files,
        )
        normalized_records = validate_batch_ledger_records(
            ledger_records if ledger_records is not None else {"records": []},
            inventory=checkpoint["inventory"],
            assigned_check_ids=active,
            pass_no=checkpoint["pass_no"],
            existing=checkpoint.get("ledger_records", []),
            batch_evidence_ids={row["evidence_id"] for row in normalized_evidence["evidence"]},
            allowed_source_files=allowed_source_files,
            contract_version=checkpoint["contract_version"],
        )
        ledger = apply_ledger_patch(checkpoint["ledger"], active, normalized_patch)
        patch_json = _json_bytes(normalized_patch, "batch patch")
        evidence_json = _json_bytes(normalized_evidence, "batch evidence")
        check_ids_json = _json_bytes(active, "batch check IDs")
        source_files_json = _json_bytes(sorted(allowed_source_files), "batch source files")
        records_json = _json_bytes(normalized_records, "batch ledger records")
        if max(map(len, (patch_json, evidence_json, check_ids_json, source_files_json, records_json))) > 8 * 1024 * 1024:
            raise CheckpointError("batch result exceeds the storage limit")
    except (BatchOutputError, WorkItemInputError) as exc:
        raise CheckpointError("batch patch or evidence is invalid") from exc

    batch_key = hashlib.sha256(
        _json_bytes({"pass_no": checkpoint["pass_no"], "check_ids": active}, "batch key").encode("utf-8")
    ).hexdigest()
    persisted_records_json, records_digest = encode_batch_ledger_manifest(normalized_records)
    existing_result = next((item for item in checkpoint.get("batch_results", []) if item["pass_no"] == checkpoint["pass_no"] and item["check_ids"] == active), None)
    if existing_result is not None:
        return _append_reexecuted_batch_revision(
            db, checkpoint=checkpoint, expected_version=expected_version,
            patch=normalized_patch,
            batch_key=batch_key, patch_json=patch_json, evidence_json=evidence_json,
            records_json=persisted_records_json, records_digest=records_digest,
        )
    try:
        with db.begin_nested():
            version = _store_state(
                db,
                checkpoint=checkpoint,
                expected_version=expected_version,
                ledger=ledger,
                active_batch_ids=[],
                status="READY",
            )
            result = AuditRunBatchResult(
                run_id=checkpoint["run_id"],
                pass_no=checkpoint["pass_no"],
                batch_key=batch_key,
                check_ids_json=check_ids_json,
                source_files_json=source_files_json,
                patch_json=patch_json,
                evidence_json=evidence_json,
                patch_digest=hashlib.sha256(patch_json.encode("utf-8")).hexdigest(),
                evidence_digest=hashlib.sha256(evidence_json.encode("utf-8")).hexdigest(),
                checkpoint_version=version,
            )
            db.add(result)
            db.flush()
            db.add(AuditRunBatchLedger(
                run_id=checkpoint["run_id"], batch_result_id=result.id,
                records_json=persisted_records_json,
                records_digest=records_digest,
            ))
            db.flush()
        return version
    except IntegrityError as exc:
        raise CheckpointError("batch result already exists for this run") from exc


def _import_prior_pass_one(db: Session, *, source_run_id: int, target_run_id: int) -> None:
    """Copy only unchanged, unrevised Pass 1 batches; later passes must earn new gate receipts."""
    source_row = db.get(AuditRunCheckpoint, source_run_id, populate_existing=True)
    target_row = db.get(AuditRunCheckpoint, target_run_id, populate_existing=True)
    source_run = db.get(AuditRun, source_run_id, populate_existing=True)
    target_run = db.get(AuditRun, target_run_id, populate_existing=True)
    if source_row is None or target_row is None or source_run is None or target_run is None:
        return
    if source_run.status == "RUNNING" or source_run.job_id != target_run.job_id:
        return
    if source_run.attempt >= target_run.attempt:
        return
    if target_row.derived_from_run_id not in (None, source_run_id):
        return
    if any((
        source_row.source_digest != target_row.source_digest,
        source_row.skill_digest != target_row.skill_digest,
        source_row.checker_digest != target_row.checker_digest,
        source_row.contract_version != target_row.contract_version,
        source_row.approval_id != target_row.approval_id,
        source_row.inventory_digest != target_row.inventory_digest,
        source_row.batch_plan_digest != target_row.batch_plan_digest,
        source_row.lesson_snapshot_digest != target_row.lesson_snapshot_digest,
    )):
        return
    try:
        source = load_run_checkpoint(
            db, run_id=source_run_id,
            current_source_digest=target_row.source_digest,
            current_skill_digest=target_row.skill_digest,
        )
    except CheckpointError:
        return
    result_rows = {
        result.id: result
        for result in db.scalars(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == source_run_id))
    }
    candidates = []
    for prior in source["batch_results"]:
        if prior["pass_no"] != 1 or prior["revision_no"] != 0 or prior["stale"]:
            continue
        result = result_rows.get(prior["base_result_id"])
        if result is not None:
            candidates.append((prior, result))
    imported: set[int] = set()
    while candidates:
        checkpoint = load_run_checkpoint(
            db, run_id=target_run_id,
            current_source_digest=target_row.source_digest,
            current_skill_digest=target_row.skill_digest,
        )
        by_id = {item["check_id"]: item for item in checkpoint["ledger"]}
        progressed = False
        for prior, result in candidates:
            if prior["base_result_id"] in imported:
                continue
            checks = prior["check_ids"]
            if any(by_id.get(check_id, {}).get("execution_status") != "PENDING" for check_id in checks):
                continue
            try:
                with db.begin_nested():
                    version = start_checkpoint_batch(db, checkpoint=checkpoint, expected_version=checkpoint["version"], batch_ids=checks)
                    running = load_run_checkpoint(
                        db, run_id=target_run_id,
                        current_source_digest=target_row.source_digest,
                        current_skill_digest=target_row.skill_digest,
                    )
                    finish_checkpoint_batch(
                        db, checkpoint=running, expected_version=version,
                        patch=prior["patch"], evidence=prior["evidence"],
                        ledger_records={"records": prior["ledger_records"]},
                        allowed_source_files=set(json.loads(result.source_files_json)),
                    )
                    copied = db.scalar(select(AuditRunBatchResult).where(
                        AuditRunBatchResult.run_id == target_run_id,
                        AuditRunBatchResult.batch_key == result.batch_key,
                    ))
                    if copied is None:
                        raise CheckpointError("imported batch result was not persisted")
                    copied.derived_from_run_id = source_run_id
                    db.get(AuditRunCheckpoint, target_run_id, populate_existing=True).derived_from_run_id = source_run_id
                    db.flush()
            except CheckpointError:
                continue
            imported.add(prior["base_result_id"])
            progressed = True
            break
        if not progressed:
            return


def recover_checkpoint(db: Session, *, checkpoint: dict[str, Any], expected_version: int) -> int:
    if checkpoint["status"] != "RUNNING":
        raise CheckpointError("checkpoint is not running")
    active = set(checkpoint["active_batch_ids"])
    if not active:
        raise CheckpointError("no active batch to recover")
    ledger = copy.deepcopy(checkpoint["ledger"])
    for item in ledger:
        if item["check_id"] in active:
            item.update(transition_work_item(item, "BLOCKED"))
    return _store_state(db, checkpoint=checkpoint, expected_version=expected_version, ledger=ledger, active_batch_ids=[], status="INTERRUPTED")


def retry_checkpoint_batch(db: Session, *, checkpoint: dict[str, Any], expected_version: int, batch_ids: list[str]) -> int:
    if checkpoint["status"] != "INTERRUPTED":
        raise CheckpointError("checkpoint is not interrupted")
    plan = checkpoint["batch_plan"].get(str(checkpoint["pass_no"]), [])
    if batch_ids not in plan:
        raise CheckpointError("batch is not in the trusted checkpoint plan")
    ledger = copy.deepcopy(checkpoint["ledger"])
    by_id = {item["check_id"]: item for item in ledger}
    if any(by_id.get(check_id, {}).get("execution_status") != "BLOCKED" for check_id in batch_ids):
        raise CheckpointError("retry requires BLOCKED work items")
    for item in ledger:
        if item["check_id"] in batch_ids:
            item.update(transition_work_item(item, "PENDING"))
    return _store_state(db, checkpoint=checkpoint, expected_version=expected_version, ledger=ledger, active_batch_ids=[], status="READY")


def validate_repair_dependency_scope(checkpoint: dict[str, Any], batch_ids: list[str]) -> set[str]:
    """Return the source work items affected by a repair, rejecting an incomplete graph."""
    items = checkpoint.get("inventory", {}).get("expected_work_items", [])
    dependencies = {item["check_id"]: item["dependencies"] for item in items}
    try:
        affected = affected_nodes(dependencies, batch_ids)
    except (DependencyError, KeyError, TypeError) as exc:
        raise CheckpointError("repair dependency graph is incomplete or invalid") from exc
    return affected


def _dependent_result_check_ids(checkpoint: dict[str, Any], batch_ids: list[str], base_result_id: int) -> list[str]:
    affected = validate_repair_dependency_scope(checkpoint, batch_ids) - set(batch_ids)
    return sorted({
        check_id
        for result in checkpoint.get("batch_results", [])
        if result["base_result_id"] != base_result_id
        and affected.intersection(result["check_ids"])
        for check_id in result["check_ids"]
    })


def _append_reexecuted_batch_revision(
    db: Session, *, checkpoint: dict[str, Any], expected_version: int,
    patch: dict[str, Any],
    batch_key: str, patch_json: str, evidence_json: str, records_json: str, records_digest: str,
) -> int:
    effective = next((item for item in checkpoint["batch_results"] if item["pass_no"] == checkpoint["pass_no"] and item["check_ids"] == checkpoint["active_batch_ids"]), None)
    if effective is None or not effective.get("stale") or effective["revision_no"] >= 2:
        raise CheckpointError("existing batch result is not an invalidated rerun or its repair budget is exhausted")
    base = db.get(AuditRunBatchResult, effective["base_result_id"])
    gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == checkpoint["run_id"]).order_by(AuditRunStageGate.id.desc()).limit(1))
    try:
        receipt_raw = gate.receipt_json.encode("utf-8") if gate else b""
        receipt = strict_json_loads(receipt_raw, label="invalidation gate receipt")
    except (UnicodeError, StrictJsonError) as exc:
        raise CheckpointError("invalidated batch rerun has no valid blocking gate") from exc
    blockers = receipt.get("blocking_checks") if isinstance(receipt, dict) else None
    if (
        base is None or base.batch_key != batch_key or gate is None or gate.run_id != checkpoint["run_id"]
        or gate.pass_no != checkpoint["pass_no"] or gate.advanced
        or hashlib.sha256(receipt_raw).hexdigest() != gate.receipt_digest
        or not isinstance(receipt, dict) or receipt.get("controller_status") != "BLOCKED"
        or receipt.get("validator_exit_code") != 2 or receipt.get("run_id") != checkpoint["run_id"]
        or receipt.get("stage") != f"pass{checkpoint['pass_no']}"
        or receipt.get("source_digest") != checkpoint["source_digest"]
        or receipt.get("skill_digest") != checkpoint["skill_digest"]
        or not isinstance(blockers, list) or not blockers
        or any(not isinstance(value, str) or not value.strip() for value in blockers)
    ):
        raise CheckpointError("invalidated batch rerun is not bound to the current blocked stage gate")
    invalidated_check_ids = _dependent_result_check_ids(checkpoint, checkpoint["active_batch_ids"], base.id)
    ledger = apply_ledger_patch(checkpoint["ledger"], checkpoint["active_batch_ids"], patch)
    by_id = {item["check_id"]: item for item in ledger}
    for check_id in invalidated_check_ids:
        item = by_id[check_id]
        if item["execution_status"] == "DONE":
            invalidated = transition_work_item(transition_work_item(item, "INVALIDATED"), "PENDING")
            item.clear()
            item.update(invalidated)
        elif item["execution_status"] != "PENDING":
            raise CheckpointError("downstream batch is not in a rerunnable state")
    _validate_ledger(checkpoint["inventory"], ledger)
    invalidated_json = _json_bytes(invalidated_check_ids, "invalidated check IDs")
    fingerprint = hashlib.sha256(_json_bytes({
        "pass_no": checkpoint["pass_no"], "batch_key": batch_key,
        "blocking_checks": sorted(set(blockers)),
    }, "rerun blocker fingerprint").encode("utf-8")).hexdigest()
    with db.begin_nested():
        version = _store_state(
            db, checkpoint=checkpoint, expected_version=expected_version, ledger=ledger,
            active_batch_ids=[], status="READY",
        )
        db.add(AuditRunBatchRevision(
            run_id=checkpoint["run_id"], base_result_id=base.id, gate_id=gate.id,
            revision_no=effective["revision_no"] + 1, blocker_fingerprint=fingerprint,
            patch_json=patch_json, evidence_json=evidence_json, records_json=records_json,
            patch_digest=hashlib.sha256(patch_json.encode("utf-8")).hexdigest(),
            evidence_digest=hashlib.sha256(evidence_json.encode("utf-8")).hexdigest(),
            records_digest=records_digest, invalidated_check_ids_json=invalidated_json,
            invalidation_digest=hashlib.sha256(invalidated_json.encode("utf-8")).hexdigest(),
            checkpoint_version=version,
        ))
        db.flush()
        load_run_checkpoint(
            db, run_id=checkpoint["run_id"], current_source_digest=checkpoint["source_digest"],
            current_skill_digest=checkpoint["skill_digest"],
        )
        run = db.get(AuditRun, checkpoint["run_id"])
        record_mutation_invalidation(db, run.job_id, "checkpoint-worker", f"invalidated batch rerun accepted on audit run {run.id}")
    return version


def append_checkpoint_batch_revision(
    db: Session, *, checkpoint: dict[str, Any], expected_version: int, gate_id: int,
    batch_ids: list[str], patch: Any, evidence: Any, ledger_records: Any,
    current_source_digest: str, current_skill_digest: str,
) -> int:
    """Atomically replace one current-pass batch view while preserving every prior result."""
    current = load_run_checkpoint(db, run_id=checkpoint["run_id"], current_source_digest=current_source_digest, current_skill_digest=current_skill_digest)
    if current["version"] != expected_version or current != checkpoint:
        raise CheckpointConflict("checkpoint changed before repair")
    run = db.get(AuditRun, current["run_id"])
    latest_attempt = db.scalar(select(func.max(AuditRun.attempt)).where(AuditRun.job_id == run.job_id))
    if (
        run.attempt != latest_attempt
        or (run.job.status, run.status) not in {
            (JobStatus.PROCESSING, "RUNNING"), (JobStatus.WAITING_FOR_INFO, JobStatus.WAITING_FOR_INFO.value),
        }
        or current["status"] != "READY" or current["active_batch_ids"]
        or batch_ids not in current["batch_plan"][str(current["pass_no"])]
    ):
        raise CheckpointError("repair requires the latest paused or running checkpoint and an approved current-pass batch")
    pass_items = [item for item in current["ledger"] if item["pass_no"] == current["pass_no"]]
    if not pass_items or any(item["execution_status"] != "DONE" for item in pass_items):
        raise CheckpointError("repair requires the completed current pass")
    batch_result = next((item for item in current["batch_results"] if item["pass_no"] == current["pass_no"] and item["check_ids"] == batch_ids), None)
    if batch_result is None or batch_result["revision_no"] >= 2:
        raise CheckpointError("batch repair budget is exhausted or result is missing")
    validate_repair_dependency_scope(current, batch_ids)

    gate = db.get(AuditRunStageGate, gate_id)
    latest_gate_id = db.scalar(select(AuditRunStageGate.id).where(AuditRunStageGate.run_id == run.id).order_by(AuditRunStageGate.id.desc()).limit(1))
    try:
        receipt_raw = gate.receipt_json.encode("utf-8") if gate else b""
        receipt = strict_json_loads(receipt_raw, label="repair gate receipt")
    except (UnicodeError, StrictJsonError) as exc:
        raise CheckpointError("repair gate receipt is invalid") from exc
    blockers = receipt.get("blocking_checks") if isinstance(receipt, dict) else None
    if (
        gate is None or gate.id != latest_gate_id or gate.run_id != run.id or gate.pass_no != current["pass_no"]
        or gate.checkpoint_version != expected_version or gate.advanced
        or hashlib.sha256(receipt_raw).hexdigest() != gate.receipt_digest
        or not isinstance(receipt, dict) or receipt.get("controller_status") != "BLOCKED"
        or receipt.get("schema_version") != 1 or receipt.get("validator_exit_code") != 2
        or receipt.get("run_id") != run.id or receipt.get("stage") != f"pass{current['pass_no']}"
        or receipt.get("source_digest") != current["source_digest"] or receipt.get("skill_digest") != current["skill_digest"]
        or receipt.get("snapshot_id") != gate.snapshot_id
        or not isinstance(blockers, list) or not blockers or any(not isinstance(value, str) or not value.strip() for value in blockers)
    ):
        raise CheckpointError("repair requires the current verified BLOCKED gate")
    base = db.get(AuditRunBatchResult, batch_result["base_result_id"])
    source_files = set(json.loads(base.source_files_json))
    try:
        normalized_patch, normalized_evidence = validate_batch_payload(
            patch, evidence, assigned_check_ids=batch_ids, allowed_source_files=source_files,
        )
        other_records = [record for item in current["batch_results"] if item["base_result_id"] != base.id for record in item["ledger_records"]]
        normalized_records = validate_batch_ledger_records(
            ledger_records, inventory=current["inventory"], assigned_check_ids=batch_ids,
            pass_no=current["pass_no"], existing=other_records,
            batch_evidence_ids={item["evidence_id"] for item in normalized_evidence["evidence"]},
            allowed_source_files=source_files,
            contract_version=current["contract_version"],
        )
    except (BatchOutputError, TypeError, ValueError) as exc:
        raise CheckpointError("repair payload or evidence is invalid") from exc
    if (normalized_patch, normalized_evidence, normalized_records) == (batch_result["patch"], batch_result["evidence"], batch_result["ledger_records"]):
        raise CheckpointError("repair did not change the effective batch result")
    invalidated_check_ids = _dependent_result_check_ids(current, batch_ids, batch_result["base_result_id"])
    ledger = copy.deepcopy(current["ledger"])
    by_id = {item["check_id"]: item for item in ledger}
    for item in normalized_patch["items"]:
        by_id[item["check_id"]].update(
            result_status=item["result_status"], evidence_ids=item["evidence_ids"],
            applicability_reason=item.get("applicability_reason"),
        )
    for check_id in invalidated_check_ids:
        item = by_id[check_id]
        if item["execution_status"] == "DONE":
            invalidated = transition_work_item(transition_work_item(item, "INVALIDATED"), "PENDING")
            item.clear()
            item.update(invalidated)
    _validate_ledger(current["inventory"], ledger)
    patch_json = _json_bytes(normalized_patch, "repair patch")
    evidence_json = _json_bytes(normalized_evidence, "repair evidence")
    records_json, records_digest = encode_batch_ledger_manifest(normalized_records)
    invalidated_json = _json_bytes(invalidated_check_ids, "invalidated check IDs")
    ledger_json = _json_bytes(ledger, "repair ledger")
    _check_total_size(_json_bytes(current["inventory"], "inventory"), _json_bytes(current["batch_plan"], "batch_plan"), ledger_json, _json_bytes(current["gate_reports"], "gate reports"), "[]")
    fingerprint = hashlib.sha256(_json_bytes({
        "pass_no": current["pass_no"], "batch_key": base.batch_key,
        "blocking_checks": sorted(set(blockers)),
    }, "repair blocker fingerprint").encode("utf-8")).hexdigest()
    with db.begin_nested():
        db.add(AuditRunBatchRevision(
            run_id=run.id, base_result_id=base.id, gate_id=gate.id,
            revision_no=batch_result["revision_no"] + 1, blocker_fingerprint=fingerprint,
            patch_json=patch_json, evidence_json=evidence_json, records_json=records_json,
            patch_digest=hashlib.sha256(patch_json.encode("utf-8")).hexdigest(),
            evidence_digest=hashlib.sha256(evidence_json.encode("utf-8")).hexdigest(),
            records_digest=records_digest, invalidated_check_ids_json=invalidated_json,
            invalidation_digest=hashlib.sha256(invalidated_json.encode("utf-8")).hexdigest(),
            checkpoint_version=expected_version + 1,
        ))
        db.flush()
        updated = db.execute(update(AuditRunCheckpoint).where(
            AuditRunCheckpoint.run_id == run.id, AuditRunCheckpoint.version == expected_version,
            AuditRunCheckpoint.status == "READY",
        ).values(ledger_json=ledger_json, version=expected_version + 1, updated_at=utcnow()))
        if updated.rowcount != 1:
            raise CheckpointConflict("checkpoint changed during repair")
        load_run_checkpoint(db, run_id=run.id, current_source_digest=current_source_digest, current_skill_digest=current_skill_digest)
        record_mutation_invalidation(db, run.job_id, "checkpoint-worker", f"batch revision accepted on audit run {run.id}")
    return expected_version + 1


def _verified_gate_receipt(checkpoint: dict[str, Any], gate_result: ValidationResult) -> bytes:
    if not isinstance(gate_result, ValidationResult):
        raise CheckpointError("stage gate requires a trusted validator result")
    receipt = gate_result.receipt
    if (
        receipt.schema_version != 1
        or receipt.run_id != checkpoint["run_id"]
        or receipt.stage != f"pass{checkpoint['pass_no']}"
        or receipt.source_digest != checkpoint["source_digest"]
        or receipt.controller_status not in {"PASS", "BLOCKED", "ERROR"}
        or (receipt.controller_status != "ERROR" and receipt.skill_digest != checkpoint["skill_digest"])
    ):
        raise CheckpointError("stage gate receipt does not match this checkpoint")
    try:
        receipt_bytes = _read_regular_file(
            gate_result.receipt_path, max_bytes=64 * 1024,
            label="validator receipt", error_type=QualityAdapterError,
        )
        if strict_json_loads(receipt_bytes, label="gate receipt") != receipt.to_dict():
            raise CheckpointError("validator receipt file does not match its result")
    except (OSError, TypeError, StrictJsonError, QualityAdapterError) as exc:
        raise CheckpointError("validator receipt is unavailable or invalid") from exc
    return receipt_bytes


def record_checkpoint_gate(
    db: Session, *, checkpoint: dict[str, Any], gate_result: ValidationResult, advanced: bool,
) -> None:
    """Append one source-bound gate result in the caller's checkpoint transaction."""
    receipt_bytes = _verified_gate_receipt(checkpoint, gate_result)
    receipt = gate_result.receipt
    row = db.get(AuditRunCheckpoint, checkpoint["run_id"], populate_existing=True)
    expected_version = checkpoint["version"] + int(advanced)
    expected_pass = checkpoint["pass_no"] + int(advanced)
    if row is None or row.version != expected_version or row.pass_no != expected_pass or row.source_digest != checkpoint["source_digest"] or row.skill_digest != checkpoint["skill_digest"]:
        raise CheckpointConflict("checkpoint changed before gate history could be recorded")
    if advanced and (receipt.controller_status != "PASS" or receipt.validator_exit_code != 0):
        raise CheckpointError("only a passed gate can advance the checkpoint")
    db.add(AuditRunStageGate(
        run_id=checkpoint["run_id"], pass_no=checkpoint["pass_no"], checkpoint_version=checkpoint["version"],
        snapshot_id=receipt.snapshot_id, stage=receipt.stage, receipt_json=receipt_bytes.decode("utf-8"),
        error_code=gate_result.error_code, receipt_digest=hashlib.sha256(receipt_bytes).hexdigest(),
        advanced=advanced,
    ))


def advance_checkpoint_pass(
    db: Session,
    *,
    checkpoint: dict[str, Any],
    expected_version: int,
    next_pass: int,
    gate_result: ValidationResult,
) -> int:
    current_pass = checkpoint["pass_no"]
    if checkpoint["status"] != "READY" or checkpoint["active_batch_ids"]:
        raise CheckpointError("pass can advance only from an idle READY checkpoint")
    if next_pass != current_pass + 1 or next_pass not in _STAGES:
        raise CheckpointError("pass must advance one step from 1 to 2 or 2 to 3")
    completed = [item for item in checkpoint["ledger"] if item["pass_no"] == current_pass]
    if not completed or any(item["execution_status"] != "DONE" for item in completed):
        raise CheckpointError("current pass work items must be DONE before gate evaluation")
    if not isinstance(gate_result, ValidationResult):
        raise CheckpointError("pass advance requires a trusted validator result")
    receipt = gate_result.receipt
    if (
        receipt.run_id != checkpoint["run_id"]
        or receipt.stage != f"pass{current_pass}"
        or receipt.source_digest != checkpoint["source_digest"]
        or receipt.skill_digest != checkpoint["skill_digest"]
        or receipt.schema_version != 1
        or receipt.controller_status != "PASS"
        or receipt.validator_exit_code != 0
        or receipt.candidate_release_status != "METADATA_STAGE_COMPLETE"
        or receipt.blocking_checks
        or receipt.open_record_count != 0
    ):
        raise CheckpointError("trusted stage gate did not pass for this checkpoint")
    receipt_bytes = _verified_gate_receipt(checkpoint, gate_result)
    gate_digest = hashlib.sha256(receipt_bytes).hexdigest()
    gate_reports = dict(checkpoint["gate_reports"])
    if str(current_pass) in gate_reports:
        raise CheckpointError("current pass already has a gate receipt")
    gate_reports[str(current_pass)] = gate_digest
    row = db.get(AuditRunCheckpoint, checkpoint["run_id"], populate_existing=True)
    if row is None or row.contract_version != checkpoint["contract_version"] or row.version != expected_version or checkpoint["version"] != expected_version or row.pass_no != current_pass or row.status != "READY":
        raise CheckpointConflict("checkpoint changed; reload before advancing pass")
    try:
        persisted_inventory = validate_pass1_source_coverage(json.loads(row.inventory_json))
        persisted_ledger = json.loads(row.ledger_json)
        persisted_plan = json.loads(row.batch_plan_json)
        persisted_active = json.loads(row.active_batch_ids_json)
        persisted_gates = json.loads(row.gate_reports_json)
    except (TypeError, json.JSONDecodeError, CoverageInputError) as exc:
        raise CheckpointError("persisted checkpoint is invalid") from exc
    if (
        persisted_inventory != checkpoint["inventory"]
        or persisted_ledger != checkpoint["ledger"]
        or persisted_plan != checkpoint["batch_plan"]
        or persisted_active != checkpoint["active_batch_ids"]
        or persisted_gates != checkpoint["gate_reports"]
        or _validate_batch_results(db, run_id=checkpoint["run_id"], inventory=persisted_inventory, ledger=persisted_ledger, batch_plan=persisted_plan, contract_version=checkpoint["contract_version"]) != checkpoint.get("batch_results")
        or row.source_digest != checkpoint["source_digest"]
        or row.skill_digest != checkpoint["skill_digest"]
        or row.approval_id != checkpoint["approval_id"]
    ):
        raise CheckpointConflict("loaded checkpoint state is stale or modified")
    if str(current_pass) in persisted_gates:
        raise CheckpointError("current pass already has a gate receipt")
    gate_json = _json_bytes(gate_reports, "gate_reports")
    _check_total_size(row.inventory_json, row.batch_plan_json, row.ledger_json, gate_json, row.active_batch_ids_json)
    if any(item["execution_status"] != "DONE" for item in persisted_ledger if item["pass_no"] == current_pass):
        raise CheckpointError("current pass is no longer complete")
    result = db.execute(
        update(AuditRunCheckpoint)
        .where(AuditRunCheckpoint.run_id == checkpoint["run_id"], AuditRunCheckpoint.version == expected_version)
        .values(pass_no=next_pass, gate_reports_json=gate_json, version=expected_version + 1, updated_at=utcnow())
    )
    if result.rowcount != 1:
        raise CheckpointConflict("checkpoint version changed; reload before advancing pass")
    return expected_version + 1


__all__ = [
    "CheckpointConflict", "CheckpointError", "create_run_checkpoint", "load_run_checkpoint",
    "start_checkpoint_batch", "finish_checkpoint_batch", "recover_checkpoint",
    "retry_checkpoint_batch", "append_checkpoint_batch_revision",
    "validate_repair_dependency_scope",
    "advance_checkpoint_pass", "record_checkpoint_gate",
]
