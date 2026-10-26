import json
from pathlib import Path

import pytest

from app.services.audit_coverage import CoverageInputError, calculate_coverage


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
