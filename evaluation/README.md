# Q10 evaluation schemas and synthetic fixtures

Status: Q10a schema baseline with lead decisions applied. This directory contains schemas and synthetic fixtures only. It does **not** contain a scorer, a loader, runtime labels, pilot documents, customer data, or Kênh Xáng data.

All fixture filenames and SHA-256 values are synthetic labels. No source PDF, XLSX, or customer document is stored here.

## Layout

```text
evaluation/
├── README.md
├── schemas/
│   ├── reference.schema.json
│   ├── adjudication.schema.json
│   └── run_record.schema.json
└── synthetic/
    ├── reference_confirmed.synthetic.json
    ├── reference_clean.synthetic.json
    ├── reference_ambiguous.synthetic.json
    ├── adjudication.synthetic.json
    ├── run_record_completed.synthetic.json
    ├── run_record_partial.synthetic.json
    └── run_record_failed.synthetic.json
```

## Schema versions

| File | `schema_version` | Purpose |
|---|---|---|
| `schemas/reference.schema.json` | `q10.reference/1` | Versioned reference package with source revisions/hashes, scope, exclusions, limitations, stable IDs, severity, evidence, numerical basis, and human adjudication status. |
| `schemas/adjudication.schema.json` | `q10.adjudication/1` | Human mapping of reported findings to at most one reference, plus missed-reference FN entries and duplicate behavior. |
| `schemas/run_record.schema.json` | `q10.run_record/1` | One retained attempt with pinned application/runtime/source/input/output digests, artifact hashes, and evaluation metrics. |

The schemas use JSON Schema draft 2020-12 and are intentionally not yet wired into a scorer or loader.

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
| `run_record_partial.synthetic.json` | Partial attempt retained; `tp=1`, `fp=0`, `fn=0`; precision `1.0`, recall `1.0`, explicitly not eligible for pass. |
| `run_record_failed.synthetic.json` | Failed attempt retained; zero denominators, `precision=null`, `recall=null`, explicit N/A notes, `output_digest=null`, no artifacts. |

## Self-check

The Q10a check is minimal and stdlib-only in the repository's default Python (where `jsonschema` is not installed). It parses every JSON file, checks unique schema `$id` values and schema versions, checks fixture labels/`data_origin`, stable/unique IDs, duplicate and FN rules, denominator/metric consistency, top-level source/input/output digest presence, repeated synthetic hashes, and scans for banned real-data markers and binary source files. It was run inline; no self-check script is committed because Q10a is limited to README, schemas, and synthetic fixtures.

Last run result:

```text
SELF-CHECK PASS
schemas=3 fixtures=7 errors=0
```

An optional full Draft 2020-12 validation was also run with a locally installed `jsonschema` validator (Python 3.12): all 3 schemas passed `check_schema`, and all 7 fixtures validated. Twelve negative mutations were rejected, including clean-with-source-mismatch, duplicate-counted-as-TP, TP-without-reference, zero-denominator-with-numeric-metric, missing root ID, missed-reference-not-FN, missing skill/source digest, and completed/partial attempts without an output digest. That validator is not part of the repository self-check or the project test environment.

## NOT_RUN and limitations

- Scorer, loader, runtime labels, holdout labels, and pilot-document updates: NOT_RUN and out of Q10a scope.
- Backend pytest: NOT_RUN, as required.
- Python 3.12 CI compatibility: NOT_RUN; the repository stdlib check used Python 3.14, while the optional schema validation used a separate Python 3.12 environment.
- Full validation with the project's declared test environment: NOT_RUN because `/tmp/boq-excel-testvenv-20261023` is not present in this checkout.
- Fixture source files and artifact files are not present; only synthetic filenames and hashes are recorded.
- These schemas are v1; Q10b/evaluator wiring is not implemented here.

## Lead decisions (accepted)

1. One reference package per evaluated case; package-level scope is sufficient.
2. `SCOPED_CLEAN` requires `source_verification.status = MATCH`. `MISMATCH` may be represented for non-clean inventory but cannot be scored clean.
3. `unscored` means reference labels excluded from scoring, distinct from unresolved findings.
4. A duplicate finding may retain `ref_id` only when `duplicate_of` points to a root finding. It never counts as TP and cannot create another root mapping.
5. One `run_record` per attempt; every attempt is retained as its own record.
6. The current v1 classification/severity enums are accepted, including `OTHER` and the `CRITICAL/HIGH/MEDIUM/LOW/NOTE` palette; do not expand without adjudicated need.
7. `source_digest`, `input_digest`, and `output_digest` are explicit run-record fields, consistent with the contract.

No open Q10a schema decisions remain.
