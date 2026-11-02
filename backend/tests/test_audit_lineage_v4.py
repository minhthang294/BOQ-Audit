import copy

import pytest

from app.services.audit_batch import BatchOutputError
from app.services.audit_batch_ledgers import validate_batch_ledger_records, verify_domain_references, project_batch_ledger_records
from app.services.audit_coverage import CoverageInputError, validate_pass1_source_coverage
from app.services.audit_lineage_v4 import (
    boq_lineage_id, calculation_input_id, estimate_line_group_id, estimate_line_id, source_observation_id,
)


def _inventory():
    revision = "sha256:" + "a" * 64
    line_id = estimate_line_id("xlsx", revision, "xlsx-S00001", "B3:D3")
    group_id = estimate_line_group_id([line_id])
    base = {"document_id": "xlsx", "revision_digest": revision, "page_ids": [], "sheet_ids": ["xlsx-S00001"], "target_ids": [], "estimate_line_group_ids": [group_id]}
    return {
        "documents": [{"document_id": "xlsx", "revision_digest": revision}],
        "pages": [],
        "sheets": [{"sheet_id": "xlsx-S00001", "document_id": "xlsx", "revision_digest": revision, "sheet_index": 1, "sheet_name": "BOQ"}],
        "targets": [],
        "estimate_lines": [{"line_id": line_id, "document_id": "xlsx", "revision_digest": revision, "sheet_id": "xlsx-S00001", "cell_range": "B3:D3"}],
        "estimate_line_groups": [{"line_group_id": group_id, "document_id": "xlsx", "revision_digest": revision, "sheet_id": "xlsx-S00001", "line_ids": [line_id]}],
        "expected_work_items": [
            {"check_id": f"p{stage}", **base, "check_kind": f"pass{stage}", "pass_no": stage, "dependencies": [] if stage == 1 else [f"p{stage - 1}"]}
            for stage in (1, 2, 3)
        ],
    }


def _records(inventory):
    line_id = inventory["estimate_lines"][0]["line_id"]
    revision = inventory["documents"][0]["revision_digest"]
    anchor = {"kind": "xlsx_range", "sheet_id": "xlsx-S00001", "range": "C8", "row_span": 1}
    observation_id = source_observation_id("xlsx", revision, anchor)
    calc_id = "CALC-1"
    refs = {"page_ids": [], "sheet_ids": ["xlsx-S00001"], "target_ids": []}
    observation = {"record_type": "source_observations", "record_id": observation_id, "check_id": "p1", "source_refs": refs, "payload": {
        "observation_id": observation_id, "document_id": "xlsx", "revision_digest": revision,
        "observed_value": 12.5, "unit": "m", "anchor": anchor, "evidence_ids": ["e1"],
    }}
    calc = {"record_type": "calculation_ledger", "record_id": calc_id, "check_id": "p1", "source_refs": refs, "payload": {
        "calc_id": calc_id, "formula": "L × W", "inputs": [{"input_id": calculation_input_id(calc_id, 0, observation_id), "observation_id": observation_id, "value": 12.5, "unit": "m"}],
        "raw_result": 12.5, "unit": "m", "status": "VERIFIED", "source_anchors": ["C8"],
        "calculation_class": "INDEPENDENT_TAKEOFF", "pass1_result": 12.5, "assumption_ids": [],
    }}
    metadata = {"record_type": "run_metadata", "record_id": "META-p1", "check_id": "p1", "source_refs": refs, "payload": {
        "metadata_id": "META-p1", "stage": "pass1", "references_read": [], "memory_cases_consulted": [], "completed_checks": [],
    }}
    return observation, calc, metadata


