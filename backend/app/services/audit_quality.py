"""Trusted filesystem snapshot and validator adapter for BOQ audit quality.

Q01a builds immutable snapshots. Q01b verifies the pinned validator bundle,
preflights strict JSON, runs the pinned validator against a private staging
copy, parses only its fresh report, rechecks the snapshot, and atomically writes
a controller-owned receipt. This module does not touch the database/API/runner,
publish reports, or enforce process isolation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

SNAPSHOT_MANIFEST_NAME = "manifest.json"
SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_SIZE = 1024 * 1024

VALIDATOR_STAGES = ("pass1", "pass2", "pass3", "release")
VALIDATOR_TIMEOUT_SECONDS = 60.0
DIAGNOSTIC_LIMIT_BYTES = 64 * 1024
VALIDATOR_RELATIVE_PATH = "scripts/validate_run.py"
SKILL_MANIFEST_ALGORITHM = "sha256(canonical JSON path/hash list)"
# Contract-pinned v6.4 bundle digest. Never derive this from the manifest under test.
TRUSTED_SKILL_DIGEST = "17c14f63f78c2670428e233c4bfc56623473d1d644cf38d003db477d80c14a2c"
_MAX_SKILL_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_JSON_BYTES = 16 * 1024 * 1024

STAGE_COMPLETE_STATUS = "METADATA_STAGE_COMPLETE"
RELEASE_READY_STATUS = "READY_FOR_ENGINEER_REVIEW"
RELEASE_PARTIAL_STATUS = "PARTIAL_WITH_OPEN_ITEMS_REVIEW_REQUIRED"
BLOCKED_STATUS = "BLOCKED"

_ALLOWED_EXIT_STATUSES: dict[str, dict[int, frozenset[str]]] = {
    "pass1": {0: frozenset({STAGE_COMPLETE_STATUS}), 2: frozenset({BLOCKED_STATUS})},
    "pass2": {0: frozenset({STAGE_COMPLETE_STATUS}), 2: frozenset({BLOCKED_STATUS})},
    "pass3": {0: frozenset({STAGE_COMPLETE_STATUS}), 2: frozenset({BLOCKED_STATUS})},
    "release": {
        0: frozenset({RELEASE_READY_STATUS, RELEASE_PARTIAL_STATUS}),
        2: frozenset({BLOCKED_STATUS}),
    },
}


class SnapshotError(RuntimeError):
    """Base error for an unsafe or failed snapshot."""

    code = "SNAPSHOT_ERROR"


class UnsafePathError(SnapshotError):
    """A path is absolute, traversing, a symlink, or a special file."""

    code = "UNSAFE_PATH"


class SnapshotLimitError(SnapshotError):
    """A file/count/byte limit would be exceeded."""

    code = "SNAPSHOT_LIMIT"


class SourceChangedError(SnapshotError):
    """The source changed while the snapshot was being copied."""

    code = "SOURCE_CHANGED"


class QualityAdapterError(RuntimeError):
    """Base error for the trusted validator adapter."""

    code = "QUALITY_ADAPTER_ERROR"


class StrictJsonError(QualityAdapterError):
    """JSON is malformed, has duplicate keys, or contains non-finite values."""

    code = "JSON_INVALID"


class BundleVerificationError(QualityAdapterError):
    """The pinned bundle or its manifest is missing, altered, or inconsistent."""

    code = "BUNDLE_INVALID"


class InputPreflightError(QualityAdapterError):
    """Snapshot input JSON is malformed or has a mismatched checkpoint stage."""

    code = "INPUT_PREFLIGHT"


class ValidatorProcessError(QualityAdapterError):
    """The validator could not be started, timed out, or exited unexpectedly."""

    code = "VALIDATOR_PROCESS"


class ReportValidationError(QualityAdapterError):
    """The fresh validator report is missing or violates its contract."""

    code = "REPORT_INVALID"


class SnapshotIntegrityError(SnapshotError):
    """The snapshot changed, or its manifest no longer matches its files."""

    code = "SNAPSHOT_INTEGRITY"


class ReceiptError(QualityAdapterError):
    """A controller-owned receipt could not be written safely."""

    code = "RECEIPT_ERROR"


class ReceiptExistsError(ReceiptError):
    """A receipt already exists for this immutable snapshot/stage."""

    code = "RECEIPT_EXISTS"


@dataclass(frozen=True, slots=True)
class SnapshotLimits:
    max_files: int = 10_000
    max_work_file_bytes: int = 16 * 1024 * 1024
    max_output_file_bytes: int = 256 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in ("max_files", "max_work_file_bytes", "max_output_file_bytes", "max_total_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        validate_relative_path(self.path)
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise SnapshotError("manifest sha256 must be 64 lowercase hex characters")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise SnapshotError("manifest size must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class Snapshot:
    snapshot_id: str
    path: Path
    work_dir: Path
    output_dir: Path
    manifest: tuple[ManifestEntry, ...]
    manifest_digest: str
    input_digest: str
    output_digest: str

    @property
    def manifest_path(self) -> Path:
        return self.path / SNAPSHOT_MANIFEST_NAME


@dataclass(frozen=True, slots=True)
class PinnedBundle:
    """A validator bundle verified against its pinned manifest."""

    root: Path
    manifest_path: Path
    digest: str
    validator_path: Path
    validator_sha256: str


@dataclass(frozen=True, slots=True)
class ValidatorReceipt:
    """The exact Q01b receipt contract, serialized without adapter metadata."""

    schema_version: int
    run_id: int
    snapshot_id: str
    stage: str
    source_digest: str
    skill_digest: str | None
    input_digest: str
    validator_exit_code: int | None
    candidate_release_status: str | None
    blocking_checks: tuple[str, ...]
    open_record_count: int | None
    validated_at: str
    controller_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "stage": self.stage,
            "source_digest": self.source_digest,
            "skill_digest": self.skill_digest,
            "input_digest": self.input_digest,
            "validator_exit_code": self.validator_exit_code,
            "candidate_release_status": self.candidate_release_status,
            "blocking_checks": list(self.blocking_checks),
            "open_record_count": self.open_record_count,
            "validated_at": self.validated_at,
            "controller_status": self.controller_status,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of one adapter run; diagnostics are bounded and never in receipts."""

    receipt: ValidatorReceipt
    receipt_path: Path
    report: dict[str, Any] | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error_code: str | None = None
    error_message: str | None = None

    @property
    def controller_status(self) -> str:
        return self.receipt.controller_status


