# Q10 evaluation schemas and synthetic fixtures

Status: Q10a schema baseline, Q10b deterministic scorer, and Q10c offline manifest/isolation validator. This directory contains schemas, synthetic fixtures, and evaluation code only. It does **not** contain human-labeled real cases, runtime labels, pilot documents, customer data, or Kênh Xáng data.

All fixture filenames and SHA-256 values are synthetic labels. No source PDF, XLSX, or customer document is stored here.

## Layout

```text
evaluation/
├── README.md
├── schemas/
│   ├── reference.schema.json
│   ├── adjudication.schema.json
│   ├── run_record.schema.json
│   └── corpus_manifest.schema.json
├── corpus_manifest.py
├── test_corpus_manifest.py
├── test_evaluate_audit_quality.py
├── test_evaluate_audit_corpus.py
└── synthetic/
    ├── reference_confirmed.synthetic.json
    ├── reference_clean.synthetic.json
    ├── reference_ambiguous.synthetic.json
    ├── adjudication.synthetic.json
    ├── run_record_completed.synthetic.json
    ├── run_record_partial.synthetic.json
    └── run_record_failed.synthetic.json
```

`scripts/evaluate_audit_quality.py` reads one reference package, one adjudication, and one attempt record. Tests live in `evaluation/test_evaluate_audit_quality.py`.

## Schema versions

| File | `schema_version` | Purpose |
|---|---|---|
| `schemas/reference.schema.json` | `q10.reference/1` | Versioned reference package with source revisions/hashes, scope, exclusions, limitations, stable IDs, severity, evidence, numerical basis, and human adjudication status. |
| `schemas/adjudication.schema.json` | `q10.adjudication/1` | Human mapping of reported findings to at most one reference, plus missed-reference FN entries and duplicate behavior. |
| `schemas/run_record.schema.json` | `q10.run_record/1` | One retained attempt with pinned application/runtime/source/input/output digests, artifact hashes, and evaluation metrics. |

The schemas use JSON Schema draft 2020-12. The scorer applies its own identity, source-binding, and adjudication checks with the Python standard library; it does not claim full JSON Schema validation.

## Reference package rules

- A package has a stable `package_id`, `corpus_id`, `label_version`, `case_type`, and `data_origin`.
- `source.documents[]` carries each document's `doc_id`, filename, revision, SHA-256, and kind.
- `source_verification.status` must be `MATCH` for `SCOPED_CLEAN`; a source revision mismatch cannot be scored as clean. A non-clean package may retain `MISMATCH` with an explicit note/limitation so the rejected source stays in inventory.
- Every reference has a stable `ref_id`, `root_ref_id`, and `object_id`. Root references self-reference in `root_ref_id` in these fixtures; a later split/supersession may point to another root.
- Each reference carries `classification`, `severity`, `location`, `evidence[]`, `numerical_basis`, `adjudication_status`, `reviewer_id`, and `version`.
- `SCOPED_CLEAN` packages have zero references. `CONFIRMED_ERROR` and `SYNTHETIC_SEED` packages have at least one.

## Adjudication rules

- Each finding has a single `ref_id` (string or null), so it cannot map to more than one reference.
- Finding dispositions are `TP`, `FP`, `UNRESOLVED`, `OUT_OF_SCOPE`, and `DUPLICATE`.
- `counts_as_unique` is true for unique TP and FP findings, and false for unresolved, out-of-scope, and duplicate findings.
- A duplicate has a non-null `duplicate_of` and `disposition = DUPLICATE`; it may retain `ref_id` only when `duplicate_of` points to a root finding, and its `root_finding_id` must match that root. It never increases TP and cannot create another root mapping.
- A reference has one outcome: matched (TP), missed (FN in `missed_references[]`), or excluded as unresolved/out-of-scope. A TP reference cannot also appear in `missed_references[]`.
- A missed reference is recorded in `missed_references[]` with `disposition = FN`; it is not invented as a reported finding.
- The fixture deliberately maps a TP and its duplicate to the same root reference while counting only the TP.

## Run record rules

