import json

import pytest

from app.services.audit_lessons import (
    LessonAuthorizationError,
    LessonStateError,
    LessonStore,
    LessonStoreError,
)

LESSON_ID = "LSN-TEST-001"
REVIEWER = "reviewer-a"


def _candidate(
    store: LessonStore,
    lesson_id: str = LESSON_ID,
    *,
    designated_reviewer_id: str | None = REVIEWER,
    **overrides,
):
    params = {
        "error_class": "MISSING_DIMENSION",
        "trigger_conditions": ["drawing has a dimension gap"],
        "required_checks": ["recompute the chain from the baseline"],
        "counterexamples": ["revision adds the missing dimension"],
        "source_case_ids": ["case-001"],
        "evidence_refs": ["evidence-001"],
        "project_scope": "project-test",
        "visibility_scope": "private",
        "actor_id": "creator",
        "designated_reviewer_id": designated_reviewer_id,
    }
    params.update(overrides)
    return store.create_candidate(lesson_id=lesson_id, **params)


def _verify(
    store: LessonStore,
    lesson_id: str = LESSON_ID,
    version: int = 1,
    *,
    reviewer_id: str = REVIEWER,
    designated_reviewer_id: str = REVIEWER,
    evidence_refs=("review-evidence-001",),
    reason: str = "checked against the drawing",
):
    return store.verify(
        lesson_id,
        version,
        reviewer_id=reviewer_id,
        designated_reviewer_id=designated_reviewer_id,
        evidence_refs=evidence_refs,
        reason=reason,
    )


def _reject(
    store: LessonStore,
    lesson_id: str = LESSON_ID,
    version: int = 1,
    *,
    reviewer_id: str = REVIEWER,
    designated_reviewer_id: str = REVIEWER,
    evidence_refs=("review-evidence-001",),
    reason: str = "does not reproduce",
):
    return store.reject(
        lesson_id,
        version,
        reviewer_id=reviewer_id,
        designated_reviewer_id=designated_reviewer_id,
        evidence_refs=evidence_refs,
        reason=reason,
    )


def test_candidate_round_trip_and_version_file_is_immutable(tmp_path):
    store = LessonStore(tmp_path / "lessons")
    created = _candidate(store)

    assert created["lesson_id"] == LESSON_ID
    assert created["version"] == 1
    assert created["status"] == "CANDIDATE"
    assert created["reviewer_id"] is None
    assert created["designated_reviewer_id"] == REVIEWER
    assert created["supersedes"] is None
    assert store.select_current(LESSON_ID) is None

    version_file = store.root / LESSON_ID / "versions" / "v0001.json"
    before = version_file.read_bytes()
    content = json.loads(before)
    assert content["error_class"] == "MISSING_DIMENSION"
    assert content["trigger_conditions"] == ["drawing has a dimension gap"]
    assert "status" not in content
    assert "reviewer_id" not in content

    verified = _verify(store)

    assert verified["status"] == "VERIFIED"
    assert verified["reviewer_id"] == REVIEWER
    assert verified["reviewed_at"]
    assert version_file.read_bytes() == before

    reloaded = LessonStore(tmp_path / "lessons").get_version(LESSON_ID, 1)
    assert reloaded["status"] == "VERIFIED"
    assert store.select_current(LESSON_ID)["version"] == 1


