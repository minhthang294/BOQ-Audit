import json
import tempfile
import unittest
from pathlib import Path

from app.services.audit_batch import BatchOutputError, build_pass1_payload, build_pass2_payload, build_pass3_payload, load_batch_outputs
from app.services.audit_lesson_selection import select_lessons
from app.services.audit_lineage_v4 import estimate_line_group_id, estimate_line_id


def _lesson_snapshot(inventory):
    return select_lessons(project_scope="BOQ-TEST", expected_work_items=inventory["expected_work_items"], current_lessons=[])


class BatchOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.patch_path = self.root / "batch_patch.json"
        self.evidence_path = self.root / "batch_evidence.json"
        self.ledger_records_path = self.root / "batch_ledger_records.json"
        self.patch = {"items": [{"check_id": "c-1", "result_status": "CHECKED", "evidence_ids": ["ev-1"]}]}
        self.evidence = {"evidence": [{
            "evidence_id": "ev-1", "check_id": "c-1", "source_file": "drawing.pdf",
            "location": "sheet 2 / detail D-4", "observation": "dimension shown as 1.20 m", "method": "read dimension label",
        }]}
        self.write()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self):
        self.patch_path.write_text(json.dumps(self.patch), encoding="utf-8")
        self.evidence_path.write_text(json.dumps(self.evidence), encoding="utf-8")
        self.ledger_records_path.write_text(json.dumps({"records": []}), encoding="utf-8")

    def load(self):
        return load_batch_outputs(
            self.patch_path,
            self.evidence_path,
            assigned_check_ids=["c-1"],
            allowed_source_files={"drawing.pdf"},
        )

    def test_accepts_exact_patch_and_bound_source_evidence(self):
        patch, evidence = self.load()
        self.assertEqual(patch, self.patch)
        self.assertEqual(evidence, self.evidence)

    def test_loads_required_ledger_record_output_when_requested(self):
        result = load_batch_outputs(
            self.patch_path, self.evidence_path, ledger_records_path=self.ledger_records_path,
            assigned_check_ids=["c-1"], allowed_source_files={"drawing.pdf"},
        )
        self.assertEqual(result[2], {"records": []})
        self.ledger_records_path.write_text('{"unexpected":[]}', encoding="utf-8")
        with self.assertRaises(BatchOutputError):
            load_batch_outputs(
                self.patch_path, self.evidence_path, ledger_records_path=self.ledger_records_path,
                assigned_check_ids=["c-1"], allowed_source_files={"drawing.pdf"},
            )

    def test_rejects_unassigned_patch_and_evidence_ids(self):
        self.patch["items"][0]["check_id"] = "other"
        self.write()
        with self.assertRaises(BatchOutputError):
            self.load()

    def test_rejects_unlisted_source_and_traversal(self):
        self.evidence["evidence"][0]["source_file"] = "../private.pdf"
        self.write()
        with self.assertRaises(BatchOutputError):
            self.load()

    def test_rejects_unhashable_evidence_id_without_leaking_type_error(self):
        self.evidence["evidence"][0]["evidence_id"] = []
        self.write()
        with self.assertRaises(BatchOutputError):
            self.load()

    def test_rejects_hardlinked_model_output(self):
        outside = self.root / "outside.json"
        outside.write_text(self.patch_path.read_text(encoding="utf-8"), encoding="utf-8")
        self.patch_path.unlink()
        self.patch_path.hardlink_to(outside)
        with self.assertRaises(BatchOutputError):
            self.load()

    def test_pass2_payload_strips_all_prior_pass_results(self):
        inventory = {
            "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
            "pages": [{"page_id": "p1", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1}],
            "sheets": [],
            "targets": [{"target_id": "beam-1", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "sheet_ids": []}],
            "expected_work_items": [
                {"check_id": "p1-check", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "target_ids": ["beam-1"], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
                {"check_id": "p2-check", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "target_ids": ["beam-1"], "check_kind": "independent_recheck", "pass_no": 2, "dependencies": ["p1-check"]},
            ],
        }
        prior_ledger = [
            {"check_id": "p1-check", "pass_no": 1, "execution_status": "DONE", "result_status": "CHECKED", "evidence_ids": ["SECRET_FINDING_7"], "applicability_reason": None},
            {"check_id": "p2-check", "pass_no": 2, "execution_status": "PENDING", "result_status": "NOT_CHECKED", "evidence_ids": [], "applicability_reason": None},
        ]
        checkpoint = {
            "run_id": 42,
            "pass_no": 2,
            "status": "READY",
            "batch_plan": {"1": [["p1-check"]], "2": [["p2-check"]], "3": []},
            "inventory": inventory,
            "lesson_snapshot": _lesson_snapshot(inventory),
            "ledger": prior_ledger,
        }

        context, scoped_inventory, scoped_ledger = build_pass2_payload(
            checkpoint,
            batch_ids=["p2-check"],
            source_files={"drawing.pdf"},
        )
        serialized = json.dumps([context, scoped_inventory, scoped_ledger])
        self.assertNotIn("SECRET_FINDING_7", serialized)
        self.assertFalse(context["prior_pass_results_included"])
        self.assertEqual([item["check_id"] for item in scoped_ledger["items"]], ["p2-check"])
        self.assertEqual(scoped_ledger["items"][0]["result_status"], "NOT_CHECKED")
        with self.assertRaises(BatchOutputError):
            build_pass2_payload(checkpoint, batch_ids=["p2-check"], source_files={"../drawing.pdf"})

    def test_v4_pass2_receives_only_the_prior_lineage_ids_it_must_review(self):
        revision = "sha256:source"
        line_id = estimate_line_id("xlsx", revision, "s1", "B3:D3")
        group_id = estimate_line_group_id([line_id])
        inventory = {
            "documents": [{"document_id": "xlsx", "revision_digest": revision}],
            "pages": [],
            "sheets": [{"sheet_id": "s1", "document_id": "xlsx", "revision_digest": revision, "sheet_index": 1, "sheet_name": "BOQ"}],
            "targets": [],
            "estimate_lines": [{"line_id": line_id, "document_id": "xlsx", "revision_digest": revision, "sheet_id": "s1", "cell_range": "B3:D3"}],
            "estimate_line_groups": [{"line_group_id": group_id, "document_id": "xlsx", "revision_digest": revision, "sheet_id": "s1", "line_ids": [line_id]}],
            "expected_work_items": [
                {"check_id": "p1", "document_id": "xlsx", "revision_digest": revision, "page_ids": [], "sheet_ids": ["s1"], "target_ids": [], "estimate_line_group_ids": [group_id], "check_kind": "pass1", "pass_no": 1, "dependencies": []},
                {"check_id": "p2", "document_id": "xlsx", "revision_digest": revision, "page_ids": [], "sheet_ids": ["s1"], "target_ids": [], "estimate_line_group_ids": [group_id], "check_kind": "pass2", "pass_no": 2, "dependencies": ["p1"]},
                {"check_id": "p3", "document_id": "xlsx", "revision_digest": revision, "page_ids": [], "sheet_ids": ["s1"], "target_ids": [], "estimate_line_group_ids": [group_id], "check_kind": "pass3", "pass_no": 3, "dependencies": ["p2"]},
            ],
        }
        checkpoint = {
            "run_id": 1, "pass_no": 2, "contract_version": 4, "status": "READY",
            "batch_plan": {"1": [["p1"]], "2": [["p2"]], "3": [["p3"]]},
            "inventory": inventory, "lesson_snapshot": _lesson_snapshot(inventory),
            "ledger": [{"check_id": "p2", "execution_status": "PENDING"}],
            "ledger_records": [
                {"record_type": "boq_lineage", "record_id": "LINK-1", "check_id": "p1", "payload": {"estimate_line_ids": [line_id]}},
                {"record_type": "boq_lineage", "record_id": "LINK-other", "check_id": "p1", "payload": {"estimate_line_ids": ["outside"]}},
            ],
        }
        context, _, _ = build_pass2_payload(checkpoint, batch_ids=["p2"], source_files={"source/estimate.xlsx"})
        self.assertEqual(context["prior_lineage_ids"], ["LINK-1"])
        self.assertNotIn("ledger_records", context)

    def test_pass1_payload_is_scoped_and_contains_no_prior_results(self):
        inventory = {
            "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
            "pages": [
                {"page_id": "p1", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1},
                {"page_id": "p2", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 2},
            ],
            "sheets": [],
            "targets": [
                {"target_id": "beam-1", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "sheet_ids": []},
                {"target_id": "beam-2", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p2"], "sheet_ids": []},
            ],
            "expected_work_items": [
                {"check_id": "p1-check", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "target_ids": ["beam-1"], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
                {"check_id": "p2-check", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p2"], "target_ids": ["beam-2"], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
            ],
        }
        checkpoint = {
            "run_id": 7, "pass_no": 1, "status": "READY",
            "batch_plan": {"1": [["p1-check"]], "2": [], "3": []},
            "inventory": inventory,
            "lesson_snapshot": _lesson_snapshot(inventory),
            "ledger": [
                {"check_id": "p1-check", "execution_status": "PENDING"},
                {"check_id": "p2-check", "execution_status": "PENDING", "evidence_ids": ["OLD_RESULT_SECRET"]},
            ],
            "batch_results": [{"patch": {"items": [{"check_id": "p2-check", "evidence_ids": ["OLD_RESULT_SECRET"]}]}}],
        }

        context, scoped_inventory, scoped_ledger = build_pass1_payload(
            checkpoint, batch_ids=["p1-check"], source_files={"drawing.pdf"}
        )
        serialized = json.dumps([context, scoped_inventory, scoped_ledger])
        self.assertNotIn("OLD_RESULT_SECRET", serialized)
        self.assertEqual(context["review_mode"], "source-grounded-initial-review")
        self.assertEqual([page["page_id"] for page in scoped_inventory["pages"]], ["p1"])
        self.assertEqual([item["check_id"] for item in scoped_ledger["items"]], ["p1-check"])
        with self.assertRaises(BatchOutputError):
            build_pass1_payload(checkpoint, batch_ids=["p2-check"], source_files={"drawing.pdf"})

    def test_pass2_payload_rejects_unapproved_batch(self):
        checkpoint = {"pass_no": 2, "status": "READY", "batch_plan": {"2": [["c2"]]}}
        with self.assertRaises(BatchOutputError):
            build_pass2_payload(checkpoint, batch_ids=["other"], source_files={"drawing.pdf"})

    def test_pass3_payload_contains_only_explicitly_depended_on_results(self):
        inventory = {
            "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
            "pages": [{"page_id": "p1", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1}],
            "sheets": [],
            "targets": [{"target_id": "t1", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "sheet_ids": []}],
            "expected_work_items": [
                {"check_id": "p1", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "target_ids": ["t1"], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
                {"check_id": "p2", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "target_ids": ["t1"], "check_kind": "independent_recheck", "pass_no": 2, "dependencies": ["p1", "unrelated"]},
                {"check_id": "p3", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "target_ids": ["t1"], "check_kind": "falsification", "pass_no": 3, "dependencies": ["p1", "p2"]},
                {"check_id": "unrelated", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"], "target_ids": ["t1"], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
            ],
        }
        results = [
            {"pass_no": 1, "patch": {"items": [{"check_id": "p1", "result_status": "CHECKED", "evidence_ids": ["ev1"]}]}, "evidence": {"evidence": [{"evidence_id": "ev1", "check_id": "p1", "source_file": "drawing.pdf", "location": "p1", "observation": "claim 1", "method": "read"}]}, "ledger_records": [{"record_type": "detail_rows", "record_id": "row-p1", "check_id": "p1", "payload": {"row_id": "row-p1"}}]},
            {"pass_no": 2, "patch": {"items": [{"check_id": "p2", "result_status": "UNRESOLVED", "evidence_ids": ["ev2"]}]}, "evidence": {"evidence": [{"evidence_id": "ev2", "check_id": "p2", "source_file": "drawing.pdf", "location": "p1", "observation": "claim 2", "method": "read"}]}, "ledger_records": [{"record_type": "pass2_review", "record_id": "review-p2", "check_id": "p2", "payload": {"review_id": "review-p2"}}]},
            {"pass_no": 1, "patch": {"items": [{"check_id": "unrelated", "result_status": "CHECKED", "evidence_ids": ["secret"]}]}, "evidence": {"evidence": [{"evidence_id": "secret", "check_id": "unrelated", "source_file": "drawing.pdf", "location": "p1", "observation": "unrelated sentinel", "method": "read"}]}, "ledger_records": [{"record_type": "detail_rows", "record_id": "row-secret", "check_id": "unrelated", "payload": {"row_id": "row-secret"}}]},
        ]
        checkpoint = {
            "run_id": 42,
            "pass_no": 3,
            "status": "READY",
            "batch_plan": {"1": [["p1"], ["unrelated"]], "2": [["p2"]], "3": [["p3"]]},
            "inventory": inventory,
            "lesson_snapshot": select_lessons(project_scope="BOQ-TEST", expected_work_items=inventory["expected_work_items"], current_lessons=[{
                "lesson_id": "LSN-1", "version": 1, "status": "VERIFIED", "project_scope": "BOQ-TEST",
                "error_class": "MISSED_CONNECTION", "applicability_check_kinds": ["drawing_review"], "applicability_target_ids": [],
                "trigger_conditions": ["detail appears"], "required_checks": ["inspect both views"], "counterexamples": [],
            }]),
            "ledger": [{"check_id": "p3", "execution_status": "PENDING"}],
            "batch_results": results,
        }
        context, _, _ = build_pass3_payload(checkpoint, batch_ids=["p3"], source_files={"drawing.pdf"})
        serialized = json.dumps(context)
        self.assertEqual({item["check_id"] for item in context["prior_pass_results"]}, {"p1", "p2"})
        self.assertEqual({item["check_id"] for item in context["prior_ledger_records"]}, {"p1", "p2"})
        self.assertNotIn("unrelated sentinel", serialized)
        self.assertNotIn("row-secret", serialized)
        self.assertEqual(context["lesson_regression_cases"][0]["case_id"], "MEM-LSN-1-v1")
        self.assertEqual(context["lesson_regression_cases"][0]["target_ids"], ["t1"])

    def test_pass3_payload_blocks_missing_dependency_result(self):
        checkpoint = {"pass_no": 3, "status": "READY", "batch_plan": {"3": [["c3"]]}}
        with self.assertRaises(BatchOutputError):
            build_pass3_payload(checkpoint, batch_ids=["c3"], source_files={"drawing.pdf"})

    def test_pass3_payload_does_not_reuse_stale_dependency_result(self):
        checkpoint = {
            "pass_no": 3, "status": "READY", "batch_plan": {"3": [["c3"]]},
            "inventory": {
                "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
                "pages": [{"page_id": "p1", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1}],
                "sheets": [], "targets": [], "expected_work_items": [
                    {"check_id": "p1-check", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"],
                     "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
                    {"check_id": "p2-check", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"],
                     "target_ids": [], "check_kind": "independent-review", "pass_no": 2, "dependencies": ["p1-check"]},
                    {"check_id": "c3", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["p1"],
                     "target_ids": [], "check_kind": "falsification", "pass_no": 3, "dependencies": ["p2-check"]},
                ],
            },
            "ledger": [{"check_id": "c3", "execution_status": "PENDING"}],
            "batch_results": [{
                "pass_no": 1, "stale": True,
                "patch": {"items": [{"check_id": "p1-check", "result_status": "CHECKED", "evidence_ids": ["old-ev"]}]},
                "evidence": {"evidence": [{"evidence_id": "old-ev", "observation": "stale secret"}]},
                "ledger_records": [{"record_type": "detail_rows", "payload": {"row_id": "stale-row"}}],
            }],
        }
        with self.assertRaisesRegex(BatchOutputError, "durable results"):
            build_pass3_payload(checkpoint, batch_ids=["c3"], source_files={"drawing.pdf"})


if __name__ == "__main__":
    unittest.main()
