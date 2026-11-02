"""Bind report inputs and admin counts to one validated checkpoint snapshot."""

import hashlib
import json
from collections import Counter
from typing import Any

from app.services.audit_batch_ledgers import project_batch_ledger_records, verify_domain_references


def pdf_mark_required_issue_ids(source: dict[str, Any], page_ids: set[str] | None = None) -> set[str]:
    active = {row["issue_id"] for row in source["projection"]["issues"] if row["final_status"] in {"CONFIRMED_ERROR", "INCONSISTENT", "UNRESOLVED"}}
    return {
        record["record_id"] for record in source["ledger_records"]
        if record["record_type"] == "issues" and record["record_id"] in active
        and any(page_ids is None or page_id in page_ids for page_id in record.get("source_refs", {}).get("page_ids", []))
    }


def build_report_source(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic export input; callers must load/verify the checkpoint first."""
    material = {key: checkpoint[key] for key in (
        "run_id", "approval_id", "source_digest", "skill_digest", "inventory_digest",
        "batch_plan_digest", "version", "pass_no", "gate_reports", "ledger", "batch_results", "ledger_records",
    )}
    material.setdefault("contract_version", checkpoint.get("contract_version", 3))
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    detached_records = json.loads(canonical)["ledger_records"]
    projection = project_batch_ledger_records(detached_records, contract_version=material["contract_version"])
    verify_domain_references(projection, contract_version=material["contract_version"])
    statuses = Counter(row["final_status"] for row in projection["issues"])
    source = {
        "snapshot_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "checkpoint_version": material["version"],
        "contract_version": material["contract_version"],
        "source_digest": material["source_digest"],
        "projection": projection,
        "ledger_records": detached_records,
        "ledger_record_counts": {kind: len(rows) for kind, rows in projection.items()},
        "issue_final_status_counts": dict(sorted(statuses.items())),
        "issue_candidate_count": len(projection["issue_candidates"]),
    }
    source["pdf_mark_required_issue_count"] = len(pdf_mark_required_issue_ids(source))
    return source


def build_classification_state(source: dict[str, Any], revisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Bind latest reviewer classifications to the exact ledger source digest."""
    issue_statuses = {row["issue_id"]: row["final_status"] for row in source["projection"]["issues"]}
    current: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, int]] = set()
    for row in revisions:
        issue_id, revision = row["issue_id"], row["revision_no"]
        if (
            issue_id not in issue_statuses or row["final_status"] != issue_statuses[issue_id]
            or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1
            or (issue_id, revision) in seen
            or (row["final_status"] in {"FALSE_POSITIVE", "CLOSED_WITH_EXPLANATION"}) != (row["severity"] is None)
            or (row["severity"] is not None and row["severity"] not in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "NOTE"})
            or not isinstance(row["reviewer_user_id"], int) or isinstance(row["reviewer_user_id"], bool) or row["reviewer_user_id"] < 1
            or not isinstance(row["reason"], str) or len(row["reason"].strip()) < 12
        ):
            raise ValueError("issue classification does not match the report source")
        seen.add((issue_id, revision))
        if issue_id not in current or revision > current[issue_id]["revision_no"]:
            current[issue_id] = row
    effective = [current[key] for key in sorted(current)]
    canonical = json.dumps({"snapshot_digest": source["snapshot_digest"], "classifications": effective}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "classification_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "decisions": current,
        "classified_issue_count": len(current),
        "unclassified_issue_count": len(issue_statuses) - len(current),
    }