def test_v4_inventory_binds_estimate_line_and_line_group_to_source_revision():
    inventory = _inventory()
    assert validate_pass1_source_coverage(inventory)["estimate_line_groups"] == inventory["estimate_line_groups"]
    broken = copy.deepcopy(inventory)
    broken["estimate_lines"][0]["revision_digest"] = "sha256:" + "b" * 64
    with pytest.raises(CoverageInputError, match="estimate line"):
        validate_pass1_source_coverage(broken)
    missing_group = copy.deepcopy(inventory)
    missing_group["expected_work_items"][0]["estimate_line_group_ids"] = []
    with pytest.raises(CoverageInputError, match="line group"):
        validate_pass1_source_coverage(missing_group)
    reversed_range = copy.deepcopy(inventory)
    reversed_range["estimate_lines"][0]["cell_range"] = "D3:B3"
    with pytest.raises(CoverageInputError, match="range is invalid"):
        validate_pass1_source_coverage(reversed_range)


def test_v4_calculation_input_must_resolve_to_source_observation_and_match_value_unit():
    inventory = _inventory()
    observation, calc, metadata = _records(inventory)
    valid = validate_batch_ledger_records({"records": [observation, calc, metadata]}, inventory=inventory, assigned_check_ids=["p1"], pass_no=1, batch_evidence_ids={"e1"}, allowed_source_files={"source/estimate.xlsx"}, contract_version=4)
    projection = project_batch_ledger_records(valid, contract_version=4)
    verify_domain_references(projection, contract_version=4)

    split_batch = validate_batch_ledger_records(
        {"records": [calc, metadata]}, inventory=inventory, assigned_check_ids=["p1"],
        pass_no=1, existing=[observation], batch_evidence_ids={"e1"},
        allowed_source_files={"source/estimate.xlsx"}, contract_version=4,
    )
    assert [record["record_type"] for record in split_batch] == ["calculation_ledger", "run_metadata"]

    wrong_value = copy.deepcopy(calc)
    wrong_value["payload"]["inputs"][0]["value"] = 99
    with pytest.raises(BatchOutputError, match="value or unit"):
        validate_batch_ledger_records({"records": [observation, wrong_value, metadata]}, inventory=inventory, assigned_check_ids=["p1"], pass_no=1, batch_evidence_ids={"e1"}, allowed_source_files={"source/estimate.xlsx"}, contract_version=4)

    dangling = copy.deepcopy(calc)
    dangling["payload"]["inputs"][0]["observation_id"] = "OBS-missing"
    dangling["payload"]["inputs"][0]["input_id"] = calculation_input_id("CALC-1", 0, "OBS-missing")
    with pytest.raises(BatchOutputError, match="value or unit"):
        validate_batch_ledger_records({"records": [dangling, metadata]}, inventory=inventory, assigned_check_ids=["p1"], pass_no=1, batch_evidence_ids={"e1"}, allowed_source_files={"source/estimate.xlsx"}, contract_version=4)


def test_v4_observation_anchor_cannot_escape_batch_source_scope():
    inventory = _inventory()
    observation, calc, metadata = _records(inventory)
    observation["payload"]["anchor"]["sheet_id"] = "other-sheet"
    observation["payload"]["observation_id"] = source_observation_id("xlsx", inventory["documents"][0]["revision_digest"], observation["payload"]["anchor"])
    observation["record_id"] = observation["payload"]["observation_id"]
    calc["payload"]["inputs"][0]["observation_id"] = observation["record_id"]
    calc["payload"]["inputs"][0]["input_id"] = calculation_input_id("CALC-1", 0, observation["record_id"])
    with pytest.raises(BatchOutputError, match="source_refs"):
        validate_batch_ledger_records({"records": [observation, calc, metadata]}, inventory=inventory, assigned_check_ids=["p1"], pass_no=1, batch_evidence_ids={"e1"}, allowed_source_files={"source/estimate.xlsx"}, contract_version=4)


def test_v4_source_observation_rejects_empty_or_whitespace_value():
    inventory = _inventory()
    observation, calc, metadata = _records(inventory)
    for value in ("", "  "):
        broken = copy.deepcopy(observation)
        broken["payload"]["observed_value"] = value
        with pytest.raises(BatchOutputError, match="observation value must not be empty"):
            validate_batch_ledger_records(
                {"records": [broken, calc, metadata]}, inventory=inventory,
                assigned_check_ids=["p1"], pass_no=1, batch_evidence_ids={"e1"},
                allowed_source_files={"source/estimate.xlsx"}, contract_version=4,
            )


