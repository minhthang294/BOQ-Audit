from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from app.services import audit_quality as aq


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _make_attempt(
    tmp_path: Path,
    *,
    work: dict[str, bytes] | None = None,
    output: dict[str, bytes] | None = None,
) -> tuple[Path, Path]:
    jobs_root = tmp_path / "jobs"
    attempt = jobs_root / "job" / "attempts" / "attempt-1"
    _write_tree(attempt / "work", work or {})
    _write_tree(attempt / "output", output or {})
    # Production must pass the exact assigned attempt, not the whole jobs root.
    return attempt, attempt


def _snapshot(
    tmp_path: Path,
    *,
    work: dict[str, bytes] | None = None,
    output: dict[str, bytes] | None = None,
    snapshot_id: str = "snap-1",
    limits: aq.SnapshotLimits | None = None,
) -> aq.Snapshot:
    attempt, allowed = _make_attempt(tmp_path, work=work, output=output)
    return aq.create_snapshot(
        attempt,
        tmp_path / "controller",
        allowed_source_root=allowed,
        snapshot_id=snapshot_id,
        limits=limits,
    )


def _assert_no_partial_snapshot(controller: Path, snapshot_id: str = "snap-1") -> None:
    snapshots = controller / "snapshots"
    assert not (snapshots / snapshot_id).exists()
    if snapshots.exists():
        assert not list(snapshots.glob(".staging-*"))


def test_create_snapshot_copies_trees_and_writes_canonical_manifest(tmp_path: Path) -> None:
    work = {
        "nested/b.txt": b"bee",
        "a.json": b'{"a":1}',
        "unicode-\u00e9.txt": b"utf8",
    }
    output = {"result.xlsx": b"xlsx-bytes"}
    snapshot = _snapshot(tmp_path, work=work, output=output, snapshot_id="snap-1")

    assert snapshot.snapshot_id == "snap-1"
    assert snapshot.path == tmp_path / "controller" / "snapshots" / "snap-1"
    assert snapshot.work_dir == snapshot.path / "work"
    assert snapshot.output_dir == snapshot.path / "output"
    assert (snapshot.work_dir / "nested/b.txt").read_bytes() == b"bee"
    assert (snapshot.work_dir / "a.json").read_bytes() == b'{"a":1}'
    assert (snapshot.output_dir / "result.xlsx").read_bytes() == b"xlsx-bytes"

    expected_paths = ["output/result.xlsx", "work/a.json", "work/nested/b.txt", "work/unicode-\u00e9.txt"]
    assert [entry.path for entry in snapshot.manifest] == expected_paths
    for entry in snapshot.manifest:
        assert entry.size == len((snapshot.path / entry.path).read_bytes())
        assert entry.sha256 == hashlib.sha256((snapshot.path / entry.path).read_bytes()).hexdigest()

    assert snapshot.manifest_path == snapshot.path / "manifest.json"
    assert snapshot.manifest_path.read_bytes() == aq.canonical_manifest_bytes(snapshot.manifest)
    assert snapshot.manifest_digest == aq.canonical_manifest_digest(snapshot.manifest)
    assert snapshot.input_digest == aq.canonical_manifest_digest(
        entry for entry in snapshot.manifest if entry.path.startswith("work/")
    )
    assert snapshot.output_digest == aq.canonical_manifest_digest(
        entry for entry in snapshot.manifest if entry.path.startswith("output/")
    )

    payload = json.loads(snapshot.manifest_path.read_text(encoding="utf-8"))
    assert [item["path"] for item in payload] == expected_paths
    assert all(set(item) == {"path", "sha256", "size"} for item in payload)


def test_snapshot_id_is_generated_when_omitted(tmp_path: Path) -> None:
    attempt, allowed = _make_attempt(tmp_path, work={}, output={})

    snapshot = aq.create_snapshot(attempt, tmp_path / "controller", allowed_source_root=allowed)

    assert re.fullmatch(r"[0-9a-f]{32}", snapshot.snapshot_id)
    assert snapshot.path.name == snapshot.snapshot_id
    assert snapshot.path.is_dir()


def test_snapshot_is_independent_of_source_after_copy(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, work={"a.json": b"original"})
    source_file = tmp_path / "jobs/job/attempts/attempt-1/work/a.json"

    source_file.write_bytes(b"changed-after-snapshot")

    assert (snapshot.work_dir / "a.json").read_bytes() == b"original"
    assert snapshot.manifest_digest == aq.canonical_manifest_digest(snapshot.manifest)
    assert snapshot.input_digest == aq.canonical_manifest_digest(snapshot.manifest)


