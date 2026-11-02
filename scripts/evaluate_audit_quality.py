#!/usr/bin/env python3
"""Deterministically score a human-adjudicated BOQ audit attempt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


class EvaluationError(ValueError):
    pass


_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{2,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvaluationError(f"cannot read valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"expected a JSON object: {path}")
    return value


def _require_same(left: dict[str, Any], right: dict[str, Any], keys: tuple[str, ...], label: str) -> None:
    if any(
        not isinstance(left.get(key), str)
        or not _IDENTIFIER.fullmatch(left[key])
        or left.get(key) != right.get(key)
        for key in keys
    ):
        raise EvaluationError(f"{label} identity does not match")


def _source_map(sources: list[dict[str, Any]], label: str) -> dict[str, tuple[str, str]]:
    if not isinstance(sources, list):
        raise EvaluationError(f"{label} sources must be a list")
    result: dict[str, tuple[str, str]] = {}
    for item in sources:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("doc_id"), str)
            or not _IDENTIFIER.fullmatch(item["doc_id"])
            or not isinstance(item.get("revision"), str)
            or not item["revision"].strip()
            or not isinstance(item.get("sha256"), str)
            or not _SHA256.fullmatch(item["sha256"])
        ):
            raise EvaluationError(f"{label} contains an invalid source entry")
        doc_id = item["doc_id"]
        if doc_id in result:
            raise EvaluationError(f"{label} contains duplicate source document IDs")
        result[doc_id] = item["revision"], item["sha256"]
    return result


def score(reference: dict[str, Any], adjudication: dict[str, Any], run_record: dict[str, Any]) -> dict[str, Any]:
    """Score only explicit, source-matched, one-to-one human adjudications."""
    if not all(isinstance(item, dict) for item in (reference, adjudication, run_record)):
        raise EvaluationError("reference, adjudication, and run record must be JSON objects")
    _require_same(adjudication, run_record, ("corpus_id", "package_id", "data_origin"), "adjudication/run")
    _require_same(adjudication, run_record, ("run_record_id", "attempt_id"), "adjudication/run attempt")
    if reference.get("corpus_id") != adjudication.get("corpus_id") or reference.get("data_origin") != adjudication.get("data_origin"):
        raise EvaluationError("reference corpus or data origin does not match")
    if not isinstance(reference.get("package_id"), str) or not _IDENTIFIER.fullmatch(reference["package_id"]) or reference.get("package_id") != run_record.get("reference_package_id"):
        raise EvaluationError("run points to a different reference package")
    if adjudication.get("package_id") != run_record.get("package_id"):
        raise EvaluationError("adjudication points to a different audit package")
    if not isinstance(adjudication.get("reference_package_id"), str) or not _IDENTIFIER.fullmatch(adjudication["reference_package_id"]) or adjudication.get("reference_package_id") != reference.get("package_id"):
        raise EvaluationError("adjudication points to a different reference package")
    if adjudication.get("data_origin") not in {"SYNTHETIC", "ANONYMIZED_APPROVED", "REAL_APPROVED"}:
        raise EvaluationError("data origin is invalid")
    if not isinstance(reference.get("label_version"), int) or isinstance(reference.get("label_version"), bool) or reference["label_version"] < 1:
        raise EvaluationError("reference label version is invalid")
    if adjudication.get("reference_version") != reference.get("label_version"):
        raise EvaluationError("adjudication reference version is stale")
    if not isinstance(adjudication.get("reference_version"), int) or isinstance(adjudication.get("reference_version"), bool) or adjudication["reference_version"] < 1:
        raise EvaluationError("adjudication reference version is invalid")
    if not isinstance(run_record.get("reference_version"), int) or isinstance(run_record.get("reference_version"), bool) or run_record.get("reference_version") < 1 or run_record.get("reference_version") != reference.get("label_version"):
        raise EvaluationError("run record reference version is stale")
    verification = reference.get("source_verification")
    if not isinstance(verification, dict) or verification.get("status") != "MATCH":
        raise EvaluationError("reference source is not verified as MATCH")

    source = reference.get("source")
    if not isinstance(source, dict):
        raise EvaluationError("reference source block is invalid")
    reference_sources = _source_map(source.get("documents"), "reference")
    run_sources = _source_map(run_record.get("sources", []), "run record")
    if not reference_sources or reference_sources != run_sources:
        raise EvaluationError("run source IDs, revisions, or hashes do not match the reference")

    references = reference.get("references")
    findings = adjudication.get("findings")
    missed = adjudication.get("missed_references")
    if not isinstance(references, list) or not isinstance(findings, list) or not isinstance(missed, list):
        raise EvaluationError("reference or adjudication collections are invalid")
    refs: dict[str, dict[str, Any]] = {}
    for item in references:
        if not isinstance(item, dict) or not isinstance(item.get("ref_id"), str) or not _IDENTIFIER.fullmatch(item["ref_id"]) or item["ref_id"] in refs:
            raise EvaluationError("reference IDs must be valid and unique")
        label_status = item.get("adjudication_status")
        if not isinstance(label_status, str) or label_status not in {"ADJUDICATED", "UNRESOLVED", "EXCLUDED"}:
            raise EvaluationError("reference adjudication status is invalid")
        refs[item["ref_id"]] = item

    for ref_id, item in refs.items():
        root_ref_id = item.get("root_ref_id")
        if not isinstance(root_ref_id, str) or not _IDENTIFIER.fullmatch(root_ref_id):
            raise EvaluationError("reference root ID is invalid")
        if root_ref_id not in refs:
            raise EvaluationError("reference root ID does not exist")
    for ref_id in refs:
        visited: set[str] = set()
        current = ref_id
        while refs[current]["root_ref_id"] != current:
            if current in visited:
                raise EvaluationError("reference root chain contains a cycle")
            visited.add(current)
            current = refs[current]["root_ref_id"]

    findings_by_id: dict[str, dict[str, Any]] = {}
    for item in findings:
        if not isinstance(item, dict) or not isinstance(item.get("finding_id"), str) or not _IDENTIFIER.fullmatch(item["finding_id"]) or item["finding_id"] in findings_by_id:
            raise EvaluationError("finding IDs must be valid and unique")
        findings_by_id[item["finding_id"]] = item
    tp_refs: set[str] = set()
    fn_refs: set[str] = set()
    tp = fp = unresolved = 0

    for item in findings:
        finding_id = item["finding_id"]
        disposition = item.get("disposition")
        duplicate_of = item.get("duplicate_of")
        ref_id = item.get("ref_id")
        if disposition == "TP":
            if not isinstance(ref_id, str) or not _IDENTIFIER.fullmatch(ref_id) or ref_id not in refs or ref_id in tp_refs or duplicate_of is not None or item.get("counts_as_unique") is not True or item.get("evidence_checked") is not True:
                raise EvaluationError("TP must map once to an existing reference")
            if item.get("root_finding_id") != finding_id:
                raise EvaluationError("unique TP must be its own root finding")
            if refs[ref_id].get("adjudication_status") != "ADJUDICATED":
                raise EvaluationError("TP cannot map to an unadjudicated reference")
            tp_refs.add(ref_id)
            tp += 1
        elif disposition == "FP":
            if ref_id is not None or duplicate_of is not None or item.get("counts_as_unique") is not True or item.get("evidence_checked") is not True:
                raise EvaluationError("FP must be a unique finding without a reference")
            if item.get("root_finding_id") != finding_id:
                raise EvaluationError("unique FP must be its own root finding")
            fp += 1
        elif disposition == "UNRESOLVED":
            if ref_id is not None or duplicate_of is not None or item.get("counts_as_unique") is not False or not isinstance(item.get("evidence_checked"), bool):
                raise EvaluationError("unresolved finding cannot claim a reference or unique count")
            unresolved += 1
        elif disposition == "OUT_OF_SCOPE":
            if ref_id is not None or duplicate_of is not None or item.get("counts_as_unique") is not False or not isinstance(item.get("evidence_checked"), bool):
                raise EvaluationError("out-of-scope finding cannot claim a reference or unique count")
        elif disposition == "DUPLICATE":
            root_id = duplicate_of
            if not isinstance(root_id, str) or not _IDENTIFIER.fullmatch(root_id):
                raise EvaluationError("duplicate must point to a unique root finding")
            root = findings_by_id.get(root_id)
            if (
                not root
                or not isinstance(root.get("disposition"), str)
                or root.get("disposition") not in {"TP", "FP"}
                or root.get("duplicate_of") is not None
                or root.get("root_finding_id") != root_id
                or item.get("root_finding_id") != root.get("root_finding_id")
                or item.get("counts_as_unique") is not False
            ):
                raise EvaluationError("duplicate must point to a unique root finding")
            if item.get("ref_id") != root.get("ref_id"):
                raise EvaluationError("duplicate reference must match its root finding")
        else:
            raise EvaluationError("unknown finding disposition")

    for item in missed:
        if not isinstance(item, dict) or item.get("disposition") != "FN" or item.get("source_checked") is not True:
            raise EvaluationError("missed reference must be source-checked FN")
        ref_id = item.get("ref_id")
        if not isinstance(ref_id, str) or not _IDENTIFIER.fullmatch(ref_id):
            raise EvaluationError("missed reference ID is invalid")
        if ref_id not in refs or ref_id in fn_refs or ref_id in tp_refs:
            raise EvaluationError("FN must map once to an existing, unmatched reference")
        if refs[ref_id].get("adjudication_status") != "ADJUDICATED":
            raise EvaluationError("FN cannot map to an unadjudicated reference")
        fn_refs.add(ref_id)

    scorable_refs = {ref_id for ref_id, item in refs.items() if item.get("adjudication_status") == "ADJUDICATED"}
    unmatched_refs = scorable_refs - tp_refs - fn_refs
    status = run_record.get("attempt_status")
    if not isinstance(status, str) or status not in {"COMPLETED", "PARTIAL", "FAILED", "BLOCKED"}:
        raise EvaluationError("run record attempt status is invalid")
    app = run_record.get("app")
    runtime = run_record.get("runtime")
    if not isinstance(app, dict) or not isinstance(runtime, dict):
        raise EvaluationError("run record app/runtime blocks are invalid")
    if status == "COMPLETED" and unmatched_refs:
        raise EvaluationError("every adjudicated reference must be matched once or listed as a missed FN")
    unscored = len(refs) - len(scorable_refs) + len(unmatched_refs)

    precision_denominator = tp + fp
    recall_denominator = tp + len(fn_refs)
    notes = []
    if not precision_denominator:
        notes.append("precision: N/A (zero denominator)")
    if not recall_denominator:
        notes.append("recall: N/A (zero denominator)")
    if unresolved:
        notes.append("Unresolved findings are excluded from precision and recall denominators.")
    if unscored:
        notes.append("Unresolved/excluded reference labels are retained as unscored.")
    if status != "COMPLETED":
        notes.append(f"{status or 'Unknown'} attempt retained; metrics are not eligible as a completed pilot case.")
    if unmatched_refs:
        notes.append("References not reached by this incomplete attempt are unscored, not false negatives.")

    metrics = {
        "tp": tp,
        "fp": fp,
        "fn": len(fn_refs),
        "unresolved": unresolved,
        "unscored": unscored,
        "precision": tp / precision_denominator if precision_denominator else None,
        "recall": tp / recall_denominator if recall_denominator else None,
        "precision_denominator": precision_denominator,
        "recall_denominator": recall_denominator,
        "denominator_notes": notes,
    }
    return {
        "schema_version": "q10.evaluation_result/1",
        "corpus_id": reference["corpus_id"],
        "package_id": run_record["package_id"],
        "reference_package_id": reference["package_id"],
        "reference_version": reference["label_version"],
        "run_record_id": run_record["run_record_id"],
        "attempt_id": run_record["attempt_id"],
        "data_origin": reference["data_origin"],
        "attempt_status": status,
        "scoring_complete": status == "COMPLETED" and not unresolved and not unscored,
        "run_context": {
            "app_revision": app.get("app_revision"),
            "model_id": runtime.get("model_id"),
            "skill_digest": runtime.get("skill_digest"),
            "prompt_sha256": runtime.get("prompt_sha256"),
            "config_sha256": runtime.get("config_sha256"),
            "source_digest": run_record.get("source_digest"),
            "input_digest": run_record.get("input_digest"),
            "output_digest": run_record.get("output_digest"),
        },
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--adjudication", required=True, type=Path)
    parser.add_argument("--run-record", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = score(_load(args.reference), _load(args.adjudication), _load(args.run_record))
    except EvaluationError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
