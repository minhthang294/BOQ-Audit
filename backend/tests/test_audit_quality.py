from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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


_FAKE_VALIDATOR = '''#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

assert len(sys.argv) == 4, sys.argv
assert sys.argv[2] == "--stage", sys.argv
assert "--out" not in sys.argv
work = Path(sys.argv[1])
stage = sys.argv[3]
control_path = work / "validator_control.json"
control = json.loads(control_path.read_text(encoding="utf-8")) if control_path.exists() else {}
if control.get("require_clean_env"):
    assert os.environ.get("BOQ_AUDIT_TEST_SECRET") is None, "parent secret leaked"
    assert "PYTHONPATH" not in os.environ, "PYTHONPATH leaked"
    assert os.environ["PATH"] == control["expected_path"]
    assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
if control.get("expected_snapshot_work"):
    assert str(work.resolve()) != control["expected_snapshot_work"], "ran on snapshot"
if control.get("stdout_bytes"):
    sys.stdout.write("o" * int(control["stdout_bytes"]))
if control.get("stderr_bytes"):
    sys.stderr.write("e" * int(control["stderr_bytes"]))
if control.get("sleep"):
    time.sleep(float(control["sleep"]))
exit_code = int(control.get("exit_code", 0))
name = "coverage_result.json" if stage == "release" else f"stage_{stage}_result.json"
if control.get("omit_report"):
    sys.exit(exit_code)
status = (
    "BLOCKED"
    if exit_code == 2
    else "READY_FOR_ENGINEER_REVIEW" if stage == "release" else "METADATA_STAGE_COMPLETE"
)
report = {
    "protocol_version": "v6.4",
    "execution_contract": "checkpoint-v1",
    "stage": stage,
    "gate": "METADATA_ONLY",
    "candidate_release_status": status,
    "automatic_publication_allowed": False,
    "human_engineer_signoff_required": True,
    "open_issue_count": 1 if exit_code == 2 else 0,
    "open_record_count": 2 if exit_code == 2 else 0,
    "blocking_checks": ["missing required ledger"] if exit_code == 2 else [],
    "warning": "metadata only",
}
report.update(control.get("report_patch", {}))
raw = control.get("report_text")
if raw is not None:
    (work / name).write_text(raw, encoding="utf-8")
    sys.exit(exit_code)
(work / name).write_text(json.dumps(report, ensure_ascii=False) + "\\n", encoding="utf-8")
print(json.dumps({"stage": stage, "report": str(work / name)}, ensure_ascii=False))
sys.exit(exit_code)
'''


