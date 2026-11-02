import copy
import json
from pathlib import Path

import pytest

from app.services.audit_coverage import CoverageInputError, calculate_coverage, validate_inventory, validate_pass1_source_coverage


FIXTURE = Path(__file__).parent / "fixtures/audit_quality/coverage_missing_scope.json"


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_missing_page_and_target_are_reported_even_when_present_page_is_checked():
    data = _fixture()

    result = calculate_coverage(data["inventory"], data["work_items"])

    assert result["missing_page_ids"] == ["drawing-a-P00002"]
    assert result["missing_target_ids"] == ["joint-1"]
    assert result["page_coverage"] == 0.5
    assert result["target_coverage"] == 0.5
    assert result["missing_check_ids"] == ["check-joint-1"]
    assert result["coverage"] == 0.5
    assert result["coverage_status"] == "INCOMPLETE"


def test_unresolved_is_checked_but_not_clear():
    data = _fixture()
    data["inventory"]["pages"] = data["inventory"]["pages"][:1]
    data["inventory"]["targets"] = data["inventory"]["targets"][:1]
    data["inventory"]["expected_work_items"] = data["inventory"]["expected_work_items"][:1]
    data["work_items"][0]["result_status"] = "UNRESOLVED"

    result = calculate_coverage(data["inventory"], data["work_items"])

    assert result["checked_count"] == 1
    assert result["unresolved_count"] == 1
    assert result["coverage_status"] == "COVERED_WITH_UNRESOLVED"


def test_complete_requires_every_expected_work_item():
    data = _fixture()
    data["work_items"].append({
        **data["inventory"]["expected_work_items"][1],
        "execution_status": "DONE",
        "result_status": "CHECKED",
        "evidence_ids": ["drawing-a-P00002:detail-2"],
    })

    result = calculate_coverage(data["inventory"], data["work_items"])

    assert result["coverage"] == 1.0
    assert result["page_coverage"] == 1.0
    assert result["target_coverage"] == 1.0
    assert result["coverage_status"] == "COMPLETE"


def test_blocked_and_not_applicable_states_keep_distinct_counts():
    data = _fixture()
    data["inventory"]["pages"] = data["inventory"]["pages"][:1]
    data["inventory"]["targets"] = data["inventory"]["targets"][:1]
    data["inventory"]["expected_work_items"] = data["inventory"]["expected_work_items"][:1]
    item = data["work_items"][0]
    item["execution_status"] = "BLOCKED"
    item["result_status"] = "NOT_CHECKED"

    result = calculate_coverage(data["inventory"], data["work_items"])

    assert result["blocked_count"] == 1
    assert result["not_checked_count"] == 1
    assert result["coverage_status"] == "BLOCKED"

    item["execution_status"] = "DONE"
    item["result_status"] = "NOT_APPLICABLE"
    item["applicability_reason"] = "Synthetic: target absent in revision."
    item["evidence_ids"] = ["drawing-a-P00001:scope"]
    result = calculate_coverage(data["inventory"], data["work_items"])
    assert result["not_applicable_count"] == 1
    assert result["checked_count"] == 1

    item["evidence_ids"] = []
    with pytest.raises(CoverageInputError, match="checked result requires evidence"):
        calculate_coverage(data["inventory"], data["work_items"])


def test_multi_page_target_requires_full_target_scope():
    data = _fixture()
    data["inventory"]["targets"][0]["page_ids"] = ["drawing-a-P00001", "drawing-a-P00002"]

    with pytest.raises(CoverageInputError, match="target outside work-item pages"):
        calculate_coverage(data["inventory"], data["work_items"])


def test_duplicate_inventory_ids_and_unknown_references_are_rejected():
    data = _fixture()
    data["inventory"]["pages"].append(data["inventory"]["pages"][0])
    with pytest.raises(CoverageInputError, match="duplicate page_id"):
        calculate_coverage(data["inventory"], data["work_items"])

    data = _fixture()
    data["inventory"]["expected_work_items"][0]["page_ids"] = ["unknown-page"]
    with pytest.raises(CoverageInputError, match="unknown or mismatched page"):
        calculate_coverage(data["inventory"], data["work_items"])

    data = _fixture()
    data["work_items"][0]["page_ids"] = ["drawing-a-P00002"]
    with pytest.raises(CoverageInputError, match="scope changed"):
        calculate_coverage(data["inventory"], data["work_items"])


def test_empty_inventory_has_na_not_zero_percent():
    result = calculate_coverage({"documents": [], "pages": [], "targets": [], "expected_work_items": []}, [])

    assert result["coverage"] is None
    assert result["page_coverage"] is None
    assert result["target_coverage"] is None
    assert result["coverage_status"] == "N/A"


def _valid_inventory():
    return {
        "documents": [{"document_id": "doc", "revision_digest": "rev"}],
        "pages": [{"page_id": "p1", "document_id": "doc", "revision_digest": "rev", "pdf_page": 1}],
        "targets": [{"target_id": "t1", "document_id": "doc", "revision_digest": "rev", "page_ids": ["p1"]}],
        "expected_work_items": [{
            "check_id": "c1",
            "document_id": "doc",
            "revision_digest": "rev",
            "page_ids": ["p1"],
            "target_ids": ["t1"],
            "check_kind": "boq",
            "pass_no": 1,
            "dependencies": [],
        }],
    }


