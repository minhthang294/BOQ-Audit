"""Versioned JSON lesson store for approved BOQ audit lessons.

Layout (under an injectable root, default ``Settings.data_dir / "lessons"``)::

    <root>/<lesson_id>/versions/v0001.json
    <root>/<lesson_id>/events/v0001/000001.json
    <root>/<lesson_id>/assignments/v0001/000001.json

Version files are immutable content records. Status transitions and reviewer
assignments are append-only events; a lesson record is the version content
projected with the latest event. The latest version is selectable only while its
latest event is ``VERIFIED``; retired, rejected, candidate, or superseded-latest
versions are never returned as current.

The store has no API, database, or chat-import dependency. A candidate must have
a recorded designated reviewer, and the acting reviewer must match that
assignment before a version can be verified or rejected.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.core.config import get_settings

LESSON_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VERSION_FILE_RE = re.compile(r"^v(\d{4,})\.json$")
EVENT_FILE_RE = re.compile(r"^(\d{6,})\.json$")
STATUSES = ("CANDIDATE", "VERIFIED", "RETIRED", "REJECTED")
REVIEWABLE_FROM = ("CANDIDATE",)
REJECTABLE_FROM = ("CANDIDATE", "VERIFIED")


class LessonStoreError(ValueError):
    """Base error for lesson store validation and state failures."""


class LessonNotFound(LessonStoreError):
    """The requested lesson or version does not exist."""


class LessonConflict(LessonStoreError):
    """An immutable record or event already exists."""


class LessonStateError(LessonStoreError):
    """The requested lifecycle transition is not allowed from the current state."""


class LessonAuthorizationError(LessonStoreError):
    """The acting reviewer is not the explicitly designated reviewer."""


class LessonCorrupt(LessonStoreError):
    """Stored JSON is missing or malformed."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise LessonStoreError(f"{field} must be a string")
    cleaned = value.strip()
    if required and not cleaned:
        raise LessonStoreError(f"{field} must not be empty")
    return cleaned


def _text_list(value: Any, field: str, *, required: bool = False) -> list[str]:
    if value is None:
        values: Iterable[Any] = ()
    elif isinstance(value, str):
        raise LessonStoreError(f"{field} must be a list of strings")
    else:
        try:
            values = list(value)
        except TypeError as exc:
            raise LessonStoreError(f"{field} must be a list of strings") from exc
    cleaned: list[str] = []
    for item in values:
        text = _text(item, field)
        if text not in cleaned:
            cleaned.append(text)
    if required and not cleaned:
        raise LessonStoreError(f"{field} must contain at least one item")
    return cleaned