@dataclass(frozen=True, slots=True)
class _SourceFile:
    source: Path
    relative: str
    stat: os.stat_result


@dataclass(slots=True)
class _Budget:
    files: int = 0
    total_bytes: int = 0


@dataclass(slots=True)
class _CapturedStream:
    data: bytes = b""
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool = False
    spawn_error: str | None = None


def validate_relative_path(path: str) -> str:
    """Return a canonical POSIX relative path or raise ``UnsafePathError``."""
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        raise UnsafePathError(f"invalid relative path: {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", path):
        raise UnsafePathError(f"absolute path is not allowed: {path!r}")
    if not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise UnsafePathError(f"traversing path is not allowed: {path!r}")
    if pure.as_posix() != path:
        raise UnsafePathError(f"non-canonical path: {path!r}")
    return path


def canonical_manifest_bytes(entries: Iterable[ManifestEntry]) -> bytes:
    """Serialize manifest entries deterministically as UTF-8 JSON."""
    ordered = sorted(tuple(entries), key=lambda entry: entry.path)
    seen: set[str] = set()
    payload: list[dict[str, object]] = []
    for entry in ordered:
        if entry.path in seen:
            raise SnapshotError(f"duplicate manifest path: {entry.path}")
        seen.add(entry.path)
        payload.append({"path": entry.path, "sha256": entry.sha256, "size": entry.size})
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_manifest_digest(entries: Iterable[ManifestEntry]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(entries)).hexdigest()


def validate_snapshot_id(snapshot_id: str) -> str:
    if not isinstance(snapshot_id, str) or not SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        raise UnsafePathError("snapshot_id must be a safe generated identifier")
    return snapshot_id