def test_reviewer_assignment_is_required_and_must_match_acting_reviewer(tmp_path):
    store = LessonStore(tmp_path / "lessons")
    _candidate(store, designated_reviewer_id=None)

    with pytest.raises(LessonAuthorizationError, match="no designated reviewer"):
        _verify(store)
    assert store.get_version(LESSON_ID, 1)["status"] == "CANDIDATE"

    store.assign_reviewer(LESSON_ID, 1, designated_reviewer_id=REVIEWER, assigned_by="lead")
    with pytest.raises(LessonAuthorizationError, match="does not match"):
        _verify(store, reviewer_id="reviewer-b", designated_reviewer_id="reviewer-b")
    with pytest.raises(LessonAuthorizationError, match="does not match"):
        _verify(store, reviewer_id=REVIEWER, designated_reviewer_id="reviewer-b")

    store.assign_reviewer(LESSON_ID, 1, designated_reviewer_id="reviewer-b", assigned_by="lead")
    with pytest.raises(LessonAuthorizationError):
        _verify(store, reviewer_id=REVIEWER, designated_reviewer_id=REVIEWER)
    assert store.get_version(LESSON_ID, 1)["status"] == "CANDIDATE"

    verified = _verify(store, reviewer_id="reviewer-b", designated_reviewer_id="reviewer-b")
    assert verified["status"] == "VERIFIED"
    assert verified["reviewer_id"] == "reviewer-b"
    assignments = [item["designated_reviewer_id"] for item in store.assignment_history(LESSON_ID, 1)]
    assert assignments == ["reviewer-a", "reviewer-b"]


@pytest.mark.parametrize(
    "evidence_refs,reason",
    [
        ([], "reason"),
        ([""], "reason"),
        ([" "], "reason"),
        (["evidence"], ""),
        (["evidence"], "   "),
    ],
)
def test_verify_requires_evidence_and_reason(tmp_path, evidence_refs, reason):
    store = LessonStore(tmp_path / "lessons")
    _candidate(store)

    with pytest.raises(LessonStoreError):
        _verify(store, evidence_refs=evidence_refs, reason=reason)

    assert store.get_version(LESSON_ID, 1)["status"] == "CANDIDATE"


@pytest.mark.parametrize(
    "evidence_refs,reason",
    [([], "reason"), (["evidence"], ""), (["evidence"], "   ")],
)
def test_reject_requires_evidence_and_reason(tmp_path, evidence_refs, reason):
    store = LessonStore(tmp_path / "lessons")
    _candidate(store)

    with pytest.raises(LessonStoreError):
        _reject(store, evidence_refs=evidence_refs, reason=reason)

    assert store.get_version(LESSON_ID, 1)["status"] == "CANDIDATE"


def test_verified_record_exposes_review_and_blocks_selection_after_rejection(tmp_path):
    store = LessonStore(tmp_path / "lessons")
    _candidate(store)
    verified = _verify(store)
    assert verified["review_evidence_refs"] == ["review-evidence-001"]
    assert verified["review_reason"] == "checked against the drawing"
    assert [event["event_type"] for event in store.history(LESSON_ID, 1)] == ["CREATED", "VERIFIED"]

    rejected = _reject(store)
    assert rejected["status"] == "REJECTED"
    assert rejected["reviewer_id"] == REVIEWER
    assert store.select_current(LESSON_ID) is None
    assert [event["event_type"] for event in store.history(LESSON_ID, 1)] == ["CREATED", "VERIFIED", "REJECTED"]

    with pytest.raises(LessonStateError):
        _verify(store)
    with pytest.raises(LessonStateError):
        store.retire(LESSON_ID, 1, actor_id="lead", reason="obsolete")
    with pytest.raises(LessonStateError):
        store.assign_reviewer(LESSON_ID, 1, designated_reviewer_id="reviewer-c")


def test_candidate_can_be_rejected(tmp_path):
    store = LessonStore(tmp_path / "lessons")
    _candidate(store)

    rejected = _reject(store)

    assert rejected["status"] == "REJECTED"
    assert rejected["review_reason"] == "does not reproduce"
    assert store.select_current(LESSON_ID) is None
    with pytest.raises(LessonStateError):
        _verify(store)


