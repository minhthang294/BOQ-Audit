"""Validate and project source-bound records returned by an audit batch."""

from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from typing import Any

from app.services.audit_batch import BatchOutputError
from app.services.audit_coverage import validate_inventory
from app.services.audit_lineage_v4 import (
    boq_lineage_id, calculation_input_id, source_observation_id, validate_anchor,
)

_CONTRACTS = {
    version: json.loads((Path(__file__).parents[1] / f"schemas/audit_batch_ledger_contract.v{version}.json").read_text(encoding="utf-8"))
    for version in (3, 4)
}
if any(contract.get("contract_version") != version or not isinstance(contract.get("record_types"), dict) for version, contract in _CONTRACTS.items()):
    raise RuntimeError("unsupported audit batch ledger contract")


def _contract(version: int) -> dict[str, Any]:
    try:
        return _CONTRACTS[version]
    except KeyError as exc:
        raise BatchOutputError("unsupported audit ledger contract version") from exc


_LEDGER_CONTRACT = _CONTRACTS[3]
_RECORD_TYPES = _LEDGER_CONTRACT["record_types"]
_ID_FIELDS = {kind: contract["id_field"] for kind, contract in _RECORD_TYPES.items()}
_PASS_TYPES = {pass_no: {kind for kind, item in _RECORD_TYPES.items() if pass_no in item["passes"]} for pass_no in (1, 2, 3)}
_REF_FIELDS = {"page_ids", "sheet_ids", "target_ids"}
_SINGULAR_REF_FIELDS = {"page_id": "page_ids", "sheet_id": "sheet_ids", "target_id": "target_ids"}
_MAX_BYTES = 8 * 1024 * 1024
_ENUMS = _LEDGER_CONTRACT["enums"]
_REVIEWED = set(_ENUMS["review_status"])
_ISSUE_FINAL = set(_ENUMS["issue_final_status"])
_DISCRETE_UNITS = {"cái", "bộ", "biển", "lỗ", "cọc", "gối", "khe", "thanh", "chiếc"}
_MANIFEST_FLAGS = set(_ENUMS["completed_check_flag"])
_DOMAIN_REFERENCES = {
    "summary_lineage": {"source_rows": "detail_rows"},
    "calculation_ledger": {"assumption_ids": "assumption_ledger"},
    "issues": {"calc_ids": "calculation_ledger", "candidate_ids": "issue_candidates"},
    "pass2_review": {"calc_id": "calculation_ledger", "case_ids": "regression_checks", "issue_ids": "issues"},
    "pass3_review": {"calc_id": "calculation_ledger", "case_ids": "regression_checks", "issue_ids": "issues"},
}


def _require_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BatchOutputError(f"{label} must be a non-empty string")


def _require_strings(value: Any, label: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, list) or (nonempty and not value) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise BatchOutputError(f"{label} must be a string array")


