import copy

import pytest

from app.services.audit_lesson_selection import LessonSelectionError, lessons_for_batch, lessons_for_pass3, select_lessons


WORK = [
    {"check_id": "CHECK-DETAIL", "check_kind": "DETAIL_RECONCILIATION", "target_ids": ["TARGET-1"]},
    {"check_id": "CHECK-GEOM", "check_kind": "PAIRED_GEOMETRY", "target_ids": ["TARGET-2"]},
]


def _lesson(**overrides):
    item = {
        "lesson_id": "LSN-001", "version": 1, "status": "VERIFIED", "project_scope": "JOB-001",
        "error_class": "MISSING_DIMENSION", "applicability_check_kinds": ["DETAIL_RECONCILIATION"],
        "applicability_target_ids": [], "trigger_conditions": ["A source dimension gap exists"],
        "required_checks": ["Read both source views and recompute"], "counterexamples": ["A later page may resolve the gap"],
    }
    item.update(overrides)
    return item


def test_exact_selectors_and_batch_payload_are_deterministic():
    lesson = _lesson()
    first = select_lessons(project_scope="JOB-001", expected_work_items=WORK, current_lessons=[lesson])
    second = select_lessons(project_scope="JOB-001", expected_work_items=copy.deepcopy(WORK), current_lessons=[lesson])
    assert first == second
    assert first["dispositions"][0]["disposition"] == "SELECTED"
    assert first["dispositions"][0]["matched_check_ids"] == ["CHECK-DETAIL"]
    assert lessons_for_batch(first, ["CHECK-GEOM"]) == []
    payload = lessons_for_batch(first, ["CHECK-DETAIL"])
    assert payload[0]["lesson_id"] == "LSN-001"
    assert payload[0]["check_ids"] == ["CHECK-DETAIL"]


def test_other_project_private_lesson_is_not_considered_or_exposed():
    selection = select_lessons(
        project_scope="JOB-001", expected_work_items=WORK,
        current_lessons=[_lesson(project_scope="JOB-002")],
    )
    assert selection["dispositions"] == []
    assert selection["blocking_lesson_ids"] == []


def test_matching_candidate_blocks_instead_of_silent_skip():
    selection = select_lessons(
        project_scope="JOB-001", expected_work_items=WORK,
        current_lessons=[_lesson(status="CANDIDATE")],
    )
    assert selection["dispositions"][0]["disposition"] == "PENDING_REVIEW"
    assert selection["blocking_lesson_ids"] == ["LSN-001"]
    assert lessons_for_batch(selection, ["CHECK-DETAIL"]) == []


def test_scope_filters_are_conjunctive_and_free_text_never_matches():
    lesson = _lesson(
        applicability_target_ids=["TARGET-2"],
        trigger_conditions=["DETAIL_RECONCILIATION TARGET-1"],
    )
    selection = select_lessons(project_scope="JOB-001", expected_work_items=WORK, current_lessons=[lesson])
    assert selection["dispositions"][0]["disposition"] == "NOT_APPLICABLE"
    assert selection["dispositions"][0]["matched_check_ids"] == []


def test_pass3_carries_verified_lesson_to_batches_for_the_same_target():
    work = [WORK[0], {"check_id": "CHECK-FALSIFY", "check_kind": "FALSIFICATION", "target_ids": ["TARGET-1"]}]
    snapshot = select_lessons(project_scope="JOB-001", expected_work_items=work, current_lessons=[_lesson()])
    assert lessons_for_batch(snapshot, ["CHECK-FALSIFY"]) == []
    carried = lessons_for_pass3(snapshot, ["CHECK-FALSIFY"], ["TARGET-1"])
    assert carried[0]["lesson_id"] == "LSN-001"
    assert carried[0]["target_ids"] == ["TARGET-1"]
    assert lessons_for_pass3(snapshot, ["CHECK-FALSIFY"], ["TARGET-2"]) == []


def test_unscoped_selector_and_tampered_snapshot_fail_closed():
    with pytest.raises(LessonSelectionError, match="exact identifier"):
        select_lessons(project_scope="", expected_work_items=WORK, current_lessons=[])
    selection = select_lessons(project_scope="JOB-001", expected_work_items=WORK, current_lessons=[_lesson()])
    selection["project_scope"] = "JOB-002"
    with pytest.raises(LessonSelectionError, match="digest mismatch"):
        lessons_for_batch(selection, ["CHECK-DETAIL"])