def _atomic_write_once(path: Path, payload: dict[str, Any]) -> None:
    """Create ``path`` once with JSON content; never overwrite an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise LessonConflict(f"immutable record already exists: {path.name}") from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class LessonStore:
    """Filesystem-backed, append-only version store for audit lessons."""

    def __init__(self, root: Path | str | None = None):
        base = Path(root).expanduser() if root is not None else get_settings().data_dir / "lessons"
        self.root = base.resolve()

    # ---- read side -----------------------------------------------------
    def _lesson_dir(self, lesson_id: str) -> Path:
        lesson_id = _text(lesson_id, "lesson_id")
        if not LESSON_ID_RE.fullmatch(lesson_id):
            raise LessonStoreError("lesson_id contains unsupported characters")
        return self.root / lesson_id

    def _version_paths(self, lesson_id: str) -> list[tuple[int, Path]]:
        directory = self._lesson_dir(lesson_id) / "versions"
        if not directory.is_dir():
            return []
        found: list[tuple[int, Path]] = []
        for path in directory.iterdir():
            match = VERSION_FILE_RE.fullmatch(path.name)
            if match and path.is_file():
                found.append((int(match.group(1)), path))
        return sorted(found)

    def lesson_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            path.name for path in self.root.iterdir()
            if not path.is_symlink() and path.is_dir() and LESSON_ID_RE.fullmatch(path.name)
        )

    def _event_paths(self, lesson_id: str, version: int) -> list[tuple[int, Path]]:
        directory = self._lesson_dir(lesson_id) / "events" / f"v{version:04d}"
        if not directory.is_dir():
            return []
        found: list[tuple[int, Path]] = []
        for path in directory.iterdir():
            match = EVENT_FILE_RE.fullmatch(path.name)
            if match and path.is_file():
                found.append((int(match.group(1)), path))
        return sorted(found)

    def _assignment_paths(self, lesson_id: str, version: int) -> list[tuple[int, Path]]:
        directory = self._lesson_dir(lesson_id) / "assignments" / f"v{version:04d}"
        if not directory.is_dir():
            return []
        found: list[tuple[int, Path]] = []
        for path in directory.iterdir():
            match = EVENT_FILE_RE.fullmatch(path.name)
            if match and path.is_file():
                found.append((int(match.group(1)), path))
        return sorted(found)

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LessonCorrupt(f"cannot read immutable record {path.name}: {exc}") from exc

    def _events(self, lesson_id: str, version: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for sequence, path in self._event_paths(lesson_id, version):
            event = self._read_json(path)
            if not isinstance(event, dict) or event.get("event_seq") != sequence:
                raise LessonCorrupt(f"invalid event record {path.name}")
            events.append(event)
        return events

    def _assignments(self, lesson_id: str, version: int) -> list[dict[str, Any]]:
        assignments: list[dict[str, Any]] = []
        for sequence, path in self._assignment_paths(lesson_id, version):
            assignment = self._read_json(path)
            if not isinstance(assignment, dict) or assignment.get("event_seq") != sequence:
                raise LessonCorrupt(f"invalid reviewer assignment {path.name}")
            assignments.append(assignment)
        return assignments

    def _project(self, lesson_id: str, version: int, content: dict[str, Any]) -> dict[str, Any]:
        if content.get("lesson_id") != lesson_id or content.get("version") != version:
            raise LessonCorrupt(f"version record does not match lesson {lesson_id} version {version}")
        events = self._events(lesson_id, version)
        if not events:
            raise LessonCorrupt(f"version {version} has no lifecycle event")
        assignments = self._assignments(lesson_id, version)
        latest = events[-1]
        status = latest.get("to_status")
        if status not in STATUSES:
            raise LessonCorrupt(f"version {version} has invalid status {status!r}")
        review_event = next(
            (event for event in reversed(events) if event.get("event_type") in {"VERIFIED", "REJECTED"}),
            None,
        )
        record = dict(content)
        record["status"] = status
        record["designated_reviewer_id"] = assignments[-1].get("designated_reviewer_id") if assignments else None
        record["reviewer_id"] = review_event.get("reviewer_id") if review_event else None
        record["reviewed_at"] = review_event.get("occurred_at") if review_event else None
        record["review_evidence_refs"] = list(review_event.get("evidence_refs") or []) if review_event else []
        record["review_reason"] = (review_event.get("reason") or "") if review_event else ""
        return record

    def get_version(self, lesson_id: str, version: int) -> dict[str, Any]:
        path = self._lesson_dir(lesson_id) / "versions" / f"v{int(version):04d}.json"
        if not path.is_file():
            raise LessonNotFound(f"lesson {lesson_id} version {version} does not exist")
        content = self._read_json(path)
        if not isinstance(content, dict):
            raise LessonCorrupt(f"invalid version record {path.name}")
        return self._project(lesson_id, int(version), content)

    def list_versions(self, lesson_id: str) -> list[dict[str, Any]]:
        return [self.get_version(lesson_id, version) for version, _ in self._version_paths(lesson_id)]

    def select_current(self, lesson_id: str) -> dict[str, Any] | None:
        """Return the latest version only when it is currently VERIFIED.

        Older VERIFIED versions are not used as a fallback after a newer version
        is candidate, rejected, or retired. This prevents silently selecting a
        superseded lesson.
        """
        versions = self.list_versions(lesson_id)
        if not versions:
            return None
        latest = versions[-1]
        return latest if latest["status"] == "VERIFIED" else None

    def history(self, lesson_id: str, version: int) -> list[dict[str, Any]]:
        return self._events(lesson_id, int(version))

    def assignment_history(self, lesson_id: str, version: int) -> list[dict[str, Any]]:
        return self._assignments(lesson_id, int(version))

    # ---- write side ----------------------------------------------------
    def _append_assignment(
        self,
        *,
        lesson_id: str,
        version: int,
        designated_reviewer_id: str,
        assigned_by: str,
    ) -> dict[str, Any]:
        assignments = self._assignment_paths(lesson_id, version)
        sequence = (assignments[-1][0] + 1) if assignments else 1
        payload = {
            "event_seq": sequence,
            "lesson_id": lesson_id,
            "version": version,
            "event_type": "REVIEWER_ASSIGNED",
            "designated_reviewer_id": designated_reviewer_id,
            "assigned_by": assigned_by,
            "occurred_at": _utcnow(),
        }
        _atomic_write_once(
            self._lesson_dir(lesson_id) / "assignments" / f"v{version:04d}" / f"{sequence:06d}.json",
            payload,
        )
        return payload

    def _append_event(
        self,
        *,
        lesson_id: str,
        version: int,
        event_type: str,
        from_status: str | None,
        to_status: str,
        actor_id: str,
        reviewer_id: str | None = None,
        designated_reviewer_id: str | None = None,
        evidence_refs: list[str] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        events = self._event_paths(lesson_id, version)
        sequence = (events[-1][0] + 1) if events else 1
        payload = {
            "event_seq": sequence,
            "lesson_id": lesson_id,
            "version": version,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "actor_id": actor_id,
            "reviewer_id": reviewer_id,
            "designated_reviewer_id": designated_reviewer_id,
            "evidence_refs": list(evidence_refs or []),
            "reason": reason,
            "occurred_at": _utcnow(),
        }
        _atomic_write_once(
            self._lesson_dir(lesson_id) / "events" / f"v{version:04d}" / f"{sequence:06d}.json",
            payload,
        )
        return payload

    def create_candidate(
        self,
        *,
        error_class: str,
        applicability_check_kinds: Iterable[str],
        applicability_target_ids: Iterable[str] | None = None,
        trigger_conditions: Iterable[str],
        required_checks: Iterable[str],
        counterexamples: Iterable[str] | None = None,
        source_case_ids: Iterable[str] | None = None,
        evidence_refs: Iterable[str] | None = None,
        project_scope: str = "unscoped",
        visibility_scope: str = "private",
        supersedes: int | None = None,
        lesson_id: str | None = None,
        actor_id: str = "system",
        designated_reviewer_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a CANDIDATE version; later versions must supersede the latest.

        ``designated_reviewer_id`` is optional: a candidate without a reviewer
        assignment remains parked and cannot be verified or rejected.
        """
        lesson_id = _text(lesson_id, "lesson_id") if lesson_id is not None else f"LSN-{uuid.uuid4().hex[:12].upper()}"
        if not LESSON_ID_RE.fullmatch(lesson_id):
            raise LessonStoreError("lesson_id contains unsupported characters")
        creator = _text(actor_id, "actor_id")
        designated = (
            _text(designated_reviewer_id, "designated_reviewer_id")
            if designated_reviewer_id is not None
            else None
        )
        if supersedes is not None and (
            isinstance(supersedes, bool) or not isinstance(supersedes, int) or supersedes < 1
        ):
            raise LessonStoreError("supersedes must be a positive integer version")
        existing = self._version_paths(lesson_id)
        if existing:
            if supersedes is None:
                raise LessonStateError("a new version must declare supersedes=latest version")
            latest_version = existing[-1][0]
            if supersedes != latest_version:
                raise LessonStateError("supersedes must reference the latest version")
            version = latest_version + 1
        else:
            if supersedes is not None:
                raise LessonStateError("supersedes cannot reference a lesson with no prior version")
            version = 1

        content = {
            "lesson_id": lesson_id,
            "version": version,
            "created_at": _utcnow(),
            "supersedes": supersedes,
            "error_class": _text(error_class, "error_class"),
            "applicability_check_kinds": _text_list(applicability_check_kinds, "applicability_check_kinds"),
            "applicability_target_ids": _text_list(applicability_target_ids, "applicability_target_ids"),
            "trigger_conditions": _text_list(trigger_conditions, "trigger_conditions", required=True),
            "required_checks": _text_list(required_checks, "required_checks", required=True),
            "counterexamples": _text_list(counterexamples, "counterexamples"),
            "source_case_ids": _text_list(source_case_ids, "source_case_ids"),
            "evidence_refs": _text_list(evidence_refs, "evidence_refs"),
            "project_scope": _text(project_scope, "project_scope"),
            "visibility_scope": _text(visibility_scope, "visibility_scope"),
        }
        if not content["applicability_check_kinds"] and not content["applicability_target_ids"]:
            raise LessonStoreError("at least one exact applicability check kind or target ID is required")
        path = self._lesson_dir(lesson_id) / "versions" / f"v{version:04d}.json"
        _atomic_write_once(path, content)
        self._append_event(
            lesson_id=lesson_id,
            version=version,
            event_type="CREATED",
            from_status=None,
            to_status="CANDIDATE",
            actor_id=creator,
            reason="candidate created",
        )
        if designated is not None:
            self._append_assignment(
                lesson_id=lesson_id,
                version=version,
                designated_reviewer_id=designated,
                assigned_by=creator,
            )
        return self.get_version(lesson_id, version)

    def assign_reviewer(
        self,
        lesson_id: str,
        version: int,
        *,
        designated_reviewer_id: str,
        assigned_by: str = "system",
    ) -> dict[str, Any]:
        """Record or replace the designated reviewer while a version is CANDIDATE."""
        record = self.get_version(lesson_id, version)
        if record["status"] != "CANDIDATE":
            raise LessonStateError(f"cannot assign a reviewer to a {record['status']} version")
        designated = _text(designated_reviewer_id, "designated_reviewer_id")
        actor = _text(assigned_by, "assigned_by")
        self._append_assignment(
            lesson_id=lesson_id,
            version=int(version),
            designated_reviewer_id=designated,
            assigned_by=actor,
        )
        return self.get_version(lesson_id, version)

    def _require_reviewer(
        self,
        reviewer_id: str,
        designated_reviewer_id: str,
        assigned_reviewer_id: str | None,
    ) -> str:
        reviewer = _text(reviewer_id, "reviewer_id")
        designated = _text(designated_reviewer_id, "designated_reviewer_id")
        if not assigned_reviewer_id:
            raise LessonAuthorizationError("no designated reviewer is assigned to this version")
        if reviewer != designated or reviewer != assigned_reviewer_id:
            raise LessonAuthorizationError("reviewer_id does not match the designated reviewer")
        return reviewer

    def _review(self, lesson_id: str, version: int, *, to_status: str, reviewer_id: str,
                designated_reviewer_id: str, evidence_refs: Iterable[str], reason: str) -> dict[str, Any]:
        record = self.get_version(lesson_id, version)
        allowed = REVIEWABLE_FROM if to_status == "VERIFIED" else REJECTABLE_FROM
        if record["status"] not in allowed:
            raise LessonStateError(f"cannot transition {record['status']} to {to_status}")
        reviewer = self._require_reviewer(
            reviewer_id,
            designated_reviewer_id,
            record.get("designated_reviewer_id"),
        )
        evidence = _text_list(evidence_refs, "evidence_refs", required=True)
        review_reason = _text(reason, "reason")
        self._append_event(
            lesson_id=lesson_id,
            version=int(version),
            event_type=to_status,
            from_status=record["status"],
            to_status=to_status,
            actor_id=reviewer,
            reviewer_id=reviewer,
            designated_reviewer_id=_text(designated_reviewer_id, "designated_reviewer_id"),
            evidence_refs=evidence,
            reason=review_reason,
        )
        return self.get_version(lesson_id, version)

    def verify(self, lesson_id: str, version: int, *, reviewer_id: str, designated_reviewer_id: str,
               evidence_refs: Iterable[str], reason: str) -> dict[str, Any]:
        return self._review(
            lesson_id, version, to_status="VERIFIED", reviewer_id=reviewer_id,
            designated_reviewer_id=designated_reviewer_id, evidence_refs=evidence_refs, reason=reason,
        )

    def reject(self, lesson_id: str, version: int, *, reviewer_id: str, designated_reviewer_id: str,
               evidence_refs: Iterable[str], reason: str) -> dict[str, Any]:
        return self._review(
            lesson_id, version, to_status="REJECTED", reviewer_id=reviewer_id,
            designated_reviewer_id=designated_reviewer_id, evidence_refs=evidence_refs, reason=reason,
        )

    def retire(self, lesson_id: str, version: int, *, actor_id: str, reason: str) -> dict[str, Any]:
        record = self.get_version(lesson_id, version)
        if record["status"] != "VERIFIED":
            raise LessonStateError(f"cannot retire a {record['status']} version")
        actor = _text(actor_id, "actor_id")
        retired_reason = _text(reason, "reason")
        self._append_event(
            lesson_id=lesson_id,
            version=int(version),
            event_type="RETIRED",
            from_status="VERIFIED",
            to_status="RETIRED",
            actor_id=actor,
            reason=retired_reason,
        )
        return self.get_version(lesson_id, version)
