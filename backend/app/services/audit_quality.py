"""Trusted filesystem snapshot primitives for BOQ audit quality artifacts.

Q01a scope only: build an immutable snapshot of an attempt's ``work/`` and
``output/`` directories, and compute canonical digests over the copied bytes.
This module intentionally does not invoke the validator, write receipts, touch
the database/API/runner, or enforce process isolation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

SNAPSHOT_MANIFEST_NAME = "manifest.json"
SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_SIZE = 1024 * 1024


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
class _SourceFile:
    source: Path
    relative: str
    stat: os.stat_result


@dataclass(slots=True)
class _Budget:
    files: int = 0
    total_bytes: int = 0


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