def test_v4_review_must_cover_exact_lineages_for_its_approved_estimate_group():
    inventory = _inventory()
    first_line = inventory["estimate_lines"][0]["line_id"]
    revision = inventory["documents"][0]["revision_digest"]
    inventory["pages"].append({
        "page_id": "xlsx-P00001", "document_id": "xlsx", "revision_digest": revision, "pdf_page": 1,
    })
    for work_item in inventory["expected_work_items"]:
        work_item["page_ids"] = ["xlsx-P00001"]
    item = inventory["expected_work_items"][1]
    item["pass_no"] = 2
    refs = {"page_ids": ["xlsx-P00001"], "sheet_ids": ["xlsx-S00001"], "target_ids": []}

    lineage_payload = {
        "estimate_line_ids": [first_line], "detail_row_ids": [], "summary_row_ids": [], "calc_ids": [],
        "evidence_ids": ["e1"], "reconciliation_status": "UNMATCHED", "reason": "not reconciled",
    }
    lineage_id = boq_lineage_id(lineage_payload)
    expected_lineage = {
        "record_type": "boq_lineage", "record_id": lineage_id, "check_id": "p1",
        "source_refs": refs, "payload": {"lineage_id": lineage_id, **lineage_payload},
    }

    def review(lineage_ids):
        return {
            "record_type": "pass2_review", "record_id": "REV-p2", "check_id": "p2",
            "source_refs": refs,
            "payload": {
                "review_id": "REV-p2", "pass_no": 2, "target": "approved estimate group",
                "result": "checked", "method": "re-open source", "evidence": ["e2"],
                "page_ids": ["xlsx-P00001"], "sheet_ids": ["xlsx-S00001"],
                "case_ids": [], "target_ids": [], "issue_ids": [], "lineage_ids": lineage_ids,
            },
        }

    def validate(lineage_ids):
        return validate_batch_ledger_records(
            {"records": [review(lineage_ids)]}, inventory=inventory,
            assigned_check_ids=["p2"], pass_no=2, existing=[expected_lineage],
            batch_evidence_ids={"e2"}, allowed_source_files={"source/estimate.xlsx"}, contract_version=4,
        )

    assert validate([lineage_id])[0]["payload"]["lineage_ids"] == [lineage_id]
    with pytest.raises(BatchOutputError, match="exact approved estimate lineages"):
        validate([])
    with pytest.raises(BatchOutputError, match="refers to a missing BOQ lineage"):
        validate([lineage_id, "LINK-EXTRA"])


def test_v4_calculation_can_reference_an_observation_from_another_assigned_source_ref():
    inventory = _inventory()
    inventory["sheets"].append({"sheet_id": "xlsx-S00002", "document_id": "xlsx", "revision_digest": inventory["documents"][0]["revision_digest"], "sheet_index": 2, "sheet_name": "Other"})
    inventory["expected_work_items"][0]["sheet_ids"].append("xlsx-S00002")
    observation, calc, metadata = _records(inventory)
    calc["source_refs"] = {"page_ids": [], "sheet_ids": ["xlsx-S00002"], "target_ids": []}
    normalized = validate_batch_ledger_records(
        {"records": [calc, metadata]}, inventory=inventory, assigned_check_ids=["p1"],
        pass_no=1, existing=[observation], batch_evidence_ids={"e1"},
        allowed_source_files={"source/estimate.xlsx"}, contract_version=4,
    )
    projection = project_batch_ledger_records([observation, *normalized], contract_version=4)
    verify_domain_references(projection, contract_version=4)


def test_v4_xlsx_anchor_rejects_reversed_columns():
    from app.services.audit_lineage_v4 import validate_anchor

    with pytest.raises(ValueError, match="A1 range"):
        validate_anchor({"kind": "xlsx_range", "sheet_id": "s1", "range": "D3:B3", "row_span": 1}, page_ids=set(), sheet_ids={"s1"})
