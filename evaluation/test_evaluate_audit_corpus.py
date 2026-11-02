import json
from contextlib import redirect_stdout
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import evaluation.corpus_manifest as corpus_manifest
from evaluation.corpus_manifest import CorpusManifestError
from scripts.evaluate_audit_corpus import evaluate_manifest, main


ROOT = Path(__file__).resolve().parent


class CorpusEvaluationTests(unittest.TestCase):
    def _fixture(self, root: Path, partition: str = "DEVELOPMENT") -> Path:
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
                "partition": partition,
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

    def test_scores_manifest_cases_and_keeps_partition_outputs_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._fixture(Path(temp))
            result = evaluate_manifest(path)
            development = result["partitions"]["DEVELOPMENT"]
            holdout = result["partitions"]["HOLDOUT"]
            self.assertEqual(development["aggregate"]["case_count"], 1)
            self.assertEqual(development["aggregate"]["tp"], 1)
            self.assertEqual(development["aggregate"]["precision"], 0.5)
            self.assertEqual(development["cases"][0]["case_id"], "CASE-SYNTHETIC-001")
            self.assertEqual(result["manifest_revision"], 1)
            self.assertEqual(result["curator_id"], "REVIEWER-SYN-001")
            self.assertEqual(result["manifest_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(
                development["cases"][0]["reference_sha256"],
                hashlib.sha256((Path(temp) / "reference_confirmed.synthetic.json").read_bytes()).hexdigest(),
            )
            self.assertEqual(holdout["aggregate"]["case_count"], 0)
            self.assertIsNone(holdout["aggregate"]["precision"])

    def test_requires_both_partitions_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(CorpusManifestError, "both development and holdout"):
                evaluate_manifest(self._fixture(Path(temp)), require_holdout=True)

    def test_runtime_read_grant_is_checked_before_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._fixture(root, partition="HOLDOUT")
            with self.assertRaisesRegex(CorpusManifestError, "cannot be granted"):
                evaluate_manifest(manifest, [root / "reference_confirmed.synthetic.json"])

    def test_validated_package_files_are_read_once_before_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._fixture(Path(temp))
            original_read_document = corpus_manifest._read_json_document
            with patch.object(corpus_manifest, "_read_json_document", wraps=original_read_document) as read_document:
                evaluate_manifest(path)
            filenames = [call.args[0].name for call in read_document.call_args_list]
            for name in ("reference_confirmed.synthetic.json", "adjudication.synthetic.json", "run_record_completed.synthetic.json"):
                self.assertEqual(filenames.count(name), 1)

    def test_cli_emits_json_for_a_valid_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._fixture(Path(temp))
            output = io.StringIO()
            with patch.object(sys, "argv", ["evaluate_audit_corpus", "--manifest", str(path)]), redirect_stdout(output):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.getvalue())["corpus_id"], "CORPUS-SYN-001")


if __name__ == "__main__":
    unittest.main()
