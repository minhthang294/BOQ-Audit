import unittest
import json
from pathlib import Path

from app.services.audit_batch import BatchOutputError
from app.services import audit_batch_ledgers as ledger_validation
from app.services.audit_batch_ledgers import decode_batch_ledger_manifest, encode_batch_ledger_manifest, project_batch_ledger_records, validate_batch_ledger_records


def _valid_schema_value(declared):
    if declared == "any":
        return None
    options = declared.split("|")
    if "null" in options:
        return None
    value_type = options[0]
    if value_type.endswith("[]"):
        return []
    return {"string": "synthetic", "integer": 1, "number": 1.0, "boolean": False, "object": {}}[value_type]


def _valid_record(kind, pass_no):
    contract = ledger_validation._RECORD_TYPES[kind]
    record_id = {
        "page_ledger": "p1", "detail_rows": "row-1", "summary_lineage": "summary-1",
        "calculation_ledger": "CALC-1", "assumption_ledger": "ASM-1",
        "issue_candidates": "candidate-1", "regression_checks": "R-01",
        "physical_instance_ledger": "instance-1", "run_metadata": "META-check-p1",
        "pass2_review": "review-2", "pass3_review": "review-3", "issues": "issue-1",
    }[kind]
    payload = {field: _valid_schema_value(value_type) for field, value_type in contract["field_types"].items()}
    payload[contract["id_field"]] = record_id
    payload.update(status="VERIFIED")
    if kind == "page_ledger":
        payload.update(source_pdf="drawing.pdf", pdf_page=1, evidence_anchors=["page p1"])
    elif kind == "detail_rows":
        payload.update(source_anchor="page p1, row 1", spec_status="VERIFIED", qty_status="VERIFIED")
    elif kind == "summary_lineage":
        payload.update(source_rows=["row-1"], reported_total=1, source_anchor="page p1", name_spec_match=True, unit_match=True, scope_match=True)
    elif kind == "calculation_ledger":
        payload.update(formula="n × q", inputs=[{"name": "n", "value": 1}], raw_result=1, unit="cái", source_anchors=["page p1"], calculation_class="INDEPENDENT_TAKEOFF", pass1_result=1)
    elif kind == "assumption_ledger":
        payload.update(variable="n", evidence_class="OBSERVED")
    elif kind == "issue_candidates":
        payload.update(claim="synthetic candidate", evidence_ids=["ev-1"])
    elif kind == "regression_checks":
        payload.update(status="NOT_APPLICABLE", evidence_anchors=["page p1"], reason="No matching scope", target_ids=[])
    elif kind == "physical_instance_ledger":
        payload.update(object_type="pier", view_evidence=["ev-1"])
    elif kind == "run_metadata":
        payload.update(stage="pass1", references_read=[], memory_cases_consulted=[], completed_checks=[])
    elif kind in {"pass2_review", "pass3_review"}:
        payload.pop("sheet_ids")
        payload.update(pass_no=pass_no, target="page p1", result="checked", method="source reinspection", evidence=["ev-1"], page_ids=["p1"])
    elif kind == "issues":
        payload.update(status="UNRESOLVED", final_status="UNRESOLVED", evidence=["ev-1"])
    return {
        "record_type": kind, "record_id": record_id, "check_id": "check-p1",
        "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []}, "payload": payload,
    }


