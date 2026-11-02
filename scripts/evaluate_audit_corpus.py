#!/usr/bin/env python3
"""Validate and deterministically score every package in a Q10 corpus manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evaluation.corpus_manifest import CorpusManifestError, _assert_holdout_not_granted, load_corpus


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = {
        key: sum(result["metrics"][key] for result in results)
        for key in ("tp", "fp", "fn", "unresolved", "unscored")
    }
    precision_denominator = metrics["tp"] + metrics["fp"]
    recall_denominator = metrics["tp"] + metrics["fn"]
    return {
        **metrics,
        "precision_denominator": precision_denominator,
        "recall_denominator": recall_denominator,
        "precision": metrics["tp"] / precision_denominator if precision_denominator else None,
        "recall": metrics["tp"] / recall_denominator if recall_denominator else None,
        "case_count": len(results),
        "completed_case_count": sum(result["attempt_status"] == "COMPLETED" for result in results),
    }


def evaluate_manifest(
    manifest_path: Path,
    runtime_read_paths: list[Path] | None = None,
    require_holdout: bool = False,
) -> dict[str, Any]:
    manifest, manifest_sha256, packages = load_corpus(manifest_path, require_holdout=require_holdout)
    root = manifest_path.resolve(strict=True).parent
    _assert_holdout_not_granted(root, manifest, runtime_read_paths or [])
    results_by_partition: dict[str, list[dict[str, Any]]] = {"DEVELOPMENT": [], "HOLDOUT": []}
    for package in packages:
        result = {
            **package["result"],
            "case_id": package["case_id"],
            "partition": package["partition"],
            "reference_sha256": package["reference_sha256"],
            "adjudication_sha256": package["adjudication_sha256"],
            "run_record_sha256": package["run_record_sha256"],
        }
        results_by_partition[package["partition"]].append(result)
    return {
        "schema_version": "q10.corpus_evaluation/1",
        "corpus_id": manifest["corpus_id"],
        "manifest_revision": manifest["manifest_revision"],
        "curator_id": manifest["curator_id"],
        "curated_at": manifest["curated_at"],
        "change_reason": manifest["change_reason"],
        "manifest_sha256": manifest_sha256,
        "partitions": {
            name: {"aggregate": _aggregate(results), "cases": results}
            for name, results in results_by_partition.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--runtime-read-path", action="append", default=[], type=Path)
    parser.add_argument("--require-holdout", action="store_true")
    args = parser.parse_args()
    try:
        result = evaluate_manifest(args.manifest, args.runtime_read_path, args.require_holdout)
    except (CorpusManifestError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
