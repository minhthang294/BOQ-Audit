import copy
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from scripts.evaluate_audit_quality import EvaluationError, _load, score


ROOT = Path(__file__).resolve().parent
SCHEMAS = ROOT / "schemas"
SYNTHETIC = ROOT / "synthetic"


def _fixture(name):
    return json.loads((ROOT / "synthetic" / name).read_text(encoding="utf-8"))


def _inputs(run_name="run_record_completed.synthetic.json"):
    return (
        _fixture("reference_confirmed.synthetic.json"),
        _fixture("adjudication.synthetic.json"),
        _fixture(run_name),
    )


class EvaluationTests(unittest.TestCase):
    def test_schema_ids_are_unique_and_schema_versions_match_filenames(self):
        schema_ids = set()
        schema_validators = {}
        for path in SCHEMAS.glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schema_id = schema.get("$id")
            version = schema.get("properties", {}).get("schema_version", {}).get("const")
            self.assertIsInstance(schema_id, str, path.name)
            self.assertNotIn(schema_id, schema_ids, path.name)
            schema_ids.add(schema_id)
            expected = f"q10.{path.name.removesuffix('.schema.json')}/1"
            self.assertEqual(version, expected, path.name)
            Draft202012Validator.check_schema(schema)
            schema_validators[version] = Draft202012Validator(schema, format_checker=FormatChecker())
        self.assertTrue(schema_validators)

    def test_synthetic_fixtures_validate_against_their_draft2020_schema(self):
        schema_validators = {}
        for path in SCHEMAS.glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            version = schema["properties"]["schema_version"]["const"]
            schema_validators[version] = Draft202012Validator(schema, format_checker=FormatChecker())
        fixtures = list(SYNTHETIC.glob("*.synthetic.json"))
        self.assertTrue(fixtures)
        for path in fixtures:
            fixture = json.loads(path.read_text(encoding="utf-8"))
            validator = schema_validators.get(fixture.get("schema_version"))
            self.assertIsNotNone(validator, path.name)
            self.assertEqual(fixture.get("data_origin"), "SYNTHETIC", path.name)
            validator.validate(fixture)

    def test_draft2020_validator_rejects_missing_required_fixture_fields(self):
        schema = json.loads((SCHEMAS / "reference.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        reference, _, _ = _inputs()
        reference.pop("label_version")
        self.assertFalse(validator.is_valid(reference))

    def test_score_counts_unique_adjudications_and_keeps_unresolved_out_of_denominators(self):
        result = score(*_inputs())
        self.assertEqual(result["metrics"], {
            "tp": 1, "fp": 1, "fn": 1, "unresolved": 1, "unscored": 1,
            "precision": 0.5, "recall": 0.5, "precision_denominator": 2, "recall_denominator": 2,
            "denominator_notes": [
                "Unresolved findings are excluded from precision and recall denominators.",
                "Unresolved/excluded reference labels are retained as unscored.",
            ],
        })
        self.assertFalse(result["scoring_complete"])

    def test_partial_attempt_is_not_complete_and_unseen_references_are_unscored(self):
        reference, adjudication, partial = _inputs("run_record_partial.synthetic.json")
        partial_adjudication = copy.deepcopy(adjudication)
        partial_adjudication["findings"] = [
            item for item in partial_adjudication["findings"] if item["disposition"] in {"TP", "UNRESOLVED", "OUT_OF_SCOPE"}
        ]
        partial_adjudication["missed_references"] = []
        partial_adjudication.update(run_record_id=partial["run_record_id"], attempt_id=partial["attempt_id"])
        result = score(reference, partial_adjudication, partial)
        self.assertFalse(result["scoring_complete"])
        self.assertEqual(result["metrics"]["unscored"], 2)
        self.assertEqual(result["metrics"]["fn"], 0)

    def test_failed_attempt_with_no_checked_artifact_is_retained_as_unscored(self):
        reference, adjudication, failed = _inputs("run_record_failed.synthetic.json")
        adjudication["findings"] = []
        adjudication["missed_references"] = []
        adjudication.update(run_record_id=failed["run_record_id"], attempt_id=failed["attempt_id"])
        result = score(reference, adjudication, failed)
        self.assertFalse(result["scoring_complete"])
        self.assertEqual(result["metrics"]["unscored"], len(reference["references"]))
        self.assertIsNone(result["metrics"]["precision"])
        self.assertIsNone(result["metrics"]["recall"])

    def test_rejects_source_revision_or_hash_drift(self):
        reference, adjudication, run = _inputs()
        run["sources"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(EvaluationError, "do not match the reference"):
            score(reference, adjudication, run)

    def test_rejects_duplicate_mapping_as_an_extra_true_positive(self):
        reference, adjudication, run = _inputs()
        adjudication["findings"][2]["disposition"] = "TP"
        adjudication["findings"][2]["duplicate_of"] = None
        adjudication["findings"][2]["counts_as_unique"] = True
        with self.assertRaisesRegex(EvaluationError, "TP must map once"):
            score(reference, adjudication, run)

    def test_duplicate_can_precede_its_root_without_inflating_metrics(self):
        reference, adjudication, run = _inputs()
        adjudication["findings"].insert(0, adjudication["findings"].pop(2))
        self.assertEqual(score(reference, adjudication, run)["metrics"]["tp"], 1)

    def test_rejects_non_object_top_level_inputs_with_domain_error(self):
        with self.assertRaisesRegex(EvaluationError, "must be JSON objects"):
            score([], {}, {})

    def test_rejects_unhashable_ids_and_missing_identity_without_crashing(self):
        reference, adjudication, run = _inputs()
        adjudication["findings"][0]["ref_id"] = ["REF-SYN-001"]
        with self.assertRaisesRegex(EvaluationError, "TP must map"):
            score(reference, adjudication, run)
        adjudication["findings"][0]["ref_id"] = "REF-SYN-001"
        adjudication.pop("run_record_id")
        run.pop("run_record_id")
        with self.assertRaisesRegex(EvaluationError, "identity does not match"):
            score(reference, adjudication, run)

    def test_rejects_stale_reference_version_and_unverified_source(self):
        reference, adjudication, run = _inputs()
        run["reference_version"] += 1
        with self.assertRaisesRegex(EvaluationError, "run record reference version is stale"):
            score(reference, adjudication, run)
        run["reference_version"] -= 1
        reference["source_verification"]["status"] = "MISMATCH"
        with self.assertRaisesRegex(EvaluationError, "not verified as MATCH"):
            score(reference, adjudication, run)

    def test_split_references_sharing_a_root_remain_independently_scorable(self):
        reference, adjudication, run = _inputs()
        reference["references"][2]["root_ref_id"] = "REF-SYN-001"
        adjudication["findings"].append({
            "finding_id": "FIND-SYN-006", "root_finding_id": "FIND-SYN-006", "duplicate_of": None,
            "ref_id": "REF-SYN-003", "disposition": "TP", "rationale": "split child finding",
            "evidence_checked": True, "counts_as_unique": True,
        })
        adjudication["missed_references"] = []
        result = score(reference, adjudication, run)
        self.assertEqual(result["metrics"]["tp"], 2)
        self.assertEqual(result["metrics"]["recall"], 1.0)

    def test_rejects_reference_root_missing_from_package(self):
        reference, adjudication, run = _inputs()
        reference["references"][0]["root_ref_id"] = "REF-MISSING-001"
        with self.assertRaisesRegex(EvaluationError, "root ID does not exist"):
            score(reference, adjudication, run)

    def test_rejects_reference_root_cycle(self):
        reference, adjudication, run = _inputs()
        reference["references"][0]["root_ref_id"] = "REF-SYN-002"
        reference["references"][1]["root_ref_id"] = "REF-SYN-001"
        with self.assertRaisesRegex(EvaluationError, "root chain contains a cycle"):
            score(reference, adjudication, run)

    def test_loader_wraps_deep_json_recursion_as_evaluation_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "deep.json"
            path.write_text('{"x":' + "[" * 50000 + "]" * 50000 + "}", encoding="utf-8")
            with self.assertRaisesRegex(EvaluationError, "cannot read valid JSON"):
                _load(path)

    def test_zero_denominators_are_null_and_explicit(self):
        reference, adjudication, run = _inputs()
        adjudication["findings"] = []
        adjudication["missed_references"] = []
        for item in reference["references"]:
            item["adjudication_status"] = "UNRESOLVED"
        result = score(reference, adjudication, run)
        self.assertIsNone(result["metrics"]["precision"])
        self.assertIsNone(result["metrics"]["recall"])
        self.assertEqual(result["metrics"]["unscored"], len(reference["references"]))
        self.assertTrue(any("precision: N/A" in note for note in result["metrics"]["denominator_notes"]))
        self.assertTrue(any("recall: N/A" in note for note in result["metrics"]["denominator_notes"]))


if __name__ == "__main__":
    unittest.main()