class BatchLedgerRecordTests(unittest.TestCase):
    def setUp(self):
        self.inventory = {
            "documents": [{"document_id": "pdf", "revision_digest": "sha256:r1"}],
            "pages": [
                {"page_id": "p1", "document_id": "pdf", "revision_digest": "sha256:r1", "pdf_page": 1},
                {"page_id": "p2", "document_id": "pdf", "revision_digest": "sha256:r1", "pdf_page": 2},
            ],
            "sheets": [], "targets": [],
            "expected_work_items": [{
                "check_id": "check-p1", "document_id": "pdf", "revision_digest": "sha256:r1",
                "page_ids": ["p1"], "target_ids": [], "check_kind": "drawing_review", "pass_no": 1,
                "dependencies": [],
            }],
        }
        self.record = {
            "record_type": "page_ledger", "record_id": "p1", "check_id": "check-p1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
            "payload": {"page_id": "p1", "source_pdf": "drawing.pdf", "pdf_page": 1, "status": "VERIFIED", "evidence_anchors": ["title block"]},
        }

    def validate(self, records=None, *, existing=None, pass_no=1, batch_evidence_ids=None):
        if records is None:
            records = [self.record, self.metadata()]
        elif pass_no == 1 and not any(row.get("record_type") == "run_metadata" for row in records):
            records = [*records, self.metadata()]
        return validate_batch_ledger_records(
            {"records": records},
            inventory=self.inventory, assigned_check_ids=["check-p1"], pass_no=pass_no, existing=existing,
            allowed_source_files={"drawing.pdf"}, batch_evidence_ids=batch_evidence_ids,
        )

    def inventory_for_pass(self, pass_no):
        inventory = json.loads(json.dumps(self.inventory))
        work_item = inventory["expected_work_items"][0]
        for prior_pass in range(1, pass_no):
            prior_id = f"prior-pass-{prior_pass}"
            inventory["expected_work_items"].append({
                **work_item,
                "check_id": prior_id,
                "pass_no": prior_pass,
                "dependencies": [f"prior-pass-{prior_pass - 1}"] if prior_pass > 1 else [],
            })
            work_item["dependencies"] = [prior_id]
        work_item["pass_no"] = pass_no
        return inventory

    @staticmethod
    def metadata():
        return {
            "record_type": "run_metadata", "record_id": "META-check-p1", "check_id": "check-p1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
            "payload": {"metadata_id": "META-check-p1", "stage": "pass1", "references_read": [], "memory_cases_consulted": [], "completed_checks": []},
        }

    def test_accepts_bound_record_and_projects_flat_ledger(self):
        records = self.validate()
        self.assertEqual(records, [self.record, self.metadata()])
        self.assertEqual(project_batch_ledger_records(records)["page_ledger"], [self.record["payload"]])
        self.assertEqual(project_batch_ledger_records(records)["run_metadata"], [self.metadata()["payload"]])

    def test_versioned_contract_covers_every_controller_record_kind(self):
        contract = json.loads((Path(__file__).parents[1] / "app/schemas/audit_batch_ledger_contract.v3.json").read_text())
        record_types = contract["record_types"]
        self.assertEqual(contract["contract_version"], 3)
        self.assertTrue(contract["enums"])
        self.assertEqual(set(record_types), {
            "page_ledger", "detail_rows", "summary_lineage", "calculation_ledger", "assumption_ledger",
            "issue_candidates", "regression_checks", "physical_instance_ledger", "run_metadata",
            "pass2_review", "pass3_review", "issues",
        })
        for contract_row in record_types.values():
            self.assertTrue(contract_row["id_field"])
            self.assertTrue(contract_row["passes"])
            self.assertTrue(contract_row["required_fields"])
            self.assertTrue(set(contract_row["required_fields"]) <= set(contract_row["field_types"]))
            self.assertIn(contract_row["id_field"], contract_row["field_types"])
            self.assertTrue(set(contract_row.get("enum_fields", {}).values()) <= set(contract["enums"]))
        metadata_nested = record_types["run_metadata"]["nested_object_arrays"]["completed_checks"]
        self.assertEqual(set(metadata_nested["required_fields"]), set(metadata_nested["field_types"]))
        self.assertEqual(metadata_nested["enum_fields"], {"flag": "completed_check_flag"})
        self.assertEqual(record_types["assumption_ledger"]["prefixes"]["assumption_id"], "ASM-")
        self.assertEqual(record_types["issues"]["numeric_bounds"]["pass_found"], {"minimum": 1, "maximum": 3})
        self.assertEqual(record_types["pass3_review"]["unique_items"], ["page_ids", "sheet_ids", "case_ids", "target_ids", "issue_ids"])

    def test_zero_scope_reason_must_be_a_nonempty_model_record(self):
        metadata = self.metadata()
        metadata["payload"]["no_discrete_rows_reason"] = "No discrete units found on this checked page."
        self.validate([self.record, metadata])
        metadata["payload"]["no_discrete_rows_reason"] = "  "
        with self.assertRaisesRegex(BatchOutputError, "no_discrete_rows_reason"):
            self.validate([self.record, metadata])

    def test_sheet_only_review_requires_approved_sheet_scope(self):
        inventory = self.inventory_for_pass(3)
        inventory["sheets"] = [{"sheet_id": "s1", "document_id": "pdf", "revision_digest": "sha256:r1", "sheet_index": 1, "sheet_name": "BOQ"}]
        for item in inventory["expected_work_items"]:
            item["sheet_ids"] = ["s1"]
        review = _valid_record("pass3_review", 3)
        review["payload"].pop("page_ids")
        review["payload"]["sheet_ids"] = ["s1"]
        review["source_refs"]["sheet_ids"] = ["s1"]
        validate_batch_ledger_records({"records": [review]}, inventory=inventory, assigned_check_ids=["check-p1"], pass_no=3, batch_evidence_ids={"ev-1"})
        review["payload"]["sheet_ids"] = ["missing"]
        with self.assertRaisesRegex(BatchOutputError, "controller-bound source_refs"):
            validate_batch_ledger_records({"records": [review]}, inventory=inventory, assigned_check_ids=["check-p1"], pass_no=3, batch_evidence_ids={"ev-1"})

    def test_declared_field_type_maps_reject_malformed_values_for_all_record_types(self):
        invalid_values = {
            "string": 1, "integer": True, "number": True, "boolean": "true",
            "object": [], "null": False, "string|null": {}, "number|null": {},
            "string|integer": {}, "number|string|null": {}, "string[]": "not-an-array",
            "object[]": ["not-an-object"],
        }
        exercised = 0
        for kind, contract in ledger_validation._RECORD_TYPES.items():
            for field, declared in contract["field_types"].items():
                invalid = invalid_values.get(declared)
                if invalid is None:
                    continue
                with self.subTest(kind=kind, field=field, declared=declared):
                    with self.assertRaisesRegex(BatchOutputError, "declared JSON type"):
                        ledger_validation._validate_declared_field_types(kind, {field: invalid})
                exercised += 1
        expected = sum(
            declared in invalid_values
            for contract in ledger_validation._RECORD_TYPES.values()
            for declared in contract["field_types"].values()
        )
        self.assertEqual(exercised, expected)

    def test_declared_enums_reject_invalid_values_and_cover_every_enum_field(self):
        exercised = set()
        for kind, contract in ledger_validation._RECORD_TYPES.items():
            for field, enum_name in contract.get("enum_fields", {}).items():
                with self.subTest(kind=kind, field=field):
                    with self.assertRaisesRegex(BatchOutputError, "declared enum"):
                        ledger_validation._validate_declared_field_enums(kind, {field: "__invalid_enum__"})
                exercised.add((kind, field))
        self.assertGreaterEqual(len(exercised), 15)

    def test_every_record_type_and_permitted_pass_accepts_a_full_known_field_example(self):
        for kind, contract in ledger_validation._RECORD_TYPES.items():
            for pass_no in contract["passes"]:
                with self.subTest(kind=kind, pass_no=pass_no):
                    inventory = self.inventory_for_pass(pass_no)
                    record = _valid_record(kind, pass_no)
                    records = [record]
                    if pass_no == 1 and kind != "run_metadata":
                        records.append(_valid_record("run_metadata", 1))
                    accepted = validate_batch_ledger_records(
                        {"records": records}, inventory=inventory,
                        assigned_check_ids=["check-p1"], pass_no=pass_no,
                        allowed_source_files={"drawing.pdf"}, batch_evidence_ids={"ev-1"},
                    )
                    self.assertEqual(accepted, records)

    def test_metadata_nested_flag_enum_rejects_unknown_flag(self):
        metadata = {
            "record_type": "run_metadata", "record_id": "META-check-p1", "check_id": "check-p1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
            "payload": {"metadata_id": "META-check-p1", "stage": "pass1", "references_read": [],
                        "memory_cases_consulted": [], "completed_checks": [
                            {"flag": "unsupported_flag", "evidence_ids": ["ev-1"], "reason": "synthetic"}
                        ]},
        }
        with self.assertRaisesRegex(BatchOutputError, "declared enum"):
            self.validate([metadata], batch_evidence_ids={"ev-1"})

    def test_pass1_requires_exactly_one_metadata_record(self):
        with self.assertRaisesRegex(BatchOutputError, "exactly one run_metadata"):
            validate_batch_ledger_records(
                {"records": [self.record]}, inventory=self.inventory,
                assigned_check_ids=["check-p1"], pass_no=1,
                allowed_source_files={"drawing.pdf"},
            )
        metadata = self.metadata()
        duplicate = self.metadata()
        duplicate["record_id"] = duplicate["payload"]["metadata_id"] = "META-check-p1-duplicate"
        with self.assertRaisesRegex(BatchOutputError, "exactly one run_metadata"):
            validate_batch_ledger_records(
                {"records": [self.record, metadata, duplicate]}, inventory=self.inventory,
                assigned_check_ids=["check-p1"], pass_no=1,
                allowed_source_files={"drawing.pdf"},
            )

    def test_rejects_record_outside_assigned_source_scope(self):
        self.record["source_refs"]["page_ids"] = ["p2"]
        with self.assertRaises(BatchOutputError):
            self.validate()

    def test_rejects_payload_scope_wider_than_controller_refs(self):
        self.record["payload"]["page_ids"] = ["p1", "p2"]
        with self.assertRaises(BatchOutputError):
            self.validate()

    def test_rejects_cross_scope_reference_nested_in_anchor(self):
        self.record["payload"]["source_anchor"] = {"page_id": "p2"}
        with self.assertRaises(BatchOutputError):
            self.validate()

    def test_rejects_duplicate_or_overwrite_record_id(self):
        with self.assertRaises(BatchOutputError):
            self.validate([self.record, self.record])
        with self.assertRaises(BatchOutputError):
            self.validate(existing=[self.record])

    def test_rejects_wrong_pass_and_unknown_keys(self):
        with self.assertRaises(BatchOutputError):
            self.validate(pass_no=2)
        self.record["unexpected"] = True
        with self.assertRaises(BatchOutputError):
            self.validate()

    def test_review_payload_pass_must_match_assigned_pass(self):
        inventory = self.inventory_for_pass(2)
        record = {
            "record_type": "pass2_review", "record_id": "review-1", "check_id": "check-p1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
            "payload": {"review_id": "review-1", "pass_no": 1},
        }
        with self.assertRaises(BatchOutputError):
            validate_batch_ledger_records(
                {"records": [record]}, inventory=inventory, assigned_check_ids=["check-p1"], pass_no=2,
            )

    def test_rejects_mismatched_domain_id(self):
        self.record["payload"]["page_id"] = "p2"
        with self.assertRaises(BatchOutputError):
            self.validate()

    def test_rejects_page_metadata_not_bound_to_inventory_or_source(self):
        self.record["payload"]["pdf_page"] = 2
        with self.assertRaises(BatchOutputError):
            self.validate()
        self.record["payload"]["pdf_page"] = 1
        self.record["payload"]["source_pdf"] = "unapproved.pdf"
        with self.assertRaises(BatchOutputError):
            self.validate()

    def test_rejects_unrecognized_status_without_type_error(self):
        self.record["payload"]["status"] = {"VERIFIED": True}
        with self.assertRaises(BatchOutputError):
            self.validate()

    def test_unchecked_page_state_is_rejected_before_pass_gate(self):
        self.record["payload"]["status"] = "NOT_CHECKED"
        with self.assertRaisesRegex(BatchOutputError, "declared enum"):
            self.validate()

    def test_candidate_evidence_must_link_to_batch_evidence(self):
        candidate = {
            "record_type": "issue_candidates", "record_id": "candidate-1", "check_id": "check-p1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
            "payload": {"candidate_id": "candidate-1", "claim": "dimension differs", "evidence_ids": ["ev-1"]},
        }
        with self.assertRaises(BatchOutputError):
            self.validate([candidate])
        self.assertEqual(validate_batch_ledger_records(
            {"records": [candidate, self.metadata()]}, inventory=self.inventory, assigned_check_ids=["check-p1"], pass_no=1,
            allowed_source_files={"drawing.pdf"}, batch_evidence_ids={"ev-1"},
        ), [candidate, self.metadata()])

    def test_reported_total_rejects_boolean_through_public_validator(self):
        summary = {
            "record_type": "summary_lineage", "record_id": "summary-1", "check_id": "check-p1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
            "payload": {"summary_row_id": "summary-1", "source_rows": ["row-1"],
                        "reported_total": True, "status": "VERIFIED", "source_anchor": "page p1"},
        }
        with self.assertRaisesRegex(BatchOutputError, "declared JSON type"):
            self.validate([summary])

    def test_calculation_inputs_follow_v64_object_array_contract(self):
        calculation = {
            "record_type": "calculation_ledger", "record_id": "CALC-1", "check_id": "check-p1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
            "payload": {"calc_id": "CALC-1", "formula": "n × q", "inputs": [{"name": "n", "value": 2}],
                        "raw_result": 2, "unit": "cái", "status": "VERIFIED", "source_anchors": ["page p1"],
                        "calculation_class": "INDEPENDENT_TAKEOFF", "pass1_result": 2},
        }
        self.assertEqual(self.validate([calculation])[0], calculation)
        for invalid_inputs in ({"n": 2}, [2], []):
            with self.subTest(inputs=invalid_inputs):
                invalid = {**calculation, "payload": {**calculation["payload"], "inputs": invalid_inputs}}
                with self.assertRaises(BatchOutputError):
                    self.validate([invalid])

    def test_assumption_id_pattern_and_issue_pass_found_bounds_are_enforced(self):
        assumption = _valid_record("assumption_ledger", 1)
        assumption["record_id"] = assumption["payload"]["assumption_id"] = "ASSUMPTION-1"
        with self.assertRaisesRegex(BatchOutputError, "invalid prefix"):
            self.validate([assumption])

        issue = _valid_record("issues", 3)
        inventory = self.inventory_for_pass(3)
        for invalid_pass in (0, 4):
            with self.subTest(pass_found=invalid_pass):
                invalid = {**issue, "payload": {**issue["payload"], "pass_found": invalid_pass}}
                with self.assertRaisesRegex(BatchOutputError, "outside its allowed range"):
                    validate_batch_ledger_records(
                        {"records": [invalid]}, inventory=inventory,
                        assigned_check_ids=["check-p1"], pass_no=3, batch_evidence_ids={"ev-1"},
                    )

    def test_pass_review_schema_rejects_duplicate_reference_arrays(self):
        inventory = self.inventory_for_pass(3)
        review = _valid_record("pass3_review", 3)
        review["payload"]["page_ids"] = []
        with self.assertRaisesRegex(BatchOutputError, "too few items"):
            validate_batch_ledger_records(
                {"records": [review]}, inventory=inventory,
                assigned_check_ids=["check-p1"], pass_no=3, batch_evidence_ids={"ev-1"},
            )

        review["payload"]["page_ids"] = ["p1", "p1"]
        with self.assertRaisesRegex(BatchOutputError, "items must be unique"):
            validate_batch_ledger_records(
                {"records": [review]}, inventory=inventory,
                assigned_check_ids=["check-p1"], pass_no=3, batch_evidence_ids={"ev-1"},
            )

        review["payload"]["page_ids"] = ["p1"]
        review["payload"]["case_ids"] = ["R-01", "R-01"]
        with self.assertRaisesRegex(BatchOutputError, "items must be unique"):
            validate_batch_ledger_records(
                {"records": [review]}, inventory=inventory,
                assigned_check_ids=["check-p1"], pass_no=3, batch_evidence_ids={"ev-1"},
            )

    def test_projection_rejects_duplicate_persisted_ids(self):
        with self.assertRaises(BatchOutputError):
            project_batch_ledger_records([self.record, self.record])

    def test_batch_manifest_detects_mutation_and_accepts_empty_batch(self):
        encoded, digest = encode_batch_ledger_manifest([self.record])
        self.assertEqual(decode_batch_ledger_manifest(encoded, digest), [self.record])
        with self.assertRaises(BatchOutputError):
            decode_batch_ledger_manifest(encoded + " ", digest)
        empty, empty_digest = encode_batch_ledger_manifest([])
        self.assertEqual(decode_batch_ledger_manifest(empty, empty_digest), [])

    def test_run_metadata_is_bounded_to_pass1_references_and_batch_evidence(self):
        metadata = {
            "record_type": "run_metadata", "record_id": "META-check-p1", "check_id": "check-p1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
            "payload": {
                "metadata_id": "META-check-p1", "stage": "pass1",
                "references_read": ["references/protocol-v6.4.md"],
                "memory_cases_consulted": ["R-01"],
                "completed_checks": [{"flag": "tables_inventory_verified", "evidence_ids": ["ev-1"], "reason": "Table inventory reviewed against the scoped source."}],
            },
        }
        self.assertEqual(self.validate([metadata], batch_evidence_ids={"ev-1"}), [metadata])
        with self.assertRaises(BatchOutputError):
            self.validate([metadata], batch_evidence_ids=set())
        metadata["payload"]["references_read"] = ["../../private.txt"]
        with self.assertRaises(BatchOutputError):
            self.validate([metadata], batch_evidence_ids={"ev-1"})

    def test_run_metadata_rejects_non_string_completion_flag_as_batch_error(self):
        metadata = self.metadata()
        metadata["payload"]["completed_checks"] = [{"flag": [], "evidence_ids": ["ev-1"], "reason": "synthetic"}]
        with self.assertRaises(BatchOutputError):
            self.validate([metadata], batch_evidence_ids={"ev-1"})

    def test_run_metadata_rejects_unknown_nested_completion_field(self):
        metadata = self.metadata()
        metadata["payload"]["completed_checks"] = [{
            "flag": "tables_inventory_verified", "evidence_ids": ["ev-1"],
            "reason": "synthetic", "extra": "not in the contract",
        }]
        with self.assertRaisesRegex(BatchOutputError, "missing or unknown fields"):
            self.validate([metadata], batch_evidence_ids={"ev-1"})

    def test_issue_candidate_ids_reject_non_string_values_as_batch_error(self):
        inventory = self.inventory_for_pass(3)
        issue = {
            "record_type": "issues", "record_id": "issue-1", "check_id": "check-p1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
            "payload": {"issue_id": "issue-1", "claim": "synthetic", "status": "UNRESOLVED",
                        "final_status": "UNRESOLVED", "candidate_ids": [{}]},
        }
        with self.assertRaises(BatchOutputError):
            validate_batch_ledger_records(
                {"records": [issue]}, inventory=inventory,
                assigned_check_ids=["check-p1"], pass_no=3,
                allowed_source_files={"drawing.pdf"},
            )


