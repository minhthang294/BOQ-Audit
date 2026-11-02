import json
import hashlib
import runpy
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import pymupdf
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import audit_quality
from app.services.audit_annotation_draft import render_annotation_draft
from app.services.audit_excel_report import build_excel_report_draft, validate_excel_report_draft
from app.services.audit_report_source import build_classification_state, build_report_source
from app.services.audit_stage_gate import StageGateInputError, StageLedgerReferenceError, assess_pass3_closure, write_checkpoint_stage_inputs
from app.services.audit_lesson_selection import lesson_memory_case_id, lesson_memory_source, select_lessons


_NOTO_SANS_FONT = next((path for path in (
    Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    Path("/usr/share/fonts/noto/NotoSans-Regular.ttf"),
) if path.is_file()), Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"))


def _empty_lesson_snapshot():
    return select_lessons(project_scope="JOB-TEST", expected_work_items=[{"check_id": "c", "check_kind": "TEST", "target_ids": []}], current_lessons=[])


def _revision_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


class StageGateInputTests(unittest.TestCase):
    def test_v4_pass3_closure_requires_lineage_and_open_issue_for_unmatched_scope(self):
        checkpoint = {
            "pass_no": 3,
            "inventory": {
                "pages": [], "sheets": [{"sheet_id": "s1"}], "targets": [],
                "estimate_line_groups": [{"line_group_id": "g1", "line_ids": ["l1"]}],
                "expected_work_items": [],
            },
            "ledger": [{"pass_no": 3, "execution_status": "DONE"}],
            "lesson_snapshot": _empty_lesson_snapshot(),
            "ledger_records": [{
                "record_type": "pass3_review", "record_id": "r1",
                "source_refs": {"page_ids": [], "sheet_ids": ["s1"], "target_ids": []},
                "payload": {"page_ids": [], "sheet_ids": ["s1"], "lineage_ids": []},
            }],
        }
        missing = assess_pass3_closure(checkpoint)
        self.assertEqual(missing["status"], "REVIEW_REQUIRED")
        self.assertIn("line-group:g1", " ".join(missing["gaps"]))

        checkpoint["ledger_records"].extend([
            {"record_type": "boq_lineage", "record_id": "link1", "source_refs": {},
             "payload": {"estimate_line_ids": ["l1"], "reconciliation_status": "UNMATCHED"}},
        ])
        checkpoint["ledger_records"][0]["payload"]["lineage_ids"] = ["link1"]
        unmatched = assess_pass3_closure(checkpoint)
        self.assertIn("lineage:link1", " ".join(unmatched["gaps"]))

    def test_pass3_sheet_only_target_requires_explicit_sheet_review(self):
        checkpoint = {
            "inventory": {"pages": [{"page_id": "p1"}], "sheets": [{"sheet_id": "s1"}], "targets": [{"target_id": "t1", "page_ids": [], "sheet_ids": ["s1"]}]},
            "ledger": [{"pass_no": 3, "execution_status": "DONE"}], "lesson_snapshot": _empty_lesson_snapshot(),
            "ledger_records": [
                {"record_type": "pass3_review", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []}, "payload": {"page_ids": ["p1"]}},
                {"record_type": "pass3_review", "source_refs": {"page_ids": [], "sheet_ids": ["s1"], "target_ids": ["t1"]}, "payload": {"sheet_ids": ["s1"], "target_ids": ["t1"]}},
            ],
        }
        self.assertEqual(assess_pass3_closure(checkpoint)["status"], "METADATA_COMPLETE")
        checkpoint["ledger_records"][1]["payload"].pop("sheet_ids")
        self.assertFalse(assess_pass3_closure(checkpoint)["target_fit_check_complete"])

    def test_pass3_sheet_only_issue_requires_sheet_review_but_no_pdf_mark(self):
        checkpoint = {
            "inventory": {"pages": [{"page_id": "p1"}], "sheets": [{"sheet_id": "s1"}], "targets": [{"target_id": "t1", "page_ids": [], "sheet_ids": ["s1"]}]},
            "ledger": [{"pass_no": 3, "execution_status": "DONE"}], "lesson_snapshot": _empty_lesson_snapshot(),
            "ledger_records": [
                {"record_type": "pass3_review", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []}, "payload": {"page_ids": ["p1"]}},
                {"record_type": "pass3_review", "source_refs": {"page_ids": [], "sheet_ids": ["s1"], "target_ids": ["t1"]}, "payload": {"sheet_ids": ["s1"], "target_ids": ["t1"], "issue_ids": ["i1"]}},
                {"record_type": "issues", "record_id": "i1", "source_refs": {"page_ids": [], "sheet_ids": ["s1"], "target_ids": ["t1"]}, "payload": {"issue_id": "i1", "final_status": "CONFIRMED_ERROR", "evidence": ["ev1"]}},
            ],
        }
        self.assertEqual(assess_pass3_closure(checkpoint)["status"], "METADATA_COMPLETE")
        checkpoint["ledger_records"][1]["payload"].pop("sheet_ids")
        self.assertIn("i1", " ".join(assess_pass3_closure(checkpoint)["gaps"]))

    def test_pass3_closure_requires_review_of_every_source_sheet(self):
        checkpoint = {
            "inventory": {"pages": [], "sheets": [{"sheet_id": "s1"}, {"sheet_id": "s2"}], "targets": []},
            "ledger": [{"pass_no": 3, "execution_status": "DONE"}], "lesson_snapshot": _empty_lesson_snapshot(),
            "ledger_records": [{
                "record_type": "pass3_review", "record_id": "r-s1", "check_id": "c-s1",
                "source_refs": {"page_ids": [], "sheet_ids": ["s1"], "target_ids": []},
                "payload": {"review_id": "r-s1", "page_ids": [], "sheet_ids": ["s1"]},
            }],
        }
        incomplete = assess_pass3_closure(checkpoint)
        self.assertFalse(incomplete["review_complete"])
        self.assertEqual(incomplete["status"], "REVIEW_REQUIRED")
        checkpoint["ledger_records"].append({
            "record_type": "pass3_review", "record_id": "r-s2", "check_id": "c-s2",
            "source_refs": {"page_ids": [], "sheet_ids": ["s2"], "target_ids": []},
            "payload": {"review_id": "r-s2", "page_ids": [], "sheet_ids": ["s2"]},
        })
        self.assertTrue(assess_pass3_closure(checkpoint)["review_complete"])

    def test_pass2_complete_requires_review_of_every_source_sheet(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            xlsx = root / "estimate.xlsx"
            xlsx.write_bytes(b"synthetic xlsx")
            revision = _revision_digest(xlsx)
            sheets = [
                {"sheet_id": "s1", "document_id": "xlsx", "revision_digest": revision, "sheet_index": 1, "sheet_name": "Estimate"},
                {"sheet_id": "s2", "document_id": "xlsx", "revision_digest": revision, "sheet_index": 2, "sheet_name": "Hidden"},
            ]
            records = [{
                "record_type": "pass2_review", "record_id": "r-s1", "check_id": "c2-s1",
                "source_refs": {"page_ids": [], "sheet_ids": ["s1"], "target_ids": []},
                "payload": {"review_id": "r-s1", "pass_no": 2, "page_ids": [], "sheet_ids": ["s1"]},
            }]
            checkpoint = {
                "pass_no": 2,
                "inventory": {"documents": [{"document_id": "xlsx", "revision_digest": revision}], "pages": [], "sheets": sheets, "targets": []},
                "ledger": [
                    {"check_id": "c1-s1", "pass_no": 1, "execution_status": "DONE"},
                    {"check_id": "c1-s2", "pass_no": 1, "execution_status": "DONE"},
                    {"check_id": "c2-s1", "pass_no": 2, "execution_status": "DONE"},
                    {"check_id": "c2-s2", "pass_no": 2, "execution_status": "DONE"},
                ],
                "ledger_records": records,
            }
            work = root / "work"
            write_checkpoint_stage_inputs(work, checkpoint=checkpoint, source_files={"source/estimate.xlsx": xlsx})
            manifest = json.loads((work / "run_manifest.json").read_text())
            self.assertFalse(manifest["pass2_complete"])

    def test_pass3_closure_requires_selected_lesson_regression_and_target_review(self):
        work = [
            {"check_id": "c1", "check_kind": "DRAWING_REVIEW", "target_ids": ["t1"]},
            {"check_id": "c3", "check_kind": "FALSIFICATION", "target_ids": ["t1"]},
        ]
        lesson = {
            "lesson_id": "LSN-1", "version": 2, "status": "VERIFIED", "project_scope": "JOB-1",
            "error_class": "MISSED_CONNECTION", "applicability_check_kinds": ["DRAWING_REVIEW"],
            "applicability_target_ids": [], "trigger_conditions": ["connection detail exists"],
            "required_checks": ["inspect both connection views"], "counterexamples": [],
        }
        snapshot = select_lessons(project_scope="JOB-1", expected_work_items=work, current_lessons=[lesson])
        disposition = snapshot["dispositions"][0]
        case_id = lesson_memory_case_id(lesson["lesson_id"], lesson["version"])
        checkpoint = {
            "inventory": {"pages": [{"page_id": "p1"}], "targets": [{"target_id": "t1", "page_ids": ["p1"]}]},
            "ledger": [{"pass_no": 3, "execution_status": "DONE"}], "lesson_snapshot": snapshot,
            "ledger_records": [{"record_type": "pass3_review", "record_id": "r3", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]}, "payload": {"page_ids": ["p1"], "target_ids": ["t1"], "case_ids": [case_id]}}],
        }
        blocked = assess_pass3_closure(checkpoint)
        self.assertFalse(blocked["memory_regression_complete"])
        self.assertTrue(blocked["controller_contract_blocked"])
        checkpoint["ledger_records"].append({
            "record_type": "regression_checks", "record_id": case_id,
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
            "payload": {"status": "CHECKED", "memory_source": lesson_memory_source(disposition), "target_ids": ["t1"]},
        })
        complete = assess_pass3_closure(checkpoint)
        self.assertTrue(complete["memory_regression_complete"])
        self.assertEqual(complete["status"], "METADATA_COMPLETE")

    def test_pass3_allows_reopened_confirmed_issue_but_blocks_unresolved_candidate_and_closure(self):
        checkpoint = {
            "inventory": {"pages": [{"page_id": "p1"}], "targets": [{"target_id": "t1", "page_ids": ["p1"]}]},
            "ledger": [{"pass_no": 3, "execution_status": "DONE"}], "lesson_snapshot": _empty_lesson_snapshot(),
            "ledger_records": [
                {"record_type": "pass3_review", "record_id": "r1", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
                 "payload": {"page_ids": ["p1"], "target_ids": ["t1"], "issue_ids": ["i1"], "target": "t1"}},
                {"record_type": "issues", "record_id": "i1", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
                 "payload": {"issue_id": "i1", "final_status": "CONFIRMED_ERROR", "evidence": ["ev1"]}},
            ],
        }
        self.assertEqual(assess_pass3_closure(checkpoint)["status"], "METADATA_COMPLETE")
        legacy_checkpoint = dict(checkpoint)
        legacy_checkpoint.pop("lesson_snapshot")
        self.assertFalse(assess_pass3_closure(legacy_checkpoint)["memory_regression_complete"])
        self.assertTrue(assess_pass3_closure(legacy_checkpoint)["controller_contract_blocked"])
        self.assertTrue(assess_pass3_closure(checkpoint)["false_positive_propagation_complete"])
        checkpoint["ledger_records"][0]["payload"]["issue_ids"] = []
        self.assertIn("i1", " ".join(assess_pass3_closure(checkpoint)["gaps"]))
        checkpoint["ledger_records"][0]["payload"]["issue_ids"] = ["i1"]
        checkpoint["ledger_records"][1]["source_refs"]["page_ids"] = ["p1", "p2"]
        checkpoint["inventory"]["pages"].append({"page_id": "p2"})
        checkpoint["ledger_records"].append({"record_type": "pass3_review", "record_id": "r2", "source_refs": {"page_ids": ["p2"], "sheet_ids": [], "target_ids": []}, "payload": {"page_ids": ["p2"], "target_ids": []}})
        self.assertIn("i1", " ".join(assess_pass3_closure(checkpoint)["gaps"]))
        checkpoint["ledger_records"].pop()
        checkpoint["inventory"]["pages"].pop()
        checkpoint["ledger_records"][1]["source_refs"]["page_ids"] = ["p1"]
        checkpoint["ledger_records"].append({"record_type": "issue_candidates", "record_id": "c1", "payload": {"candidate_id": "c1"}})
        self.assertTrue(assess_pass3_closure(checkpoint)["controller_contract_blocked"])
        checkpoint["ledger_records"][2]["source_refs"] = {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]}
        checkpoint["ledger_records"][1]["payload"]["candidate_ids"] = ["c1"]
        self.assertEqual(assess_pass3_closure(checkpoint)["status"], "METADATA_COMPLETE")
        checkpoint["ledger_records"][2]["source_refs"] = {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t2"]}
        self.assertTrue(assess_pass3_closure(checkpoint)["controller_contract_blocked"])
        checkpoint["ledger_records"][2]["source_refs"]["target_ids"] = ["t1"]
        checkpoint["ledger_records"][1]["payload"]["candidate_ids"] = ["c1", "missing"]
        self.assertIn("missing", " ".join(assess_pass3_closure(checkpoint)["gaps"]))
        checkpoint["ledger_records"].pop()
        for final_status in ("FALSE_POSITIVE", "CLOSED_WITH_EXPLANATION"):
            checkpoint["ledger_records"][1]["payload"]["final_status"] = final_status
            closure = assess_pass3_closure(checkpoint)
            self.assertFalse(closure["false_positive_propagation_complete"])
            self.assertTrue(closure["controller_contract_blocked"])
            self.assertIn("downstream", " ".join(closure["gaps"]))

    def test_pass3_closure_requires_page_and_target_reviews_and_no_unlinked_issues(self):
        checkpoint = {
            "inventory": {
                "pages": [{"page_id": "p1"}, {"page_id": "p2"}],
                "targets": [{"target_id": "t1", "page_ids": ["p1"]}],
            },
            "lesson_snapshot": _empty_lesson_snapshot(),
            "ledger": [{"pass_no": 3, "execution_status": "DONE"}],
            "ledger_records": [
                {"record_type": "pass3_review", "source_refs": {"page_ids": ["p1"], "target_ids": ["t1"]},
                 "payload": {"page_ids": ["p1"], "target_ids": ["t1"]}},
                {"record_type": "pass3_review", "source_refs": {"page_ids": ["p2"], "target_ids": []},
                 "payload": {"page_ids": ["p2"], "target_ids": []}},
            ],
        }
        complete = assess_pass3_closure(checkpoint)
        self.assertEqual(complete["status"], "METADATA_COMPLETE")
        self.assertTrue(complete["target_fit_check_complete"])
        self.assertTrue(complete["false_positive_propagation_complete"])
        self.assertFalse(complete["controller_contract_blocked"])

        checkpoint["ledger_records"].append({"record_type": "issue_candidates", "payload": {}})
        with_candidate = assess_pass3_closure(checkpoint)
        self.assertFalse(with_candidate["false_positive_propagation_complete"])
        self.assertTrue(with_candidate["controller_contract_blocked"])
        checkpoint["ledger_records"].pop()
        checkpoint["ledger_records"][0]["payload"]["issue_ids"] = ["i1"]
        self.assertFalse(assess_pass3_closure(checkpoint)["false_positive_propagation_complete"])
        del checkpoint["ledger_records"][0]["payload"]["issue_ids"]
        checkpoint["ledger_records"][0]["source_refs"]["target_ids"] = []
        without_target = assess_pass3_closure(checkpoint)
        self.assertFalse(without_target["target_fit_check_complete"])
        self.assertFalse(without_target["controller_contract_blocked"])

    def test_closed_issue_requires_downstream_pass3_review_bound_to_approved_dependency_graph(self):
        checkpoint = {
            "inventory": {
                "pages": [{"page_id": "p1"}],
                "targets": [{"target_id": "t1", "page_ids": ["p1"]}],
                "expected_work_items": [
                    {"check_id": "origin", "pass_no": 1, "dependencies": [], "page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
                    {"check_id": "downstream", "pass_no": 3, "dependencies": ["origin"], "page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
                ],
            },
            "ledger": [{"pass_no": 3, "execution_status": "DONE"}],
            "lesson_snapshot": _empty_lesson_snapshot(),
            "ledger_records": [
                {"record_type": "pass3_review", "record_id": "r1", "check_id": "downstream", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
                 "payload": {"page_ids": ["p1"], "target_ids": ["t1"], "issue_ids": ["i1"]}},
                {"record_type": "issue_candidates", "record_id": "candidate-1", "check_id": "origin", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]}, "payload": {"candidate_id": "candidate-1"}},
                {"record_type": "issues", "record_id": "i1", "check_id": "downstream", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
                 "payload": {"issue_id": "i1", "final_status": "FALSE_POSITIVE", "candidate_ids": ["candidate-1"]}},
            ],
        }
        self.assertTrue(assess_pass3_closure(checkpoint)["false_positive_propagation_complete"])
        checkpoint["ledger_records"].append({
            "record_type": "issue_candidates", "record_id": "candidate-2", "check_id": "origin",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
            "payload": {"candidate_id": "candidate-2"},
        })
        checkpoint["ledger_records"][2]["payload"]["candidate_ids"] = ["candidate-1", "candidate-2"]
        self.assertTrue(assess_pass3_closure(checkpoint)["false_positive_propagation_complete"])
        checkpoint["ledger_records"].pop()
        checkpoint["ledger_records"][2]["payload"]["candidate_ids"] = ["missing-candidate"]
        closure = assess_pass3_closure(checkpoint)
        self.assertFalse(closure["false_positive_propagation_complete"])
        self.assertIn("ứng viên lỗi không tồn tại", " ".join(closure["gaps"]))
        checkpoint["ledger_records"][2]["payload"]["candidate_ids"] = ["candidate-1"]
        checkpoint["ledger_records"][0]["payload"]["issue_ids"] = []
        closure = assess_pass3_closure(checkpoint)
        self.assertFalse(closure["false_positive_propagation_complete"])
        self.assertIn("downstream", " ".join(closure["gaps"]))

    def test_closed_issue_without_descendants_still_requires_issue_source_review(self):
        checkpoint = {
            "inventory": {
                "pages": [{"page_id": "p1"}],
                "targets": [{"target_id": "t1", "page_ids": ["p1"]}],
                "expected_work_items": [{"check_id": "c3", "pass_no": 3, "dependencies": [], "page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]}],
            },
            "ledger": [{"pass_no": 3, "execution_status": "DONE"}],
            "lesson_snapshot": _empty_lesson_snapshot(),
            "ledger_records": [
                {"record_type": "pass3_review", "record_id": "r1", "check_id": "c3", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
                 "payload": {"page_ids": ["p1"], "target_ids": ["t1"], "issue_ids": []}},
                {"record_type": "issues", "record_id": "i1", "check_id": "c3", "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
                 "payload": {"issue_id": "i1", "final_status": "CLOSED_WITH_EXPLANATION"}},
            ],
        }
        closure = assess_pass3_closure(checkpoint)
        self.assertFalse(closure["false_positive_propagation_complete"])
        self.assertIn("issue_source_review_missing", " ".join(closure["gaps"]))
        checkpoint["ledger_records"][0]["payload"]["issue_ids"] = ["i1"]
        self.assertTrue(assess_pass3_closure(checkpoint)["false_positive_propagation_complete"])

    def test_materializer_sets_pass3_closure_flags_only_for_supported_empty_issue_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.pdf"
            source.write_bytes(b"synthetic source")
            revision = _revision_digest(source)
            checkpoint = {
                "pass_no": 3,
                "lesson_snapshot": _empty_lesson_snapshot(),
                "inventory": {
                    "documents": [{"document_id": "pdf", "revision_digest": revision}],
                    "pages": [{"page_id": "p1", "document_id": "pdf", "revision_digest": revision, "pdf_page": 1}],
                    "targets": [{"target_id": "t1", "page_ids": ["p1"]}],
                },
                "ledger": [{"pass_no": stage, "execution_status": "DONE", "result_status": "CHECKED"} for stage in (1, 2, 3)],
                "ledger_records": [{
                    "record_type": "pass3_review", "record_id": "review-1", "check_id": "c3",
                    "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
                    "payload": {"review_id": "review-1", "pass_no": 3, "page_ids": ["p1"], "target_ids": ["t1"], "method": "source recheck", "evidence": ["ev-1"], "target": "t1", "result": "confirmed"},
                }],
            }
            work = root / "work"
            write_checkpoint_stage_inputs(work, checkpoint=checkpoint, source_files={"source/input.pdf": source})
            manifest = json.loads((work / "run_manifest.json").read_text())
            self.assertTrue(manifest["false_positive_propagation_complete"])
            self.assertTrue(manifest["target_fit_check_complete"])

            checkpoint["ledger_records"].append({"record_type": "issue_candidates", "record_id": "candidate-1", "payload": {"candidate_id": "candidate-1"}})
            self.assertFalse(assess_pass3_closure(checkpoint)["false_positive_propagation_complete"])

    def test_materializes_checkpoint_and_model_attested_metadata_separately(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.pdf"
            source.write_bytes(b"synthetic source")
            revision = _revision_digest(source)
            checkpoint = {
                "pass_no": 1,
                "inventory": {
                    "documents": [{"document_id": "pdf", "revision_digest": revision}],
                    "pages": [{"page_id": "p1", "document_id": "pdf", "revision_digest": revision, "pdf_page": 1}],
                },
                "ledger": [{"check_id": "c1", "pass_no": 1, "execution_status": "DONE", "result_status": "UNRESOLVED"}],
                "ledger_records": [{
                    "record_type": "run_metadata", "record_id": "META-c1", "check_id": "c1",
                    "payload": {
                        "metadata_id": "META-c1", "stage": "pass1",
                        "references_read": ["references/protocol-v6.4.md"],
                        "memory_cases_consulted": ["R-01"],
                        "no_detail_tables_reason": "No quantity table on the checked page.",
                        "no_summary_tables_reason": "No summary table on the checked page.",
                        "no_sequence_sections_reason": "No sequence table on the checked page.",
                        "no_discrete_rows_reason": "No discrete unit row on the checked page.",
                        "completed_checks": [{"flag": "tables_inventory_verified", "evidence_ids": ["ev-1"], "reason": "Model reports review."}],
                    },
                }],
            }
            work = root / "work"
            write_checkpoint_stage_inputs(work, checkpoint=checkpoint, source_files={"source/input.pdf": source})
            manifest = json.loads((work / "run_manifest.json").read_text())
            self.assertEqual(manifest["pass1_complete"], True)
            self.assertEqual(manifest["inventory_verified"], True)
            self.assertEqual(manifest["tables_inventory_verified"], True)
            self.assertEqual(manifest["references_read"], ["references/protocol-v6.4.md"])
            self.assertEqual(manifest["memory_cases_consulted"], ["R-01"])
            self.assertEqual(manifest["memory_lookup"]["status"], "UNAVAILABLE")
            self.assertEqual(manifest["no_discrete_rows_reason"], "No discrete unit row on the checked page.")
            self.assertEqual(manifest["no_detail_tables_reason"], "No quantity table on the checked page.")
            stage_checkpoint = json.loads((work / "checkpoint.json").read_text())
            self.assertEqual(stage_checkpoint["stage"], "pass1")
            self.assertEqual(stage_checkpoint["pending_rechecks"], [])

    def test_materializer_records_xlsx_sheets_without_pdf_pages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "input.pdf"
            xlsx = root / "estimate.xlsx"
            pdf.write_bytes(b"synthetic pdf")
            xlsx.write_bytes(b"synthetic xlsx")
            checkpoint = {
                "pass_no": 1,
                "inventory": {
                    "documents": [
                        {"document_id": "pdf", "revision_digest": _revision_digest(pdf)},
                        {"document_id": "xlsx", "revision_digest": _revision_digest(xlsx)},
                    ],
                    "pages": [{"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": _revision_digest(pdf), "pdf_page": 1}],
                    "sheets": [{"sheet_id": "xlsx-S00001", "document_id": "xlsx", "revision_digest": _revision_digest(xlsx), "sheet_index": 1, "sheet_name": "BOQ"}],
                },
                "ledger": [], "ledger_records": [],
            }
            work = root / "work"
            write_checkpoint_stage_inputs(work, checkpoint=checkpoint, source_files={"source/input.pdf": pdf, "source/estimate.xlsx": xlsx})
            manifest = json.loads((work / "run_manifest.json").read_text())
            self.assertEqual(manifest["document_inventory"], [
                {"doc_id": "pdf", "filename": "source/input.pdf", "page_count": 1, "sha256": _revision_digest(pdf).split(":", 1)[1]},
                {"doc_id": "xlsx", "filename": "source/estimate.xlsx", "sheet_count": 1, "sha256": _revision_digest(xlsx).split(":", 1)[1]},
            ])
            self.assertEqual(manifest["scope_pages_total"], 1)

    def test_snapshot_adapter_runs_pinned_validator_and_reports_unproven_metadata(self):
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt = root / "attempt"
            attempt.mkdir()
            (attempt / "output").mkdir()
            source = root / "input.pdf"
            estimate = root / "estimate.xlsx"
            source.write_bytes(b"synthetic source")
            estimate.write_bytes(b"synthetic workbook")
            revision = _revision_digest(source)
            checkpoint = {
                "pass_no": 1,
                "inventory": {
                    "documents": [{"document_id": "pdf", "revision_digest": revision}, {"document_id": "xlsx", "revision_digest": _revision_digest(estimate)}],
                    "pages": [{"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": revision, "pdf_page": 1}],
                    "sheets": [{"sheet_id": "xlsx-S00001", "document_id": "xlsx", "revision_digest": _revision_digest(estimate), "sheet_index": 1, "sheet_name": "BOQ"}],
                },
                "ledger": [{"check_id": "c1", "pass_no": 1, "execution_status": "DONE", "result_status": "CHECKED"}],
                "ledger_records": [{"record_type": "page_ledger", "record_id": "pdf-P00001", "payload": {"page_id": "pdf-P00001", "source_pdf": "source/input.pdf", "pdf_page": 1, "status": "VERIFIED", "evidence_anchors": ["PDF page 1"]}}],
            }
            write_checkpoint_stage_inputs(attempt / "work", checkpoint=checkpoint, source_files={"source/input.pdf": source, "source/estimate.xlsx": estimate})
            snapshot = audit_quality.create_snapshot(attempt, root / "controller", allowed_source_root=attempt)
            result = audit_quality.validate_snapshot(
                snapshot, run_id=1, stage="pass1", source_digest="a" * 64,
                bundle_root=repo / ".orchestration" / "skill",
                manifest_path=repo / "backend" / "app" / "boq_audit_skill_manifest.json",
            )
            self.assertEqual(result.receipt.controller_status, "BLOCKED")
            self.assertEqual(result.receipt.validator_exit_code, 2)
            self.assertIn("tables_inventory_verified not positively completed", result.receipt.blocking_checks)
            self.assertFalse(any("Document inventory" in item for item in result.receipt.blocking_checks))

    def test_pdf_xlsx_detail_all_passes_reach_real_metadata_gate(self):
        repo = Path(__file__).resolve().parents[2]
        skill = repo / ".orchestration" / "skill"
        reference_ids = runpy.run_path(str(skill / "scripts/validate_run.py"))["MANDATORY_REFS"]
        case_ids = [line.split()[1] for line in (skill / "references/regression.md").read_text().splitlines() if line.startswith("## R-")]
        flags = (
            "tables_inventory_verified", "summary_reconciliation_complete", "drawing_boq_orphan_check_complete",
            "paired_geometry_checks_complete", "assumption_ledger_complete", "reconciliation_labeled_complete",
            "scope_overlap_check_complete", "paired_side_instance_check_complete",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf, xlsx = root / "input.pdf", root / "estimate.xlsx"
            drawing = pymupdf.open()
            page = drawing.new_page(width=200, height=120)
            page.set_cropbox(pymupdf.Rect(10, 10, 190, 110))
            page.set_rotation(90)
            drawing.save(pdf)
            drawing.close()
            workbook = Workbook()
            workbook.active.title = "BOQ"
            workbook.active["A1"] = "Synthetic estimate"
            workbook.save(xlsx)
            pdf_refs = {"page_ids": ["pdf-P00001"], "sheet_ids": [], "target_ids": []}
            checkpoint = {
                "run_id": 1, "approval_id": 1, "source_digest": "a" * 64,
                "skill_digest": "b" * 64, "inventory_digest": "c" * 64,
                "batch_plan_digest": "d" * 64, "version": 1,
                "gate_reports": {}, "batch_results": [],
                "pass_no": 1,
                "inventory": {
                    "documents": [{"document_id": "pdf", "revision_digest": _revision_digest(pdf)}, {"document_id": "xlsx", "revision_digest": _revision_digest(xlsx)}],
                    "pages": [{"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": _revision_digest(pdf), "pdf_page": 1}],
                    "sheets": [{"sheet_id": "xlsx-S00001", "document_id": "xlsx", "revision_digest": _revision_digest(xlsx), "sheet_index": 1, "sheet_name": "BOQ"}],
                    "targets": [],
                },
                "ledger": [{"check_id": "c1", "pass_no": 1, "execution_status": "DONE", "result_status": "UNRESOLVED"}],
                "ledger_records": [
                    {"record_type": "page_ledger", "record_id": "pdf-P00001", "source_refs": pdf_refs,
                     "payload": {"page_id": "pdf-P00001", "source_pdf": "source/input.pdf", "pdf_page": 1, "status": "UNRESOLVED"}},
                    {"record_type": "run_metadata", "record_id": "META-c1", "source_refs": pdf_refs,
                     "payload": {
                         "metadata_id": "META-c1", "stage": "pass1", "references_read": sorted(reference_ids),
                         "memory_cases_consulted": [],
                         "completed_checks": [{"flag": flag, "evidence_ids": ["ev-1"], "reason": "Synthetic source review recorded."} for flag in flags],
                         "no_summary_tables_reason": "No summary table on the synthetic source.",
                     }},
                    {"record_type": "detail_rows", "record_id": "ROW-1", "source_refs": pdf_refs,
                     "payload": {"row_id": "ROW-1", "section_id": "SEC-1", "stt": 1,
                                 "source_anchor": "PDF page 1, row 1", "unit": "cái", "reported_quantity": 2,
                                 "spec_status": "VERIFIED", "qty_status": "VERIFIED", "status": "VERIFIED",
                                 "sequence_checked": False, "integer_sanity_checked": True}},
                    {"record_type": "calculation_ledger", "record_id": "CALC-1", "source_refs": pdf_refs,
                     "payload": {"calc_id": "CALC-1", "formula": "1 * 2", "inputs": [{"value": 1}, {"value": 2}],
                                 "source_anchors": ["PDF page 1, row 1"], "raw_result": 2, "unit": "cái",
                                 "pass1_result": 2, "status": "VERIFIED", "calculation_class": "INDEPENDENT_TAKEOFF"}},
                    {"record_type": "issue_candidates", "record_id": "CAND-1", "check_id": "c1",
                     "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": ["xlsx-S00001"], "target_ids": []},
                     "payload": {"candidate_id": "CAND-1", "claim": "Synthetic drawing and workbook values differ.", "evidence_ids": ["ev-1"]}},
                    *({"record_type": "regression_checks", "record_id": case_id, "source_refs": pdf_refs,
                       "payload": {"case_id": case_id, "status": "NOT_APPLICABLE", "target_ids": [], "evidence_anchors": ["PDF page 1"], "reason": "No matching detail in the synthetic source."}} for case_id in case_ids),
                ],
            }
            for record in checkpoint["ledger_records"]:
                record.setdefault("check_id", "c1")
            incomplete = root / "incomplete"
            incomplete.mkdir()
            (incomplete / "output").mkdir()
            write_checkpoint_stage_inputs(incomplete / "work", checkpoint=checkpoint, source_files={"source/input.pdf": pdf, "source/estimate.xlsx": xlsx})
            manifest = json.loads((incomplete / "work/run_manifest.json").read_text())
            self.assertEqual((manifest["sequence_sections_total"], manifest["sequence_sections_checked"]), (1, 0))
            self.assertEqual((manifest["discrete_rows_total"], manifest["discrete_rows_checked"]), (1, 1))
            blocked_snapshot = audit_quality.create_snapshot(incomplete, root / "controller-incomplete", allowed_source_root=incomplete)
            blocked = audit_quality.validate_snapshot(blocked_snapshot, run_id=1, stage="pass1", source_digest="a" * 64,
                bundle_root=skill, manifest_path=repo / "backend/app/boq_audit_skill_manifest.json")
            self.assertEqual(blocked.receipt.controller_status, "BLOCKED")
            self.assertIn("sequence_sections_checked does not cover entire scope", blocked.receipt.blocking_checks)
            detail = next(record for record in checkpoint["ledger_records"] if record["record_type"] == "detail_rows")
            detail["payload"]["sequence_checked"] = True
            for pass_no in (1, 2, 3):
                checkpoint["pass_no"] = pass_no
                if pass_no > 1:
                    calc = next(record for record in checkpoint["ledger_records"] if record["record_type"] == "calculation_ledger")
                    calc["payload"][f"pass{pass_no}_result"] = 2
                    checkpoint["ledger"].append({"check_id": f"c{pass_no}", "pass_no": pass_no, "execution_status": "DONE", "result_status": "CHECKED"})
                    checkpoint["ledger_records"].append({
                        "record_type": f"pass{pass_no}_review", "record_id": f"review-{pass_no}", "check_id": f"c{pass_no}",
                        "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": ["xlsx-S00001"], "target_ids": []},
                        "payload": {"review_id": f"review-{pass_no}", "pass_no": pass_no,
                                    "page_ids": ["pdf-P00001"], "sheet_ids": ["xlsx-S00001"],
                                    "target": "PDF and workbook scope", "method": "Synthetic independent review",
                                    "result": "Synthetic detail and calculation reviewed", "evidence": ["PDF page 1", "BOQ sheet"],
                                    **({"issue_ids": ["I-01"]} if pass_no == 3 else {})},
                    })
                if pass_no == 3:
                    checkpoint["lesson_snapshot"] = _empty_lesson_snapshot()
                    checkpoint["ledger_records"].append({
                        "record_type": "issues", "record_id": "I-01", "check_id": "c3",
                        "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": ["xlsx-S00001"], "target_ids": []},
                        "payload": {"issue_id": "I-01", "claim": "Synthetic discrepancy retained for review.",
                                    "status": "CONFIRMED_ERROR", "final_status": "CONFIRMED_ERROR",
                                    "candidate_ids": ["CAND-1"], "evidence": ["PDF page 1", "BOQ sheet"]},
                    })
                stage_attempt = root / f"attempt-{pass_no}"
                stage_attempt.mkdir()
                (stage_attempt / "output").mkdir()
                write_checkpoint_stage_inputs(stage_attempt / "work", checkpoint=checkpoint, source_files={"source/input.pdf": pdf, "source/estimate.xlsx": xlsx})
                stage_manifest = json.loads((stage_attempt / "work/run_manifest.json").read_text())
                self.assertEqual((stage_manifest["sequence_sections_total"], stage_manifest["sequence_sections_checked"]), (1, 1))
                self.assertEqual((stage_manifest["discrete_rows_total"], stage_manifest["discrete_rows_checked"]), (1, 1))
                if pass_no == 3:
                    self.assertTrue(stage_manifest["false_positive_propagation_complete"])
                    self.assertEqual(json.loads((stage_attempt / "work/issues.json").read_text())[0]["issue_id"], "I-01")
                snapshot = audit_quality.create_snapshot(stage_attempt, root / f"controller-{pass_no}", allowed_source_root=stage_attempt)
                result = audit_quality.validate_snapshot(snapshot, run_id=1, stage=f"pass{pass_no}", source_digest="a" * 64,
                    bundle_root=skill, manifest_path=repo / "backend/app/boq_audit_skill_manifest.json")
                self.assertEqual(result.receipt.controller_status, "PASS", result.receipt.blocking_checks)
                self.assertEqual(result.receipt.candidate_release_status, "METADATA_STAGE_COMPLETE")
                self.assertGreater(result.receipt.open_record_count, 0)
            source = build_report_source(checkpoint)
            classifications = build_classification_state(source, [{
                "issue_id": "I-01", "revision_no": 1, "final_status": "CONFIRMED_ERROR",
                "severity": "HIGH", "reviewer_user_id": 1,
                "reason": "Synthetic issue checked against the PDF and workbook.",
            }])
            with pymupdf.open(pdf) as drawing:
                pix = drawing[0].get_pixmap(dpi=144)
            mark = {
                "mark_id": "MARK-1", "issue_id": "I-01", "page_id": "pdf-P00001", "dpi": 144,
                "render_size_px": [pix.width, pix.height],
                "target_bbox_px": [20, 20, 55, 32], "note_bbox_px": [55, 45, 200, 330],
                "leader_points_px": [[57, 47], [55, 28]],
                "note": "HIGH · I-01 — Sai.\nKiểm tra.",
                "snapshot_digest": source["snapshot_digest"],
                "classification_digest": classifications["classification_digest"],
            }
            annotated_pdf, annotation_qa = render_annotation_draft(
                pdf_path=pdf, inventory=checkpoint["inventory"], source=source,
                classifications=classifications, marks=[mark],
                font_path=_NOTO_SANS_FONT,
            )
            self.assertTrue(annotated_pdf.startswith(b"%PDF"))
            self.assertEqual(annotation_qa["annotation_qa_status"], "UNVERIFIED")
            self.assertEqual(annotation_qa["missing_active_issue_ids"], [])
            report = build_excel_report_draft(source=source, classifications=classifications, source_workbook=xlsx, annotation_qa=annotation_qa)
            qa = validate_excel_report_draft(report, source=source, classifications=classifications, source_workbook=xlsx, annotation_qa=annotation_qa)
            self.assertEqual(qa["status"], "PASS", qa["checks"])
            output = load_workbook(BytesIO(report))
            issue_sheet = output["01_Lỗi_kiến_nghị"]
            columns = {cell.value: cell.column for cell in issue_sheet[1]}
            self.assertEqual(issue_sheet.cell(2, columns["record_id"]).value, "I-01")
            self.assertEqual(issue_sheet.cell(2, columns["reviewer_severity"]).value, "HIGH")
            self.assertEqual(issue_sheet.cell(2, columns["source_page_ids"]).value, '["pdf-P00001"]')
            self.assertEqual(issue_sheet.cell(2, columns["source_sheet_ids"]).value, '["xlsx-S00001"]')
            self.assertEqual(output["10_Đánh_dấu_PDF"]["A2"].value, "MARK-1")
            self.assertEqual(output.sheetnames[-1], "BOQ")

    def test_materializer_rejects_missing_or_changed_approved_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.pdf"
            source.write_bytes(b"approved source")
            revision = _revision_digest(source)
            checkpoint = {
                "pass_no": 1,
                "inventory": {
                    "documents": [{"document_id": "pdf", "revision_digest": revision}],
                    "pages": [], "sheets": [], "targets": [],
                },
                "ledger": [], "ledger_records": [],
            }
            with self.assertRaisesRegex(StageGateInputError, "no trusted stage source"):
                write_checkpoint_stage_inputs(root / "missing", checkpoint=checkpoint, source_files={})
            source.write_bytes(b"changed source")
            with self.assertRaisesRegex(StageGateInputError, "differs from approved inventory"):
                write_checkpoint_stage_inputs(root / "changed", checkpoint=checkpoint, source_files={"source/input.pdf": source})

    def test_materializer_blocks_domain_records_with_dangling_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.pdf"
            source.write_bytes(b"synthetic source")
            revision = _revision_digest(source)
            refs = {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []}
            checkpoint = {
                "pass_no": 3,
                "lesson_snapshot": _empty_lesson_snapshot(),
                "inventory": {
                    "documents": [{"document_id": "pdf", "revision_digest": revision}],
                    "pages": [{"page_id": "p1", "document_id": "pdf", "revision_digest": revision, "pdf_page": 1}],
                    "sheets": [], "targets": [],
                },
                "ledger": [{"check_id": "c3", "pass_no": 3, "execution_status": "DONE", "result_status": "CHECKED"}],
                "ledger_records": [
                    {"record_type": "detail_rows", "record_id": "ROW-1", "check_id": "c3", "source_refs": refs,
                     "payload": {"row_id": "ROW-1", "source_anchor": "PDF page 1", "spec_status": "VERIFIED",
                                 "qty_status": "VERIFIED", "status": "VERIFIED"}},
                    {"record_type": "summary_lineage", "record_id": "SUM-1", "check_id": "c3", "source_refs": refs,
                     "payload": {"summary_row_id": "SUM-1", "source_rows": ["ROW-1"], "reported_total": 1,
                                 "source_anchor": "PDF page 1", "status": "VERIFIED"}},
                    {"record_type": "issues", "record_id": "I-01", "check_id": "c3", "source_refs": refs,
                     "payload": {"issue_id": "I-01", "claim": "synthetic", "status": "UNRESOLVED",
                                 "final_status": "UNRESOLVED", "evidence": ["ev-1"]}},
                ],
            }
            write_checkpoint_stage_inputs(root / "resolved", checkpoint=checkpoint, source_files={"source/input.pdf": source})

            checkpoint["ledger_records"][1]["payload"]["source_rows"] = ["ROW-MISSING"]
            with self.assertRaises(StageLedgerReferenceError) as raised:
                write_checkpoint_stage_inputs(root / "dangling", checkpoint=checkpoint, source_files={"source/input.pdf": source})
            self.assertEqual(raised.exception.code, "STAGE_LEDGER_REFERENCE")
            self.assertIn("summary_lineage.source_rows -> missing detail_rows ROW-MISSING", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
