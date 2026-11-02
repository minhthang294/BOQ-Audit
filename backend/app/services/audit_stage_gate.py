"""Materialize trusted checkpoint ledgers for the pinned v6.4 stage validator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.audit_batch import BatchOutputError
from app.services.audit_batch_ledgers import project_batch_ledger_records, verify_domain_references
from app.services.audit_lesson_selection import lesson_memory_case_id, lesson_memory_source
from app.services.audit_dependencies import DependencyError, affected_nodes


_LEDGER_FILES = {
    "page_ledger": "page_ledger",
    "detail_rows": "detail_rows",
    "summary_lineage": "summary_lineage",
    "calculation_ledger": "calculation_ledger",
    "assumption_ledger": "assumption_ledger",
    "regression_checks": "regression_checks",
    "physical_instance_ledger": "physical_instance_ledger",
    "pass2_review": "pass2_review",
    "pass3_review": "pass3_review",
    "issues": "issues",
    "source_observations": "source_observations",
    "boq_lineage": "boq_lineage",
}


class StageGateInputError(ValueError):
    """A stage snapshot cannot be bound to its approved source inventory."""

    code = "STAGE_SOURCE_BINDING"


class StageLedgerReferenceError(StageGateInputError):
    """Persisted ledger records declare domain references that do not resolve."""

    code = "STAGE_LEDGER_REFERENCE"


def _reviewed_source_scope(records: list[dict[str, Any]], record_type: str) -> tuple[set[str], set[str]]:
    reviews = [record for record in records if record["record_type"] == record_type]
    reviewed_pages = {
        page_id for record in reviews
        for page_id in record["payload"].get("page_ids", [])
        if page_id in record["source_refs"].get("page_ids", [])
    }
    reviewed_sheets = {
        sheet_id for record in reviews
        for sheet_id in record["payload"].get("sheet_ids", [])
        if sheet_id in record["source_refs"].get("sheet_ids", [])
    }
    return reviewed_pages, reviewed_sheets


def _review_covers_source(inventory: dict[str, Any], records: list[dict[str, Any]], record_type: str) -> bool:
    reviews = [record for record in records if record["record_type"] == record_type]
    if not reviews:
        return False
    reviewed_pages, reviewed_sheets = _reviewed_source_scope(records, record_type)
    source_covered = (
        reviewed_pages == {page["page_id"] for page in inventory["pages"]}
        and reviewed_sheets == {sheet["sheet_id"] for sheet in inventory.get("sheets", [])}
    )
    if not source_covered:
        return False
    lineage_ids = {record["record_id"] for record in records if record["record_type"] == "boq_lineage"}
    if not lineage_ids:
        return not inventory.get("estimate_line_groups")
    reviewed_lineages = {value for record in reviews for value in record["payload"].get("lineage_ids", [])}
    return reviewed_lineages == lineage_ids


def assess_pass3_closure(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Derive only closure claims supported by persisted, scope-bound records."""
    inventory = checkpoint["inventory"]
    records = checkpoint["ledger_records"]
    pass3_items = [item for item in checkpoint["ledger"] if item["pass_no"] == 3]
    reviews = [record for record in records if record["record_type"] == "pass3_review"]
    all_lineages = {record["record_id"] for record in records if record["record_type"] == "boq_lineage"}
    reviewed_lineages = {value for record in reviews for value in record["payload"].get("lineage_ids", [])}
    reviewed_pages, reviewed_sheets = _reviewed_source_scope(records, "pass3_review")
    pages = {page["page_id"] for page in inventory["pages"]}
    sheets = {sheet["sheet_id"] for sheet in inventory.get("sheets", [])}
    review_complete = (
        bool(pass3_items)
        and all(item["execution_status"] == "DONE" for item in pass3_items)
        and bool(reviews)
        and reviewed_pages == pages
        and reviewed_sheets == sheets
        and reviewed_lineages == all_lineages
    )

    targets = {target["target_id"]: target for target in inventory["targets"]}
    reviewed_targets = set()
    for target_id, target in targets.items():
        linked = [record for record in reviews if target_id in record["payload"].get("target_ids", []) and target_id in record["source_refs"].get("target_ids", [])]
        if linked and all(set(target.get(field, [])) <= {value for record in linked for value in record["payload"].get(field, []) if value in record["source_refs"].get(field, [])} for field in ("page_ids", "sheet_ids")):
            reviewed_targets.add(target_id)
    target_fit_complete = review_complete and targets.keys() <= reviewed_targets

    candidates = [record for record in records if record["record_type"] == "issue_candidates"]
    issues = [record for record in records if record["record_type"] == "issues"]
    issue_refs = {issue_id for record in reviews for issue_id in record["payload"].get("issue_ids", [])}
    issue_ids = {record["record_id"] for record in issues}
    dangling_issue_refs = issue_refs - issue_ids
    candidate_links: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        for candidate_id in issue["payload"].get("candidate_ids", []):
            candidate_links.setdefault(candidate_id, []).append(issue)
    candidate_ids = {record.get("record_id") for record in candidates}
    dangling_candidate_refs = set(candidate_links) - candidate_ids
    candidates_by_id = {record["record_id"]: record for record in candidates if isinstance(record.get("record_id"), str)}
    unresolved_candidates = []
    for candidate in candidates:
        linked_issues = candidate_links.get(candidate.get("record_id"), [])
        candidate_refs = candidate.get("source_refs", {})
        candidate_scope = any(candidate_refs.get(field, []) for field in ("page_ids", "sheet_ids", "target_ids"))
        if len(linked_issues) != 1 or not candidate_scope or any(
            not set(candidate_refs.get(field, [])) <= set(linked_issues[0]["source_refs"].get(field, []))
            for field in ("page_ids", "sheet_ids", "target_ids")
        ):
            unresolved_candidates.append(str(candidate.get("record_id", "không có ID")))
    unlinked_issues = []
    unrechecked_closures: dict[str, list[str]] = {}
    work_items = inventory.get("expected_work_items", [])
    work_by_id = {item["check_id"]: item for item in work_items}
    dependencies = {item["check_id"]: item["dependencies"] for item in work_items}

    def covered_scope(linked_reviews: list[dict[str, Any]]) -> dict[str, set[str]]:
        return {
            "page_ids": {value for review in linked_reviews for value in review["payload"].get("page_ids", []) if value in review["source_refs"].get("page_ids", [])},
            "sheet_ids": {value for review in linked_reviews for value in review["payload"].get("sheet_ids", []) if value in review["source_refs"].get("sheet_ids", [])},
            "target_ids": {value for review in linked_reviews for value in review["payload"].get("target_ids", []) if value in review["source_refs"].get("target_ids", [])},
        }

    for issue in issues:
        issue_id = issue["record_id"]
        final_status = issue["payload"].get("final_status")
        if final_status in {"FALSE_POSITIVE", "CLOSED_WITH_EXPLANATION"}:
            candidate_ids_for_issue = issue["payload"].get("candidate_ids", [])
            origin_candidates = [candidates_by_id.get(candidate_id) for candidate_id in candidate_ids_for_issue]
            origin_checks = {
                candidate["check_id"]
                for candidate in origin_candidates
                if candidate is not None and candidate.get("check_id") in work_by_id
            }
            unresolved_origin = any(candidate is None or candidate.get("check_id") not in work_by_id for candidate in origin_candidates)
            if not candidate_ids_for_issue and issue.get("check_id") in work_by_id:
                origin_checks.add(issue["check_id"])
            try:
                downstream = affected_nodes(dependencies, sorted(origin_checks)) - origin_checks if origin_checks and not unresolved_origin else set()
            except DependencyError:
                downstream = set()
                unresolved_origin = True
            expected_checks = {check_id for check_id in downstream if work_by_id[check_id]["pass_no"] == 3}
            linked_reviews = [
                review for review in reviews
                if issue_id in review["payload"].get("issue_ids", [])
            ]
            issue_refs = issue["source_refs"]
            issue_covered = covered_scope(linked_reviews)
            issue_rechecked = bool(linked_reviews) and any(issue_refs.values()) and all(
                set(issue_refs.get(field, [])) <= issue_covered[field] for field in issue_covered
            )
            reviewed_check_ids = set()
            for check_id in expected_checks:
                item = work_by_id[check_id]
                check_reviews = [review for review in linked_reviews if review.get("check_id") == check_id]
                covered = covered_scope(check_reviews)
                if all(set(item.get(field, [])) <= covered[field] for field in covered):
                    reviewed_check_ids.add(check_id)
            missing = expected_checks - reviewed_check_ids
            if not origin_checks or unresolved_origin or not issue_rechecked or missing:
                blockers = set(missing)
                if not origin_checks or unresolved_origin:
                    blockers.add("origin_check_unresolved")
                if not issue_rechecked:
                    blockers.add("issue_source_review_missing")
                unrechecked_closures[issue_id] = sorted(blockers)
            continue
        if final_status not in {"CONFIRMED_ERROR", "INCONSISTENT", "UNRESOLVED"} or (final_status in {"CONFIRMED_ERROR", "INCONSISTENT"} and not issue["payload"].get("evidence")):
            unlinked_issues.append(issue_id)
            continue
        linked = [review for review in reviews if issue_id in review["payload"].get("issue_ids", []) or review["payload"].get("target") == issue_id]
        issue_pages = set(issue["source_refs"].get("page_ids", []))
        issue_sheets = set(issue["source_refs"].get("sheet_ids", []))
        issue_targets = set(issue["source_refs"].get("target_ids", []))
        reviewed_issue_pages = {page_id for review in linked for page_id in review["payload"].get("page_ids", []) if page_id in review["source_refs"].get("page_ids", [])}
        reviewed_issue_sheets = {sheet_id for review in linked for sheet_id in review["payload"].get("sheet_ids", []) if sheet_id in review["source_refs"].get("sheet_ids", [])}
        reviewed_issue_targets = {target_id for review in linked for target_id in review["payload"].get("target_ids", []) if target_id in review["source_refs"].get("target_ids", [])}
        if not linked or not (issue_pages or issue_sheets) or not issue_pages <= reviewed_issue_pages or not issue_sheets <= reviewed_issue_sheets or not issue_targets <= reviewed_issue_targets:
            unlinked_issues.append(issue_id)
    lineages = [record for record in records if record["record_type"] == "boq_lineage"]
    invalid_unmatched = [
        lineage["record_id"] for lineage in lineages
        if lineage["payload"].get("reconciliation_status") == "UNMATCHED"
        and not any(
            lineage["record_id"] in issue["payload"].get("lineage_ids", [])
            and issue["payload"].get("final_status") in {"UNRESOLVED", "CONFIRMED_ERROR", "INCONSISTENT"}
            for issue in issues
        )
    ]
    mapped_line_ids = {line_id for lineage in lineages for line_id in lineage["payload"].get("estimate_line_ids", [])}
    missing_line_groups = [
        group["line_group_id"] for group in inventory.get("estimate_line_groups", [])
        if not set(group["line_ids"]) <= mapped_line_ids
    ]
    unlinked_issues.extend(f"lineage:{value}" for value in invalid_unmatched)
    unlinked_issues.extend(f"line-group:{value}" for value in missing_line_groups)
    propagation_complete = review_complete and not unresolved_candidates and not dangling_candidate_refs and not unrechecked_closures and not unlinked_issues and not dangling_issue_refs
    gaps = []
    if not review_complete:
        gaps.append("Pass 3 chưa có review đủ mọi trang, sheet và work item.")
    if missing := sorted(targets.keys() - reviewed_targets):
        gaps.append("Target chưa có review Pass 3: " + ", ".join(target_id[:100] for target_id in missing[:20]))
    if unresolved_candidates:
        gaps.append("Ứng viên lỗi chưa gắn đúng một issue cuối phủ nguồn: " + ", ".join(candidate_id[:100] for candidate_id in unresolved_candidates[:20]))
    if dangling_candidate_refs:
        gaps.append("Issue trỏ ứng viên lỗi không tồn tại: " + ", ".join(sorted(dangling_candidate_refs)[:20]))
    if unrechecked_closures:
        details = [f"{issue_id} → {', '.join(checks[:10])}" for issue_id, checks in sorted(unrechecked_closures.items())]
        gaps.append("False positive/issue đã đóng thiếu review Pass 3 gắn issue và phủ đủ downstream theo dependency graph: " + "; ".join(details[:20]))
    if unlinked_issues:
        gaps.append("Issue chưa có review Pass 3 phủ nguồn: " + ", ".join(unlinked_issues[:20]))
    if dangling_issue_refs:
        gaps.append("Review Pass 3 trỏ issue không tồn tại: " + ", ".join(sorted(dangling_issue_refs)[:20]))
    if invalid_unmatched:
        gaps.append("BOQ lineage UNMATCHED chưa gắn issue đang mở: " + ", ".join(invalid_unmatched[:20]))
    if missing_line_groups:
        gaps.append("Estimate line group chưa có lineage: " + ", ".join(missing_line_groups[:20]))
    snapshot = checkpoint.get("lesson_snapshot")
    snapshot_missing = snapshot is None
    snapshot = snapshot or {}
    selected_lessons = [item for item in snapshot.get("dispositions", []) if item.get("disposition") == "SELECTED"]
    regression_records = {record["record_id"]: record for record in records if record["record_type"] == "regression_checks"}
    missing_memory_regressions = []
    for lesson in selected_lessons:
        case_id = lesson_memory_case_id(lesson["lesson_id"], lesson["version"])
        regression = regression_records.get(case_id)
        related_reviews = [review for review in reviews if case_id in review["payload"].get("case_ids", [])]
        expected_targets = set(lesson.get("matched_target_ids", []))
        regression_ok = bool(
            regression
            and regression["payload"].get("status") == "CHECKED"
            and regression["payload"].get("memory_source") == lesson_memory_source(lesson)
            and expected_targets <= set(regression["payload"].get("target_ids", []))
        )
        reviewed_targets = {target for review in related_reviews for target in review["payload"].get("target_ids", [])}
        if not regression_ok or not related_reviews or not expected_targets <= reviewed_targets:
            missing_memory_regressions.append(case_id)
    memory_regression_complete = not snapshot_missing and not missing_memory_regressions
    if snapshot_missing:
        gaps.append("Checkpoint thiếu lesson selection snapshot có digest.")
    if missing_memory_regressions:
        gaps.append("Lesson đã chọn chưa có regression Pass 1 và kiểm lại Pass 3 theo target: " + ", ".join(missing_memory_regressions[:20]))
    return {
        "status": "METADATA_COMPLETE" if target_fit_complete and propagation_complete and memory_regression_complete else "REVIEW_REQUIRED",
        "review_complete": review_complete,
        "false_positive_propagation_complete": propagation_complete,
        "target_fit_check_complete": target_fit_complete,
        "memory_regression_complete": memory_regression_complete,
        "controller_contract_blocked": bool(unresolved_candidates or dangling_candidate_refs or unrechecked_closures or unlinked_issues or dangling_issue_refs or snapshot_missing or missing_memory_regressions),
        "gaps": gaps,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_checkpoint_stage_inputs(
    work_dir: Path,
    *,
    checkpoint: dict[str, Any],
    source_files: dict[str, Path],
) -> None:
    """Write validated persisted ledgers and only controller-derivable metadata."""
    work_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    contract_version = checkpoint.get("contract_version", 3)
    projection = project_batch_ledger_records(checkpoint["ledger_records"], contract_version=contract_version)
    try:
        verify_domain_references(projection, contract_version=contract_version)
    except BatchOutputError as exc:
        raise StageLedgerReferenceError(str(exc)) from exc
    inventory = checkpoint["inventory"]
    stage = f"pass{checkpoint['pass_no']}"
    document_inventory = []
    for document in inventory["documents"]:
        source_name = {"pdf": "source/input.pdf", "xlsx": "source/estimate.xlsx"}.get(document["document_id"])
        source_path = source_files.get(source_name) if source_name else None
        pages = [page for page in inventory["pages"] if page["document_id"] == document["document_id"]]
        sheets = [sheet for sheet in inventory.get("sheets", []) if sheet["document_id"] == document["document_id"]]
        if source_name is None or source_path is None or source_path.is_symlink() or not source_path.is_file():
            raise StageGateInputError("approved inventory document has no trusted stage source")
        try:
            with source_path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
        except OSError as exc:
            raise StageGateInputError("approved inventory source cannot be read") from exc
        if document.get("revision_digest") != f"sha256:{digest}":
            raise StageGateInputError("stage source digest differs from approved inventory")
        is_pdf = document["document_id"] == "pdf"
        document_inventory.append({
            "doc_id": document["document_id"],
            "filename": source_name,
            "page_count" if is_pdf else "sheet_count": len(pages) if is_pdf else len(sheets),
            "sha256": digest,
        })

    detail_rows = projection["detail_rows"]
    run_metadata = projection["run_metadata"]
    references_read = sorted({value for record in run_metadata for value in record["references_read"]})
    memory_cases_consulted = sorted({value for record in run_metadata for value in record["memory_cases_consulted"]})
    completed_flags = {check["flag"] for record in run_metadata for check in record["completed_checks"]}
    sections = {str(row.get("section_id")) for row in detail_rows if row.get("section_id") is not None}
    checked_sections = {
        section for section in sections
        if all(row.get("sequence_checked") is True for row in detail_rows if str(row.get("section_id")) == section)
    }
    discrete_units = {"cái", "bộ", "biển", "lỗ", "cọc", "gối", "khe", "thanh", "chiếc"}
    discrete_rows = [row for row in detail_rows if str(row.get("unit", "")).strip().lower() in discrete_units]
    pass_items = [item for item in checkpoint["ledger"] if item["pass_no"] == checkpoint["pass_no"]]
    completed_passes = {
        pass_no for pass_no in (1, 2, 3)
        if (items := [item for item in checkpoint["ledger"] if item["pass_no"] == pass_no])
        and all(item["execution_status"] == "DONE" for item in items)
    }
    closure = assess_pass3_closure(checkpoint) if checkpoint["pass_no"] == 3 else None
    pass2_complete = (
        2 in completed_passes
        and _review_covers_source(inventory, checkpoint["ledger_records"], "pass2_review")
    )
    manifest = {
        "protocol_version": "v6.4",
        "execution_contract": "checkpoint-v1",
        "contract_version": contract_version,
        "stage": stage,
        "document_inventory": document_inventory,
        "scope_pages_total": len(inventory["pages"]),
        "detail_rows_total": len(detail_rows),
        "summary_rows_total": len(projection["summary_lineage"]),
        "source_observations_total": len(projection.get("source_observations", [])),
        "boq_lineage_total": len(projection.get("boq_lineage", [])),
        "estimate_line_group_count": len(inventory.get("estimate_line_groups", [])),
        "sequence_sections_total": len(sections),
        "sequence_sections_checked": len(checked_sections),
        "discrete_rows_total": len(discrete_rows),
        "discrete_rows_checked": sum(row.get("integer_sanity_checked") is True for row in discrete_rows),
        "inventory_verified": True,
        "tables_inventory_verified": "tables_inventory_verified" in completed_flags,
        "references_read": references_read,
        "memory_lookup": {"status": "UNAVAILABLE", "reason": "Controller memory lookup is not integrated."},
        "memory_cases_consulted": memory_cases_consulted,
        "pass1_complete": 1 in completed_passes,
        "pass2_complete": pass2_complete,
        "pass3_complete": 3 in completed_passes and bool(closure and closure["review_complete"]),
        "summary_reconciliation_complete": "summary_reconciliation_complete" in completed_flags,
        "drawing_boq_orphan_check_complete": "drawing_boq_orphan_check_complete" in completed_flags,
        "paired_geometry_checks_complete": "paired_geometry_checks_complete" in completed_flags,
        "assumption_ledger_complete": "assumption_ledger_complete" in completed_flags,
        "reconciliation_labeled_complete": "reconciliation_labeled_complete" in completed_flags,
        "scope_overlap_check_complete": "scope_overlap_check_complete" in completed_flags,
        "paired_side_instance_check_complete": "paired_side_instance_check_complete" in completed_flags,
        # shortcut: closure records lack downstream-reference and recheck fields; extend the issue contract before allowing them through.
        "false_positive_propagation_complete": closure["false_positive_propagation_complete"] if closure else False,
        "target_fit_check_complete": closure["target_fit_check_complete"] if closure else False,
        "language_qa_pass": False,
        "annotation_qa_pass": False,
    }
    for field in ("no_detail_tables_reason", "no_summary_tables_reason", "no_sequence_sections_reason", "no_discrete_rows_reason"):
        reasons = sorted({record[field].strip() for record in run_metadata if isinstance(record.get(field), str) and record[field].strip()})
        if reasons:
            manifest[field] = "; ".join(reasons)
    stage_checkpoint = {
        "stage": stage,
        "next_action": f"Complete {stage} validator blockers before advancing.",
        "pending_rechecks": [],
    }
    _write_json(work_dir / "run_manifest.json", manifest)
    _write_json(work_dir / "checkpoint.json", stage_checkpoint)
    for record_type, filename in _LEDGER_FILES.items():
        _write_json(work_dir / f"{filename}.json", projection.get(record_type, []))
    _write_json(work_dir / "annotation_qa.json", [])
