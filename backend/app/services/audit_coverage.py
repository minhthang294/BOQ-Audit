"""Metadata coverage against an explicitly declared, trusted work-item scope.

Coverage measures ledger execution only; it cannot prove source inventory completeness.
"""

import copy
import json
from collections import Counter
from typing import Any

from app.services.audit_lineage_v4 import estimate_line_group_id, estimate_line_id, validate_xlsx_cell_range


class CoverageInputError(ValueError):
    """The inventory or work-item ledger is inconsistent."""


_EXECUTION = {"PENDING", "RUNNING", "DONE", "BLOCKED", "INVALIDATED"}
_RESULT = {"NOT_CHECKED", "CHECKED", "UNRESOLVED", "NOT_APPLICABLE"}
_SCOPE_FIELDS = ("document_id", "revision_digest", "page_ids", "sheet_ids", "target_ids", "estimate_line_group_ids")


def validate_inventory(inventory: Any) -> dict[str, Any]:
    """Validate controller-owned inventory structure without claiming completeness."""
    if not isinstance(inventory, dict):
        raise CoverageInputError("inventory must be an object")

    try:
        json.dumps(inventory, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise CoverageInputError("inventory must contain JSON-compatible values") from exc

    validated = copy.deepcopy(inventory)

    documents = _unique_records(validated.get("documents"), "document_id", "documents")
    pages = _unique_records(validated.get("pages"), "page_id", "pages")
    sheets = _unique_records(validated.get("sheets", []), "sheet_id", "sheets")
    targets = _unique_records(validated.get("targets", []), "target_id", "targets")
    lines = _unique_records(validated.get("estimate_lines", []), "line_id", "estimate_lines")
    line_groups = _unique_records(validated.get("estimate_line_groups", []), "line_group_id", "estimate_line_groups")
    expected = _unique_records(validated.get("expected_work_items"), "check_id", "expected_work_items")

    for document in documents.values():
        if not isinstance(document.get("revision_digest"), str) or not document["revision_digest"].strip():
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

    for sheet in sheets.values():
        document = document_ref(sheet)
        if sheet.get("revision_digest") != document["revision_digest"]:
            raise CoverageInputError(f"sheet revision mismatch: {sheet['sheet_id']}")
        sheet_index = sheet.get("sheet_index")
        if isinstance(sheet_index, bool) or not isinstance(sheet_index, int) or sheet_index < 1:
            raise CoverageInputError(f"invalid sheet_index: {sheet['sheet_id']}")
        sheet_name = sheet.get("sheet_name")
        if not isinstance(sheet_name, str) or not sheet_name.strip():
            raise CoverageInputError(f"sheet requires sheet_name: {sheet['sheet_id']}")

    for target in targets.values():
        document = document_ref(target)
        if target.get("revision_digest") != document["revision_digest"]:
            raise CoverageInputError(f"target revision mismatch: {target['target_id']}")
        target_pages = target.get("page_ids", [])
        target_sheets = target.get("sheet_ids", [])
        if "page_ids" in target and not isinstance(target_pages, list):
            raise CoverageInputError(f"target page_ids must be a list: {target['target_id']}")
        if "sheet_ids" in target and not isinstance(target_sheets, list):
            raise CoverageInputError(f"target sheet_ids must be a list: {target['target_id']}")
        if not target_pages and not target_sheets:
            raise CoverageInputError(f"target requires page_ids or sheet_ids: {target['target_id']}")
        if target_pages and (not all(isinstance(value, str) and value.strip() for value in target_pages) or len(set(target_pages)) != len(target_pages)):
            raise CoverageInputError(f"target page_ids must be unique non-empty strings: {target['target_id']}")
        if target_sheets and (not all(isinstance(value, str) and value.strip() for value in target_sheets) or len(set(target_sheets)) != len(target_sheets)):
            raise CoverageInputError(f"target sheet_ids must be unique non-empty strings: {target['target_id']}")
        if any(value not in pages or pages[value]["document_id"] != target["document_id"] for value in target_pages):
            raise CoverageInputError(f"unknown or cross-document target page: {target['target_id']}")
        if any(value not in sheets or sheets[value]["document_id"] != target["document_id"] for value in target_sheets):
            raise CoverageInputError(f"unknown or cross-document target sheet: {target['target_id']}")

    for line in lines.values():
        document = document_ref(line)
        sheet = sheets.get(line.get("sheet_id"))
        cell_range = line.get("cell_range")
        try:
            validate_xlsx_cell_range(cell_range)
        except ValueError:
            raise CoverageInputError(f"estimate line range is invalid: {line['line_id']}") from None
        if (
            line.get("revision_digest") != document["revision_digest"] or sheet is None
            or sheet["document_id"] != line["document_id"] or sheet["revision_digest"] != line["revision_digest"]
            or line.get("line_id") != estimate_line_id(line["document_id"], line["revision_digest"], line["sheet_id"], cell_range)
        ):
            raise CoverageInputError(f"estimate line is malformed or not source-bound: {line['line_id']}")

    for group in line_groups.values():
        members = group.get("line_ids")
        if not isinstance(members, list) or not members or len(set(members)) != len(members):
            raise CoverageInputError(f"estimate line group requires unique line IDs: {group['line_group_id']}")
        member_lines = [lines.get(line_id) for line_id in members]
        if any(line is None for line in member_lines):
            raise CoverageInputError(f"estimate line group refers to an unknown line: {group['line_group_id']}")
        if any((line["document_id"], line["revision_digest"], line["sheet_id"]) != (group.get("document_id"), group.get("revision_digest"), group.get("sheet_id")) for line in member_lines):
            raise CoverageInputError(f"estimate line group mixes source scope: {group['line_group_id']}")
        if group.get("line_group_id") != estimate_line_group_id(members):
            raise CoverageInputError(f"estimate line group ID is not controller-derived: {group['line_group_id']}")

    for check_id, item in expected.items():
        if {"execution_status", "result_status", "evidence_ids", "applicability_reason"} & item.keys():
            raise CoverageInputError(f"expected work item contains runtime state: {check_id}")
        document = document_ref(item)
        if item.get("revision_digest") != document["revision_digest"]:
            raise CoverageInputError(f"work item revision mismatch: {check_id}")

        page_ids = item.get("page_ids", [])
        sheet_ids = item.get("sheet_ids", [])
        target_ids = item.get("target_ids", [])
        group_ids = item.get("estimate_line_group_ids", [])
        if "page_ids" in item and not isinstance(page_ids, list):
            raise CoverageInputError(f"invalid page scope: {check_id}")
        if "sheet_ids" in item and not isinstance(sheet_ids, list):
            raise CoverageInputError(f"invalid sheet scope: {check_id}")
        if "target_ids" in item and not isinstance(target_ids, list):
            raise CoverageInputError(f"invalid target scope: {check_id}")
        if not isinstance(group_ids, list) or len(set(group_ids)) != len(group_ids):
            raise CoverageInputError(f"invalid estimate line group scope: {check_id}")
        if not page_ids and not sheet_ids:
            raise CoverageInputError(f"invalid page or sheet scope: {check_id}")
        if page_ids and (not all(isinstance(value, str) and value.strip() for value in page_ids) or len(set(page_ids)) != len(page_ids)):
            raise CoverageInputError(f"invalid page scope: {check_id}")
        if sheet_ids and (not all(isinstance(value, str) and value.strip() for value in sheet_ids) or len(set(sheet_ids)) != len(sheet_ids)):
            raise CoverageInputError(f"invalid sheet scope: {check_id}")
        if target_ids and (not all(isinstance(value, str) and value.strip() for value in target_ids) or len(set(target_ids)) != len(target_ids)):
            raise CoverageInputError(f"invalid target scope: {check_id}")

        check_kind = item.get("check_kind")
        if not isinstance(check_kind, str) or not check_kind.strip():
            raise CoverageInputError(f"invalid check_kind: {check_id}")

        pass_no = item.get("pass_no")
        if isinstance(pass_no, bool) or not isinstance(pass_no, int) or pass_no not in {1, 2, 3}:
            raise CoverageInputError(f"invalid pass_no: {check_id}")

        if "dependencies" not in item:
            raise CoverageInputError(f"missing dependencies: {check_id}")
        dependencies = item.get("dependencies")
        if not isinstance(dependencies, list):
            raise CoverageInputError(f"dependencies must be a list: {check_id}")
        seen_dependencies: set[str] = set()
        for dependency in dependencies:
            if not isinstance(dependency, str) or not dependency.strip():
                raise CoverageInputError(f"invalid dependency: {check_id}")
            if dependency in seen_dependencies:
                raise CoverageInputError(f"duplicate dependency: {check_id}: {dependency}")
            seen_dependencies.add(dependency)
            if dependency not in expected:
                raise CoverageInputError(f"unknown dependency: {check_id}: {dependency}")
            dependency_pass_no = expected[dependency].get("pass_no")
            if isinstance(dependency_pass_no, bool) or not isinstance(dependency_pass_no, int) or dependency_pass_no > pass_no:
                raise CoverageInputError(f"dependency pass order: {check_id}: {dependency}")

        for page_id in page_ids:
            page = pages.get(page_id)
            if page is None or page["document_id"] != item["document_id"] or page["revision_digest"] != item["revision_digest"]:
                raise CoverageInputError(f"unknown or mismatched page: {page_id!r}")

        for sheet_id in sheet_ids:
            sheet = sheets.get(sheet_id)
            if sheet is None or sheet["document_id"] != item["document_id"] or sheet["revision_digest"] != item["revision_digest"]:
                raise CoverageInputError(f"unknown or mismatched sheet: {sheet_id!r}")

        for target_id in target_ids:
            target = targets.get(target_id)
            if target is None or target["document_id"] != item["document_id"] or target["revision_digest"] != item["revision_digest"]:
                raise CoverageInputError(f"unknown or mismatched target: {target_id!r}")
            if not set(target.get("page_ids", [])) <= set(page_ids):
                raise CoverageInputError(f"target outside work-item pages: {target_id!r}")
            if not set(target.get("sheet_ids", [])) <= set(sheet_ids):
                raise CoverageInputError(f"target outside work-item sheets: {target_id!r}")
        for group_id in group_ids:
            group = line_groups.get(group_id)
            if group is None or (group["document_id"], group["revision_digest"]) != (item["document_id"], item["revision_digest"]) or group["sheet_id"] not in sheet_ids:
                raise CoverageInputError(f"unknown or out-of-scope estimate line group: {group_id!r}")

    prior_pass_sources: dict[tuple[int, str, str], dict[tuple[str, str], set[str]]] = {}
    for parent_id, parent in expected.items():
        key = (parent["pass_no"], parent["document_id"], parent["revision_digest"])
        source_index = prior_pass_sources.setdefault(key, {})
        for source_id in parent.get("page_ids", []):
            source_index.setdefault(("page", source_id), set()).add(parent_id)
        for source_id in parent.get("sheet_ids", []):
            source_index.setdefault(("sheet", source_id), set()).add(parent_id)

    for check_id, item in expected.items():
        pass_no = item["pass_no"]
        if pass_no == 1:
            continue
        scope = {("page", source_id) for source_id in item.get("page_ids", [])} | {
            ("sheet", source_id) for source_id in item.get("sheet_ids", [])
        }
        source_index = prior_pass_sources.get((pass_no - 1, item["document_id"], item["revision_digest"]), {})
        missing_sources = scope - source_index.keys()
        if missing_sources:
            raise CoverageInputError(f"pass dependency does not cover source scope: {check_id}")
        required_dependencies = set().union(*(source_index[source_id] for source_id in scope))
        if not required_dependencies <= set(item["dependencies"]):
            raise CoverageInputError(f"missing prior-pass dependency: {check_id}")

    for group_id in line_groups:
        for pass_no in (1, 2, 3):
            if not any(item["pass_no"] == pass_no and group_id in item.get("estimate_line_group_ids", []) for item in expected.values()):
                raise CoverageInputError(f"estimate line group is not assigned in pass {pass_no}: {group_id}")

    return validated


def validate_pass1_source_coverage(inventory: Any) -> dict[str, Any]:
    """Require the approved Pass 1 plan to include every declared source unit and target."""
    validated = validate_inventory(inventory)
    pass1 = [item for item in validated["expected_work_items"] if item["pass_no"] == 1]
    pages = {value for item in pass1 for value in item.get("page_ids", [])}
    sheets = {value for item in pass1 for value in item.get("sheet_ids", [])}
    targets = {value for item in pass1 for value in item.get("target_ids", [])}
    missing_pages = sorted({item["page_id"] for item in validated["pages"]} - pages)
    missing_sheets = sorted({item["sheet_id"] for item in validated.get("sheets", [])} - sheets)
    missing_targets = sorted({item["target_id"] for item in validated.get("targets", [])} - targets)
    line_groups = {item["line_group_id"] for item in validated.get("estimate_line_groups", [])}
    covered_groups = {value for item in pass1 for value in item.get("estimate_line_group_ids", [])}
    missing_groups = sorted(line_groups - covered_groups)
    if missing_pages or missing_sheets or missing_targets or missing_groups:
        raise CoverageInputError(
            "Pass 1 work items do not cover every source page, sheet, target, and estimate line group: "
            f"pages={missing_pages[:20]}, sheets={missing_sheets[:20]}, targets={missing_targets[:20]}, line_groups={missing_groups[:20]}"
        )
    return validated


def calculate_coverage(inventory: dict[str, Any], work_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare expected inventory work items with submitted execution records."""
    if not isinstance(inventory, dict) or not isinstance(work_items, list):
        raise CoverageInputError("inventory and work_items must be objects/lists")

    documents = _unique_records(inventory.get("documents"), "document_id", "documents")
    pages = _unique_records(inventory.get("pages"), "page_id", "pages")
    sheets = _unique_records(inventory.get("sheets", []), "sheet_id", "sheets")
    targets = _unique_records(inventory.get("targets", []), "target_id", "targets")
    line_groups = _unique_records(inventory.get("estimate_line_groups", []), "line_group_id", "estimate_line_groups")
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

    for sheet in sheets.values():
        document = document_ref(sheet)
        if sheet.get("revision_digest") != document["revision_digest"]:
            raise CoverageInputError(f"sheet revision mismatch: {sheet['sheet_id']}")
        sheet_index = sheet.get("sheet_index")
        if isinstance(sheet_index, bool) or not isinstance(sheet_index, int) or sheet_index < 1:
            raise CoverageInputError(f"invalid sheet_index: {sheet['sheet_id']}")
        sheet_name = sheet.get("sheet_name")
        if not isinstance(sheet_name, str) or not sheet_name.strip():
            raise CoverageInputError(f"sheet requires sheet_name: {sheet['sheet_id']}")

    for target in targets.values():
        document = document_ref(target)
        if target.get("revision_digest") != document["revision_digest"]:
            raise CoverageInputError(f"target revision mismatch: {target['target_id']}")
        target_pages = target.get("page_ids", [])
        target_sheets = target.get("sheet_ids", [])
        if "page_ids" in target and not isinstance(target_pages, list):
            raise CoverageInputError(f"target page_ids must be a list: {target['target_id']}")
        if "sheet_ids" in target and not isinstance(target_sheets, list):
            raise CoverageInputError(f"target sheet_ids must be a list: {target['target_id']}")
        if not target_pages and not target_sheets:
            raise CoverageInputError(f"target requires page_ids or sheet_ids: {target['target_id']}")
        if target_pages and (not all(isinstance(value, str) and value.strip() for value in target_pages) or len(set(target_pages)) != len(target_pages)):
            raise CoverageInputError(f"target page_ids must be unique non-empty strings: {target['target_id']}")
        if target_sheets and (not all(isinstance(value, str) and value.strip() for value in target_sheets) or len(set(target_sheets)) != len(target_sheets)):
            raise CoverageInputError(f"target sheet_ids must be unique non-empty strings: {target['target_id']}")
        if any(value not in pages or pages[value]["document_id"] != target["document_id"] for value in target_pages):
            raise CoverageInputError(f"unknown or cross-document target page: {target['target_id']}")
        if any(value not in sheets or sheets[value]["document_id"] != target["document_id"] for value in target_sheets):
            raise CoverageInputError(f"unknown or cross-document target sheet: {target['target_id']}")

    for check_id, item in expected.items():
        document = document_ref(item)
        if item.get("revision_digest") != document["revision_digest"]:
            raise CoverageInputError(f"work item revision mismatch: {check_id}")

        page_ids = item.get("page_ids", [])
        sheet_ids = item.get("sheet_ids", [])
        target_ids = item.get("target_ids", [])
        group_ids = item.get("estimate_line_group_ids", [])
        if "page_ids" in item and not isinstance(page_ids, list):
            raise CoverageInputError(f"invalid page scope: {check_id}")
        if "sheet_ids" in item and not isinstance(sheet_ids, list):
            raise CoverageInputError(f"invalid sheet scope: {check_id}")
        if "target_ids" in item and not isinstance(target_ids, list):
            raise CoverageInputError(f"invalid target scope: {check_id}")
        if not isinstance(group_ids, list) or not all(isinstance(value, str) and value.strip() for value in group_ids) or len(set(group_ids)) != len(group_ids):
            raise CoverageInputError(f"invalid estimate line group scope: {check_id}")
        if not page_ids and not sheet_ids:
            raise CoverageInputError(f"invalid page or sheet scope: {check_id}")
        if page_ids and (not all(isinstance(value, str) and value.strip() for value in page_ids) or len(set(page_ids)) != len(page_ids)):
            raise CoverageInputError(f"invalid page scope: {check_id}")
        if sheet_ids and (not all(isinstance(value, str) and value.strip() for value in sheet_ids) or len(set(sheet_ids)) != len(sheet_ids)):
            raise CoverageInputError(f"invalid sheet scope: {check_id}")
        if not all(isinstance(value, str) for value in target_ids):
            raise CoverageInputError(f"invalid target scope: {check_id}")

        for page_id in page_ids:
            page = pages.get(page_id)
            if page is None or page["document_id"] != item["document_id"] or page["revision_digest"] != item["revision_digest"]:
                raise CoverageInputError(f"unknown or mismatched page: {page_id!r}")

        for sheet_id in sheet_ids:
            sheet = sheets.get(sheet_id)
            if sheet is None or sheet["document_id"] != item["document_id"] or sheet["revision_digest"] != item["revision_digest"]:
                raise CoverageInputError(f"unknown or mismatched sheet: {sheet_id!r}")

        for target_id in target_ids:
            target = targets.get(target_id)
            if target is None or target["document_id"] != item["document_id"] or target["revision_digest"] != item["revision_digest"]:
                raise CoverageInputError(f"unknown or mismatched target: {target_id!r}")
            if not set(target.get("page_ids", [])) <= set(page_ids):
                raise CoverageInputError(f"target outside work-item pages: {target_id!r}")
            if not set(target.get("sheet_ids", [])) <= set(sheet_ids):
                raise CoverageInputError(f"target outside work-item sheets: {target_id!r}")
        for group_id in group_ids:
            group = line_groups.get(group_id)
            if group is None or group["document_id"] != item["document_id"] or group["revision_digest"] != item["revision_digest"] or group["sheet_id"] not in sheet_ids:
                raise CoverageInputError(f"unknown or out-of-scope estimate line group: {group_id!r}")

    unknown = sorted(set(actual) - set(expected))
    if unknown:
        raise CoverageInputError(f"unknown work-item IDs: {unknown}")
    for check_id, item in actual.items():
        if any(item.get(key, []) != expected[check_id].get(key, []) for key in _SCOPE_FIELDS):
            raise CoverageInputError(f"work-item scope changed: {check_id}")

    page_checks: dict[str, list[str]] = {page_id: [] for page_id in pages}
    sheet_checks: dict[str, list[str]] = {sheet_id: [] for sheet_id in sheets}
    target_checks: dict[str, list[str]] = {target_id: [] for target_id in targets}
    line_group_checks: dict[str, list[str]] = {group_id: [] for group_id in line_groups}
    for check_id, item in expected.items():
        for page_id in item.get("page_ids", []):
            page_checks[page_id].append(check_id)
        for sheet_id in item.get("sheet_ids", []):
            sheet_checks[sheet_id].append(check_id)
        for target_id in item.get("target_ids", []):
            target_checks[target_id].append(check_id)
        for group_id in item.get("estimate_line_group_ids", []):
            if group_id not in line_group_checks:
                raise CoverageInputError(f"unknown estimate line group: {group_id}")
            line_group_checks[group_id].append(check_id)

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
    covered_sheets = sum(bool(ids) and all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids) for ids in sheet_checks.values())
    covered_targets = sum(bool(ids) and all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids) for ids in target_checks.values())
    missing_pages = sorted(page_id for page_id, ids in page_checks.items() if not ids or not all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids))
    missing_sheets = sorted(sheet_id for sheet_id, ids in sheet_checks.items() if not ids or not all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids))
    missing_targets = sorted(target_id for target_id, ids in target_checks.items() if not ids or not all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids))
    missing_line_groups = sorted(group_id for group_id, ids in line_group_checks.items() if not ids or not all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids))
    total = len(expected)
    incomplete = bool(missing_checks or missing_pages or missing_sheets or missing_targets or missing_line_groups or not_checked)
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
        "sheet_coverage": covered_sheets / len(sheets) if sheets else None,
        "target_coverage": covered_targets / len(targets) if targets else None,
        "estimate_line_group_coverage": sum(bool(ids) and all(actual.get(check_id, {}).get("execution_status") == "DONE" and actual.get(check_id, {}).get("result_status") in {"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"} for check_id in ids) for ids in line_group_checks.values()) / len(line_group_checks) if line_group_checks else None,
        "coverage_status": "N/A" if not total and not pages and not sheets and not targets else ("BLOCKED" if blocked else ("INCOMPLETE" if incomplete else ("COVERED_WITH_UNRESOLVED" if unresolved else "COMPLETE"))),
        "missing_check_ids": missing_checks,
        "missing_page_ids": missing_pages,
        "missing_sheet_ids": missing_sheets,
        "missing_target_ids": missing_targets,
        "missing_estimate_line_group_ids": missing_line_groups,
        "work_item_status_counts": dict(sorted(counts.items())),
        "inventory_limitations": ["Coverage is limited to declared pages, targets, sheets, and expected work items; it does not verify that drawing object inventory is complete."],
    }


def _unique_records(records: Any, id_key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise CoverageInputError(f"{label} must be a list")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get(id_key), str) or not record[id_key].strip():
            raise CoverageInputError(f"{label} entries require {id_key}")
        record_id = record.get(id_key)
        if record_id in result:
            raise CoverageInputError(f"duplicate {id_key}: {record_id}")
        result[record_id] = record
    return result
