"""Validate one model-authored batch result before it reaches checkpoint state."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.audit_coverage import CoverageInputError, validate_pass1_source_coverage
from app.services.audit_quality import QualityAdapterError, strict_json_loads
from app.services.audit_lesson_selection import LessonSelectionError, lesson_memory_case_id, lesson_memory_source, lessons_for_batch, lessons_for_pass3


class BatchOutputError(ValueError):
    """A batch output is malformed, stale, or outside the assigned scope."""


_PATCH_KEYS = {"check_id", "result_status", "evidence_ids", "applicability_reason"}
_EVIDENCE_KEYS = {"evidence_id", "check_id", "source_file", "location", "observation", "method"}
_RESULTS = {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"}
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024


def _lesson_context(
    checkpoint: dict[str, Any], batch_ids: list[str], *,
    pass3: bool = False, batch_target_ids: list[str] | None = None,
) -> dict[str, Any]:
    snapshot = checkpoint.get("lesson_snapshot")
    if not isinstance(snapshot, dict):
        raise BatchOutputError("checkpoint has no pinned lesson selection snapshot")
    try:
        lessons = lessons_for_pass3(snapshot, batch_ids, batch_target_ids or []) if pass3 else lessons_for_batch(snapshot, batch_ids)
    except LessonSelectionError as exc:
        raise BatchOutputError("checkpoint lesson selection is invalid") from exc
    return {"lesson_selection_digest": snapshot["selection_digest"], "lessons": lessons}


def build_pass1_payload(
    checkpoint: dict[str, Any],
    *,
    batch_ids: list[str],
    source_files: set[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build the first-pass batch from approved scope, without prior results."""
    if checkpoint.get("pass_no") != 1 or checkpoint.get("status") != "READY":
        raise BatchOutputError("Pass 1 payload requires a READY pass-1 checkpoint")
    if not batch_ids or any(not _nonempty_string(value) for value in batch_ids) or len(set(batch_ids)) != len(batch_ids):
        raise BatchOutputError("Pass 1 batch IDs must be non-empty and unique")
    if not source_files or any(not _safe_relative_path(value) for value in source_files):
        raise BatchOutputError("Pass 1 source files must be relative source names")
    plan = checkpoint.get("batch_plan")
    if not isinstance(plan, dict) or batch_ids not in plan.get("1", []):
        raise BatchOutputError("Pass 1 batch is not in the approved plan")
    try:
        inventory = validate_pass1_source_coverage(checkpoint.get("inventory"))
    except CoverageInputError as exc:
        raise BatchOutputError("approved inventory is invalid") from exc
    expected = {item["check_id"]: item for item in inventory["expected_work_items"]}
    if any(check_id not in expected or expected[check_id]["pass_no"] != 1 for check_id in batch_ids):
        raise BatchOutputError("Pass 1 batch does not match approved work items")
    ledger = checkpoint.get("ledger")
    if not isinstance(ledger, list):
        raise BatchOutputError("checkpoint ledger is missing")
    ledger_by_id = {item.get("check_id"): item for item in ledger if isinstance(item, dict)}
    if any(ledger_by_id.get(check_id, {}).get("execution_status") != "PENDING" for check_id in batch_ids):
        raise BatchOutputError("Pass 1 work items must still be PENDING")
    assigned = [expected[check_id] for check_id in batch_ids]
    revision_pairs = {(item["document_id"], item["revision_digest"]) for item in assigned}
    page_ids = {page_id for item in assigned for page_id in item.get("page_ids", [])}
    sheet_ids = {sheet_id for item in assigned for sheet_id in item.get("sheet_ids", [])}
    target_ids = {target_id for item in assigned for target_id in item["target_ids"]}
    line_group_ids = {group_id for item in assigned for group_id in item.get("estimate_line_group_ids", [])}
    line_groups = [item for item in inventory.get("estimate_line_groups", []) if item["line_group_id"] in line_group_ids]
    estimate_line_ids = {line_id for group in line_groups for line_id in group["line_ids"]}
    scoped_inventory = {
        "documents": [item for item in inventory["documents"] if (item["document_id"], item["revision_digest"]) in revision_pairs],
        "pages": [item for item in inventory["pages"] if item["page_id"] in page_ids],
        "sheets": [item for item in inventory["sheets"] if item["sheet_id"] in sheet_ids],
        "targets": [item for item in inventory["targets"] if item["target_id"] in target_ids],
        "estimate_lines": [item for item in inventory.get("estimate_lines", []) if item["line_id"] in estimate_line_ids],
        "estimate_line_groups": line_groups,
        "expected_work_items": [dict(item) for item in assigned],
    }
    scoped_ledger = {"items": [
        {**dict(item), "execution_status": "PENDING", "result_status": "NOT_CHECKED", "evidence_ids": [], "applicability_reason": None}
        for item in assigned
    ]}
    context = {
        "run_id": checkpoint.get("run_id"),
        "contract_version": checkpoint.get("contract_version", 3),
        "pass_no": 1,
        "check_ids": list(batch_ids),
        "source_files": sorted(source_files),
        "review_mode": "source-grounded-initial-review",
        "prior_pass_results_included": False,
        **_lesson_context(checkpoint, batch_ids),
    }
    return context, scoped_inventory, scoped_ledger


