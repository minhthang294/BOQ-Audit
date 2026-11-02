# Q00 audit-quality synthetic fixtures

**Synthetic metadata only — not engineering evidence.** These fixtures exercise the pinned BOQ Audit v6.4 validator (`scripts/validate_run.py`) and the Q00b data contract. No customer/Kênh Xáng data is present.

## Files

- `complete_metadata.json` — object `files`: mapping of the 12 JSON stems consumed by the validator to their JSON values:
  `run_manifest`, `checkpoint`, `page_ledger`, `detail_rows`, `summary_lineage`, `calculation_ledger`, `issues`, `regression_checks`, `assumption_ledger`, `pass2_review`, `pass3_review`, `annotation_qa`.
- `negative_cases.json` — mutations applied to a deep copy of the complete fixture. Supported ops: `delete_file`, `delete_array_item`, `set`, `write_json`.
- This README.

Provenance: values are adapted from the synthetic fixture in `.orchestration/skill/tests/test_workflow.py:25-54`; contract mapping is `docs/plans/BOQ_AUDIT_DATA_CONTRACT.md`. Baseline `8cdacddc…`, skill digest `17c14f63…a2c`, validator `86839c…`.

## Materialization rule

A test harness must, in a private temporary directory:

1. Create sibling directories `work/` and `output/`.
2. Write each `files[stem]` value to `work/<stem>` as UTF-8 JSON.
3. Set `checkpoint.json.stage` to the stage being requested (`pass1|pass2|pass3|release`); the controller must verify it matches.
4. Create **placeholder bytes outside this fixture**:
   - `output/report.xlsx`
   - `output/report.pdf`
   These are not committed and prove only that the validator accepts non-empty paths/bytes. They do not prove XLSX/PDF validity.
5. Run the pinned validator with argv only:
   `[python, .orchestration/skill/scripts/validate_run.py, <tmp>/work, --stage, <stage>]`
6. Read the fresh report (`stage_<stage>_result.json` or `coverage_result.json`), not stdout or a pre-existing gate file.

No `.xlsx`, `.pdf`, or generated gate result is part of the fixture.

## Commands used (Q00b, NOT backend tests)

```sh
/tmp/boq-excel-testvenv-20261023/bin/python - <<'PY'
# Load complete_metadata.json; materialize 12 JSON files under a tempfile work/;
# create placeholder output/report.xlsx and output/report.pdf; set checkpoint.stage
# per stage; run validator with subprocess argv; assert exit/status and reset between runs.
PY
```

Negative cases use the same materialization, then apply `negative_cases.json` mutations before running the case's declared stage.

## Observed results (Q00b, Python 3.14.8 venv)

| Run | Stage | checkpoint.stage | Exit | candidate_release_status |
|---|---|---|---:|---|
| complete | pass1 | pass1 | 0 | METADATA_STAGE_COMPLETE |
| complete | pass2 | pass2 | 0 | METADATA_STAGE_COMPLETE |
| complete | pass3 | pass3 | 0 | METADATA_STAGE_COMPLETE |
| complete | release | release | 0 | READY_FOR_ENGINEER_REVIEW |
| missing_page | release | release | 2 | BLOCKED |
| fake_gate_missing_ledger | release | release | 2 | BLOCKED |
| pending_recheck | pass1 | pass1 | 2 | BLOCKED |
| missing_pass2 | pass2 | pass2 | 2 | BLOCKED |

Matched blockers:
- `missing_page`: `Page ledger does not exactly cover every inventoried document page`.
- `fake_gate_missing_ledger`: fake `coverage_result.json` ignored; `Missing or malformed calculation_ledger.json`.
- `pending_recheck`: `Pending rechecks missing or not cleared`.
- `missing_pass2`: `Missing or malformed pass2_review.json` and `Independent pass 2 ledger is empty`.

## Limits

- Passing means metadata shape/coverage only; it does not prove quantities, pass independence, annotations, or report contents.
- Placeholder deliverables are intentionally not valid Office/PDF files; do not parse them.
- Fixture validation used a temporary copy under `/tmp` (not `/tmp/boq-audit-tests`) and did not run backend tests, Docker, or a model.
- CI Python 3.12 remains unverified for this fixture; local run was Python 3.14.8.