- One run record represents one attempt; `attempt_status` is `COMPLETED`, `PARTIAL`, `FAILED`, or `BLOCKED`. Failed and partial attempts are retained as their own records.
- `app`, `runtime`, `sources[]`, and `artifacts[]` pin application revision, deployment revision, model/provider, CLI/skill/prompt/config hashes, per-source hashes, and per-artifact hashes.
- `source_digest`, `input_digest`, and `output_digest` are explicit top-level digests. They follow the contract's separate source/snapshot-output boundaries and must not be conflated with per-source or per-artifact hashes. `output_digest` is null only when an attempt produced no output.
- Metrics are `tp`, `fp`, `fn`, `unresolved`, `unscored`, `precision`, `recall`, the two denominators, and `denominator_notes`.
- `unresolved` counts findings whose disposition is `UNRESOLVED`. `unscored` counts reference labels excluded from scoring, such as a label whose own status is `UNRESOLVED`.
- Precision denominator is `tp + fp`; recall denominator is `tp + fn`.
- When a denominator is zero, the corresponding metric is `null` and `denominator_notes` contains an explicit `N/A` note. A failed attempt must not be reported as perfect precision/recall.

## Synthetic fixtures

The fixtures cover the three Q10 groups: confirmed errors, independently checked clean scope, and missing/ambiguous data.

| Fixture | What it demonstrates |
|---|---|
| `reference_confirmed.synthetic.json` | `SYNTHETIC_SEED`; one TP quantity reference, one unresolved geometry reference, one confirmed omission that the report misses (FN). |
| `reference_clean.synthetic.json` | `SCOPED_CLEAN`; zero references and `source_verification.status = MATCH`. |
| `reference_ambiguous.synthetic.json` | `AMBIGUOUS`; one unresolved geometry reference that must not be treated as clean or as a confirmed numerical error. |
| `adjudication.synthetic.json` | TP, FP, duplicate, unresolved, out-of-scope, and one missed-reference FN. |
| `run_record_completed.synthetic.json` | Completed attempt; `tp=1`, `fp=1`, `fn=1`, `unresolved=1`, `unscored=1`; precision `0.5`, recall `0.5`. |
| `run_record_partial.synthetic.json` | Partial attempt retained; `tp=1`, `fp=0`, `fn=0`, `unscored=2`; precision `1.0`, recall `1.0`, explicitly not eligible for pass. One confirmed reference not reached by the partial attempt is unscored, not assumed clean. |
| `run_record_failed.synthetic.json` | Failed attempt retained; zero denominators, `precision=null`, `recall=null`, explicit N/A notes, `output_digest=null`, no artifacts. |

## Self-check

Run the deterministic scorer on the synthetic completed case:

```sh
python scripts/evaluate_audit_quality.py \
  --reference evaluation/synthetic/reference_confirmed.synthetic.json \
  --adjudication evaluation/synthetic/adjudication.synthetic.json \
  --run-record evaluation/synthetic/run_record_completed.synthetic.json
python -m pip install -r backend/requirements-dev.txt
python -m unittest evaluation.test_evaluate_audit_quality evaluation.test_corpus_manifest evaluation.test_evaluate_audit_corpus -v
python scripts/evaluate_audit_corpus.py \
  --manifest /secure/corpus/manifest.json \
  --require-holdout \
  --runtime-read-path /path/to/audit/runtime/source.pdf
```

The scorer requires source verification `MATCH`, current reference versions, and exact document ID/revision/hash agreement. A completed attempt must account for each adjudicated reference as TP or source-checked FN. Partial/failed attempts retain explicitly adjudicated findings; references not reached are counted as unscored, never as clean or FN. Duplicate findings do not increase TP/FP. A zero denominator yields `null` plus an N/A note. `scoring_complete` means only that this completed attempt has no unresolved or unscored reference; it does not determine holdout eligibility or an aggregate pilot pass.

The committed evaluation suite uses `jsonschema` to check all four Draft 2020-12 schema definitions, validate all seven synthetic fixtures against their matching schemas, and validate the generated corpus manifest. It also checks unique schema `$id`s, filename/version alignment, synthetic origins, and a negative required-field mutation. The scorer and manifest tests separately cover identity, source binding, adjudication, path, and holdout-grant invariants.

Last run result:

```text
Ran 41 tests
OK
```