def test_validate_inventory_accepts_valid_inventory_and_is_immutable():
    inventory = _valid_inventory()
    snapshot = copy.deepcopy(inventory)
    result = validate_inventory(inventory)
    assert result == inventory
    assert result is not inventory
    assert result["expected_work_items"][0]["page_ids"] is not inventory["expected_work_items"][0]["page_ids"]
    result["expected_work_items"][0]["page_ids"].append("mutated")
    assert inventory == snapshot


def test_pass1_inventory_must_cover_every_declared_page_sheet_and_target():
    inventory = _valid_inventory()
    inventory["pages"].append({"page_id": "p2", "document_id": "doc", "revision_digest": "rev", "pdf_page": 2})
    with pytest.raises(CoverageInputError, match=r"pages=\['p2'\]"):
        validate_pass1_source_coverage(inventory)

    inventory = {
        "documents": [{"document_id": "doc", "revision_digest": "rev"}],
        "pages": [],
        "sheets": [
            {"sheet_id": "s1", "document_id": "doc", "revision_digest": "rev", "sheet_index": 1, "sheet_name": "Estimate"},
            {"sheet_id": "s2", "document_id": "doc", "revision_digest": "rev", "sheet_index": 2, "sheet_name": "Hidden"},
        ],
        "targets": [],
        "expected_work_items": [
            {"check_id": "p1-s1", "document_id": "doc", "revision_digest": "rev", "page_ids": [], "sheet_ids": ["s1"], "target_ids": [], "check_kind": "review", "pass_no": 1, "dependencies": []},
            {"check_id": "p2-s1", "document_id": "doc", "revision_digest": "rev", "page_ids": [], "sheet_ids": ["s1"], "target_ids": [], "check_kind": "review", "pass_no": 2, "dependencies": ["p1-s1"]},
            {"check_id": "p3-s1", "document_id": "doc", "revision_digest": "rev", "page_ids": [], "sheet_ids": ["s1"], "target_ids": [], "check_kind": "review", "pass_no": 3, "dependencies": ["p2-s1"]},
        ],
    }
    with pytest.raises(CoverageInputError, match=r"sheets=\['s2'\]"):
        validate_pass1_source_coverage(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["target_ids"] = []
    with pytest.raises(CoverageInputError, match=r"targets=\['t1'\]"):
        validate_pass1_source_coverage(inventory)


def test_validate_inventory_rejects_duplicate_and_missing_references():
    for key, id_key in (("documents", "document_id"), ("pages", "page_id"), ("targets", "target_id"), ("expected_work_items", "check_id")):
        inventory = _valid_inventory()
        duplicate = copy.deepcopy(inventory[key][0])
        duplicate[id_key] = inventory[key][0][id_key]
        inventory[key].append(duplicate)
        with pytest.raises(CoverageInputError):
            validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["pages"][0]["document_id"] = "missing"
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["page_ids"] = ["missing-page"]
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["target_ids"] = ["missing-target"]
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)


def test_validate_inventory_rejects_bool_and_invalid_scalars():
    inventory = _valid_inventory()
    inventory["pages"][0]["pdf_page"] = True
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["execution_status"] = "DONE"
    with pytest.raises(CoverageInputError, match="runtime state"):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["pages"][0]["pdf_page"] = 0
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["pass_no"] = True
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["pass_no"] = 4
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["check_kind"] = ""
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)


def test_validate_inventory_rejects_cross_document_and_revision_scope():
    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["document_id"] = "other-doc"
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["revision_digest"] = "other-rev"
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["targets"][0]["page_ids"] = ["other-page"]
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["target_ids"] = ["t1"]
    inventory["expected_work_items"][0]["page_ids"] = []
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)


def test_validate_inventory_rejects_bad_dependencies():
    inventory = _valid_inventory()
    inventory["expected_work_items"].append({
        "check_id": "c2",
        "document_id": "doc",
        "revision_digest": "rev",
        "page_ids": ["p1"],
        "target_ids": [],
        "check_kind": "boq",
        "pass_no": 1,
        "dependencies": ["missing"],
    })
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["dependencies"] = ["c1", "c1"]
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"].append({
        "check_id": "c2",
        "document_id": "doc",
        "revision_digest": "rev",
        "page_ids": ["p1"],
        "target_ids": [],
        "check_kind": "boq",
        "pass_no": 1,
        "dependencies": ["c1"],
    })
    inventory["expected_work_items"][0]["pass_no"] = 2
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)


