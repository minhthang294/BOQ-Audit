import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.audit_sandbox import codex_exec_environment, codex_permission_args, safe_output_files


def _config(args):
    overrides = [args[index + 1] for index, value in enumerate(args) if value == "-c"]
    return tomllib.loads("\n".join(overrides))


class AuditSandboxTests(unittest.TestCase):
    def test_job_profile_denies_filesystem_by_default_and_allows_only_sources_skill_and_workspace(self):
        with TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            workspace = tmp_path / "job" / "batch"
            workspace.mkdir(parents=True)
            source = tmp_path / "source.pdf"
            source.write_bytes(b"synthetic")
            skill_root = tmp_path / "skill"
            skill_root.mkdir()
            skill = skill_root / "SKILL.md"
            skill.write_text("synthetic skill", encoding="utf-8")
            runtime = tmp_path / "codex-runtime"
            runtime.write_bytes(b"synthetic runtime")

            config = _config(codex_permission_args(workspace, skill, [str(source)], [str(runtime)]))
            profile = config["permissions"]["boq_audit"]
            filesystem = profile["filesystem"]
            self.assertEqual(config["default_permissions"], "boq_audit")
            self.assertEqual(config["approval_policy"], "never")
            self.assertEqual(filesystem[":root"], "deny")
            self.assertEqual(filesystem[":minimal"], "read")
            self.assertEqual(filesystem[":slash_tmp"], "deny")
            self.assertEqual(filesystem[str(workspace)], "write")
            self.assertEqual(filesystem[str(source)], "read")
            self.assertEqual(filesystem[str(runtime)], "read")
            self.assertEqual(filesystem[str(skill_root)], "read")
            self.assertFalse(profile["network"]["enabled"])
            self.assertEqual(config["web_search"], "disabled")

    def test_job_profile_rejects_symlink_source_and_workspace_skill_overlap(self):
        with TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            workspace = tmp_path / "job"
            workspace.mkdir()
            source = tmp_path / "source.pdf"
            source.write_bytes(b"synthetic")
            alias = tmp_path / "alias.pdf"
            alias.symlink_to(source)
            hardlink = tmp_path / "hardlink.pdf"
            hardlink.hardlink_to(source)
            skill_root = tmp_path / "skill"
            skill_root.mkdir()
            skill = skill_root / "SKILL.md"
            skill.write_text("synthetic skill", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "singly-linked"):
                codex_permission_args(workspace, skill, [str(alias)])
            with self.assertRaisesRegex(RuntimeError, "singly-linked"):
                codex_permission_args(workspace, skill, [str(hardlink)])
            (workspace / "SKILL.md").write_text("synthetic skill", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "overlaps"):
                codex_permission_args(workspace, workspace / "SKILL.md", [str(source)])

    def test_controller_context_is_granted_as_one_read_only_file(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "job"
            workspace.mkdir()
            source = root / "source.pdf"
            source.write_bytes(b"synthetic")
            context = workspace / "work" / "batch_context.json"
            context.parent.mkdir()
            context.write_text("{}", encoding="utf-8")
            skill_root = root / "skill"
            skill_root.mkdir()
            skill = skill_root / "SKILL.md"
            skill.write_text("synthetic skill", encoding="utf-8")

            config = _config(codex_permission_args(workspace, skill, [str(source)], [str(context)]))
            filesystem = config["permissions"]["boq_audit"]["filesystem"]
            self.assertEqual(filesystem[str(context)], "read")
            self.assertNotIn(str(context.parent), filesystem)
            with self.assertRaisesRegex(RuntimeError, "singly-linked"):
                link = root / "controller-hardlink.json"
                link.hardlink_to(context)
                codex_permission_args(workspace, skill, [str(source)], [str(link)])

    def test_model_process_environment_drops_application_credentials(self):
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / ".tmp").mkdir()
            environment = codex_exec_environment(workspace, {
                "PATH": "/usr/bin",
                "HOME": "/root",
                "CODEX_HOME": "/root/.codex",
                "DATABASE_URL": "sqlite:////data/database/boq.db",
                "BACKEND_SECRET_KEY": "synthetic-secret",
                "OPENAI_API_KEY": "synthetic-api-key",
            })
            self.assertEqual(environment["PATH"], "/usr/bin")
            self.assertEqual(environment["TMPDIR"], str(workspace / ".tmp"))
            self.assertNotIn("DATABASE_URL", environment)
            self.assertNotIn("BACKEND_SECRET_KEY", environment)
            self.assertNotIn("OPENAI_API_KEY", environment)

    def test_output_scan_rejects_symlink_and_hardlink_escape_candidates(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            good = output / "BOQ_Audit_Report.xlsx"
            good.write_bytes(b"synthetic report")
            outside = root / "outside.pdf"
            outside.write_bytes(b"synthetic outside")
            (output / "linked.pdf").symlink_to(outside)
            (output / "hardlinked.pdf").hardlink_to(outside)

            self.assertEqual(safe_output_files(output), [good])


if __name__ == "__main__":
    unittest.main()