The CI test dependency pins `jsonschema[format]` so format constraints such as `date-time` are checked as well as structural constraints.

## Q10c manifest and holdout isolation

`schemas/corpus_manifest.schema.json` describes metadata for externally stored reference, adjudication, and run-record files. `evaluation/corpus_manifest.py` validates the manifest and each referenced reference/adjudication/run-record file against their Draft 2020-12 schemas, validates cross-file identities by running the deterministic scorer, rejects paths escaping the corpus root or direct file symlinks, and rejects source-family, audit-package, or reference-package IDs appearing in both DEVELOPMENT and HOLDOUT. It also rejects duplicate adjudication/run-record bytes and duplicate run-record/attempt IDs across cases; one attempt cannot be counted twice, even if its record is reserialized with different bytes. Source-family IDs must represent original-document lineage across revisions; the split is assigned by a human curator and is never inferred from model output.

Each manifest package pins the SHA-256 of its reference, adjudication, and run-record bytes. The evaluator rejects stale digests and includes the manifest and package digests in its output so metrics can be tied back to the exact label set and attempt record.

The manifest also requires a curator ID, curation timestamp, positive manifest revision, and change reason. These fields document split ownership and edits; they are audit metadata, not a cryptographic signature or proof of curator authorization. Keep the manifest in access-controlled storage and retain its revision history.

The offline evaluator calls `assert_holdout_not_granted(manifest_path, runtime_read_paths)` before scoring. It rejects exact or nested directory grants that expose holdout reference/adjudication/run-record files, including symlink and hard-link aliases; it also rejects a symlinked manifest. The scorer rejects missing/cyclic reference roots and treats valid split child references as separate labels; malformed deeply nested JSON becomes a typed evaluation error. This guard is not wired into the audit worker. The worker sandbox grants only explicit source/controller files, and `:root` is denied, but a live integration test must verify the actual Codex filesystem policy before claiming holdout isolation. Keep the manifest and all referenced human labels outside Git and outside audit workspaces/runtime mounts. Do not place holdout labels beside source PDFs in an audit job workspace.

`scripts/evaluate_audit_corpus.py` validates the manifest and labels, checks each supplied runtime read path against holdout files, scores every package, and emits per-case results with separate DEVELOPMENT and HOLDOUT aggregate metrics. `--require-holdout` requires both partitions. Aggregates are descriptive only; they do not decide pilot eligibility.

This scaffolding does not create or approve real labels. A reviewer must verify source bytes/revisions and adjudicate each case before a corpus manifest is accepted.

## NOT_RUN and limitations

- Real labeled corpus, holdout labels, pilot-document updates, and live Codex isolation: NOT_RUN; require reviewer-approved data and a runtime integration/canary.
- Historical Python 3.12 run before the latest Q10 follow-up: backend suite passed 444 tests (1 Starlette deprecation warning, 69.14s, outside sandbox); focused evaluation/corpus suite passed 21 tests after manifest-schema and grant-alias guards. The managed sandbox full-suite run had an AnyIO stall and was not used for that passing result. Current focused Q10 count is recorded above.
- These results do not include human-labeled corpus evaluation or a live holdout runner.
- Fixture source files and artifact files are not present; only synthetic filenames and hashes are recorded.
- These schemas are v1; live holdout evaluation through the audit worker/runtime is not implemented here.

## Lead decisions (accepted)

1. One reference package per evaluated case; package-level scope is sufficient.
2. `SCOPED_CLEAN` requires `source_verification.status = MATCH`. `MISMATCH` may be represented for non-clean inventory but cannot be scored clean.
3. `unscored` means reference labels excluded from scoring, distinct from unresolved findings.
4. A duplicate finding may retain `ref_id` only when `duplicate_of` points to a root finding. It never counts as TP and cannot create another root mapping.
5. One `run_record` per attempt; every attempt is retained as its own record.
6. The current v1 classification/severity enums are accepted, including `OTHER` and the `CRITICAL/HIGH/MEDIUM/LOW/NOTE` palette; do not expand without adjudicated need.
7. `source_digest`, `input_digest`, and `output_digest` are explicit run-record fields, consistent with the contract.

No open Q10a schema decisions remain.