def test_validate_inventory_requires_every_prior_pass_item_for_overlapping_scope():
    inventory = _valid_inventory()
    inventory["expected_work_items"].extend([
        {
            "check_id": "c1-second-check",
            "document_id": "doc",
            "revision_digest": "rev",
            "page_ids": ["p1"],
            "target_ids": [],
            "check_kind": "drawing-review",
            "pass_no": 1,
            "dependencies": [],
        },
        {
            "check_id": "c2-pass2",
            "document_id": "doc",
            "revision_digest": "rev",
            "page_ids": ["p1"],
            "target_ids": ["t1"],
            "check_kind": "independent-review",
            "pass_no": 2,
            "dependencies": ["c1"],
        },
    ])
    with pytest.raises(CoverageInputError, match="missing prior-pass dependency"):
        validate_inventory(inventory)

    inventory["expected_work_items"][-1]["dependencies"].append("c1-second-check")
    assert validate_inventory(inventory) == inventory


def test_validate_inventory_rejects_pass_scope_without_prior_pass_coverage():
    inventory = _valid_inventory()
    inventory["pages"].append({"page_id": "p2", "document_id": "doc", "revision_digest": "rev", "pdf_page": 2})
    inventory["expected_work_items"].append({
        "check_id": "c2-pass2",
        "document_id": "doc",
        "revision_digest": "rev",
        "page_ids": ["p2"],
        "target_ids": [],
        "check_kind": "independent-review",
        "pass_no": 2,
        "dependencies": ["c1"],
    })
    with pytest.raises(CoverageInputError, match="pass dependency does not cover source scope"):
        validate_inventory(inventory)


def test_validate_inventory_rejects_duplicate_scope_ids_and_non_json_values():
    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["page_ids"] = ["p1", "p1"]
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["expected_work_items"][0]["target_ids"] = ["t1", "t1"]
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["targets"][0]["page_ids"] = ["p1", "p1"]
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["diagnostic"] = float("nan")
    with pytest.raises(CoverageInputError, match="JSON-compatible"):
        validate_inventory(inventory)

    inventory = _valid_inventory()
    inventory["diagnostic"] = inventory
    with pytest.raises(CoverageInputError, match="JSON-compatible"):
        validate_inventory(inventory)


def _sheet_inventory():
    return {
        "documents": [{"document_id": "doc", "revision_digest": "rev"}],
        "pages": [],
        "sheets": [{"sheet_id": "s1", "document_id": "doc", "revision_digest": "rev", "sheet_index": 1, "sheet_name": "Sheet1"}],
        "targets": [{"target_id": "t1", "document_id": "doc", "revision_digest": "rev", "page_ids": [], "sheet_ids": ["s1"]}],
        "expected_work_items": [{
            "check_id": "c1",
            "document_id": "doc",
            "revision_digest": "rev",
            "page_ids": [],
            "sheet_ids": ["s1"],
            "target_ids": ["t1"],
            "check_kind": "boq",
            "pass_no": 1,
            "dependencies": [],
        }],
    }


def test_validate_inventory_accepts_sheet_only_inventory():
    inventory = _sheet_inventory()
    result = validate_inventory(inventory)
    assert result == inventory
    assert result is not inventory


def test_sheet_coverage_reports_declared_sheet_completion():
    data = {"inventory": _sheet_inventory(), "work_items": [{
        "check_id": "c1",
        "document_id": "doc",
        "revision_digest": "rev",
        "page_ids": [],
        "sheet_ids": ["s1"],
        "target_ids": ["t1"],
        "execution_status": "DONE",
        "result_status": "CHECKED",
        "evidence_ids": ["ev-1"],
    }]}
    result = calculate_coverage(data["inventory"], data["work_items"])
    assert result["sheet_coverage"] == 1.0
    assert result["missing_sheet_ids"] == []
    assert result["page_coverage"] is None


def test_validate_inventory_rejects_sheet_reference_failures():
    inventory = _sheet_inventory()
    inventory["sheets"].append({"sheet_id": "s1", "document_id": "doc", "revision_digest": "rev", "sheet_index": 2, "sheet_name": "Dup"})
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _sheet_inventory()
    inventory["expected_work_items"][0]["sheet_ids"] = ["missing-sheet"]
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _sheet_inventory()
    inventory["expected_work_items"][0]["document_id"] = "other-doc"
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _sheet_inventory()
    inventory["expected_work_items"][0]["revision_digest"] = "other-rev"
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)

    inventory = _sheet_inventory()
    inventory["expected_work_items"][0]["page_ids"] = []
    inventory["expected_work_items"][0]["sheet_ids"] = []
    with pytest.raises(CoverageInputError):
        validate_inventory(inventory)


def test_calculate_coverage_rejects_sheet_scope_tampering():
    data = {"inventory": _sheet_inventory(), "work_items": [{
        "check_id": "c1",
        "document_id": "doc",
        "revision_digest": "rev",
        "page_ids": [],
        "sheet_ids": ["s1"],
        "target_ids": ["t1"],
        "execution_status": "DONE",
        "result_status": "CHECKED",
        "evidence_ids": ["ev-1"],
    }]}
    data["work_items"][0]["sheet_ids"] = []
    with pytest.raises(CoverageInputError):
        calculate_coverage(data["inventory"], data["work_items"])
