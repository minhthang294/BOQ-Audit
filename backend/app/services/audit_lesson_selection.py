"""Deterministic, project-scoped selection of reviewed lessons for one run."""
from __future__ import annotations

import hashlib
import json
from typing import Any


class LessonSelectionError(ValueError):
    """Lesson applicability evidence is malformed or ambiguous."""


def lesson_memory_case_id(lesson_id: str, version: int) -> str:
    return f"MEM-{lesson_id}-v{version}"


def lesson_memory_source(lesson: dict[str, Any]) -> str:
    return f"lesson:{lesson['lesson_id']}:v{lesson['version']}:{lesson['record_digest']}"


def _canonical_digest(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise LessonSelectionError("lesson selection input must be finite JSON data") from exc
    return hashlib.sha256(raw).hexdigest()


def validate_lesson_snapshot(snapshot: Any, *, project_scope: str, expected_work_items: Any = None) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "schema_version", "project_scope", "work_items_digest", "dispositions",
        "blocking_lesson_ids", "selection_digest",
    }:
        raise LessonSelectionError("lesson selection snapshot shape is invalid")
    body = {key: value for key, value in snapshot.items() if key != "selection_digest"}
    if (
        snapshot.get("schema_version") != 1
        or snapshot.get("project_scope") != project_scope
        or (expected_work_items is not None and snapshot.get("work_items_digest") != _canonical_digest(expected_work_items))
        or snapshot.get("selection_digest") != _canonical_digest(body)
    ):
        raise LessonSelectionError("lesson selection snapshot digest mismatch or scope mismatch")
    if not isinstance(snapshot.get("dispositions"), list) or not isinstance(snapshot.get("blocking_lesson_ids"), list):
        raise LessonSelectionError("lesson selection snapshot lists are invalid")
    lessons = snapshot["dispositions"]
    if any(
        not isinstance(item, dict)
        or item.get("disposition") not in {"SELECTED", "PENDING_REVIEW", "UNRESOLVED", "NOT_APPLICABLE", "REJECTED", "RETIRED"}
        or not isinstance(item.get("matched_check_ids"), list)
        or not isinstance(item.get("matched_target_ids"), list)
        for item in lessons
    ):
        raise LessonSelectionError("lesson selection disposition is invalid")
    expected_blockers = sorted(item["lesson_id"] for item in lessons if item["disposition"] in {"PENDING_REVIEW", "UNRESOLVED"})
    if snapshot["blocking_lesson_ids"] != expected_blockers:
        raise LessonSelectionError("lesson selection blocker list is inconsistent")
    return snapshot


def load_current_project_lessons(store: Any, project_scope: str) -> list[dict[str, Any]]:
    current = []
    for lesson_id in store.lesson_ids():
        versions = store.list_versions(lesson_id)
        if versions and versions[-1].get("project_scope") == project_scope:
            current.append(versions[-1])
    return current