class DomainReferenceTests(unittest.TestCase):
    @staticmethod
    def _projection():
        return project_batch_ledger_records([
            {"record_type": "detail_rows", "record_id": "ROW-1", "payload": {"row_id": "ROW-1"}},
            {"record_type": "calculation_ledger", "record_id": "CALC-1", "payload": {"calc_id": "CALC-1"}},
            {"record_type": "assumption_ledger", "record_id": "ASM-1", "payload": {"assumption_id": "ASM-1"}},
            {"record_type": "issue_candidates", "record_id": "CAND-1", "payload": {"candidate_id": "CAND-1"}},
            {"record_type": "regression_checks", "record_id": "MEM-1-v1", "payload": {"case_id": "MEM-1-v1"}},
            {"record_type": "issues", "record_id": "I-01", "payload": {
                "issue_id": "I-01", "calc_ids": ["CALC-1"], "candidate_ids": ["CAND-1"],
            }},
            {"record_type": "summary_lineage", "record_id": "SUM-1", "payload": {
                "summary_row_id": "SUM-1", "source_rows": ["ROW-1"],
            }},
            {"record_type": "pass3_review", "record_id": "REV-1", "payload": {
                "review_id": "REV-1", "calc_id": "CALC-1", "case_ids": ["MEM-1-v1"], "issue_ids": ["I-01"],
            }},
        ])

    def test_resolved_contract_references_pass(self):
        ledger_validation.verify_domain_references(self._projection())

    def test_dangling_summary_source_row_is_rejected(self):
        projection = self._projection()
        projection["summary_lineage"][0]["source_rows"] = ["ROW-1", "ROW-MISSING"]
        with self.assertRaisesRegex(BatchOutputError, r"summary_lineage.source_rows -> missing detail_rows ROW-MISSING"):
            ledger_validation.verify_domain_references(projection)

    def test_dangling_issue_and_review_references_are_rejected(self):
        projection = self._projection()
        projection["issues"][0]["calc_ids"] = ["CALC-MISSING"]
        with self.assertRaisesRegex(BatchOutputError, "missing calculation_ledger CALC-MISSING"):
            ledger_validation.verify_domain_references(projection)

        projection = self._projection()
        projection["pass3_review"][0]["case_ids"] = ["MEM-9-v1"]
        with self.assertRaisesRegex(BatchOutputError, "missing regression_checks MEM-9-v1"):
            ledger_validation.verify_domain_references(projection)

        projection = self._projection()
        projection["issues"][0]["candidate_ids"] = ["CAND-MISSING"]
        with self.assertRaisesRegex(BatchOutputError, "missing issue_candidates CAND-MISSING"):
            ledger_validation.verify_domain_references(projection)

        projection = self._projection()
        projection["pass2_review"] = [{"review_id": "REV-2", "issue_ids": ["I-MISSING"]}]
        with self.assertRaisesRegex(BatchOutputError, "missing issues I-MISSING"):
            ledger_validation.verify_domain_references(projection)

    def test_absent_references_are_not_invented(self):
        projection = project_batch_ledger_records([
            {"record_type": "issues", "record_id": "I-01", "payload": {"issue_id": "I-01", "final_status": "UNRESOLVED"}},
        ])
        ledger_validation.verify_domain_references(projection)

    def test_non_string_reference_value_is_rejected(self):
        projection = self._projection()
        projection["issues"][0]["calc_ids"] = [{"calc_id": "CALC-1"}]
        with self.assertRaisesRegex(BatchOutputError, "must be a string or string array"):
            ledger_validation.verify_domain_references(projection)


if __name__ == "__main__":
    unittest.main()
