"""Metadata coverage against an explicitly declared, trusted work-item scope.

Coverage measures ledger execution only; it cannot prove source inventory completeness.
"""

from collections import Counter
from typing import Any


class CoverageInputError(ValueError):
    """The inventory or work-item ledger is inconsistent."""


_EXECUTION = {"PENDING", "RUNNING", "DONE", "BLOCKED", "INVALIDATED"}
_RESULT = {"NOT_CHECKED", "CHECKED", "UNRESOLVED", "NOT_APPLICABLE"}
_SCOPE_FIELDS = ("document_id", "revision_digest", "page_ids", "target_ids")


def calculate_coverage(inventory: dict[str, Any], work_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare expected inventory work items with submitted execution records."""
    if not isinstance(inventory, dict) or not isinstance(work_items, list):
        raise CoverageInputError("inventory and work_items must be objects/lists")

    documents = _unique_records(inventory.get("documents"), "document_id", "documents")
    pages = _unique_records(inventory.get("pages"), "page_id", "pages")
    targets = _unique_records(inventory.get("targets", []), "target_id", "targets")
    expected = _unique_records(inventory.get("expected_work_items"), "check_id", "expected_work_items")
    actual = _unique_records(work_items, "check_id", "work_items")

    for document in documents.values():
        if not isinstance(document.get("revision_digest"), str) or not document["revision_digest"]:
            raise CoverageInputError(f"document requires revision_digest: {document['document_id']}")

    def document_ref(record: dict[str, Any]) -> dict[str, Any]:
        document_id = record.get("document_id")
        if not isinstance(document_id, str) or document_id not in documents:
            raise CoverageInputError(f"unknown document_id: {document_id!r}")
        return documents[document_id]

    for page in pages.values():
        document = document_ref(page)
        if page.get("revision_digest") != document["revision_digest"]:
            raise CoverageInputError(f"page revision mismatch: {page['page_id']}")
        if isinstance(page.get("pdf_page"), bool) or not isinstance(page.get("pdf_page"), int) or page["pdf_page"] < 1:
            raise CoverageInputError(f"invalid pdf_page: {page['page_id']}")

    for target in targets.values():
        document = document_ref(target)
        if target.get("revision_digest") != document["revision_digest"]:
            raise CoverageInputError(f"target revision mismatch: {target['target_id']}")
        target_pages = target.get("page_ids")
        if not isinstance(target_pages, list) or not target_pages or not all(isinstance(value, str) for value in target_pages):
            raise CoverageInputError(f"target requires page_ids: {target['target_id']}")
        if any(value not in pages or pages[value]["document_id"] != target["document_id"] for value in target_pages):
            raise CoverageInputError(f"unknown or cross-document target page: {target['target_id']}")

    for check_id, item in expected.items():
        document = document_ref(item)
        if item.get("revision_digest") != document["revision_digest"]:
            raise CoverageInputError(f"work item revision mismatch: {check_id}")
        page_ids, target_ids = item.get("page_ids"), item.get("target_ids", [])
        if not isinstance(page_ids, list) or not page_ids or not all(isinstance(value, str) for value in page_ids):
            raise CoverageInputError(f"invalid page scope: {check_id}")
        if not isinstance(target_ids, list) or not all(isinstance(value, str) for value in target_ids):
            raise CoverageInputError(f"invalid target scope: {check_id}")
        for page_id in page_ids:
            page = pages.get(page_id)
            if page is None or page["document_id"] != item["document_id"] or page["revision_digest"] != item["revision_digest"]:
                raise CoverageInputError(f"unknown or mismatched page: {page_id!r}")
        for target_id in target_ids:
            target = targets.get(target_id)
            if target is None or target["document_id"] != item["document_id"] or target["revision_digest"] != item["revision_digest"]:
                raise CoverageInputError(f"unknown or mismatched target: {target_id!r}")
            if not set(target["page_ids"]) <= set(page_ids):
                raise CoverageInputError(f"target outside work-item pages: {target_id!r}")

    unknown = sorted(set(actual) - set(expected))
    if unknown:
        raise CoverageInputError(f"unknown work-item IDs: {unknown}")
    for check_id, item in actual.items():
        if any(item.get(key) != expected[check_id].get(key) for key in _SCOPE_FIELDS):
            raise CoverageInputError(f"work-item scope changed: {check_id}")

    page_checks: dict[str, list[str]] = {page_id: [] for page_id in pages}
    target_checks: dict[str, list[str]] = {target_id: [] for target_id in targets}
    for check_id, item in expected.items():
        for page_id in item["page_ids"]:
            page_checks[page_id].append(check_id)
        for target_id in item.get("target_ids", []):
            target_checks[target_id].append(check_id)

    missing_checks = sorted(set(expected) - set(actual))
    counts: Counter[str] = Counter()
    checked = unresolved = not_applicable = blocked = not_checked = 0
    for check_id, scope in expected.items():
        item = actual.get(check_id)
        if item is None:
            not_checked += 1
            continue
        execution, result = item.get("execution_status"), item.get("result_status")
        if not isinstance(execution, str) or execution not in _EXECUTION or not isinstance(result, str) or result not in _RESULT:
            raise CoverageInputError(f"invalid status: {check_id}")
        evidence_ids = item.get("evidence_ids")
        if result in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} and (not isinstance(evidence_ids, list) or not evidence_ids or not all(isinstance(value, str) and value.strip() for value in evidence_ids)):
            raise CoverageInputError(f"checked result requires evidence: {check_id}")
        if result == "NOT_APPLICABLE" and (not isinstance(item.get("applicability_reason"), str) or not item["applicability_reason"].strip()):
            raise CoverageInputError(f"NOT_APPLICABLE requires reason: {check_id}")
        counts[f"{execution}:{result}"] += 1
        if execution == "BLOCKED":
            blocked += 1
        if execution == "DONE" and result in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"}:
            checked += 1
            unresolved += result == "UNRESOLVED"
            not_applicable += result == "NOT_APPLICABLE"
        else:
            not_checked += 1

    covered_pages = sum(bool(ids) and all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids) for ids in page_checks.values())
    covered_targets = sum(bool(ids) and all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids) for ids in target_checks.values())
    missing_pages = sorted(page_id for page_id, ids in page_checks.items() if not ids or not all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids))
    missing_targets = sorted(target_id for target_id, ids in target_checks.items() if not ids or not all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids))
    total = len(expected)
    incomplete = bool(missing_checks or missing_pages or missing_targets or not_checked)
    return {
        "scope_count": total,
        "work_item_count": len(actual),
        "checked_count": checked,
        "unresolved_count": unresolved,
        "not_checked_count": not_checked,
        "not_applicable_count": not_applicable,
        "blocked_count": blocked,
        "coverage": checked / total if total else None,
        "page_coverage": covered_pages / len(pages) if pages else None,
        "target_coverage": covered_targets / len(targets) if targets else None,
        "coverage_status": "N/A" if not total and not pages and not targets else ("BLOCKED" if blocked else ("INCOMPLETE" if incomplete else ("COVERED_WITH_UNRESOLVED" if unresolved else "COMPLETE"))),
        "missing_check_ids": missing_checks,
        "missing_page_ids": missing_pages,
        "missing_target_ids": missing_targets,
        "work_item_status_counts": dict(sorted(counts.items())),
        "inventory_limitations": ["Coverage is limited to declared pages, targets, and expected work items; it does not verify that drawing object inventory is complete."],
    }


def _unique_records(records: Any, id_key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise CoverageInputError(f"{label} must be a list")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get(id_key), str) or not record[id_key].strip():
            raise CoverageInputError(f"{label} entries require {id_key}")
        record_id = record[id_key]
        if record_id in result:
            raise CoverageInputError(f"duplicate {id_key}: {record_id}")
        result[record_id] = record
    return result