def _require_number_or_none(value: Any, label: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise BatchOutputError(f"{label} must be numeric or null")


def _matches_declared_type(value: Any, declared: str) -> bool:
    if declared == "any":
        return True
    if declared.endswith("[]"):
        item_type = declared[:-2]
        return isinstance(value, list) and all(_matches_declared_type(item, item_type) for item in value)
    return any({
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "object": lambda: isinstance(value, dict),
        "null": lambda: value is None,
    }.get(type_name, lambda: False)() for type_name in declared.split("|"))


def _validate_declared_field_types(kind: str, payload: dict[str, Any], record_types: dict[str, Any] = _RECORD_TYPES) -> None:
    for field, declared in record_types[kind].get("field_types", {}).items():
        if field in payload and not _matches_declared_type(payload[field], declared):
            raise BatchOutputError(f"{kind}.{field} does not match its declared JSON type")


def _validate_declared_field_enums(kind: str, payload: dict[str, Any], record_types: dict[str, Any] = _RECORD_TYPES, enums: dict[str, Any] = _ENUMS) -> None:
    for field, enum_name in record_types[kind].get("enum_fields", {}).items():
        if field in payload and payload[field] not in enums[enum_name]:
            raise BatchOutputError(f"{kind}.{field} is outside its declared enum")


def _validate_declared_nested_objects(kind: str, payload: dict[str, Any], record_types: dict[str, Any] = _RECORD_TYPES, enums: dict[str, Any] = _ENUMS) -> None:
    for array_field, schema in record_types[kind].get("nested_object_arrays", {}).items():
        if array_field not in payload:
            continue
        required = set(schema["required_fields"])
        for item in payload[array_field]:
            if not isinstance(item, dict) or set(item) != required:
                raise BatchOutputError(f"{kind}.{array_field} item has missing or unknown fields")
            for field, declared in schema["field_types"].items():
                if not _matches_declared_type(item[field], declared):
                    raise BatchOutputError(f"{kind}.{array_field}.{field} does not match its declared JSON type")
            for field, enum_name in schema.get("enum_fields", {}).items():
                if item[field] not in enums[enum_name]:
                    raise BatchOutputError(f"{kind}.{array_field}.{field} is outside its declared enum")


def _validate_declared_field_constraints(kind: str, payload: dict[str, Any], record_types: dict[str, Any] = _RECORD_TYPES) -> None:
    contract = record_types[kind]
    for field, prefix in contract.get("prefixes", {}).items():
        if field in payload and not payload[field].startswith(prefix):
            raise BatchOutputError(f"{kind}.{field} has an invalid prefix")
    for field, bounds in contract.get("numeric_bounds", {}).items():
        if field in payload and (
            payload[field] < bounds.get("minimum", payload[field])
            or payload[field] > bounds.get("maximum", payload[field])
        ):
            raise BatchOutputError(f"{kind}.{field} is outside its allowed range")
    for field, minimum in contract.get("min_items", {}).items():
        if field in payload and len(payload[field]) < minimum:
            raise BatchOutputError(f"{kind}.{field} has too few items")
    for field in contract.get("unique_items", []):
        if field in payload and len(set(payload[field])) != len(payload[field]):
            raise BatchOutputError(f"{kind}.{field} items must be unique")


def _validate_domain_payload(
    kind: str,
    payload: dict[str, Any],
    *,
    batch_evidence_ids: set[str],
    allowed_source_files: set[str],
    assigned: dict[str, Any],
    record_types: dict[str, Any],
    enums: dict[str, Any],
) -> None:
    reviewed = set(enums["review_status"])
    issue_final = set(enums["issue_final_status"])
    manifest_flags = set(enums["completed_check_flag"])
    required = record_types[kind]["required_fields"]
    if any(field not in payload for field in required):
        raise BatchOutputError(f"{kind} payload is missing required v6.4 fields")
    _validate_declared_field_types(kind, payload, record_types)
    _validate_declared_field_enums(kind, payload, record_types, enums)
    _validate_declared_nested_objects(kind, payload, record_types, enums)
    _validate_declared_field_constraints(kind, payload, record_types)

    for field in ("source_pdf", "source_anchor", "formula", "unit", "variable", "claim", "reason", "object_type", "target", "result", "method", "stage"):
        if field in payload:
            declared = record_types[kind].get("field_types", {}).get(field, "")
            if payload[field] is None and "null" in declared.split("|"):
                continue
            _require_text(payload[field], f"{kind}.{field}")
    for field in ("status", "spec_status", "qty_status"):
        if field in payload and (not isinstance(payload[field], str) or payload[field] not in reviewed):
            raise BatchOutputError(f"{kind}.{field} is not a recognized review status")
    for field in ("evidence", "evidence_ids"):
        if field in payload:
            _require_strings(payload[field], f"{kind}.{field}", nonempty=True)
            if not set(payload[field]) <= batch_evidence_ids:
                raise BatchOutputError(f"{kind}.{field} references evidence outside this batch")
    for field in ("page_ids", "sheet_ids", "target_ids", "case_ids", "issue_ids", "candidate_ids", "source_rows", "source_anchors", "evidence_anchors", "view_evidence", "lineage_ids"):
        if field in payload:
            _require_strings(payload[field], f"{kind}.{field}", nonempty=field in {"page_ids", "sheet_ids", "evidence_anchors", "view_evidence"})

    for field in ("reported_total", "audited_total", "delta", "reported_quantity", "raw_result", "rounded_result"):
        if field in payload: _require_number_or_none(payload[field], f"{kind}.{field}")
    for field in ("has_quantity_table", "integer_sanity_checked", "name_spec_match", "unit_match", "scope_match", "discrete_integer_sanity", "affects_qty"):
        declared = record_types[kind].get("field_types", {}).get(field, "")
        if field in payload and payload[field] is None and "null" in declared.split("|"):
            continue
        if field in payload and not isinstance(payload[field], bool):
            raise BatchOutputError(f"{kind}.{field} must be boolean")

    if kind == "page_ledger":
        if isinstance(payload["pdf_page"], bool) or not isinstance(payload["pdf_page"], int) or payload["pdf_page"] < 1:
            raise BatchOutputError("page_ledger.pdf_page must be a positive integer")
        if payload["source_pdf"] not in allowed_source_files:
            raise BatchOutputError("page_ledger.source_pdf is outside the batch source files")
        page_id = payload["page_id"]
        page = next((row for row in assigned.get("_approved_pages", []) if row["page_id"] == page_id), None)
        if page is not None and page["pdf_page"] != payload["pdf_page"]:
            raise BatchOutputError("page_ledger.pdf_page differs from approved inventory")
        if payload["status"] == "VERIFIED": _require_strings(payload.get("evidence_anchors"), "page_ledger.evidence_anchors", nonempty=True)
    elif kind == "detail_rows":
        if any(payload[field] not in reviewed for field in ("spec_status", "qty_status", "status")):
            raise BatchOutputError("detail_rows review layers must each have a v6.4 status")
        if str(payload.get("unit", "")).strip().lower() in _DISCRETE_UNITS and payload.get("integer_sanity_checked") is not True:
            raise BatchOutputError("discrete detail_rows require integer_sanity_checked")
    elif kind == "summary_lineage":
        _require_strings(payload["source_rows"], "summary_lineage.source_rows")
        if not payload["source_rows"]: _require_text(payload.get("zero_source_explanation"), "summary_lineage.zero_source_explanation")
        if payload["status"] == "VERIFIED" and any(payload.get(field) is not True for field in ("name_spec_match", "unit_match", "scope_match")):
            raise BatchOutputError("verified summary_lineage requires name/spec/unit/scope parity")
    elif kind == "calculation_ledger":
        if not payload["calc_id"].startswith("CALC-") or not payload["inputs"] or not isinstance(payload["inputs"], list) or any(not isinstance(item, dict) for item in payload["inputs"]):
            raise BatchOutputError("calculation_ledger requires a CALC ID and inputs")
        _require_strings(payload["source_anchors"], "calculation_ledger.source_anchors", nonempty=True)
        if payload["raw_result"] is None or isinstance(payload["raw_result"], bool) or not isinstance(payload["raw_result"], (int, float)):
            raise BatchOutputError("calculation_ledger.raw_result must be numeric")
        if payload["pass1_result"] is None or isinstance(payload["pass1_result"], bool) or not isinstance(payload["pass1_result"], (int, float, str)):
            raise BatchOutputError("calculation_ledger.pass1_result must be recorded")
        if not isinstance(payload["calculation_class"], str) or payload["calculation_class"] not in enums["calculation_class"]:
            raise BatchOutputError("calculation_ledger.calculation_class is invalid")
        if "source_observations" in record_types:
            for index, item in enumerate(payload["inputs"]):
                if item["input_id"] != calculation_input_id(payload["calc_id"], index, item["observation_id"]):
                    raise BatchOutputError("calculation input ID is not controller-derived")
    elif kind == "source_observations":
        anchor = payload["anchor"]
        try:
            validate_anchor(anchor, page_ids=set(assigned["_record_refs"]["page_ids"]), sheet_ids=set(assigned["_record_refs"]["sheet_ids"]))
        except (TypeError, ValueError, KeyError) as exc:
            raise BatchOutputError(str(exc)) from exc
        if (payload["document_id"], payload["revision_digest"]) != (assigned["document_id"], assigned["revision_digest"]):
            raise BatchOutputError("source observation document revision differs from approved scope")
        if payload["observation_id"] != source_observation_id(payload["document_id"], payload["revision_digest"], anchor):
            raise BatchOutputError("source observation ID is not controller-derived")
        if not isinstance(payload["observed_value"], (str, int, float)) or isinstance(payload["observed_value"], bool) or (isinstance(payload["observed_value"], float) and not math.isfinite(payload["observed_value"])):
            raise BatchOutputError("source observation value must be finite text or numeric data")
        if isinstance(payload["observed_value"], str) and not payload["observed_value"].strip():
            raise BatchOutputError("source observation value must not be empty")
        _require_text(payload["unit"], "source_observations.unit")
        _require_strings(payload["evidence_ids"], "source_observations.evidence_ids", nonempty=True)
        if not set(payload["evidence_ids"]) <= batch_evidence_ids:
            raise BatchOutputError("source observation evidence is outside this batch")
    elif kind == "boq_lineage":
        _require_text(payload["lineage_id"], "boq_lineage.lineage_id")
        if payload["lineage_id"] != boq_lineage_id(payload):
            raise BatchOutputError("BOQ lineage ID is not controller-derived")
        if payload["reconciliation_status"] == "MATCHED" and not all(payload[field] for field in ("estimate_line_ids", "detail_row_ids", "summary_row_ids", "calc_ids")):
            raise BatchOutputError("matched BOQ lineage requires estimate, detail, summary, and calculation IDs")
        if payload["reconciliation_status"] == "NOT_APPLICABLE" and payload["estimate_line_ids"]:
            raise BatchOutputError("not-applicable BOQ lineage cannot map estimate lines")
        if payload["reconciliation_status"] == "UNMATCHED" and not payload["estimate_line_ids"]:
            raise BatchOutputError("unmatched BOQ lineage requires at least one estimate line")
        _require_text(payload["reason"], "boq_lineage.reason")
        _require_strings(payload["evidence_ids"], "boq_lineage.evidence_ids", nonempty=True)
        if not set(payload["evidence_ids"]) <= batch_evidence_ids:
            raise BatchOutputError("BOQ lineage evidence is outside this batch")
        approved_line_ids = set(assigned["_approved_line_ids"])
        if not set(payload["estimate_line_ids"]) <= approved_line_ids:
            raise BatchOutputError("BOQ lineage refers outside the approved estimate line scope")
    elif kind == "assumption_ledger":
        if not isinstance(payload["evidence_class"], str) or payload["evidence_class"] not in enums["evidence_class"]:
            raise BatchOutputError("assumption_ledger.evidence_class is invalid")
        if payload.get("affects_qty") is True and payload["evidence_class"] in {"USER_CLAIM", "MODEL_ASSUMPTION"} and not payload.get("closure_evidence"):
            raise BatchOutputError("quantity-affecting unverified assumptions require closure evidence")
    elif kind == "regression_checks":
        if not isinstance(payload["status"], str) or payload["status"] not in enums["regression_status"]:
            raise BatchOutputError("regression_checks.status is invalid")
        if payload["status"] in {"CHECKED", "UNRESOLVED"}:
            _require_strings(payload.get("target_ids"), "regression_checks.target_ids", nonempty=True)
        if payload["case_id"].startswith("MEM-"):
            _require_text(payload.get("memory_source"), "regression_checks.memory_source")
    elif kind == "physical_instance_ledger":
        _require_strings(payload["view_evidence"], "physical_instance_ledger.view_evidence", nonempty=True)
    elif kind in {"pass2_review", "pass3_review"}:
        _require_text(payload.get("calc_id"), f"{kind}.calc_id") if payload.get("calc_id") is not None else None
        _require_strings(payload["evidence"], f"{kind}.evidence", nonempty=True)
        if not (payload.get("page_ids") or payload.get("sheet_ids")):
            raise BatchOutputError(f"{kind} requires page_ids or sheet_ids")
        if kind == "pass3_review" and payload.get("case_ids"):
            _require_strings(payload.get("target_ids"), "pass3_review.target_ids", nonempty=True)
        if kind == "pass3_review" and payload.get("issue_ids"):
            _require_strings(payload["issue_ids"], "pass3_review.issue_ids", nonempty=True)
    elif kind == "issues":
        if not isinstance(payload["final_status"], str) or payload["final_status"] not in issue_final:
            raise BatchOutputError("issues.final_status is invalid")
        if "candidate_ids" in payload:
            _require_strings(payload["candidate_ids"], "issues.candidate_ids")
            if len(set(payload["candidate_ids"])) != len(payload["candidate_ids"]):
                raise BatchOutputError("issues.candidate_ids must be unique")
        if payload["final_status"] in {"CONFIRMED_ERROR", "INCONSISTENT"}:
            _require_strings(payload.get("evidence"), "issues.evidence", nonempty=True)
        if "lineage_ids" in record_types:
            _require_strings(payload["lineage_ids"], "issues.lineage_ids", nonempty=True)
    elif kind == "run_metadata":
        if payload["stage"] != "pass1" or not payload["metadata_id"].startswith("META-"):
            raise BatchOutputError("run_metadata is only valid for Pass 1 and requires a META ID")
        _require_strings(payload["references_read"], "run_metadata.references_read")
        if any(not value.startswith("references/") or ".." in value.split("/") for value in payload["references_read"]):
            raise BatchOutputError("run_metadata references must be safe skill-relative paths")
        _require_strings(payload["memory_cases_consulted"], "run_metadata.memory_cases_consulted")
        for field in ("no_detail_tables_reason", "no_summary_tables_reason", "no_sequence_sections_reason", "no_discrete_rows_reason"):
            if field in payload:
                _require_text(payload[field], f"run_metadata.{field}")
        if any(not value.startswith(("R-", "MEM-")) for value in payload["memory_cases_consulted"]):
            raise BatchOutputError("run_metadata memory case IDs must be R-* or MEM-*")
        checks = payload["completed_checks"]
        if not isinstance(checks, list):
            raise BatchOutputError("run_metadata.completed_checks must be an array")
        check_fields = set(record_types[kind]["nested_object_arrays"]["completed_checks"]["required_fields"])
        seen_flags = set()
        for check in checks:
            if not isinstance(check, dict) or set(check) != check_fields:
                raise BatchOutputError("run_metadata completion check has missing or unknown fields")
            _require_text(check["flag"], "run_metadata completion flag")
            if check["flag"] not in manifest_flags or check["flag"] in seen_flags:
                raise BatchOutputError("run_metadata completion flag is unknown or duplicated")
            seen_flags.add(check["flag"])
            _require_strings(check["evidence_ids"], "run_metadata completion evidence", nonempty=True)
            if not set(check["evidence_ids"]) <= batch_evidence_ids:
                raise BatchOutputError("run_metadata completion evidence is outside this batch")
            _require_text(check["reason"], "run_metadata completion reason")


def encode_batch_ledger_manifest(records: list[dict[str, Any]]) -> tuple[str, str]:
    """Return canonical JSON and its digest for one complete batch ledger."""
    try:
        encoded = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise BatchOutputError("batch ledger manifest is not finite JSON data") from exc
    if len(encoded.encode("utf-8")) > _MAX_BYTES:
        raise BatchOutputError("batch ledger manifest exceeds the storage limit")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def decode_batch_ledger_manifest(encoded: str, digest: str) -> list[dict[str, Any]]:
    """Verify a persisted manifest is canonical, complete JSON with a matching digest."""
    try:
        records = json.loads(encoded)
        canonical, expected_digest = encode_batch_ledger_manifest(records)
    except (TypeError, ValueError, json.JSONDecodeError, BatchOutputError) as exc:
        raise BatchOutputError("persisted batch ledger manifest is malformed") from exc
    if not isinstance(records, list) or canonical != encoded or expected_digest != digest:
        raise BatchOutputError("persisted batch ledger manifest digest or shape mismatch")
    return records


def _validate_nested_refs(value: Any, allowed: dict[str, set[str]]) -> None:
    if isinstance(value, dict):
        for field, ids in allowed.items():
            if field not in value or value[field] is None:
                continue
            refs = value[field]
            if field in _SINGULAR_REF_FIELDS:
                if not isinstance(refs, str) or refs not in ids:
                    raise BatchOutputError("ledger payload refers outside its controller-bound source_refs")
            elif not isinstance(refs, list) or any(not isinstance(item, str) for item in refs) or not set(refs) <= ids:
                raise BatchOutputError("ledger payload scope differs from its controller-bound source_refs")
        for child in value.values():
            _validate_nested_refs(child, allowed)
    elif isinstance(value, list):
        for child in value:
            _validate_nested_refs(child, allowed)


def validate_batch_ledger_records(
    payload: Any,
    *,
    inventory: dict[str, Any],
    assigned_check_ids: list[str],
    pass_no: int,
    existing: list[dict[str, Any]] | None = None,
    batch_evidence_ids: set[str] | None = None,
    allowed_source_files: set[str] | None = None,
    contract_version: int = 3,
) -> list[dict[str, Any]]:
    """Bind each domain record to an assigned work item and its approved scope."""
    schema = _contract(contract_version)
    record_types, enums = schema["record_types"], schema["enums"]
    id_fields = {record_type: item["id_field"] for record_type, item in record_types.items()}
    pass_types = {stage: {record_type for record_type, item in record_types.items() if stage in item["passes"]} for stage in (1, 2, 3)}
    if isinstance(pass_no, bool) or not isinstance(pass_no, int) or pass_no not in pass_types or not isinstance(assigned_check_ids, list) or not assigned_check_ids or any(not isinstance(item, str) or not item.strip() for item in assigned_check_ids) or len(set(assigned_check_ids)) != len(assigned_check_ids):
        raise BatchOutputError("ledger record batch scope is invalid")
    if not isinstance(payload, dict) or set(payload) != {"records"} or not isinstance(payload["records"], list):
        raise BatchOutputError("ledger records must contain exactly a records array")
    try:
        approved = validate_inventory(inventory)
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > _MAX_BYTES:
            raise BatchOutputError("ledger records exceed the storage limit")
    except (TypeError, ValueError, RecursionError) as exc:
        raise BatchOutputError("ledger records are not bounded JSON data") from exc
    expected = {item["check_id"]: item for item in approved["expected_work_items"]}
    pages = {item["page_id"]: item for item in approved["pages"]}
    batch_evidence_ids = batch_evidence_ids or set()
    allowed_source_files = allowed_source_files or set()
    if any(check_id not in expected or expected[check_id]["pass_no"] != pass_no for check_id in assigned_check_ids):
        raise BatchOutputError("ledger records contain an unknown or wrong-pass check")

    seen = {(item["record_type"], item["record_id"]) for item in (existing or [])}
    normalized = []
    for record in payload["records"]:
        if not isinstance(record, dict) or set(record) != {"record_type", "record_id", "check_id", "source_refs", "payload"}:
            raise BatchOutputError("ledger record has missing or unknown fields")
        kind, record_id, check_id = record["record_type"], record["record_id"], record["check_id"]
        if not isinstance(kind, str) or kind not in pass_types[pass_no] or not isinstance(check_id, str) or check_id not in assigned_check_ids:
            raise BatchOutputError("ledger record is outside the assigned pass or check scope")
        domain = record["payload"]
        if not isinstance(record_id, str) or not record_id.strip() or len(record_id) > 160 or not isinstance(domain, dict) or domain.get(id_fields[kind]) != record_id:
            raise BatchOutputError("ledger record ID does not match its domain payload")
        if kind in {"pass2_review", "pass3_review"} and domain.get("pass_no") != pass_no:
            raise BatchOutputError("pass review record has the wrong pass number")
        key = (kind, record_id)
        if key in seen:
            raise BatchOutputError("ledger record ID is duplicated or attempts to overwrite prior evidence")
        seen.add(key)
        refs = record["source_refs"]
        if not isinstance(refs, dict) or set(refs) != _REF_FIELDS:
            raise BatchOutputError("ledger source_refs must name pages, sheets, and targets")
        assigned = expected[check_id]
        for field in _REF_FIELDS:
            values = refs[field]
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values) or len(set(values)) != len(values):
                raise BatchOutputError("ledger source_refs must be unique string arrays")
            if not set(values) <= set(assigned.get(field, [])):
                raise BatchOutputError("ledger record refers outside its assigned work item")
        allowed_refs = {**{field: set(values) for field, values in refs.items()}, **{singular: set(refs[plural]) for singular, plural in _SINGULAR_REF_FIELDS.items()}}
        assigned_scope = {
            **assigned,
            "_approved_pages": [pages[page_id] for page_id in assigned["page_ids"]],
            "_record_refs": refs,
            "_approved_line_ids": [line_id for group in approved.get("estimate_line_groups", []) if group["line_group_id"] in assigned.get("estimate_line_group_ids", []) for line_id in group["line_ids"]],
        }
        try:
            _validate_nested_refs(domain, allowed_refs)
            _validate_domain_payload(
                kind, domain, batch_evidence_ids=batch_evidence_ids,
                allowed_source_files=allowed_source_files,
                assigned=assigned_scope,
                record_types=record_types, enums=enums,
            )
        except RecursionError as exc:
            raise BatchOutputError("ledger payload nesting exceeds the supported depth") from exc
        if not any(refs.values()):
            raise BatchOutputError("ledger record requires at least one approved source reference")
        try:
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise BatchOutputError("ledger record is not finite JSON data") from exc
        normalized.append(json.loads(canonical))
    metadata_count = sum(record["record_type"] == "run_metadata" for record in normalized)
    if pass_no == 1 and metadata_count != 1:
        raise BatchOutputError("Pass 1 batch must contain exactly one run_metadata record")
    if contract_version == 4:
        records = [*(existing or []), *normalized]
        observations = {record["record_id"]: record["payload"] for record in records if record["record_type"] == "source_observations"}
        lineage_records = {record["record_id"]: record for record in records if record["record_type"] == "boq_lineage"}
        lines_by_group = {group["line_group_id"]: set(group["line_ids"]) for group in approved.get("estimate_line_groups", [])}
        groups_by_line = {line_id: group_id for group_id, line_ids in lines_by_group.items() for line_id in line_ids}
        input_ids: set[str] = set()
        for record in records:
            if record["record_type"] != "calculation_ledger":
                continue
            for item in record["payload"]["inputs"]:
                if item["input_id"] in input_ids:
                    raise BatchOutputError("calculation input ID is duplicated within the run")
                input_ids.add(item["input_id"])
                observation = observations.get(item["observation_id"])
                if (
                    observation is None
                    or (item["value"], item["unit"]) != (observation["observed_value"], observation["unit"])
                ):
                    raise BatchOutputError("calculation input value or unit differs from its source observation")
        for record in records:
            kind, payload_record = record["record_type"], record["payload"]
            if kind not in {"issues", "pass2_review", "pass3_review"}:
                continue
            linked = payload_record.get("lineage_ids", [])
            if any(lineage_id not in lineage_records for lineage_id in linked):
                raise BatchOutputError(f"{kind} refers to a missing BOQ lineage")
            for lineage_id in linked:
                lineage_refs = lineage_records[lineage_id]["source_refs"]
                if any(not set(lineage_refs[field]) <= set(record["source_refs"][field]) for field in _REF_FIELDS):
                    raise BatchOutputError(f"{kind} lineage reference exceeds its source scope")
        if pass_no in {2, 3}:
            for check_id in assigned_check_ids:
                group_ids = set(expected[check_id].get("estimate_line_group_ids", []))
                if not group_ids:
                    continue
                line_ids = set().union(*(lines_by_group[group_id] for group_id in group_ids))
                expected_lineages = {lineage_id for lineage_id, record in lineage_records.items() if line_ids.intersection(record["payload"]["estimate_line_ids"])}
                reviews = [record for record in normalized if record["check_id"] == check_id and record["record_type"] == f"pass{pass_no}_review"]
                reviewed = {lineage_id for record in reviews for lineage_id in record["payload"].get("lineage_ids", [])}
                if not expected_lineages or reviewed != expected_lineages:
                    raise BatchOutputError(f"Pass {pass_no} review does not cover its exact approved estimate lineages")
    return normalized