def test_canonical_manifest_digest_is_order_independent_and_rejects_duplicates() -> None:
    first = aq.ManifestEntry("work/a.json", "a" * 64, 1)
    second = aq.ManifestEntry("output/result.xlsx", "b" * 64, 2)

    assert aq.canonical_manifest_bytes([second, first]) == aq.canonical_manifest_bytes([first, second])
    assert aq.canonical_manifest_digest([second, first]) == aq.canonical_manifest_digest([first, second])

    with pytest.raises(aq.SnapshotError, match="duplicate"):
        aq.canonical_manifest_digest([first, first])


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        ".",
        "..",
        "../work/a.json",
        "work/../../escape.json",
        "/absolute.json",
        "C:/absolute.json",
        "work//a.json",
        "work/./a.json",
        "work/",
        "work\\a.json",
        "work/\x00a.json",
    ],
)
def test_validate_relative_path_rejects_traversal_and_noncanonical_paths(bad_path: str) -> None:
    with pytest.raises(aq.UnsafePathError):
        aq.validate_relative_path(bad_path)


@pytest.mark.parametrize("good_path", ["work/a.json", "output/nested/result.xlsx", "unicode-\u00e9.txt"])
def test_validate_relative_path_accepts_canonical_paths(good_path: str) -> None:
    assert aq.validate_relative_path(good_path) == good_path


def test_snapshot_rejects_symlink_file(tmp_path: Path) -> None:
    attempt, allowed = _make_attempt(tmp_path, work={})
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    (attempt / "work" / "linked.txt").symlink_to(outside)

    with pytest.raises(aq.UnsafePathError, match="symlink"):
        aq.create_snapshot(
            attempt,
            tmp_path / "controller",
            allowed_source_root=allowed,
            snapshot_id="snap-1",
        )

    _assert_no_partial_snapshot(tmp_path / "controller")


def test_snapshot_rejects_symlink_directory(tmp_path: Path) -> None:
    attempt, allowed = _make_attempt(tmp_path, work={"nested/a.json": b"a"})
    (attempt / "work" / "linked-dir").symlink_to(attempt / "work" / "nested", target_is_directory=True)

    with pytest.raises(aq.UnsafePathError, match="symlink"):
        aq.create_snapshot(
            attempt,
            tmp_path / "controller",
            allowed_source_root=allowed,
            snapshot_id="snap-1",
        )

    _assert_no_partial_snapshot(tmp_path / "controller")


def test_snapshot_rejects_special_file(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO is not available on this platform")
    attempt, allowed = _make_attempt(tmp_path, work={})
    os.mkfifo(attempt / "work" / "pipe")

    with pytest.raises(aq.UnsafePathError, match="special file"):
        aq.create_snapshot(
            attempt,
            tmp_path / "controller",
            allowed_source_root=allowed,
            snapshot_id="snap-1",
        )

    _assert_no_partial_snapshot(tmp_path / "controller")


def test_source_attempt_outside_allowed_root_is_rejected(tmp_path: Path) -> None:
    attempt = tmp_path / "elsewhere" / "attempt-1"
    _write_tree(attempt / "work", {"a.json": b"a"})
    _write_tree(attempt / "output", {})
    allowed = tmp_path / "jobs"
    allowed.mkdir()

    with pytest.raises(aq.UnsafePathError, match="equal"):
        aq.create_snapshot(attempt, tmp_path / "controller", allowed_source_root=allowed)


def test_broader_jobs_root_as_allowed_source_root_is_rejected(tmp_path: Path) -> None:
    attempt, _ = _make_attempt(tmp_path, work={"a.json": b"a"})
    jobs_root = tmp_path / "jobs"

    with pytest.raises(aq.UnsafePathError, match="equal"):
        aq.create_snapshot(attempt, tmp_path / "controller", allowed_source_root=jobs_root)

    assert not (tmp_path / "controller").exists()


def test_symlink_in_allowed_root_ancestor_is_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real-jobs"
    attempt = real_root / "job" / "attempts" / "attempt-1"
    _write_tree(attempt / "work", {"a.json": b"a"})
    _write_tree(attempt / "output", {})
    linked_root = tmp_path / "linked-jobs"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(aq.UnsafePathError, match="symlink"):
        aq.create_snapshot(attempt, tmp_path / "controller", allowed_source_root=linked_root)


def test_symlink_source_attempt_is_rejected(tmp_path: Path) -> None:
    attempt, allowed = _make_attempt(tmp_path, work={"a.json": b"a"})
    linked_attempt = attempt.parent / "linked-attempt"
    linked_attempt.symlink_to(attempt, target_is_directory=True)

    with pytest.raises(aq.UnsafePathError, match="symlink"):
        aq.create_snapshot(linked_attempt, tmp_path / "controller", allowed_source_root=allowed)


@pytest.mark.parametrize("bad_snapshot_id", ["", ".", "..", "../escape", "a/b", "a b", "a.b", "/absolute"])
def test_bad_snapshot_id_is_rejected_before_writing(tmp_path: Path, bad_snapshot_id: str) -> None:
    attempt, allowed = _make_attempt(tmp_path, work={"a.json": b"a"})

    with pytest.raises(aq.UnsafePathError):
        aq.create_snapshot(
            attempt,
            tmp_path / "controller",
            allowed_source_root=allowed,
            snapshot_id=bad_snapshot_id,
        )

    assert not (tmp_path / "controller").exists()


def test_source_mutation_after_copy_is_detected_and_staging_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt, allowed = _make_attempt(
        tmp_path,
        work={"a.json": b"original-a", "b.json": b"original-b"},
        output={"result.xlsx": b"result"},
    )
    original_copy = aq._copy_regular_file
    mutated = False

    def copy_then_mutate(item: aq._SourceFile, destination: Path, max_file_bytes: int) -> tuple[str, int]:
        nonlocal mutated
        copied = original_copy(item, destination, max_file_bytes)
        if item.relative == "work/b.json" and not mutated:
            mutated = True
            (attempt / "work" / "a.json").write_bytes(b"changed-after-copy")
        return copied

    monkeypatch.setattr(aq, "_copy_regular_file", copy_then_mutate)

    with pytest.raises(aq.SourceChangedError, match="source changed") as caught:
        aq.create_snapshot(
            attempt,
            tmp_path / "controller",
            allowed_source_root=allowed,
            snapshot_id="snap-1",
        )

    assert caught.value.code == "SOURCE_CHANGED"
    _assert_no_partial_snapshot(tmp_path / "controller")


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        (aq.SnapshotLimits(max_files=1), "max_files"),
        (aq.SnapshotLimits(max_work_file_bytes=3), "max_file_bytes"),
        (aq.SnapshotLimits(max_output_file_bytes=3), "max_file_bytes"),
        (aq.SnapshotLimits(max_total_bytes=5), "max_total_bytes"),
    ],
)
def test_snapshot_limits_are_enforced(
    tmp_path: Path, limits: aq.SnapshotLimits, message: str
) -> None:
    attempt, allowed = _make_attempt(
        tmp_path,
        work={"a.txt": b"1234", "b.txt": b"5678"},
        output={"result.xlsx": b"1234"},
    )

    with pytest.raises(aq.SnapshotLimitError, match=message) as caught:
        aq.create_snapshot(
            attempt,
            tmp_path / "controller",
            allowed_source_root=allowed,
            snapshot_id="snap-1",
            limits=limits,
        )

    assert caught.value.code == "SNAPSHOT_LIMIT"
    _assert_no_partial_snapshot(tmp_path / "controller")


