"""Validate an offline, human-maintained Q10 corpus manifest."""

from __future__ import annotations

import json
import hashlib
import os
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from scripts.evaluate_audit_quality import score


class CorpusManifestError(ValueError):
    pass


_ID = re.compile(r"^[A-Z][A-Z0-9_-]{2,63}$")


def _read_json_document(path: Path) -> tuple[dict[str, Any], str]:
    try:
        content = path.read_bytes()
        value = json.loads(content)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CorpusManifestError(f"cannot read valid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise CorpusManifestError(f"expected JSON object: {path.name}")
    return value, hashlib.sha256(content).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return _read_json_document(path)[0]


@lru_cache(maxsize=4)
def _schema_validator(name: str) -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parent / "schemas" / name
    schema = _read_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_schema(value: dict[str, Any], name: str, label: str) -> None:
    try:
        _schema_validator(name).validate(value)
    except (SchemaError, ValidationError) as exc:
        raise CorpusManifestError(f"{label} does not match the Q10 schema") from exc


def _contained_file(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise CorpusManifestError(f"{label} must be a relative file path")
    candidate = root / value
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise CorpusManifestError(f"{label} does not exist") from exc
    if not resolved.is_relative_to(root) or candidate.is_symlink() or not resolved.is_file():
        raise CorpusManifestError(f"{label} must be a regular file inside the corpus root")
    return resolved


def load_corpus(path: Path, require_holdout: bool = False) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """Read and validate a manifest and each digest-pinned package exactly once."""
    try:
        manifest_path = path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise CorpusManifestError("manifest must be an existing regular file") from exc
    if path.is_symlink() or not manifest_path.is_file():
        raise CorpusManifestError("manifest must be a regular file")
    root = manifest_path.parent.resolve(strict=True)
    manifest, manifest_sha256 = _read_json_document(manifest_path)
    _validate_schema(manifest, "corpus_manifest.schema.json", "manifest")
    if manifest.get("schema_version") != "q10.corpus_manifest/1" or not isinstance(manifest.get("corpus_id"), str) or not _ID.fullmatch(manifest["corpus_id"]):
        raise CorpusManifestError("manifest schema version or corpus ID is invalid")
    packages = manifest.get("packages")
    if not isinstance(packages, list) or not packages:
        raise CorpusManifestError("manifest packages must be a non-empty list")

    seen_packages: set[str] = set()
    seen_adjudications: set[str] = set()
    seen_run_records: set[str] = set()
    seen_run_record_ids: set[str] = set()
    seen_attempt_ids: set[str] = set()
    reference_partitions: dict[str, str] = {}
    reference_package_partitions: dict[str, str] = {}
    audit_package_partitions: dict[str, str] = {}
    family_partitions: dict[str, str] = {}
    partitions: set[str] = set()
    validated_packages = []
    for entry in packages:
        if not isinstance(entry, dict):
            raise CorpusManifestError("package entry must be an object")
        package_id = entry.get("case_id")
        audit_package_id = entry.get("audit_package_id")
        reference_package_id = entry.get("reference_package_id")
        partition = entry.get("partition")
        if not isinstance(package_id, str) or not _ID.fullmatch(package_id) or package_id in seen_packages:
            raise CorpusManifestError("package IDs must be valid and unique")
        if partition not in {"DEVELOPMENT", "HOLDOUT"}:
            raise CorpusManifestError("package partition must be DEVELOPMENT or HOLDOUT")
        if not all(isinstance(value, str) and _ID.fullmatch(value) for value in (audit_package_id, reference_package_id)):
            raise CorpusManifestError("audit/reference package IDs are invalid")
        previous_reference_partition = reference_package_partitions.setdefault(reference_package_id, partition)
        if previous_reference_partition != partition:
            raise CorpusManifestError("reference package ID appears in both development and holdout")
        previous_audit_partition = audit_package_partitions.setdefault(audit_package_id, partition)
        if previous_audit_partition != partition:
            raise CorpusManifestError("audit package ID appears in both development and holdout")
        seen_packages.add(package_id)
        partitions.add(partition)

        family_ids = entry.get("source_family_ids")
        if not isinstance(family_ids, list) or not family_ids or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in family_ids) or len(set(family_ids)) != len(family_ids):
            raise CorpusManifestError("source_family_ids must be a non-empty unique ID list")
        for family_id in family_ids:
            previous = family_partitions.setdefault(family_id, partition)
            if previous != partition:
                raise CorpusManifestError("source family appears in both development and holdout")

        reference_path = _contained_file(root, entry.get("reference_path"), "reference_path")
        adjudication_path = _contained_file(root, entry.get("adjudication_path"), "adjudication_path")
        run_record_path = _contained_file(root, entry.get("run_record_path"), "run_record_path")
        if len({reference_path, adjudication_path, run_record_path}) != 3:
            raise CorpusManifestError("reference, adjudication, and run record must be separate files")
        reference, reference_sha256 = _read_json_document(reference_path)
        adjudication, adjudication_sha256 = _read_json_document(adjudication_path)
        run_record, run_record_sha256 = _read_json_document(run_record_path)
        for key, actual_digest in (
            ("reference_sha256", reference_sha256),
            ("adjudication_sha256", adjudication_sha256),
            ("run_record_sha256", run_record_sha256),
        ):
            if entry[key] != actual_digest:
                raise CorpusManifestError(f"case {package_id} {key} does not match package bytes")
        if adjudication_sha256 in seen_adjudications:
            raise CorpusManifestError("adjudication file is listed more than once")
        if run_record_sha256 in seen_run_records:
            raise CorpusManifestError("run record file is listed more than once")
        reference_partition = reference_partitions.setdefault(reference_sha256, partition)
        if reference_partition != partition:
            raise CorpusManifestError("reference package is used in both development and holdout")
        seen_adjudications.add(adjudication_sha256)
        seen_run_records.add(run_record_sha256)
        _validate_schema(reference, "reference.schema.json", f"case {package_id} reference")
        _validate_schema(adjudication, "adjudication.schema.json", f"case {package_id} adjudication")
        _validate_schema(run_record, "run_record.schema.json", f"case {package_id} run record")
        if run_record["run_record_id"] in seen_run_record_ids or run_record["attempt_id"] in seen_attempt_ids:
            raise CorpusManifestError("run record or attempt ID is listed more than once")
        seen_run_record_ids.add(run_record["run_record_id"])
        seen_attempt_ids.add(run_record["attempt_id"])
        if reference.get("corpus_id") != manifest["corpus_id"] or reference.get("package_id") != reference_package_id:
            raise CorpusManifestError("reference identity does not match manifest")
        if adjudication.get("corpus_id") != manifest["corpus_id"] or adjudication.get("package_id") != audit_package_id or adjudication.get("reference_package_id") != reference_package_id:
            raise CorpusManifestError("adjudication identity does not match manifest")
        try:
            result = score(reference, adjudication, run_record)
        except ValueError as exc:
            raise CorpusManifestError(f"case {package_id} failed deterministic scoring validation") from exc
        validated_packages.append({
            "case_id": package_id,
            "partition": partition,
            "reference_sha256": reference_sha256,
            "adjudication_sha256": adjudication_sha256,
            "run_record_sha256": run_record_sha256,
            "result": result,
        })

    if require_holdout and partitions != {"DEVELOPMENT", "HOLDOUT"}:
        raise CorpusManifestError("both development and holdout packages are required")
    return manifest, manifest_sha256, validated_packages


def load_manifest(path: Path, require_holdout: bool = False) -> dict[str, Any]:
    manifest, _manifest_sha256, _packages = load_corpus(path, require_holdout=require_holdout)
    return manifest


def _assert_holdout_not_granted(root: Path, manifest: dict[str, Any], runtime_read_paths: list[Path]) -> None:
    if not isinstance(runtime_read_paths, list):
        raise CorpusManifestError("runtime read paths must be a list")
    holdout_labels: set[Path] = set()
    for entry in manifest["packages"]:
        if entry["partition"] == "HOLDOUT":
            for key in ("reference_path", "adjudication_path", "run_record_path"):
                holdout_labels.add(_contained_file(root, entry[key], key))
    for item in runtime_read_paths:
        try:
            granted = Path(item).resolve(strict=True)
        except (OSError, ValueError, TypeError) as exc:
            raise CorpusManifestError("runtime read path must be an existing file or directory") from exc
        if granted in holdout_labels or (
            granted.is_file() and any(os.path.samefile(granted, label) for label in holdout_labels)
        ) or (granted.is_dir() and any(label.is_relative_to(granted) for label in holdout_labels)):
            raise CorpusManifestError("holdout labels cannot be granted to audit runtime")
        if granted.is_dir():
            def fail_on_walk_error(error: OSError) -> None:
                raise CorpusManifestError("cannot inspect runtime grant directory") from error

            for current, directories, files in os.walk(granted, onerror=fail_on_walk_error, followlinks=False):
                for name in directories + files:
                    candidate = Path(current) / name
                    if candidate.is_symlink():
                        candidate = candidate.resolve(strict=True)
                        if candidate in holdout_labels or (
                            candidate.is_dir() and any(label.is_relative_to(candidate) for label in holdout_labels)
                        ):
                            raise CorpusManifestError("holdout labels cannot be granted to audit runtime")
                    elif candidate.is_file() and any(os.path.samefile(candidate, label) for label in holdout_labels):
                        raise CorpusManifestError("holdout labels cannot be granted to audit runtime")


def assert_holdout_not_granted(manifest_path: Path, runtime_read_paths: list[Path]) -> None:
    """Fail if any holdout labels are present in the exact runtime read allowlist."""
    try:
        manifest_path = Path(manifest_path)
    except TypeError as exc:
        raise CorpusManifestError("manifest must be an existing regular file") from exc
    if manifest_path.is_symlink():
        raise CorpusManifestError("manifest must be a regular file")
    try:
        manifest_path = manifest_path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise CorpusManifestError("manifest must be an existing regular file") from exc
    manifest, _manifest_sha256, _packages = load_corpus(manifest_path)
    _assert_holdout_not_granted(manifest_path.parent, manifest, runtime_read_paths)
