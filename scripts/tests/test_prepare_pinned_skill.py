from __future__ import annotations

import tempfile
import unittest
import zipfile
import json
import re
from pathlib import Path

from scripts import prepare_pinned_skill as skill


class PinnedSkillBundleTests(unittest.TestCase):
    def test_all_skill_pins_and_v4_schema_copy_stay_synchronized(self):
        root = skill.ROOT
        manifests = [
            root / "docs/plans/BOQ_AUDIT_SKILL_MANIFEST.json",
            root / "backend/app/boq_audit_skill_manifest.json",
        ]
        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in manifests]
        self.assertTrue(all(payload == payloads[0] for payload in payloads[1:]))

        quality_source = (root / "backend/app/services/audit_quality.py").read_text(encoding="utf-8")
        trusted = re.search(r'TRUSTED_SKILL_DIGEST = "([0-9a-f]{64})"', quality_source)
        self.assertIsNotNone(trusted)
        self.assertEqual(payloads[0]["digest"], trusted.group(1))

        runtime_schema = root / "backend/app/schemas/audit_batch_ledger_contract.v4.json"
        with zipfile.ZipFile(skill.ARCHIVE) as archive:
            bundled_schema = archive.read("schemas/audit_batch_ledger_contract.v4.json")
        self.assertEqual(bundled_schema, runtime_schema.read_bytes())
        self.assertEqual(json.loads(bundled_schema)["contract_version"], 4)

    def test_archive_extracts_exact_pinned_skill(self):
        files = skill.pinned_files()
        skill.verify_archive(files)
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "skill"
            skill.extract(destination, files)
            skill.verify_tree(destination, files)
            skill.extract(destination, files)
            (destination / "SKILL.md").write_text("changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                skill.extract(destination, files)

    def test_verify_dir_detects_a_stale_installed_skill(self):
        files = skill.pinned_files()
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "skill"
            skill.extract(destination, files)
            self.assertEqual(skill.main(["verify-dir", "--destination", str(destination)]), 0)
            (destination / "SKILL.md").write_text("stale overlay")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                skill.main(["verify-dir", "--destination", str(destination)])

    def test_rejects_archive_file_set_and_content_changes(self):
        files = skill.pinned_files()
        with tempfile.TemporaryDirectory() as root:
            archive_path = Path(root) / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("unexpected.txt", "wrong")
            with self.assertRaisesRegex(ValueError, "file set differs"):
                skill.verify_archive(files, archive_path)
            with zipfile.ZipFile(skill.ARCHIVE) as original, zipfile.ZipFile(archive_path, "w") as archive:
                for entry in original.infolist():
                    archive.writestr(entry, b"changed" if entry.filename == "SKILL.md" else original.read(entry))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                skill.verify_archive(files, archive_path)