def load_batch_outputs(
    patch_path: Path,
    evidence_path: Path,
    *,
    ledger_records_path: Path | None = None,
    assigned_check_ids: list[str],
    allowed_source_files: set[str],
) -> tuple[dict[str, Any], dict[str, Any]] | tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Strictly bind batch output files to this exact invocation."""
    try:
        patch_raw = _read_batch_file(patch_path, "batch patch")
        evidence_raw = _read_batch_file(evidence_path, "batch evidence")
        patch = strict_json_loads(patch_raw, label="batch patch")
        evidence = strict_json_loads(evidence_raw, label="batch evidence")
        ledger_records = None
        if ledger_records_path is not None:
            ledger_records = strict_json_loads(_read_batch_file(ledger_records_path, "batch ledger records"), label="batch ledger records")
    except (OSError, TypeError, QualityAdapterError, BatchOutputError) as exc:
        raise BatchOutputError("batch outputs are unavailable or invalid JSON") from exc

    normalized_patch, normalized_evidence = validate_batch_payload(
        patch,
        evidence,
        assigned_check_ids=assigned_check_ids,
        allowed_source_files=allowed_source_files,
    )
    if ledger_records_path is None:
        return normalized_patch, normalized_evidence
    if not isinstance(ledger_records, dict) or set(ledger_records) != {"records"} or not isinstance(ledger_records["records"], list):
        raise BatchOutputError("ledger records must contain exactly a records array")
    return normalized_patch, normalized_evidence, ledger_records


def validate_batch_payload(
    patch: Any,
    evidence: Any,
    *,
    assigned_check_ids: list[str],
    allowed_source_files: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate decoded model output before persistence or ledger merge."""
    if not assigned_check_ids or any(not _nonempty_string(item) for item in assigned_check_ids) or len(set(assigned_check_ids)) != len(assigned_check_ids):
        raise BatchOutputError("assigned check IDs must be non-empty and unique")
    if not allowed_source_files or any(not _safe_relative_path(item) for item in allowed_source_files):
        raise BatchOutputError("allowed source files must be non-empty relative paths")
    if not isinstance(patch, dict) or set(patch) != {"items"} or not isinstance(patch["items"], list):
        raise BatchOutputError("patch must contain exactly an items array")
    if len(patch["items"]) != len(assigned_check_ids):
        raise BatchOutputError("patch must contain exactly one item per assigned check")
    referenced_evidence: dict[str, str] = {}
    normalized_items = []
    for expected_id, item in zip(assigned_check_ids, patch["items"], strict=True):
        if not isinstance(item, dict) or not {"check_id", "result_status", "evidence_ids"} <= set(item) or not set(item) <= _PATCH_KEYS:
            raise BatchOutputError("patch item has missing or unknown fields")
        if item["check_id"] != expected_id or not isinstance(item["result_status"], str) or item["result_status"] not in _RESULTS:
            raise BatchOutputError("patch item is outside the assigned order or has an invalid result")
        ids = item["evidence_ids"]
        if not isinstance(ids, list) or not ids or any(not _nonempty_string(value) for value in ids) or len(set(ids)) != len(ids):
            raise BatchOutputError("each patch item requires unique evidence IDs")
        reason = item.get("applicability_reason")
        if item["result_status"] == "NOT_APPLICABLE":
            if not _nonempty_string(reason):
                raise BatchOutputError("NOT_APPLICABLE requires a source-grounded reason")
        elif "applicability_reason" in item:
            raise BatchOutputError("applicability_reason is only valid for NOT_APPLICABLE")
        for evidence_id in ids:
            if evidence_id in referenced_evidence:
                raise BatchOutputError("evidence IDs must be unique within a batch")
            referenced_evidence[evidence_id] = expected_id
        normalized_items.append({key: value for key, value in item.items()})

    if not isinstance(evidence, dict) or set(evidence) != {"evidence"} or not isinstance(evidence["evidence"], list):
        raise BatchOutputError("evidence file must contain exactly an evidence array")
    if len(evidence["evidence"]) != len(referenced_evidence):
        raise BatchOutputError("evidence must contain exactly the referenced evidence IDs")
    seen: set[str] = set()
    for item in evidence["evidence"]:
        if not isinstance(item, dict) or set(item) != _EVIDENCE_KEYS:
            raise BatchOutputError("evidence item has missing or unknown fields")
        evidence_id = item.get("evidence_id")
        if not _nonempty_string(evidence_id) or evidence_id not in referenced_evidence or evidence_id in seen or item.get("check_id") != referenced_evidence.get(evidence_id):
            raise BatchOutputError("evidence IDs must bind one-to-one to the assigned check")
        if not _nonempty_string(item.get("source_file")) or item["source_file"] not in allowed_source_files or not _safe_relative_path(item["source_file"]):
            raise BatchOutputError("evidence source file is outside the assigned sources")
        if any(not _nonempty_string(item.get(field)) for field in ("location", "observation", "method")):
            raise BatchOutputError("evidence must include concrete location, observation, and method")
        seen.add(evidence_id)
    if seen != set(referenced_evidence):
        raise BatchOutputError("evidence IDs do not match the patch")
    return {"items": normalized_items}, evidence