def test_gate_reports_are_not_copied_into_snapshot(tmp_path: Path) -> None:
    snapshot = _snapshot(
        tmp_path,
        work={
            "coverage_result.json": b"old coverage",
            "stage_pass1_result.json": b"old pass 1",
            "stage_custom_result.json": b"old custom",
            "normal.json": b"normal",
        },
        output={
            "coverage_result.json": b"old output coverage",
            "stage_release_result.json": b"old release",
            "result.xlsx": b"new result",
        },
    )

    assert [entry.path for entry in snapshot.manifest] == ["output/result.xlsx", "work/normal.json"]
    assert not (snapshot.work_dir / "coverage_result.json").exists()
    assert not (snapshot.work_dir / "stage_pass1_result.json").exists()
    assert not (snapshot.work_dir / "stage_custom_result.json").exists()
    assert not (snapshot.output_dir / "coverage_result.json").exists()
    assert not (snapshot.output_dir / "stage_release_result.json").exists()


def test_missing_output_directory_is_rejected(tmp_path: Path) -> None:
    attempt, allowed = _make_attempt(tmp_path, work={"a.json": b"a"})
    (attempt / "output").rmdir()

    with pytest.raises(aq.UnsafePathError, match="missing"):
        aq.create_snapshot(attempt, tmp_path / "controller", allowed_source_root=allowed)


def test_existing_snapshot_id_is_not_overwritten(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, work={"a.json": b"original"}, snapshot_id="snap-1")
    attempt = tmp_path / "jobs/job/attempts/attempt-1"
    allowed = attempt
    (attempt / "work" / "a.json").write_bytes(b"replacement")

    with pytest.raises(aq.SnapshotError, match="already exists"):
        aq.create_snapshot(attempt, tmp_path / "controller", allowed_source_root=allowed, snapshot_id="snap-1")

    assert (snapshot.work_dir / "a.json").read_bytes() == b"original"


def test_controller_root_inside_source_attempt_sibling_is_rejected(tmp_path: Path) -> None:
    attempt, allowed = _make_attempt(tmp_path, work={"a.json": b"a"})
    controller = attempt / "controller"

    with pytest.raises(aq.UnsafePathError, match="overlap"):
        aq.create_snapshot(
            attempt,
            controller,
            allowed_source_root=allowed,
            snapshot_id="snap-1",
        )

    assert not controller.exists()


def test_source_attempt_inside_controller_root_is_rejected(tmp_path: Path) -> None:
    attempt, allowed = _make_attempt(tmp_path, work={"a.json": b"a"})
    controller = tmp_path / "jobs"

    with pytest.raises(aq.UnsafePathError, match="overlap"):
        aq.create_snapshot(
            attempt,
            controller,
            allowed_source_root=allowed,
            snapshot_id="snap-1",
        )

    assert not (controller / "snapshots").exists()