def select_lessons(*, project_scope: str, expected_work_items: Any, current_lessons: Any) -> dict[str, Any]:
    """Match exact check kinds/target IDs; free text never drives applicability.

    Only lessons explicitly scoped to this job are considered. A matching
    candidate is unresolved and must be reviewed before it can be ignored.
    """
    if not isinstance(project_scope, str) or not project_scope.strip():
        raise LessonSelectionError("project_scope must be a non-empty exact identifier")
    if not isinstance(expected_work_items, list) or not expected_work_items:
        raise LessonSelectionError("expected work items must be non-empty")
    work_by_id: dict[str, dict[str, Any]] = {}
    for item in expected_work_items:
        if not isinstance(item, dict):
            raise LessonSelectionError("expected work item must be an object")
        check_id = item.get("check_id")
        check_kind = item.get("check_kind")
        target_ids = item.get("target_ids", [])
        if (
            not isinstance(check_id, str) or not check_id.strip() or check_id in work_by_id
            or not isinstance(check_kind, str) or not check_kind.strip()
            or not isinstance(target_ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in target_ids)
            or len(target_ids) != len(set(target_ids))
        ):
            raise LessonSelectionError("expected work item applicability scope is invalid")
        work_by_id[check_id] = item
    if not isinstance(current_lessons, list):
        raise LessonSelectionError("current lessons must be a list")

    dispositions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lesson in current_lessons:
        if not isinstance(lesson, dict):
            raise LessonSelectionError("lesson record must be an object")
        lesson_id, version = lesson.get("lesson_id"), lesson.get("version")
        scope, state = lesson.get("project_scope"), lesson.get("status")
        if not isinstance(scope, str) or not isinstance(lesson_id, str) or not lesson_id.strip():
            raise LessonSelectionError("lesson identity or scope is invalid")
        if scope != project_scope:
            continue
        if isinstance(version, bool) or not isinstance(version, int) or version < 1 or lesson_id in seen:
            raise LessonSelectionError("lesson version must be current and unique")
        seen.add(lesson_id)
        if state not in {"CANDIDATE", "VERIFIED", "REJECTED", "RETIRED"}:
            raise LessonSelectionError("lesson lifecycle status is invalid")
        kinds, targets = lesson.get("applicability_check_kinds"), lesson.get("applicability_target_ids")
        if (
            not isinstance(kinds, list) or not isinstance(targets, list)
            or any(not isinstance(value, str) or not value.strip() for value in [*kinds, *targets])
            or len(kinds) != len(set(kinds)) or len(targets) != len(set(targets))
        ):
            raise LessonSelectionError("lesson applicability selectors are invalid")
        if not kinds and not targets:
            disposition, matched = "UNRESOLVED", []
        else:
            matched = []
            for check_id, item in work_by_id.items():
                kind_match = not kinds or item["check_kind"] in kinds
                target_match = not targets or bool(set(targets).intersection(item.get("target_ids", [])))
                if kind_match and target_match:
                    matched.append(check_id)
            if not matched:
                disposition = "NOT_APPLICABLE"
            elif state == "VERIFIED":
                disposition = "SELECTED"
            elif state == "CANDIDATE":
                disposition = "PENDING_REVIEW"
            else:
                disposition = state
        matched_targets = sorted({
            target_id
            for check_id in matched
            for target_id in work_by_id[check_id].get("target_ids", [])
            if not targets or target_id in targets
        })
        dispositions.append({
            "lesson_id": lesson_id,
            "version": version,
            "record_digest": _canonical_digest(lesson),
            "disposition": disposition,
            "matched_check_ids": sorted(matched),
            "matched_target_ids": matched_targets,
            "reason": "exact applicability selector matched approved work items" if matched else "no exact applicability match" if disposition == "NOT_APPLICABLE" else "applicability selectors are missing",
            "lesson": {
                key: lesson[key]
                for key in (
                    "error_class", "applicability_check_kinds", "applicability_target_ids",
                    "trigger_conditions", "required_checks", "counterexamples",
                )
                if key in lesson
            } if disposition == "SELECTED" else None,
        })
    dispositions.sort(key=lambda item: (item["lesson_id"], item["version"]))
    blockers = [item["lesson_id"] for item in dispositions if item["disposition"] in {"PENDING_REVIEW", "UNRESOLVED"}]
    snapshot = {
        "schema_version": 1,
        "project_scope": project_scope,
        "work_items_digest": _canonical_digest(expected_work_items),
        "dispositions": dispositions,
        "blocking_lesson_ids": blockers,
    }
    snapshot["selection_digest"] = _canonical_digest(snapshot)
    return validate_lesson_snapshot(snapshot, project_scope=project_scope, expected_work_items=expected_work_items)


def lessons_for_batch(snapshot: Any, batch_check_ids: Any) -> list[dict[str, Any]]:
    """Return only selected lessons applicable to at least one assigned item."""
    if not isinstance(snapshot, dict) or not isinstance(batch_check_ids, list) or not batch_check_ids:
        raise LessonSelectionError("lesson snapshot and assigned batch are required")
    validate_lesson_snapshot(snapshot, project_scope=snapshot.get("project_scope"))
    if any(not isinstance(value, str) or not value.strip() for value in batch_check_ids):
        raise LessonSelectionError("batch check IDs are invalid")
    selected = []
    for item in snapshot.get("dispositions", []):
        if not isinstance(item, dict) or item.get("disposition") != "SELECTED":
            continue
        relevant = sorted(set(item.get("matched_check_ids", [])) & set(batch_check_ids))
        if relevant:
            selected.append({
                "lesson_id": item["lesson_id"], "version": item["version"],
                "record_digest": item["record_digest"], "check_ids": relevant,
                "case_id": lesson_memory_case_id(item["lesson_id"], item["version"]),
                "memory_source": lesson_memory_source(item),
                **(item.get("lesson") or {}),
            })
    return selected


def lessons_for_pass3(snapshot: Any, batch_check_ids: Any, batch_target_ids: Any) -> list[dict[str, Any]]:
    """Carry selected lessons forward to Pass 3 for matching physical targets."""
    if not isinstance(batch_target_ids, list) or any(not isinstance(value, str) or not value.strip() for value in batch_target_ids):
        raise LessonSelectionError("Pass 3 target IDs are invalid")
    targets = set(batch_target_ids)
    selected = {item["lesson_id"]: item for item in lessons_for_batch(snapshot, batch_check_ids)}
    for item in snapshot["dispositions"]:
        if item["disposition"] != "SELECTED" or not targets.intersection(item["matched_target_ids"]):
            continue
        lesson = selected.get(item["lesson_id"])
        if lesson is None:
            lesson = {
                "lesson_id": item["lesson_id"], "version": item["version"],
                "record_digest": item["record_digest"], "check_ids": [],
                "case_id": lesson_memory_case_id(item["lesson_id"], item["version"]),
                "memory_source": lesson_memory_source(item), **(item.get("lesson") or {}),
            }
            selected[item["lesson_id"]] = lesson
        lesson["target_ids"] = sorted(targets.intersection(item["matched_target_ids"]))
    return [selected[key] for key in sorted(selected)]