def build_pass2_payload(
    checkpoint: dict[str, Any],
    *,
    batch_ids: list[str],
    source_files: set[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build Pass 2 inputs from approved scope without copying prior results."""
    if checkpoint.get("pass_no") != 2 or checkpoint.get("status") != "READY":
        raise BatchOutputError("Pass 2 payload requires a READY pass-2 checkpoint")
    if not batch_ids or any(not _nonempty_string(value) for value in batch_ids) or len(set(batch_ids)) != len(batch_ids):
        raise BatchOutputError("Pass 2 batch IDs must be non-empty and unique")
    if not source_files or any(not _safe_relative_path(value) for value in source_files):
        raise BatchOutputError("Pass 2 source files must be relative source names")
    plan = checkpoint.get("batch_plan")
    if not isinstance(plan, dict) or batch_ids not in plan.get("2", []):
        raise BatchOutputError("Pass 2 batch is not in the approved plan")
    try:
        inventory = validate_pass1_source_coverage(checkpoint.get("inventory"))
    except CoverageInputError as exc:
        raise BatchOutputError("approved inventory is invalid") from exc
    expected = {item.get("check_id"): item for item in inventory["expected_work_items"] if isinstance(item, dict)}
    if any(check_id not in expected or expected[check_id].get("pass_no") != 2 for check_id in batch_ids):
        raise BatchOutputError("Pass 2 batch does not match approved work items")
    ledger = checkpoint.get("ledger")
    if not isinstance(ledger, list):
        raise BatchOutputError("checkpoint ledger is missing")
    ledger_by_id = {item.get("check_id"): item for item in ledger if isinstance(item, dict)}
    if any(ledger_by_id.get(check_id, {}).get("execution_status") != "PENDING" for check_id in batch_ids):
        raise BatchOutputError("Pass 2 work items must still be PENDING")

    assigned = [expected[check_id] for check_id in batch_ids]
    revision_pairs = {(item["document_id"], item["revision_digest"]) for item in assigned}
    page_ids = {page_id for item in assigned for page_id in item.get("page_ids", [])}
    sheet_ids = {sheet_id for item in assigned for sheet_id in item.get("sheet_ids", [])}
    target_ids = {target_id for item in assigned for target_id in item["target_ids"]}
    line_group_ids = {group_id for item in assigned for group_id in item.get("estimate_line_group_ids", [])}
    line_groups = [item for item in inventory.get("estimate_line_groups", []) if item["line_group_id"] in line_group_ids]
    estimate_line_ids = {line_id for group in line_groups for line_id in group["line_ids"]}
    scoped_inventory = {
        "documents": [item for item in inventory["documents"] if (item["document_id"], item["revision_digest"]) in revision_pairs],
        "pages": [item for item in inventory["pages"] if item["page_id"] in page_ids],
        "sheets": [item for item in inventory["sheets"] if item["sheet_id"] in sheet_ids],
        "targets": [item for item in inventory["targets"] if item["target_id"] in target_ids],
        "estimate_lines": [item for item in inventory.get("estimate_lines", []) if item["line_id"] in estimate_line_ids],
        "estimate_line_groups": line_groups,
        "expected_work_items": [dict(item) for item in assigned],
    }
    ledger = {"items": [
        {
            **dict(item),
            "execution_status": "PENDING",
            "result_status": "NOT_CHECKED",
            "evidence_ids": [],
            "applicability_reason": None,
        }
        for item in assigned
    ]}
    context = {
        "run_id": checkpoint.get("run_id"),
        "contract_version": checkpoint.get("contract_version", 3),
        "pass_no": 2,
        "check_ids": list(batch_ids),
        "source_files": sorted(source_files),
        "review_mode": "fresh-source-recheck",
        "prior_pass_results_included": False,
        **_lesson_context(checkpoint, batch_ids),
    }
    if checkpoint.get("contract_version") == 4:
        dependencies = {dependency for item in assigned for dependency in item["dependencies"]}
        prior_line_ids = {
            line_id for group in line_groups for line_id in group["line_ids"]
        }
        context["prior_lineage_ids"] = sorted({
            record["record_id"]
            for record in checkpoint.get("ledger_records", [])
            if record.get("record_type") == "boq_lineage"
            and record.get("check_id") in dependencies
            and prior_line_ids.intersection(record.get("payload", {}).get("estimate_line_ids", []))
        })
    return context, scoped_inventory, ledger


def build_pass3_payload(
    checkpoint: dict[str, Any],
    *,
    batch_ids: list[str],
    source_files: set[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build Pass 3 inputs with only the explicitly depended-on prior results."""
    if checkpoint.get("pass_no") != 3 or checkpoint.get("status") != "READY":
        raise BatchOutputError("Pass 3 payload requires a READY pass-3 checkpoint")
    if not batch_ids or any(not _nonempty_string(value) for value in batch_ids) or len(set(batch_ids)) != len(batch_ids):
        raise BatchOutputError("Pass 3 batch IDs must be non-empty and unique")
    if not source_files or any(not _safe_relative_path(value) for value in source_files):
        raise BatchOutputError("Pass 3 source files must be relative source names")
    plan = checkpoint.get("batch_plan")
    if not isinstance(plan, dict) or batch_ids not in plan.get("3", []):
        raise BatchOutputError("Pass 3 batch is not in the approved plan")
    try:
        inventory = validate_pass1_source_coverage(checkpoint.get("inventory"))
    except CoverageInputError as exc:
        raise BatchOutputError("approved inventory is invalid") from exc
    expected = {item["check_id"]: item for item in inventory["expected_work_items"]}
    if any(check_id not in expected or expected[check_id]["pass_no"] != 3 for check_id in batch_ids):
        raise BatchOutputError("Pass 3 batch does not match approved work items")
    ledger = checkpoint.get("ledger")
    if not isinstance(ledger, list):
        raise BatchOutputError("checkpoint ledger is missing")
    ledger_by_id = {item.get("check_id"): item for item in ledger if isinstance(item, dict)}
    if any(ledger_by_id.get(check_id, {}).get("execution_status") != "PENDING" for check_id in batch_ids):
        raise BatchOutputError("Pass 3 work items must still be PENDING")

    assigned = [expected[check_id] for check_id in batch_ids]
    dependencies = {dependency for item in assigned for dependency in item["dependencies"]}
    prior_results = []
    for result in checkpoint.get("batch_results", []):
        if not isinstance(result, dict) or result.get("stale") or result.get("pass_no") not in {1, 2}:
            continue
        evidence_by_id = {
            item.get("evidence_id"): item
            for item in result.get("evidence", {}).get("evidence", [])
            if isinstance(item, dict)
        }
        for item in result.get("patch", {}).get("items", []):
            if not isinstance(item, dict) or item.get("check_id") not in dependencies:
                continue
            prior_results.append({
                "pass_no": result["pass_no"],
                "check_id": item["check_id"],
                "result_status": item["result_status"],
                "applicability_reason": item.get("applicability_reason"),
                "evidence": [evidence_by_id[evidence_id] for evidence_id in item["evidence_ids"] if evidence_id in evidence_by_id],
            })
    if dependencies and {item["check_id"] for item in prior_results} != dependencies:
        raise BatchOutputError("Pass 3 is missing durable results for approved dependencies")
    prior_ledger_records = [
        {**record, "pass_no": result["pass_no"]}
        for result in checkpoint.get("batch_results", [])
        if isinstance(result, dict) and not result.get("stale") and result.get("pass_no") in {1, 2}
        for record in result.get("ledger_records", [])
        if isinstance(record, dict) and record.get("check_id") in dependencies
    ]

    revision_pairs = {(item["document_id"], item["revision_digest"]) for item in assigned}
    page_ids = {page_id for item in assigned for page_id in item.get("page_ids", [])}
    sheet_ids = {sheet_id for item in assigned for sheet_id in item.get("sheet_ids", [])}
    target_ids = {target_id for item in assigned for target_id in item["target_ids"]}
    line_group_ids = {group_id for item in assigned for group_id in item.get("estimate_line_group_ids", [])}
    line_groups = [item for item in inventory.get("estimate_line_groups", []) if item["line_group_id"] in line_group_ids]
    estimate_line_ids = {line_id for group in line_groups for line_id in group["line_ids"]}
    scoped_inventory = {
        "documents": [item for item in inventory["documents"] if (item["document_id"], item["revision_digest"]) in revision_pairs],
        "pages": [item for item in inventory["pages"] if item["page_id"] in page_ids],
        "sheets": [item for item in inventory["sheets"] if item["sheet_id"] in sheet_ids],
        "targets": [item for item in inventory["targets"] if item["target_id"] in target_ids],
        "estimate_lines": [item for item in inventory.get("estimate_lines", []) if item["line_id"] in estimate_line_ids],
        "estimate_line_groups": line_groups,
        "expected_work_items": [dict(item) for item in assigned],
    }
    scope_ledger = {"items": [
        {**dict(item), "execution_status": "PENDING", "result_status": "NOT_CHECKED", "evidence_ids": [], "applicability_reason": None}
        for item in assigned
    ]}
    selected_lessons = lessons_for_pass3(checkpoint["lesson_snapshot"], batch_ids, sorted(target_ids))
    context = {
        "run_id": checkpoint.get("run_id"),
        "contract_version": checkpoint.get("contract_version", 3),
        "pass_no": 3,
        "check_ids": list(batch_ids),
        "source_files": sorted(source_files),
        "review_mode": "falsify-prior-source-grounded-results",
        "prior_pass_results_included": bool(prior_results),
        "prior_pass_results": prior_results,
        "prior_ledger_records": prior_ledger_records,
        **_lesson_context(checkpoint, batch_ids, pass3=True, batch_target_ids=sorted(target_ids)),
        "lesson_regression_cases": [{
            "case_id": lesson_memory_case_id(lesson["lesson_id"], lesson["version"]),
            "memory_source": lesson_memory_source(lesson),
            "target_ids": sorted(set(lesson.get("target_ids", [])) & target_ids),
            "required_checks": lesson.get("required_checks", []),
            "counterexamples": lesson.get("counterexamples", []),
        } for lesson in selected_lessons],
    }
    return context, scoped_inventory, scope_ledger


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_relative_path(value: Any) -> bool:
    if not _nonempty_string(value) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _read_batch_file(path: Path, label: str) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _MAX_OUTPUT_BYTES:
            raise BatchOutputError(f"{label} must be a bounded singly-linked regular file")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > _MAX_OUTPUT_BYTES:
                raise BatchOutputError(f"{label} exceeds the size limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if total != before.st_size or (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns):
            raise BatchOutputError(f"{label} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


__all__ = ["BatchOutputError", "build_pass1_payload", "build_pass2_payload", "build_pass3_payload", "load_batch_outputs", "validate_batch_payload"]