def test_superseding_version_blocks_old_current_until_verified(tmp_path):
    store = LessonStore(tmp_path / "lessons")
    _candidate(store)
    _verify(store)

    second = _candidate(store, supersedes=1, designated_reviewer_id="reviewer-b")
    assert second["version"] == 2
    assert second["supersedes"] == 1
    assert second["status"] == "CANDIDATE"
    assert store.select_current(LESSON_ID) is None
    assert store.get_version(LESSON_ID, 1)["status"] == "VERIFIED"

    _verify(store, version=2, reviewer_id="reviewer-b", designated_reviewer_id="reviewer-b")
    assert store.select_current(LESSON_ID)["version"] == 2

    store.retire(LESSON_ID, 2, actor_id="lead", reason="replaced by a later audit practice")
    retired = store.get_version(LESSON_ID, 2)
    assert retired["status"] == "RETIRED"
    assert retired["reviewer_id"] == "reviewer-b"
    assert retired["reviewed_at"]
    assert store.get_version(LESSON_ID, 1)["status"] == "VERIFIED"
    assert store.select_current(LESSON_ID) is None


def test_new_versions_require_the_immediate_predecessor(tmp_path):
    store = LessonStore(tmp_path / "lessons")

    with pytest.raises(LessonStateError, match="no prior version"):
        _candidate(store, supersedes=1)

    _candidate(store)
    with pytest.raises(LessonStateError, match="must declare supersedes"):
        _candidate(store)

    _candidate(store, supersedes=1)
    with pytest.raises(LessonStateError, match="latest version"):
        _candidate(store, supersedes=1)


@pytest.mark.parametrize("lesson_id", ["../escape", "bad/id", " bad id", ".hidden", "x" * 65])
def test_invalid_lesson_ids_are_rejected(tmp_path, lesson_id):
    store = LessonStore(tmp_path / "lessons")

    with pytest.raises(LessonStoreError):
        _candidate(store, lesson_id=lesson_id)


def test_lifecycle_transitions_are_enforced(tmp_path):
    store = LessonStore(tmp_path / "lessons")
    _candidate(store)

    with pytest.raises(LessonStateError):
        store.retire(LESSON_ID, 1, actor_id="lead", reason="not verified yet")

    _verify(store)
    with pytest.raises(LessonStateError):
        _verify(store)
    with pytest.raises(LessonStateError):
        store.assign_reviewer(LESSON_ID, 1, designated_reviewer_id="reviewer-b")

    store.retire(LESSON_ID, 1, actor_id="lead", reason="obsolete")
    with pytest.raises(LessonStateError):
        store.retire(LESSON_ID, 1, actor_id="lead", reason="twice")


def test_status_and_assignment_history_are_append_only(tmp_path):
    store = LessonStore(tmp_path / "lessons")
    _candidate(store, designated_reviewer_id=None)
    _verify_events_before = len(store.history(LESSON_ID, 1))
    store.assign_reviewer(LESSON_ID, 1, designated_reviewer_id=REVIEWER, assigned_by="lead")
    assert store.get_version(LESSON_ID, 1)["status"] == "CANDIDATE"
    assert len(store.history(LESSON_ID, 1)) == _verify_events_before

    event_files = sorted((store.root / LESSON_ID / "events" / "v0001").glob("*.json"))
    event_bytes = [path.read_bytes() for path in event_files]

    verified = _verify(store)
    assert verified["status"] == "VERIFIED"
    retired = store.retire(LESSON_ID, 1, actor_id="lead", reason="obsolete")
    assert retired["status"] == "RETIRED"

    assert [path.read_bytes() for path in event_files] == event_bytes
    assert [event["event_type"] for event in store.history(LESSON_ID, 1)] == ["CREATED", "VERIFIED", "RETIRED"]
    assert [event["to_status"] for event in store.history(LESSON_ID, 1)] == ["CANDIDATE", "VERIFIED", "RETIRED"]


def test_default_root_uses_the_application_data_directory():
    from app.core.config import get_settings

    assert LessonStore().root == (get_settings().data_dir / "lessons").resolve()


def test_candidate_can_be_created_without_an_explicit_lesson_id(tmp_path):
    store = LessonStore(tmp_path / "lessons")
    created = store.create_candidate(
        error_class="MISSING_DIMENSION",
        trigger_conditions=["dimension gap"],
        required_checks=["recompute the chain"],
    )

    assert created["lesson_id"].startswith("LSN-")
    assert (store.root / created["lesson_id"] / "versions" / "v0001.json").is_file()
