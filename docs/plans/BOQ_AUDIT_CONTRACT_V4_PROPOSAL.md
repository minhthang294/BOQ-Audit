# BOQ Audit lineage contract v4 — approved design and implementation record

**Status: IMPLEMENTED LOCALLY (2026-11-02).** The reviewer approved the recommended choices below, and the implementation is present in this worktree. This does not approve quantity conclusions, tolerances, unit conversions, corpus labels, or pilot acceptance.

## Evidence in the current worktree

- Approved inventory in `audit_coverage.py` identifies documents/revisions, PDF pages, workbook sheets, targets, and expected work items. It does not identify BOQ estimate rows or individual cells.
- `validate_batch_payload()` binds evidence to an assigned check and source file, but evidence `location` and `observation` are free text. `source_refs` bind only approved page/sheet/target IDs.
- `calculation_ledger.inputs[]` is constrained to objects by contract v3 and the pinned skill schema; neither declares nested fields. The controller therefore cannot verify an input's value, unit, or source lineage.
- `verify_domain_references()` checks the current declared edges: summary row → detail row, calculation → assumption, issue → calculation/candidate, and review → calculation/case/issue. It has no BOQ row or calculation input IDs to resolve.
- The quality plan §4.5 already requires dependency IDs across source anchor → observed value → CALC → detail/summary → BOQ/estimate → issue → report/annotation. Current v3 does not encode the full chain.

## Approved minimum shape

The reviewer should approve the data model before implementation. Preferred direction is a **normalized observation plus calculation-input link**, with the estimate's row scope added to controller-approved inventory:

1. Extend approved inventory with source-revision-bound `estimate_line_id` candidates for XLSX rows. Each line records `document_id`, `revision_digest`, `sheet_id`, row/cell range, and a reviewer decision. Only approved rows enter `expected_work_items`; parser output alone never creates approved scope.
2. Add a `source_observations` domain record with a stable `observation_id`, typed `observed_value`, `unit`, source file, exact page/sheet/cell or drawing anchor, and evidence IDs. Each observation must be bound to the record's approved `source_refs` and source revision.
3. Replace opaque `calculation_ledger.inputs[]` objects with `{input_id, observation_id, value, unit}`. The controller resolves `observation_id`, verifies the copied value/unit match the observation, and checks all IDs are unique within the run. This keeps calculations rerunnable while making the source edge explicit.
4. Add an explicit `boq_lineage` record with a stable `lineage_id`, `estimate_line_ids`, `detail_row_ids`, `summary_row_ids`, `calc_ids`, and evidence-backed reconciliation status. Lists allow legitimate one-to-many mappings; empty mappings require a source-grounded reason and remain visible as unresolved or not applicable.
5. Require issue, report row, and annotation records to resolve through the accepted lineage graph. A correction to source bytes or approved inventory invalidates observations and every downstream record in one checkpoint transition; the history remains append-only.

This proposal does not decide how a drawing quantity is technically derived, which units/tolerances are acceptable, or whether an estimate line is correct. Those remain engineer decisions. The controller validates identity, source/revision binding, graph edges, and invalidation only.

## Alternatives requiring a decision

| Choice | Advantage | Cost / unresolved risk |
| --- | --- | --- |
| Inline `inputs[]` with `input_id`, `value`, `unit`, and `evidence_ids` | Smallest schema/runtime change; each calculation input points directly to source evidence. | No reusable observation node; evidence remains batch-local free text unless its location schema also changes. |
| Normalized `source_observations` + input references (preferred) | Separates what was seen from how it was used; supports reuse, independent review, and precise invalidation. | Adds a record kind, graph checks, report projection, and migration/compatibility cases. |
| Reuse inventory `target_id` for estimate rows | Avoids another inventory list. | Existing target IDs mean engineering objects; using them for estimate rows would change their semantics and can mix drawing targets with BOQ lines. |

## Decisions approved for contract v4

- Approve the normalized observation model or choose the inline alternative.
- Define whether estimate rows are first-class approved inventory records or a distinct target kind; do not overload the current `target_id` silently.
- Confirm stable ID ownership: controller-generated IDs bound to `(document_id, revision_digest, page/sheet/cell-or-anchor)` versus reviewer-supplied IDs.
- Specify exact anchor types and coordinate conventions for PDF rectangles, workbook cells/ranges, merged cells, and multirow BOQ items.
- Define valid one-to-many mapping and explicit unmatched/zero-row states. Engineering quantities, unit conversions, and tolerances require separate approved rules.
- Decide contract versioning and compatibility for persisted v3 checkpoints. A v4 reader must not reinterpret old opaque `inputs[]`; old runs need an explicit legacy/unverified state or a new attempt.

## Acceptance criteria

- Accept a source observation and calculation input whose document revision, source anchor, value, and unit match.
- Reject dangling observation/lineage IDs, duplicate IDs, mismatched value/unit, wrong source revision, cross-document references, and source anchors outside the approved item scope.
- Cover one-to-many estimate mapping, explicit unmatched/zero scope, and a correction invalidating every downstream report/annotation edge while preserving history.
- Prove Pass 2/3 cannot write or copy prior-pass observations as independent evidence, and Pass 3 closes only the exact issue/lineage scope reviewed.
- Verify projection into Excel/PDF/UI retains source IDs and scope. Visual placement, arithmetic policy, and technical conclusion still require engineer adjudication on real examples.