def create_snapshot(
    source_attempt: Path,
    controller_root: Path,
    *,
    allowed_source_root: Path,
    snapshot_id: str | None = None,
    limits: SnapshotLimits | None = None,
) -> Snapshot:
    """Copy ``<source_attempt>/work`` and ``output`` into an atomic snapshot.

    The final layout is ``<controller_root>/snapshots/<snapshot_id>/work`` with
    sibling ``output``. All files are copied from regular source files only;
    symlinks, special files, traversal, and source mutation are rejected.

    ``allowed_source_root`` must be the exact trusted attempt directory (not the
    whole jobs root), and ``controller_root`` must be disjoint from the attempt
    tree. ``snapshot_id`` is controller-only; leave it unset to use a fresh
    random ID and never take it from a model-authored manifest.
    """
    limits = limits or SnapshotLimits()
    if snapshot_id is not None:
        validate_snapshot_id(snapshot_id)
    else:
        snapshot_id = uuid4().hex

    source_attempt = _absolute(source_attempt)
    trusted_root = _absolute(allowed_source_root)
    _require_directory(trusted_root, "allowed_source_root")
    _require_directory(source_attempt, "source_attempt")
    if source_attempt != trusted_root:
        raise UnsafePathError("source_attempt must equal allowed_source_root")

    source_work = _require_directory(source_attempt / "work", "source work")
    source_output = _require_directory(source_attempt / "output", "source output")

    controller_root = _absolute(controller_root)
    if _is_relative_or_equal(controller_root, source_attempt) or _is_relative_or_equal(source_attempt, controller_root):
        raise UnsafePathError("controller_root must not overlap source_attempt")
    _check_ancestors(controller_root, must_exist=False)
    controller_root.mkdir(parents=True, exist_ok=True)
    _require_directory(controller_root, "controller_root")

    snapshots_root = controller_root / "snapshots"
    _check_ancestors(snapshots_root, must_exist=False)
    snapshots_root.mkdir(parents=True, exist_ok=True)
    _require_directory(snapshots_root, "snapshots root")

    final_path = snapshots_root / snapshot_id
    if final_path.exists() or final_path.is_symlink():
        raise SnapshotError(f"snapshot already exists: {snapshot_id}")

    staging = snapshots_root / f".staging-{uuid4().hex}"
    os.mkdir(staging, 0o700)
    try:
        os.mkdir(staging / "work", 0o700)
        os.mkdir(staging / "output", 0o700)

        budget = _Budget()
        source_files = _scan_tree(source_work, "work", limits, budget)
        source_files += _scan_tree(source_output, "output", limits, budget)
        source_files.sort(key=lambda item: item.relative)
        before = _fingerprint(source_files)

        entries: list[ManifestEntry] = []
        for item in source_files:
            destination = staging / item.relative
            _assert_within(destination, staging)
            destination.parent.mkdir(parents=True, exist_ok=True)
            max_file_bytes = _max_file_bytes_for(item.relative, limits)
            digest, size = _copy_regular_file(item, destination, max_file_bytes)
            entries.append(ManifestEntry(item.relative, digest, size))

        after_budget = _Budget()
        after_files = _scan_tree(source_work, "work", limits, after_budget)
        after_files += _scan_tree(source_output, "output", limits, after_budget)
        after_files.sort(key=lambda item: item.relative)
        if _fingerprint(after_files) != before:
            raise SourceChangedError("source changed while creating snapshot")

        manifest = tuple(sorted(entries, key=lambda entry: entry.path))
        manifest_bytes = canonical_manifest_bytes(manifest)
        manifest_path = staging / SNAPSHOT_MANIFEST_NAME
        with manifest_path.open("xb") as manifest_file:
            manifest_file.write(manifest_bytes)
            manifest_file.flush()
            os.fsync(manifest_file.fileno())

        if final_path.exists() or final_path.is_symlink():
            raise SnapshotError(f"snapshot already exists: {snapshot_id}")
        os.rename(staging, final_path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return Snapshot(
        snapshot_id=snapshot_id,
        path=final_path,
        work_dir=final_path / "work",
        output_dir=final_path / "output",
        manifest=manifest,
        manifest_digest=canonical_manifest_digest(manifest),
        input_digest=canonical_manifest_digest(entry for entry in manifest if entry.path.startswith("work/")),
        output_digest=canonical_manifest_digest(entry for entry in manifest if entry.path.startswith("output/")),
    )


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_relative_or_equal(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _assert_within(path: Path, root: Path) -> None:
    if not _is_relative_or_equal(_absolute(path), _absolute(root)):
        raise UnsafePathError("path escapes its trusted root")


def _check_ancestors(path: Path, *, must_exist: bool) -> None:
    absolute = _absolute(path)
    parts = absolute.parts
    if not parts:
        raise UnsafePathError(f"invalid path: {path}")
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as exc:
            if must_exist:
                raise UnsafePathError(f"missing path component: {current}") from exc
            return
        except OSError as exc:
            raise UnsafePathError(f"cannot inspect path component: {current}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise UnsafePathError(f"symlink path component is not allowed: {current}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise UnsafePathError(f"non-directory path component: {current}")


def _require_directory(path: Path, label: str) -> Path:
    absolute = _absolute(path)
    _check_ancestors(absolute, must_exist=True)
    try:
        metadata = os.lstat(absolute)
    except OSError as exc:
        raise UnsafePathError(f"{label} is not accessible: {absolute}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise UnsafePathError(f"{label} is not a directory: {absolute}")
    return absolute


def _is_gate_report(name: str) -> bool:
    return name == "coverage_result.json" or (name.startswith("stage_") and name.endswith("_result.json"))


def _max_file_bytes_for(relative: str, limits: SnapshotLimits) -> int:
    if relative.startswith("output/"):
        return limits.max_output_file_bytes
    return limits.max_work_file_bytes


def _scan_tree(root: Path, prefix: str, limits: SnapshotLimits, budget: _Budget) -> list[_SourceFile]:
    files: list[_SourceFile] = []
    stack: list[tuple[Path, str]] = [(root, prefix)]
    while stack:
        directory, relative_dir = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise SnapshotError(f"cannot read source directory: {directory}") from exc
        for entry in entries:
            relative = f"{relative_dir}/{entry.name}" if relative_dir else entry.name
            validate_relative_path(relative)
            try:
                if entry.is_symlink():
                    raise UnsafePathError(f"symlink is not allowed: {entry.path}")
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SnapshotError(f"cannot inspect source entry: {entry.path}") from exc
            if stat.S_ISDIR(metadata.st_mode):
                stack.append((Path(entry.path), relative))
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise UnsafePathError(f"special file is not allowed: {entry.path}")
            if _is_gate_report(entry.name):
                continue
            if budget.files >= limits.max_files:
                raise SnapshotLimitError(f"snapshot exceeds max_files={limits.max_files}")
            max_file_bytes = _max_file_bytes_for(relative, limits)
            if metadata.st_size > max_file_bytes:
                raise SnapshotLimitError(f"file exceeds max_file_bytes={max_file_bytes}: {relative}")
            budget.files += 1
            budget.total_bytes += metadata.st_size
            if budget.total_bytes > limits.max_total_bytes:
                raise SnapshotLimitError(f"snapshot exceeds max_total_bytes={limits.max_total_bytes}")
            files.append(_SourceFile(Path(entry.path), relative, metadata))
    return files


def _fingerprint(files: Iterable[_SourceFile]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            item.relative,
            item.stat.st_dev,
            item.stat.st_ino,
            item.stat.st_mode,
            item.stat.st_size,
            item.stat.st_mtime_ns,
            item.stat.st_ctime_ns,
        )
        for item in files
    )


def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_mode == after.st_mode
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _copy_regular_file(item: _SourceFile, destination: Path, max_file_bytes: int) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(item.source, flags)
    except OSError as exc:
        raise SourceChangedError(f"cannot open source file safely: {item.relative}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not _same_file(item.stat, before):
            raise SourceChangedError(f"source file changed before copy: {item.relative}")
        digest = hashlib.sha256()
        size = 0
        with destination.open("xb") as target:
            while True:
                chunk = os.read(descriptor, _CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_file_bytes:
                    raise SnapshotLimitError(f"file exceeds max_file_bytes={max_file_bytes}: {item.relative}")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        after = os.fstat(descriptor)
        if size != item.stat.st_size or not _same_file(before, after):
            raise SourceChangedError(f"source file changed during copy: {item.relative}")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number: {value}")
    return number


def strict_json_loads(raw: bytes, *, label: str) -> object:
    """Parse strict UTF-8 JSON, rejecting duplicate keys and NaN/Infinity."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonError(f"{label}: JSON is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except (ValueError, TypeError, RecursionError) as exc:
        raise StrictJsonError(f"{label}: invalid JSON ({exc})") from exc


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _require_sha256(value: object, label: str) -> str:
    if not _is_sha256(value):
        raise ValueError(f"{label} must be 64 lowercase hex characters")
    return value


def _validate_stage(stage: object) -> str:
    if not isinstance(stage, str) or stage not in VALIDATOR_STAGES:
        raise ValueError(f"stage must be one of {VALIDATOR_STAGES}")
    return stage


def _validate_run_id(run_id: object) -> int:
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 0:
        raise ValueError("run_id must be a non-negative integer")
    return run_id


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("timeout_seconds must be a positive number")
    return float(value)


def _validate_diagnostic_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("diagnostic_limit_bytes must be a positive integer")
    return value


def _read_regular_file(
    path: Path,
    *,
    max_bytes: int,
    label: str,
    error_type: type[QualityAdapterError] | type[SnapshotError],
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise error_type(f"{label} is missing or unreadable: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise error_type(f"{label} is not a regular file: {path}")
        if before.st_size > max_bytes:
            raise error_type(f"{label} exceeds {max_bytes} bytes: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, _CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise error_type(f"{label} exceeds {max_bytes} bytes: {path}")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if total != before.st_size or not _same_file(before, after):
            raise error_type(f"{label} changed while being read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _hash_regular_file(
    path: Path,
    *,
    label: str,
    error_type: type[QualityAdapterError] | type[SnapshotError],
    max_bytes: int | None = None,
) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise error_type(f"{label} is missing or unreadable: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise error_type(f"{label} is not a regular file: {path}")
        if max_bytes is not None and before.st_size > max_bytes:
            raise error_type(f"{label} exceeds {max_bytes} bytes: {path}")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, _CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise error_type(f"{label} exceeds {max_bytes} bytes: {path}")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if size != before.st_size or not _same_file(before, after):
            raise error_type(f"{label} changed while being read: {path}")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _walk_regular_files(
    root: Path,
    *,
    label: str,
    error_type: type[QualityAdapterError] | type[SnapshotError],
    ignore_cache_dirs: bool = False,
) -> dict[str, os.stat_result]:
    """Return regular files by canonical relative path, rejecting links/specials."""
    root = _absolute(root)
    result: dict[str, os.stat_result] = {}
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise error_type(f"{label} directory is unreadable: {directory}") from exc
        for entry in entries:
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                validate_relative_path(relative)
            except UnsafePathError as exc:
                raise error_type(f"{label} has an invalid path: {entry.path}") from exc
            try:
                if entry.is_symlink():
                    raise error_type(f"{label} contains a symlink: {entry.path}")
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise error_type(f"{label} entry is unreadable: {entry.path}") from exc
            if stat.S_ISDIR(metadata.st_mode):
                if ignore_cache_dirs and entry.name == "__pycache__":
                    continue
                stack.append((Path(entry.path), relative))
            elif stat.S_ISREG(metadata.st_mode):
                result[relative] = metadata
            else:
                raise error_type(f"{label} contains a special file: {entry.path}")
    return result


def _stat_fingerprint(files: dict[str, os.stat_result]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            relative,
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
        for relative, metadata in sorted(files.items())
    )


def _canonical_skill_bytes(files: dict[str, str]) -> bytes:
    payload = [{"path": path, "sha256": files[path]} for path in sorted(files)]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_pinned_bundle(
    bundle_root: Path,
    manifest_path: Path,
    *,
    expected_digest: str,
) -> PinnedBundle:
    """Verify every pinned file against the trusted expected bundle digest.

    The validator entry point is derived from the trusted bundle root, never
    from a model-authored path. Extra files are rejected so import shadowing
    cannot add an unpinned module. A manifest and bundle that are only
    self-consistent are still rejected unless they match ``expected_digest``.
    """
    if not _is_sha256(expected_digest):
        raise BundleVerificationError("expected skill digest must be 64 lowercase hex characters")
    try:
        root = _require_directory(bundle_root, "pinned bundle root")
        manifest = _absolute(manifest_path)
        _check_ancestors(manifest.parent, must_exist=True)
    except SnapshotError as exc:
        raise BundleVerificationError("pinned bundle root or manifest path is unsafe") from exc
    raw = _read_regular_file(
        manifest,
        max_bytes=_MAX_SKILL_MANIFEST_BYTES,
        label="skill manifest",
        error_type=BundleVerificationError,
    )
    try:
        payload = strict_json_loads(raw, label="skill manifest")
    except StrictJsonError as exc:
        raise BundleVerificationError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise BundleVerificationError("skill manifest must be a JSON object")
    if set(payload) != {"digest", "algorithm", "files"}:
        raise BundleVerificationError("skill manifest must contain exactly digest, algorithm, files")
    if payload.get("algorithm") != SKILL_MANIFEST_ALGORITHM:
        raise BundleVerificationError("skill manifest algorithm is not the pinned recipe")
    declared_digest = payload.get("digest")
    if not _is_sha256(declared_digest):
        raise BundleVerificationError("skill manifest digest must be 64 lowercase hex characters")
    files_payload = payload.get("files")
    if not isinstance(files_payload, list) or not files_payload:
        raise BundleVerificationError("skill manifest files must be a non-empty array")

    files: dict[str, str] = {}
    for index, item in enumerate(files_payload):
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise BundleVerificationError(f"skill manifest file entry {index} has an invalid shape")
        try:
            path = validate_relative_path(item["path"])
        except (UnsafePathError, TypeError) as exc:
            raise BundleVerificationError(f"skill manifest file entry {index} has an unsafe path") from exc
        digest = item["sha256"]
        if not _is_sha256(digest):
            raise BundleVerificationError(f"skill manifest file entry {path} has an invalid sha256")
        if path in files:
            raise BundleVerificationError(f"skill manifest has a duplicate path: {path}")
        files[path] = digest

    computed_digest = hashlib.sha256(_canonical_skill_bytes(files)).hexdigest()
    if computed_digest != declared_digest:
        raise BundleVerificationError("skill manifest canonical digest does not match its declared digest")
    if computed_digest != expected_digest:
        raise BundleVerificationError("pinned bundle digest does not match the trusted expected digest")

    actual = _walk_regular_files(
        root,
        label="pinned bundle",
        error_type=BundleVerificationError,
        ignore_cache_dirs=True,
    )
    expected_paths = set(files)
    actual_paths = set(actual)
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise BundleVerificationError(f"pinned bundle file set differs (missing={missing}, extra={extra})")

    for path in sorted(files):
        digest, _ = _hash_regular_file(
            root / path,
            label=f"pinned bundle file {path}",
            error_type=BundleVerificationError,
        )
        if digest != files[path]:
            raise BundleVerificationError(f"pinned bundle file hash mismatch: {path}")

    if VALIDATOR_RELATIVE_PATH not in files:
        raise BundleVerificationError("pinned bundle does not list the trusted validator")
    return PinnedBundle(
        root=root,
        manifest_path=manifest,
        digest=expected_digest,
        validator_path=root / VALIDATOR_RELATIVE_PATH,
        validator_sha256=files[VALIDATOR_RELATIVE_PATH],
    )


def _report_relative(stage: str) -> str:
    if stage == "release":
        return "work/coverage_result.json"
    return f"work/stage_{stage}_result.json"


def _snapshot_entries(snapshot: Snapshot) -> dict[str, ManifestEntry]:
    expected: dict[str, ManifestEntry] = {}
    for entry in snapshot.manifest:
        validate_relative_path(entry.path)
        if not (entry.path.startswith("work/") or entry.path.startswith("output/")):
            raise SnapshotIntegrityError(f"snapshot manifest path is outside work/output: {entry.path}")
        if _is_gate_report(Path(entry.path).name):
            raise SnapshotIntegrityError(f"snapshot manifest must not contain a gate report: {entry.path}")
        if entry.path in expected:
            raise SnapshotIntegrityError(f"duplicate snapshot manifest path: {entry.path}")
        expected[entry.path] = entry
    return expected


def _verify_snapshot(snapshot: Snapshot, stage: str) -> None:
    """Recheck snapshot bytes and digests against the Q01a manifest.

    The expected fresh report is the only path excluded from the tree check;
    all other gate-report names are unexpected because Q01a never copies them.
    """
    validate_snapshot_id(snapshot.snapshot_id)
    path = _absolute(snapshot.path)
    if path.name != snapshot.snapshot_id:
        raise SnapshotIntegrityError("snapshot path basename does not match snapshot_id")
    if path.parent.name != "snapshots":
        raise SnapshotIntegrityError("snapshot is not under the controller snapshots root")
    controller_root = _require_directory(path.parent.parent, "controller root")
    snapshots_root = _require_directory(path.parent, "snapshots root")
    _require_directory(path, "snapshot")
    if not _is_relative_or_equal(path, snapshots_root) or not _is_relative_or_equal(snapshots_root, controller_root):
        raise SnapshotIntegrityError("snapshot path escapes the controller root")
    if _absolute(snapshot.work_dir) != path / "work" or _absolute(snapshot.output_dir) != path / "output":
        raise SnapshotIntegrityError("snapshot work/output paths do not match the controller layout")

    expected = _snapshot_entries(snapshot)
    manifest_bytes = canonical_manifest_bytes(snapshot.manifest)
    manifest_raw = _read_regular_file(
        path / SNAPSHOT_MANIFEST_NAME,
        max_bytes=_MAX_JSON_BYTES,
        label="snapshot manifest",
        error_type=SnapshotIntegrityError,
    )
    if manifest_raw != manifest_bytes:
        raise SnapshotIntegrityError("snapshot manifest bytes do not match its entries")

    fresh_report = _report_relative(stage)

    def visible_files() -> dict[str, os.stat_result]:
        files = _walk_regular_files(path, label="snapshot", error_type=SnapshotIntegrityError)
        files.pop(SNAPSHOT_MANIFEST_NAME, None)
        files.pop(fresh_report, None)
        return files

    actual = visible_files()
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise SnapshotIntegrityError(f"snapshot file set differs (missing={missing}, extra={extra})")

    for relative, entry in expected.items():
        digest, size = _hash_regular_file(
            path / relative,
            label=f"snapshot file {relative}",
            error_type=SnapshotIntegrityError,
        )
        if size != entry.size or digest != entry.sha256:
            raise SnapshotIntegrityError(f"snapshot file changed: {relative}")

    actual_after = visible_files()
    if _stat_fingerprint(actual) != _stat_fingerprint(actual_after):
        raise SnapshotIntegrityError("snapshot changed while it was being verified")
    manifest_after = _read_regular_file(
        path / SNAPSHOT_MANIFEST_NAME,
        max_bytes=_MAX_JSON_BYTES,
        label="snapshot manifest",
        error_type=SnapshotIntegrityError,
    )
    if manifest_after != manifest_bytes:
        raise SnapshotIntegrityError("snapshot manifest changed while it was being verified")

    if snapshot.manifest_digest != canonical_manifest_digest(snapshot.manifest):
        raise SnapshotIntegrityError("snapshot manifest_digest does not match its entries")
    input_digest = canonical_manifest_digest(
        entry for entry in snapshot.manifest if entry.path.startswith("work/")
    )
    output_digest = canonical_manifest_digest(
        entry for entry in snapshot.manifest if entry.path.startswith("output/")
    )
    if snapshot.input_digest != input_digest:
        raise SnapshotIntegrityError("snapshot input_digest does not match its entries")
    if snapshot.output_digest != output_digest:
        raise SnapshotIntegrityError("snapshot output_digest does not match its entries")


def _best_effort_skill_digest(manifest_path: Path) -> str | None:
    try:
        manifest = _absolute(manifest_path)
        raw = _read_regular_file(
            manifest,
            max_bytes=_MAX_SKILL_MANIFEST_BYTES,
            label="skill manifest",
            error_type=BundleVerificationError,
        )
        payload = strict_json_loads(raw, label="skill manifest")
        if isinstance(payload, dict) and _is_sha256(payload.get("digest")):
            return payload["digest"]
    except Exception:
        pass
    return None


def _create_staging(controller_root: Path, snapshot_id: str) -> Path:
    try:
        staging = Path(tempfile.mkdtemp(prefix=f".validation-{snapshot_id}-", dir=controller_root))
        os.chmod(staging, 0o700)
        (staging / "work").mkdir(mode=0o700)
        (staging / "output").mkdir(mode=0o700)
        return staging
    except OSError as exc:
        raise QualityAdapterError("cannot create private validator staging directory") from exc


def _copy_snapshot_to_staging(snapshot: Snapshot, staging: Path, expected: dict[str, ManifestEntry]) -> None:
    for relative in sorted(expected):
        entry = expected[relative]
        source = snapshot.path / relative
        destination = staging / relative
        _assert_within(destination, staging)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            metadata = os.lstat(source)
        except OSError as exc:
            raise SnapshotIntegrityError(f"snapshot file is unreadable: {relative}") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise SnapshotIntegrityError(f"snapshot file is not regular: {relative}")
        digest, size = _copy_regular_file(_SourceFile(source, relative, metadata), destination, entry.size)
        if digest != entry.sha256 or size != entry.size:
            raise SnapshotIntegrityError(f"snapshot file changed before staging copy: {relative}")

    actual = _walk_regular_files(staging, label="validator staging", error_type=QualityAdapterError)
    if set(actual) != set(expected):
        raise QualityAdapterError("validator staging tree does not match the snapshot manifest")


def _preflight_inputs(work_dir: Path, stage: str) -> None:
    files = _walk_regular_files(work_dir, label="snapshot work inputs", error_type=InputPreflightError)
    parsed: dict[str, object] = {}
    for relative in sorted(files):
        if not relative.endswith(".json"):
            continue
        raw = _read_regular_file(
            work_dir / relative,
            max_bytes=_MAX_JSON_BYTES,
            label=f"input JSON {relative}",
            error_type=InputPreflightError,
        )
        try:
            parsed[relative] = strict_json_loads(raw, label=f"input JSON {relative}")
        except StrictJsonError as exc:
            raise InputPreflightError(str(exc)) from exc

    checkpoint = parsed.get("checkpoint.json")
    if isinstance(checkpoint, dict) and "stage" in checkpoint and checkpoint["stage"] != stage:
        raise InputPreflightError("checkpoint stage does not match the requested validator stage")


def _capture_stream(stream: Any, limit: int, captured: _CapturedStream) -> None:
    data = bytearray()
    truncated = False
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            remaining = limit - len(data)
            if remaining > 0:
                data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass
    captured.data = bytes(data)
    captured.truncated = truncated


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except OSError:
            pass


def _run_validator(
    validator_path: Path,
    work_dir: Path,
    stage: str,
    *,
    timeout_seconds: float,
    diagnostic_limit_bytes: int,
) -> _ProcessResult:
    argv = [sys.executable, str(validator_path), str(work_dir), "--stage", stage]
    env = {
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(work_dir.parent),
            env=env,
            start_new_session=True,
            shell=False,
        )
    except OSError as exc:
        return _ProcessResult(None, b"", b"", False, False, spawn_error=str(exc))

    stdout_capture = _CapturedStream()
    stderr_capture = _CapturedStream()
    stdout_thread = threading.Thread(
        target=_capture_stream,
        args=(process.stdout, diagnostic_limit_bytes, stdout_capture),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_capture_stream,
        args=(process.stderr, diagnostic_limit_bytes, stderr_capture),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    returncode: int | None
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(process)
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = None
    finally:
        for thread in (stdout_thread, stderr_thread):
            thread.join(timeout=5)
        for thread, stream in ((stdout_thread, process.stdout), (stderr_thread, process.stderr)):
            if thread.is_alive() and stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
                thread.join(timeout=1)

    return _ProcessResult(
        returncode=returncode,
        stdout=stdout_capture.data,
        stderr=stderr_capture.data,
        stdout_truncated=stdout_capture.truncated,
        stderr_truncated=stderr_capture.truncated,
        timed_out=timed_out,
    )


def _read_fresh_report(staging: Path, stage: str) -> dict[str, Any]:
    relative = _report_relative(stage)
    report_path = staging / relative
    raw = _read_regular_file(
        report_path,
        max_bytes=_MAX_JSON_BYTES,
        label="fresh validator report",
        error_type=ReportValidationError,
    )
    try:
        report = strict_json_loads(raw, label="fresh validator report")
    except StrictJsonError as exc:
        raise ReportValidationError(str(exc)) from exc
    if not isinstance(report, dict):
        raise ReportValidationError("fresh validator report must be a JSON object")
    return report


def _require_non_negative_int(report: dict[str, Any], field: str) -> int:
    value = report.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReportValidationError(f"report {field} must be a non-negative integer")
    return value


def _validate_report(report: dict[str, Any], stage: str, exit_code: int) -> tuple[str, tuple[str, ...], int]:
    if report.get("protocol_version") != "v6.4":
        raise ReportValidationError("report protocol_version must be v6.4")
    if report.get("execution_contract") != "checkpoint-v1":
        raise ReportValidationError("report execution_contract must be checkpoint-v1")
    if report.get("stage") != stage:
        raise ReportValidationError("report stage does not match the requested stage")
    if report.get("gate") != "METADATA_ONLY":
        raise ReportValidationError("report gate must be METADATA_ONLY")
    if report.get("automatic_publication_allowed") is not False:
        raise ReportValidationError("report automatic_publication_allowed must be false")
    if report.get("human_engineer_signoff_required") is not True:
        raise ReportValidationError("report human_engineer_signoff_required must be true")

    _require_non_negative_int(report, "open_issue_count")
    open_record_count = _require_non_negative_int(report, "open_record_count")

    checks = report.get("blocking_checks")
    if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks):
        raise ReportValidationError("report blocking_checks must be an array of strings")
    if not isinstance(report.get("warning"), str):
        raise ReportValidationError("report warning must be a string")

    status = report.get("candidate_release_status")
    allowed = _ALLOWED_EXIT_STATUSES[stage].get(exit_code, frozenset())
    if not isinstance(status, str) or status not in allowed:
        raise ReportValidationError("report candidate_release_status is not allowed for this exit code")
    if exit_code == 0 and checks:
        raise ReportValidationError("exit 0 report must not contain blocking checks")
    return status, tuple(checks), open_record_count


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _receipt_location(snapshot: Snapshot, stage: str) -> tuple[Path, Path]:
    validate_snapshot_id(snapshot.snapshot_id)
    path = _absolute(snapshot.path)
    if path.name != snapshot.snapshot_id:
        raise SnapshotIntegrityError("snapshot path basename does not match snapshot_id")
    if path.parent.name != "snapshots":
        raise SnapshotIntegrityError("snapshot is not under the controller snapshots root")
    controller_root = _require_directory(path.parent.parent, "controller root")
    _require_directory(path.parent, "snapshots root")
    receipts_root = controller_root / "receipts"
    target = receipts_root / snapshot.snapshot_id / f"{stage}.json"
    if _is_relative_or_equal(target, path):
        raise UnsafePathError("receipt must not be written inside the snapshot")
    _check_ancestors(receipts_root, must_exist=False)
    return controller_root, target


def _write_receipt_atomic(path: Path, receipt: ValidatorReceipt) -> None:
    parent = path.parent
    try:
        _check_ancestors(parent, must_exist=False)
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _require_directory(parent, "receipt directory")
        if os.path.lexists(path):
            raise ReceiptExistsError(f"receipt already exists: {path}")

        payload = json.dumps(
            receipt.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        ).encode("utf-8") + b"\n"
        temporary = parent / f".{path.name}.{uuid4().hex}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write while creating receipt")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ReceiptExistsError(f"receipt already exists: {path}") from exc
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

        try:
            directory_descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return
        try:
            os.fsync(directory_descriptor)
        except OSError:
            pass
        finally:
            os.close(directory_descriptor)
    except ReceiptError:
        raise
    except (OSError, SnapshotError) as exc:
        raise ReceiptError(f"cannot write controller receipt: {path}") from exc


def validate_snapshot(
    snapshot: Snapshot,
    *,
    run_id: int,
    stage: str,
    source_digest: str,
    bundle_root: Path,
    manifest_path: Path,
    timeout_seconds: float = VALIDATOR_TIMEOUT_SECONDS,
    diagnostic_limit_bytes: int = DIAGNOSTIC_LIMIT_BYTES,
) -> ValidationResult:
    """Run the pinned validator and write one controller-owned Q01b receipt.

    Bundle trust is always anchored to ``TRUSTED_SKILL_DIGEST``; callers cannot
    supply an expected digest. Adapter failures are recorded as ``ERROR``
    results rather than raised, so a terminal receipt exists for malformed
    input, process/report failure, pin mismatch, and snapshot mutation. Invalid
    arguments or an existing receipt are caller errors and are raised.
    """
    stage = _validate_stage(stage)
    run_id = _validate_run_id(run_id)
    source_digest = _require_sha256(source_digest, "source_digest")
    timeout_seconds = _validate_timeout(timeout_seconds)
    diagnostic_limit_bytes = _validate_diagnostic_limit(diagnostic_limit_bytes)

    _, receipt_path = _receipt_location(snapshot, stage)
    if os.path.lexists(receipt_path):
        raise ReceiptExistsError(f"receipt already exists: {receipt_path}")
    skill_digest: str | None = None
    process_result: _ProcessResult | None = None
    report: dict[str, Any] | None = None
    validator_exit_code: int | None = None
    candidate_release_status: str | None = None
    blocking_checks: tuple[str, ...] = ()
    open_record_count: int | None = None
    controller_status = "ERROR"
    error: Exception | None = None
    snapshot_verified = False
    staging: Path | None = None

    try:
        pinned = verify_pinned_bundle(
            bundle_root,
            manifest_path,
            expected_digest=TRUSTED_SKILL_DIGEST,
        )
        skill_digest = pinned.digest

        _verify_snapshot(snapshot, stage)
        snapshot_verified = True
        expected = _snapshot_entries(snapshot)
        staging = _create_staging(_absolute(snapshot.path).parent.parent, snapshot.snapshot_id)
        _copy_snapshot_to_staging(snapshot, staging, expected)
        _preflight_inputs(staging / "work", stage)
        validator_digest, _ = _hash_regular_file(
            pinned.validator_path,
            label="trusted validator",
            error_type=BundleVerificationError,
        )
        if validator_digest != pinned.validator_sha256:
            raise BundleVerificationError("trusted validator changed before invocation")

        process_result = _run_validator(
            pinned.validator_path,
            staging / "work",
            stage,
            timeout_seconds=timeout_seconds,
            diagnostic_limit_bytes=diagnostic_limit_bytes,
        )
        if process_result.spawn_error is not None:
            raise ValidatorProcessError(f"validator could not start: {process_result.spawn_error}")
        if process_result.timed_out:
            raise ValidatorProcessError(f"validator timed out after {timeout_seconds:g} seconds")
        if process_result.returncode is None:
            raise ValidatorProcessError("validator did not return an exit code")
        validator_exit_code = process_result.returncode
        if validator_exit_code not in (0, 2):
            raise ValidatorProcessError(f"unexpected validator exit code: {validator_exit_code}")

        report = _read_fresh_report(staging, stage)
        candidate_release_status, blocking_checks, open_record_count = _validate_report(
            report,
            stage,
            validator_exit_code,
        )
        _verify_snapshot(snapshot, stage)
        controller_status = "BLOCKED" if validator_exit_code == 2 else "PASS"
    except BundleVerificationError as exc:
        error = exc
        if skill_digest is None:
            skill_digest = _best_effort_skill_digest(manifest_path)
    except (SnapshotError, QualityAdapterError, OSError) as exc:
        error = exc
        if snapshot_verified:
            try:
                _verify_snapshot(snapshot, stage)
            except (SnapshotError, QualityAdapterError, OSError) as post_error:
                error = post_error
    except Exception as exc:  # Keep the adapter contract: unexpected faults become ERROR receipts.
        error = exc
        if snapshot_verified:
            try:
                _verify_snapshot(snapshot, stage)
            except (SnapshotError, QualityAdapterError, OSError) as post_error:
                error = post_error
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    if error is not None:
        controller_status = "ERROR"
        report = None
        candidate_release_status = None
        blocking_checks = ()
        open_record_count = None
    elif controller_status == "ERROR":
        error = QualityAdapterError("validator adapter ended without a terminal status")

    receipt = ValidatorReceipt(
        schema_version=1,
        run_id=run_id,
        snapshot_id=snapshot.snapshot_id,
        stage=stage,
        source_digest=source_digest,
        skill_digest=skill_digest,
        input_digest=snapshot.input_digest,
        validator_exit_code=validator_exit_code,
        candidate_release_status=candidate_release_status,
        blocking_checks=blocking_checks,
        open_record_count=open_record_count,
        validated_at=_utc_timestamp(),
        controller_status=controller_status,
    )
    _write_receipt_atomic(receipt_path, receipt)

    stdout = process_result.stdout.decode("utf-8", errors="replace") if process_result else ""
    stderr = process_result.stderr.decode("utf-8", errors="replace") if process_result else ""
    return ValidationResult(
        receipt=receipt,
        receipt_path=receipt_path,
        report=report,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=process_result.stdout_truncated if process_result else False,
        stderr_truncated=process_result.stderr_truncated if process_result else False,
        error_code=getattr(error, "code", None) if error is not None else None,
        error_message=str(error) if error is not None else None,
    )
