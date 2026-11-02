import json
import os
import stat
import tempfile
import tomllib
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from app.services import audit_runner


class AuditRunnerBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.jobs = self.root / "jobs"
        self.skill_root = self.root / "skill"
        self.skill_root.mkdir()
        self.skill = self.skill_root / "SKILL.md"
        self.skill.write_text("synthetic skill", encoding="utf-8")
        self.source = self.root / "drawing.pdf"
        self.source.write_bytes(b"synthetic source")
        self.command = self.root / "fake-codex"
        self.command.write_text("""#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
Path.cwd().joinpath('codex-invocation.json').write_text(json.dumps({
    'argv': sys.argv[1:],
    'database_url': os.environ.get('DATABASE_URL'),
    'backend_secret_key': os.environ.get('BACKEND_SECRET_KEY'),
    'openai_api_key': os.environ.get('OPENAI_API_KEY'),
}))
prompt = sys.argv[-1]
patch_path = re.search(r"^PATCH_PATH=(.+)$", prompt, re.M).group(1)
evidence_path = re.search(r"^EVIDENCE_PATH=(.+)$", prompt, re.M).group(1)
ledger_records_path = re.search(r"^LEDGER_RECORDS_PATH=(.+)$", prompt, re.M).group(1)
patch = {"items": [{"check_id": "check-1", "result_status": "CHECKED", "evidence_ids": ["ev-1"]}]}
evidence = {"evidence": [{"evidence_id": "ev-1", "check_id": "check-1", "source_file": "drawing.pdf", "location": "PDF p.1 detail D1", "observation": "dimension shown", "method": "read source"}]}
open(patch_path, "w").write(json.dumps(patch))
open(evidence_path, "w").write(json.dumps(evidence))
open(ledger_records_path, "w").write(json.dumps({"records": []}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "batch done"}}))
""", encoding="utf-8")
        self.command.chmod(0o755)
        self.old_get_settings = audit_runner.get_settings
        audit_runner.get_settings = lambda: SimpleNamespace(
            jobs_dir=self.jobs,
            boq_audit_skill_path=self.skill,
            codex_command=str(self.command),
            codex_timeout_seconds=10,
        )

    def tearDown(self):
        audit_runner.get_settings = self.old_get_settings
        self.temporary.cleanup()

    def test_fresh_batch_process_reads_staged_inputs_and_returns_validated_patch(self):
        with patch.dict(os.environ, {
            "DATABASE_URL": "sqlite:////private/database.db",
            "BACKEND_SECRET_KEY": "synthetic-backend-secret",
            "OPENAI_API_KEY": "synthetic-provider-key",
        }):
            result = audit_runner._execute_codex_batch(
                SimpleNamespace(job_code="JOB-1"), 1,
                pass_no=1, batch_ids=["check-1"], source_files={"drawing.pdf": str(self.source)},
                context={"run_id": 9, "pass_no": 1, "check_ids": ["check-1"], "source_files": ["drawing.pdf"]},
                inventory={"expected_work_items": [{"check_id": "check-1"}]},
                ledger={"items": [{"check_id": "check-1", "execution_status": "PENDING"}]},
                stop=Event(), heartbeat=lambda: None,
            )

        returncode, interrupted, message, outputs = result
        self.assertEqual(returncode, 0)
        self.assertFalse(interrupted)
        self.assertEqual(message, "batch done")
        self.assertEqual(outputs[0]["items"][0]["check_id"], "check-1")
        self.assertEqual(outputs[2], {"records": []})
        attempt_root = self.jobs / "JOB-1" / "attempts" / "1"
        workspaces = list((attempt_root / "batches").glob("pass-1-*"))
        self.assertEqual(len(workspaces), 1)
        context_path = workspaces[0] / "work" / "batch_context.json"
        self.assertTrue(context_path.is_file())
        self.assertEqual(stat.S_IMODE(context_path.stat().st_mode), 0o400)
        patch_path = workspaces[0] / "work" / "batch_patch.json"
        self.assertEqual(stat.S_IMODE(patch_path.stat().st_mode), 0o600)

        invocation = json.loads((workspaces[0] / "codex-invocation.json").read_text(encoding="utf-8"))
        args = invocation["argv"]
        config_values = [args[index + 1] for index, value in enumerate(args[:-1]) if value == "-c"]
        config = tomllib.loads("\n".join(config_values))
        filesystem = config["permissions"]["boq_audit"]["filesystem"]
        self.assertEqual(config["default_permissions"], "boq_audit")
        self.assertEqual(config["approval_policy"], "never")
        self.assertEqual(filesystem[":root"], "deny")
        self.assertEqual(filesystem[":slash_tmp"], "deny")
        self.assertFalse(config["permissions"]["boq_audit"]["network"]["enabled"])
        self.assertEqual(filesystem[str(workspaces[0])], "write")
        self.assertEqual(filesystem[str(self.source)], "read")
        self.assertEqual(filesystem[str(self.skill_root)], "read")
        self.assertEqual(filesystem[str(self.command.resolve())], "read")
        for name in ("batch_context.json", "inventory.json", "ledger.json"):
            self.assertEqual(filesystem[str(workspaces[0] / "work" / name)], "read")
        self.assertNotIn(str(workspaces[0] / "work"), filesystem)
        self.assertIsNone(invocation["database_url"])
        self.assertIsNone(invocation["backend_secret_key"])
        self.assertIsNone(invocation["openai_api_key"])

    def test_controller_scope_mismatch_fails_before_spawning_process(self):
        with self.assertRaisesRegex(RuntimeError, "source map"):
            audit_runner._execute_codex_batch(
                SimpleNamespace(job_code="JOB-1"), 1,
                pass_no=1, batch_ids=["check-1"], source_files={"other.pdf": str(self.source)},
                context={"run_id": 9, "pass_no": 1, "check_ids": ["check-1"], "source_files": ["drawing.pdf"]},
                inventory={}, ledger={}, stop=Event(), heartbeat=lambda: None,
            )


if __name__ == "__main__":
    unittest.main()