The approved choices are implemented in the local contract v4 schema, checkpoint, ledger, report and UI paths. Keep `STAGE_LEDGER_REFERENCE` checks fail-closed where applicable. Do not treat a v3 PASS receipt or local contract tests as evidence that drawing-to-BOQ lineage is technically correct on real projects.

## Recommendation approved by reviewer (2026-11-02)

The reviewer approved this recommendation on 2026-11-02. Runtime changes remain subject to the acceptance tests below.

### Recommended choices

1. **Choose the normalized `source_observations` + input-reference model**, not inline `inputs[].evidence_ids`.
   Reason: `calculation_ledger.inputs[]` is typed only as `object[]` in contract v3 (`backend/app/schemas/audit_batch_ledger_contract.v3.json:114`) and the pinned skill schema does not declare nested keys, so the controller cannot verify a value or unit today. A reusable observation node is the smallest shape that lets one observed value feed several CALCs and lets a changed source invalidate every dependent edge through the existing `audit_dependencies.affected_nodes()` graph. Inline evidence IDs would still leave each input as a batch-local free-text observation.
2. **Model estimate rows as a new approved-inventory kind (`estimate_lines`), not as `targets`.**
   Reason: `audit_coverage.validate_inventory()` requires every target to carry page/sheet scope, and both `validate_pass1_source_coverage()` and `audit_stage_gate.assess_pass3_closure()` require every declared target to be covered by Pass 1 work items and reviewed in Pass 3. Overloading `targets` with hundreds or thousands of BOQ rows would force per-row work items and Pass 3 reviews, which is not the intended review granularity. Keep `targets` for engineering objects and give estimate lines their own `line_id`, sheet/row-range scope, and a coarser coverage rule tied to declared line groups.
3. **Use controller-generated deterministic IDs; reviewer approves inventory membership, not ID strings.**
   Reason: append-only revisions, `verify_domain_references()` and `affected_nodes()` all depend on stable unique identity. At the same time, `source_refs` currently binds only approved page/sheet/target IDs, so the controller must own the new namespaces. Suggested shape: `OBS-<sha256(document_id|revision_digest|anchor_kind|anchor)[:16]>`, `LINE-...`, `LINK-...`. Keep any model-authored label as descriptive text, never as identity.
4. **Store anchors in source-native coordinates.**
   PDF: `page_id` plus a float `bbox` in PyMuPDF's unrotated page space (the space `pixel_bbox_to_page()` already targets), with the page rotation recorded for audit. XLSX: `sheet_id` plus an A1 `range`, with merged ranges expanded to their top-left anchor and `row_span`. Free-text `location` stays human context only. Pixel coordinates or render DPI must not appear in the ledger because they bind evidence to one render and break invalidation.
5. **Allow one-to-many and many-to-one lineage, but never a silent empty mapping.**
   `boq_lineage` should hold lists and a `reconciliation_status` of `MATCHED`, `UNMATCHED` or `NOT_APPLICABLE` plus `reason` and evidence IDs. An empty list is valid only with `NOT_APPLICABLE` and a source-grounded reason. `UNMATCHED` without a linked issue or an explicitly accepted scope keeps the stage gate closed.
6. **Declare `contract_version: 4` on new checkpoints and reject v3 checkpoints for resume.**
   Reason: `load_run_checkpoint()` revalidates digests and versions. Reinterpreting an old opaque `inputs[]` as a typed observation link would silently change its meaning, and old `passN` receipts must not be reused. Keep v3 runs readable as `LEGACY_V3_UNVERIFIED` history; new attempts use v4 only.
7. **Keep the existing record wrapper and add new record kinds instead of overloading `source_refs`.**
   `{record_type, record_id, check_id, source_refs, payload}` already carries the invariants the controller enforces. Add streamed kinds `source_observations` and `boq_lineage`; keep `estimate_lines` in the approved inventory, not as streamed ledger output. If a record needs to cite observations, add explicit `observation_ids` / `lineage_id` fields in its payload rather than widening `source_refs` semantics.

### Phased implementation (completed locally)

- **v4a — observations and inputs:** add the record kinds, controller validation (ID uniqueness, revision binding, value/unit match), projection, stage gate and report projection. Update `verify_domain_references()` with `observation_id` and `input_id` edges.
- **v4b — estimate lines and lineage:** add inventory `estimate_lines`, line-group coverage, `boq_lineage` records, and issue/report/annotation edges. Extend `audit_dependencies` with the new edges and keep cycle/missing-node fail-closed behavior.
- **v4c — invalidation and compatibility:** one checkpoint transition invalidates observations and every downstream lineage/CALC/issue/report/annotation record; v3 resume is rejected with a typed status; report/UI expose the legacy state.

Local verification on 2026-11-02: full backend suite **522 passed**; focused lineage/checkpoint suite **57 passed**; frontend typecheck, pinned skill verification and archive/tree verification passed earlier in this worktree. Nodeterm review verdict: correctness finding fixed, security found nothing, and the restore/exact-lineage test gaps were covered. VM staging, real corpus adjudication, engineering acceptance and production rollout remain separate gates.

### Approved decisions

1. Normalized observations versus inline input evidence.
2. `estimate_lines` as a separate inventory kind versus reusing `targets`.
3. Controller-generated deterministic IDs versus reviewer/model-supplied IDs.
4. The anchor conventions above (unrotated PDF bbox and A1 XLSX range).
5. v4 incompatibility policy for persisted v3 checkpoints.

Everything else in this document can be implemented within those choices without a further domain decision. Engineering quantities, tolerances, unit conversions, severity and final conclusions still require the engineer.
