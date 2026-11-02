import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from evaluation.corpus_manifest import CorpusManifestError, assert_holdout_not_granted, load_manifest


ROOT = Path(__file__).resolve().parent


class CorpusManifestTests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        for name in ("reference_confirmed.synthetic.json", "adjudication.synthetic.json", "run_record_completed.synthetic.json"):
            (root / name).write_bytes((ROOT / "synthetic" / name).read_bytes())
        manifest = {
            "schema_version": "q10.corpus_manifest/1",
            "corpus_id": "CORPUS-SYN-001",
            "manifest_revision": 1,
            "curator_id": "REVIEWER-SYN-001",
            "curated_at": "2026-10-29T00:00:00Z",
            "change_reason": "synthetic baseline registration",
            "packages": [{
                "case_id": "CASE-SYNTHETIC-001",
                "audit_package_id": "JOB-SYN-001",
                "reference_package_id": "PKG-SYN-REF-001",
                "partition": "DEVELOPMENT",
                "source_family_ids": ["DOCFAMILY-SYNTHETIC-001"],
                "reference_path": "reference_confirmed.synthetic.json",
                "adjudication_path": "adjudication.synthetic.json",
                "run_record_path": "run_record_completed.synthetic.json",
            }],
        }
        path = root / "manifest.json"
        manifest["packages"][0].update({
            "reference_sha256": hashlib.sha256((root / "reference_confirmed.synthetic.json").read_bytes()).hexdigest(),
            "adjudication_sha256": hashlib.sha256((root / "adjudication.synthetic.json").read_bytes()).hexdigest(),
            "run_record_sha256": hashlib.sha256((root / "run_record_completed.synthetic.json").read_bytes()).hexdigest(),
        })
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_validates_manifest_identities_and_requires_holdout_only_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._fixture(Path(temp))
            manifest = load_manifest(path)
            schema = json.loads((ROOT / "schemas" / "corpus_manifest.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
            self.assertEqual(manifest["corpus_id"], "CORPUS-SYN-001")
            with self.assertRaisesRegex(CorpusManifestError, "both development and holdout"):
                load_manifest(path, require_holdout=True)

    def test_rejects_source_family_leakage_across_partitions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            holdout = copy.deepcopy(manifest["packages"][0])
            holdout.update(
                case_id="CASE-SYNTHETIC-002", audit_package_id="JOB-SYN-002",
                reference_package_id="PKG-SYN-REF-002", partition="HOLDOUT",
            )
            manifest["packages"].append(holdout)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CorpusManifestError, "source family appears in both"):
                load_manifest(path)

    def test_rejects_path_escape_and_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            manifest["packages"][0]["reference_path"] = "../outside.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CorpusManifestError, "relative file path"):
                load_manifest(path)

    def test_loader_rejects_fields_outside_manifest_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            manifest["runtime_grant"] = ["labels"]
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CorpusManifestError, "does not match.*schema"):
                load_manifest(path)

    def test_loader_requires_curator_revision_and_change_reason(self):
        for field, value in (("curator_id", None), ("manifest_revision", 0), ("change_reason", ""), ("curated_at", "not-a-date")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path = self._fixture(root)
                manifest = json.loads(path.read_text())
                if value is None:
                    del manifest[field]
                else:
                    manifest[field] = value
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(CorpusManifestError, "manifest does not match"):
                    load_manifest(path)

    def test_loader_validates_all_referenced_package_schemas(self):
        mutations = {
            "reference_confirmed.synthetic.json": ("unreviewed_field", True, "reference"),
            "adjudication.synthetic.json": ("reviewed_at", "not-a-date", "adjudication"),
            "run_record_completed.synthetic.json": ("schema_version", "q10.run_record/999", "run record"),
        }
        for filename, (key, value, label) in mutations.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path = self._fixture(root)
                package_path = root / filename
                package = json.loads(package_path.read_text())
                package[key] = value
                package_path.write_text(json.dumps(package), encoding="utf-8")
                manifest = json.loads(path.read_text())
                digest_key = {
                    "reference_confirmed.synthetic.json": "reference_sha256",
                    "adjudication.synthetic.json": "adjudication_sha256",
                    "run_record_completed.synthetic.json": "run_record_sha256",
                }[filename]
                manifest["packages"][0][digest_key] = hashlib.sha256(package_path.read_bytes()).hexdigest()
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(CorpusManifestError, f"{label} does not match"):
                    load_manifest(path)

    def test_loader_rejects_package_bytes_when_manifest_digest_is_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            reference = root / "reference_confirmed.synthetic.json"
            reference.write_bytes(reference.read_bytes() + b" ")
            with self.assertRaisesRegex(CorpusManifestError, "reference_sha256 does not match"):
                load_manifest(path)

    def test_holdout_labels_cannot_be_in_exact_runtime_read_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            manifest["packages"][0]["partition"] = "HOLDOUT"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            holdout = root / "reference_confirmed.synthetic.json"
            with self.assertRaisesRegex(CorpusManifestError, "cannot be granted"):
                assert_holdout_not_granted(path, [holdout])
            with self.assertRaisesRegex(CorpusManifestError, "cannot be granted"):
                assert_holdout_not_granted(path, [root])
            assert_holdout_not_granted(path, [])

    def test_holdout_hardlink_alias_cannot_be_granted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            manifest["packages"][0]["partition"] = "HOLDOUT"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            alias = root / "runtime-alias.json"
            alias.hardlink_to(root / "reference_confirmed.synthetic.json")
            with self.assertRaisesRegex(CorpusManifestError, "cannot be granted"):
                assert_holdout_not_granted(path, [alias])

    def test_holdout_symlink_inside_granted_directory_cannot_be_granted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            manifest["packages"][0]["partition"] = "HOLDOUT"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "alias.json").symlink_to(root / "reference_confirmed.synthetic.json")
            with self.assertRaisesRegex(CorpusManifestError, "cannot be granted"):
                assert_holdout_not_granted(path, [runtime])

    def test_holdout_guard_rejects_symlinked_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            alias = root / "manifest-alias.json"
            alias.symlink_to(path)
            with self.assertRaisesRegex(CorpusManifestError, "regular file"):
                assert_holdout_not_granted(alias, [])

    def test_missing_manifest_is_a_domain_error(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(CorpusManifestError, "existing regular file"):
                load_manifest(Path(temp) / "missing.json")

    def test_nul_byte_package_path_is_rejected_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            manifest["packages"][0]["reference_path"] = "reference\u0000.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CorpusManifestError, "relative file path"):
                load_manifest(path)

    def test_duplicate_package_listing_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            duplicate = copy.deepcopy(manifest["packages"][0])
            duplicate["case_id"] = "CASE-SYNTHETIC-002"
            manifest["packages"].append(duplicate)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CorpusManifestError, "listed more than once"):
                load_manifest(path)

    def test_run_attempt_identity_cannot_be_listed_twice_with_changed_bytes(self):
        for identity in ("run_record_id", "attempt_id"):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path = self._fixture(root)
                manifest = json.loads(path.read_text())
                adjudication = json.loads((root / "adjudication.synthetic.json").read_text())
                adjudication["findings"][1]["rationale"] += " repeated record check"
                adjudication_path = root / "adjudication-2.json"
                adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")
                run_record = json.loads((root / "run_record_completed.synthetic.json").read_text())
                run_record["app"]["deployment_revision"] += "-repackaged"
                run_record[identity] += "-UNIQUE"
                run_path = root / "run-record-2.json"
                run_path.write_text(json.dumps(run_record), encoding="utf-8")
                repeated = copy.deepcopy(manifest["packages"][0])
                repeated.update(
                    case_id="CASE-SYNTHETIC-002", adjudication_path=adjudication_path.name,
                    run_record_path=run_path.name,
                    adjudication_sha256=hashlib.sha256(adjudication_path.read_bytes()).hexdigest(),
                    run_record_sha256=hashlib.sha256(run_path.read_bytes()).hexdigest(),
                )
                manifest["packages"].append(repeated)
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(CorpusManifestError, "run record or attempt ID is listed more than once"):
                    load_manifest(path)

    def test_reference_package_cannot_span_development_and_holdout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            adjudication = json.loads((root / "adjudication.synthetic.json").read_text())
            adjudication["findings"][1]["rationale"] += " split"
            (root / "adjudication-2.json").write_text(json.dumps(adjudication), encoding="utf-8")
            run_record = json.loads((root / "run_record_completed.synthetic.json").read_text())
            run_record["app"]["deployment_revision"] += "-2"
            (root / "run-record-2.json").write_text(json.dumps(run_record), encoding="utf-8")
            holdout = copy.deepcopy(manifest["packages"][0])
            holdout.update(
                case_id="CASE-SYNTHETIC-002", partition="HOLDOUT",
                source_family_ids=["DOCFAMILY-SYNTHETIC-002"],
                adjudication_path="adjudication-2.json", run_record_path="run-record-2.json",
                adjudication_sha256=hashlib.sha256((root / "adjudication-2.json").read_bytes()).hexdigest(),
                run_record_sha256=hashlib.sha256((root / "run-record-2.json").read_bytes()).hexdigest(),
            )
            manifest["packages"].append(holdout)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CorpusManifestError, "reference package ID appears in both development and holdout"):
                load_manifest(path)

    def test_audit_package_cannot_span_development_and_holdout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            manifest = json.loads(path.read_text())
            holdout = copy.deepcopy(manifest["packages"][0])
            holdout.update(
                case_id="CASE-SYNTHETIC-002", reference_package_id="PKG-SYN-REF-002",
                partition="HOLDOUT", source_family_ids=["DOCFAMILY-SYNTHETIC-002"],
            )
            manifest["packages"].append(holdout)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CorpusManifestError, "audit package ID appears in both development and holdout"):
                load_manifest(path)

    def test_deep_json_package_is_a_domain_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            run_path = root / "run_record_completed.synthetic.json"
            run_path.write_text('{"x":' + "[" * 50000 + "]" * 50000 + "}", encoding="utf-8")
            manifest = json.loads(path.read_text())
            manifest["packages"][0]["run_record_sha256"] = hashlib.sha256(run_path.read_bytes()).hexdigest()
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CorpusManifestError, "cannot read valid JSON"):
                load_manifest(path)

    def test_holdout_guard_wraps_missing_or_non_list_grants(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            with self.assertRaisesRegex(CorpusManifestError, "existing file or directory"):
                assert_holdout_not_granted(path, [root / "missing.json"])
            with self.assertRaisesRegex(CorpusManifestError, "must be a list"):
                assert_holdout_not_granted(path, None)


if __name__ == "__main__":
    unittest.main()