def verify_domain_references(projection: dict[str, list[dict[str, Any]]], *, contract_version: int = 3) -> None:
    """Reject ledger records whose contract-declared cross-record references do not resolve."""
    record_types = _contract(contract_version)["record_types"]
    id_fields = {kind: item["id_field"] for kind, item in record_types.items()}
    references = dict(_DOMAIN_REFERENCES)
    if contract_version == 4:
        references.update({
            "boq_lineage": {"detail_row_ids": "detail_rows", "summary_row_ids": "summary_lineage", "calc_ids": "calculation_ledger"},
            "issues": {**references["issues"], "lineage_ids": "boq_lineage"},
            "pass2_review": {**references["pass2_review"], "lineage_ids": "boq_lineage"},
            "pass3_review": {**references["pass3_review"], "lineage_ids": "boq_lineage"},
        })
    identifiers = {kind: {row[id_fields[kind]] for row in rows} for kind, rows in projection.items()}
    faults: list[str] = []
    for source_kind, fields in references.items():
        for row in projection.get(source_kind, []):
            for field, target_kind in fields.items():
                value = row.get(field)
                if value is None:
                    continue
                if isinstance(value, str):
                    refs = [value]
                elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                    refs = value
                else:
                    raise BatchOutputError(f"{source_kind}.{field} must be a string or string array")
                faults.extend(
                    f"{source_kind}.{field} -> missing {target_kind} {ref}"
                    for ref in sorted(set(refs))
                    if ref not in identifiers.get(target_kind, set())
                )
    if contract_version == 4:
        observations = identifiers.get("source_observations", set())
        for row in projection.get("calculation_ledger", []):
            for item in row.get("inputs", []):
                if item.get("observation_id") not in observations:
                    faults.append(f"calculation_ledger.inputs -> missing source_observations {item.get('observation_id')}")
    if faults:
        shown = "; ".join(faults[:10])
        raise BatchOutputError(
            f"ledger domain references are dangling ({len(faults)}): {shown}"
            + (" …" if len(faults) > 10 else "")
        )


def project_batch_ledger_records(records: list[dict[str, Any]], *, contract_version: int = 3) -> dict[str, list[dict[str, Any]]]:
    """Flatten trusted wrappers into deterministic validator ledger arrays."""
    id_fields = {kind: item["id_field"] for kind, item in _contract(contract_version)["record_types"].items()}
    projected = {kind: [] for kind in id_fields}
    seen = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("record_type"), str) or not isinstance(record.get("record_id"), str) or not isinstance(record.get("payload"), dict):
            raise BatchOutputError("persisted ledger record is malformed")
        kind, record_id = record["record_type"], record["record_id"]
        key = (kind, record_id)
        if kind not in projected or key in seen or record["payload"].get(id_fields[kind]) != record_id:
            raise BatchOutputError("persisted ledger records have invalid or duplicate IDs")
        seen.add(key)
        projected[kind].append(record["payload"])
    for kind, rows in projected.items():
        rows.sort(key=lambda item: item[id_fields[kind]])
    return projected