def _write_fake_manifest(manifest_path: Path, files: list[dict[str, str]]) -> str:
    digest = hashlib.sha256(
        json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = {
        "digest": digest,
        "algorithm": aq.SKILL_MANIFEST_ALGORITHM,
        "files": files,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return digest


def _fake_bundle(tmp_path: Path) -> tuple[Path, Path, str]:
    bundle = tmp_path / "bundle"
    script = bundle / "scripts/validate_run.py"
    script.parent.mkdir(parents=True)
    script.write_text(_FAKE_VALIDATOR, encoding="utf-8")
    files = [{"path": "scripts/validate_run.py", "sha256": hashlib.sha256(script.read_bytes()).hexdigest()}]
    manifest_path = tmp_path / "skill-manifest.json"
    expected_digest = _write_fake_manifest(manifest_path, files)
    return bundle, manifest_path, expected_digest


@contextmanager
def _fake_trust(expected_digest: str) -> Iterator[None]:
    original = aq.TRUSTED_SKILL_DIGEST
    aq.TRUSTED_SKILL_DIGEST = expected_digest
    try:
        yield
    finally:
        aq.TRUSTED_SKILL_DIGEST = original


def _validator_control(tmp_path: Path, **updates: Any) -> dict[str, Any]:
    control: dict[str, Any] = {
        "expected_snapshot_work": str(tmp_path / "controller/snapshots/snap-1/work"),
    }
    control.update(updates)
    return control


def _validate(
    tmp_path: Path,
    *,
    work: dict[str, bytes] | None = None,
    output: dict[str, bytes] | None = None,
    stage: str = "pass1",
    control: dict[str, Any] | None = None,
    bundle: tuple[Path, Path, str] | None = None,
    timeout_seconds: float = aq.VALIDATOR_TIMEOUT_SECONDS,
) -> aq.ValidationResult:
    files = dict(work or {})
    files["validator_control.json"] = json.dumps(
        _validator_control(tmp_path, **(control or {})), ensure_ascii=False
    ).encode("utf-8")
    snapshot = _snapshot(tmp_path, work=files, output=output, snapshot_id="snap-1")
    bundle_root, manifest_path, expected_digest = bundle or _fake_bundle(tmp_path)
    with _fake_trust(expected_digest):
        return aq.validate_snapshot(
            snapshot,
            run_id=42,
            stage=stage,
            source_digest="a" * 64,
            bundle_root=bundle_root,
            manifest_path=manifest_path,
            timeout_seconds=timeout_seconds,
        )


def _receipt_files(tmp_path: Path) -> list[Path]:
    receipts = tmp_path / "controller/receipts"
    return sorted(path for path in receipts.rglob("*") if path.is_file()) if receipts.exists() else []


@pytest.mark.parametrize(
    ("stage", "expected_status"),
    [
        ("pass1", aq.STAGE_COMPLETE_STATUS),
        ("pass2", aq.STAGE_COMPLETE_STATUS),
        ("pass3", aq.STAGE_COMPLETE_STATUS),
        ("release", aq.RELEASE_READY_STATUS),
    ],
)
def test_validate_snapshot_valid_stages(tmp_path: Path, stage: str, expected_status: str) -> None:
    result = _validate(tmp_path, stage=stage)

    assert result.controller_status == "PASS"
    assert result.receipt.stage == stage
    assert result.receipt.candidate_release_status == expected_status
    assert result.receipt.validator_exit_code == 0
    assert result.receipt.open_record_count == 0
    assert result.receipt.blocking_checks == ()
    assert result.receipt_path == tmp_path / "controller/receipts/snap-1" / f"{stage}.json"
    payload = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "run_id",
        "snapshot_id",
        "stage",
        "source_digest",
        "skill_digest",
        "input_digest",
        "validator_exit_code",
        "candidate_release_status",
        "blocking_checks",
        "open_record_count",
        "validated_at",
        "controller_status",
    }
    assert payload["schema_version"] == 1
    assert payload["run_id"] == 42
    assert payload["source_digest"] == "a" * 64
    assert payload["input_digest"] == result.receipt.input_digest
    assert payload["controller_status"] == "PASS"


def test_release_partial_with_open_items_is_passed_through(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        stage="release",
        control={
            "report_patch": {
                "candidate_release_status": aq.RELEASE_PARTIAL_STATUS,
                "open_issue_count": 1,
                "open_record_count": 2,
            }
        },
    )

    assert result.controller_status == "PASS"
    assert result.receipt.candidate_release_status == aq.RELEASE_PARTIAL_STATUS
    assert result.receipt.open_record_count == 2
    assert result.receipt.validator_exit_code == 0


def test_validator_receives_minimal_environment_not_parent_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOQ_AUDIT_TEST_SECRET", "sentinel-secret")
    monkeypatch.setenv("PYTHONPATH", "/tmp/model-controlled-shadow-path")

    result = _validate(
        tmp_path,
        control={"require_clean_env": True, "expected_path": os.defpath},
    )

    assert result.controller_status == "PASS"
    assert "sentinel-secret" not in result.receipt_path.read_text(encoding="utf-8")


def test_validator_exit_two_with_blocked_report_is_blocked(tmp_path: Path) -> None:
    result = _validate(tmp_path, control={"exit_code": 2})

    assert result.controller_status == "BLOCKED"
    assert result.receipt.validator_exit_code == 2
    assert result.receipt.candidate_release_status == "BLOCKED"
    assert result.receipt.open_record_count == 2
    assert result.receipt.blocking_checks == ("missing required ledger",)
    assert result.report is not None


def test_unknown_validator_exit_is_error(tmp_path: Path) -> None:
    result = _validate(tmp_path, control={"exit_code": 1})

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code == 1
    assert result.receipt.candidate_release_status is None
    assert result.receipt.open_record_count is None


def test_stale_report_in_snapshot_is_never_used(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path)
    control = _validator_control(tmp_path, omit_report=True)
    snapshot = _snapshot(
        tmp_path,
        work={"validator_control.json": json.dumps(control).encode("utf-8")},
        snapshot_id="snap-1",
    )
    stale = {
        "protocol_version": "v6.4",
        "execution_contract": "checkpoint-v1",
        "stage": "pass1",
        "gate": "METADATA_ONLY",
        "candidate_release_status": "METADATA_STAGE_COMPLETE",
        "automatic_publication_allowed": False,
        "human_engineer_signoff_required": True,
        "open_issue_count": 0,
        "open_record_count": 0,
        "blocking_checks": [],
        "warning": "stale",
    }
    (snapshot.work_dir / "stage_pass1_result.json").write_text(json.dumps(stale), encoding="utf-8")

    with _fake_trust(bundle[2]):
        result = aq.validate_snapshot(
            snapshot,
            run_id=42,
            stage="pass1",
            source_digest="a" * 64,
            bundle_root=bundle[0],
            manifest_path=bundle[1],
        )

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code == 0
    assert result.receipt.candidate_release_status is None
    assert result.receipt.open_record_count is None


@pytest.mark.parametrize(
    "bad_json",
    [
        b'{"a": 1, "a": 2}',
        b'{"value": NaN}',
        b'{"value": Infinity}',
        b'{"value": -Infinity}',
        b'{"value": 1e9999}',
        b'',
        b'{"broken": ',
    ],
)
def test_strict_input_json_preflight_rejects_duplicates_nonfinite_and_malformed(
    tmp_path: Path,
    bad_json: bytes,
) -> None:
    result = _validate(tmp_path, work={"run_manifest.json": bad_json})

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code is None
    assert result.receipt.open_record_count is None
    assert result.error_code == aq.InputPreflightError.code


def test_checkpoint_stage_mismatch_is_error_before_validator(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        work={"checkpoint.json": b'{"stage": "pass2", "next_action": "x", "pending_rechecks": []}'},
        stage="pass1",
    )

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code is None
    assert result.receipt.open_record_count is None
    assert result.error_code == aq.InputPreflightError.code


def test_validator_timeout_is_error_without_inferred_counts(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _validate(tmp_path, control={"sleep": 2}, timeout_seconds=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 1.5
    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code is None
    assert result.receipt.candidate_release_status is None
    assert result.receipt.open_record_count is None
    assert result.error_code == aq.ValidatorProcessError.code


@pytest.mark.parametrize(
    "patch",
    [
        {"stage": "pass2"},
        {"protocol_version": "v6.3"},
        {"execution_contract": "legacy"},
        {"candidate_release_status": "BLOCKED"},
        {"open_issue_count": True},
        {"open_issue_count": -1},
        {"open_record_count": True},
        {"open_record_count": -1},
        {"human_engineer_signoff_required": False},
        {"gate": "AUDIT_ONLY"},
        {"warning": 42},
        {"blocking_checks": "not-an-array"},
        {"blocking_checks": ["valid", 42]},
        {"automatic_publication_allowed": True},
    ],
)
def test_wrong_fresh_report_fields_are_error(tmp_path: Path, patch: dict[str, object]) -> None:
    result = _validate(tmp_path, control={"report_patch": patch})

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code == 0
    assert result.receipt.candidate_release_status is None
    assert result.receipt.open_record_count is None
    assert result.error_code == aq.ReportValidationError.code


def test_fresh_report_duplicate_keys_and_nonfinite_values_are_error(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        control={"report_text": '{"protocol_version":"v6.4","open_record_count":NaN}'},
    )

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code == 0
    assert result.receipt.open_record_count is None


def test_exit_two_with_non_blocked_report_is_error(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        control={
            "exit_code": 2,
            "report_patch": {"candidate_release_status": aq.STAGE_COMPLETE_STATUS},
        },
    )

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code == 2
    assert result.receipt.candidate_release_status is None
    assert result.receipt.open_record_count is None


def test_pin_mismatch_is_error(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path)
    validator = bundle[0] / "scripts/validate_run.py"
    validator.write_text(validator.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

    result = _validate(tmp_path, bundle=bundle)

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code is None
    assert result.receipt.candidate_release_status is None
    assert result.receipt.open_record_count is None
    assert result.receipt.skill_digest is not None
    assert result.error_code == aq.BundleVerificationError.code


def test_self_consistent_tampered_bundle_fails_trusted_expected_digest(tmp_path: Path) -> None:
    bundle_root, manifest_path, original_digest = _fake_bundle(tmp_path)
    validator = bundle_root / "scripts/validate_run.py"
    validator.write_text(validator.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    tampered_digest = _write_fake_manifest(
        manifest_path,
        [{"path": "scripts/validate_run.py", "sha256": hashlib.sha256(validator.read_bytes()).hexdigest()}],
    )
    assert tampered_digest not in {original_digest, aq.TRUSTED_SKILL_DIGEST}
    snapshot = _snapshot(
        tmp_path,
        work={"validator_control.json": json.dumps(_validator_control(tmp_path)).encode("utf-8")},
        snapshot_id="snap-1",
    )

    result = aq.validate_snapshot(
        snapshot,
        run_id=42,
        stage="pass1",
        source_digest="a" * 64,
        bundle_root=bundle_root,
        manifest_path=manifest_path,
    )

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code is None
    assert result.receipt.candidate_release_status is None
    assert result.receipt.open_record_count is None
    assert result.error_code == aq.BundleVerificationError.code


def test_snapshot_mutation_before_validation_is_error(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path)
    snapshot = _snapshot(
        tmp_path,
        work={"a.json": b'{"a":1}'},
        snapshot_id="snap-1",
    )
    (snapshot.work_dir / "a.json").write_bytes(b'{"a":2}')

    with _fake_trust(bundle[2]):
        result = aq.validate_snapshot(
            snapshot,
            run_id=42,
            stage="pass1",
            source_digest="a" * 64,
            bundle_root=bundle[0],
            manifest_path=bundle[1],
        )

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code is None
    assert result.receipt.open_record_count is None
    assert result.error_code == aq.SnapshotIntegrityError.code


def test_snapshot_mutation_after_validator_run_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _fake_bundle(tmp_path)
    snapshot = _snapshot(
        tmp_path,
        work={"a.json": b'{"a":1}'},
        snapshot_id="snap-1",
    )
    original_run = aq._run_validator

    def run_then_mutate(*args: object, **kwargs: object) -> aq._ProcessResult:
        result = original_run(*args, **kwargs)
        (snapshot.work_dir / "a.json").write_bytes(b'{"a":2}')
        return result

    monkeypatch.setattr(aq, "_run_validator", run_then_mutate)
    with _fake_trust(bundle[2]):
        result = aq.validate_snapshot(
            snapshot,
            run_id=42,
            stage="pass1",
            source_digest="a" * 64,
            bundle_root=bundle[0],
            manifest_path=bundle[1],
        )

    assert result.controller_status == "ERROR"
    assert result.receipt.validator_exit_code == 0
    assert result.receipt.open_record_count is None
    assert result.error_code == aq.SnapshotIntegrityError.code


def test_diagnostics_are_capped_per_stream(tmp_path: Path) -> None:
    result = _validate(tmp_path, control={"stdout_bytes": 100_000, "stderr_bytes": 100_000})

    assert result.controller_status == "PASS"
    assert len(result.stdout.encode("utf-8")) <= aq.DIAGNOSTIC_LIMIT_BYTES
    assert len(result.stderr.encode("utf-8")) <= aq.DIAGNOSTIC_LIMIT_BYTES
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


def test_receipt_is_atomic_and_existing_receipt_is_not_overwritten(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path)
    snapshot = _snapshot(
        tmp_path,
        work={"validator_control.json": json.dumps(_validator_control(tmp_path)).encode("utf-8")},
        snapshot_id="snap-1",
    )
    kwargs = {
        "run_id": 42,
        "stage": "pass1",
        "source_digest": "a" * 64,
        "bundle_root": bundle[0],
        "manifest_path": bundle[1],
    }
    with _fake_trust(bundle[2]):
        result = aq.validate_snapshot(snapshot, **kwargs)
    receipt_path = result.receipt_path
    original = receipt_path.read_bytes()
    assert [path for path in _receipt_files(tmp_path) if path.suffix == ".tmp"] == []
    assert not list(receipt_path.parent.glob(".*.tmp"))

    with _fake_trust(bundle[2]):
        with pytest.raises(aq.ReceiptExistsError):
            aq.validate_snapshot(snapshot, **kwargs)

    assert receipt_path.read_bytes() == original
    assert not list(receipt_path.parent.glob(".*.tmp"))


def test_receipt_write_failure_leaves_no_partial_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_link(source: Path, destination: Path) -> None:
        raise OSError("simulated link failure")

    monkeypatch.setattr(aq.os, "link", fail_link)

    with pytest.raises(aq.ReceiptError):
        _validate(tmp_path)

    assert _receipt_files(tmp_path) == []
    receipt_dir = tmp_path / "controller/receipts/snap-1"
    assert not list(receipt_dir.glob(".*.tmp"))


def test_actual_pinned_bundle_blocks_missing_ledger(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bundle_root = repo_root / ".orchestration/skill"
    manifest_path = repo_root / "docs/plans/BOQ_AUDIT_SKILL_MANIFEST.json"
    if not bundle_root.is_dir() or not manifest_path.is_file():
        pytest.skip("pinned validator bundle/manifest is not present")

    snapshot = _snapshot(tmp_path, work={}, output={}, snapshot_id="snap-1")
    result = aq.validate_snapshot(
        snapshot,
        run_id=42,
        stage="pass1",
        source_digest="a" * 64,
        bundle_root=bundle_root,
        manifest_path=manifest_path,
    )

    assert result.controller_status == "BLOCKED"
    assert result.receipt.validator_exit_code == 2
    assert result.receipt.candidate_release_status == "BLOCKED"
    assert result.receipt.open_record_count == 0
    assert result.receipt.blocking_checks
    assert result.receipt.skill_digest == aq.TRUSTED_SKILL_DIGEST
    assert not (snapshot.work_dir / "stage_pass1_result.json").exists()
