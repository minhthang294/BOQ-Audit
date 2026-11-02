from __future__ import annotations

import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import storage_owner


class StorageOwnerTests(unittest.TestCase):
    def test_storage_scan_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, codex = root / "data", root / "codex"
            data.mkdir()
            codex.mkdir()
            (data / "linked").symlink_to(codex, target_is_directory=True)
            with patch.object(storage_owner, "ROOTS", (data, codex)):
                with self.assertRaisesRegex(RuntimeError, "symlink"):
                    storage_owner.entries()

    def test_storage_scan_rejects_symlink_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / "actual"
            actual.mkdir()
            linked = root / "linked"
            linked.symlink_to(actual, target_is_directory=True)
            with patch.object(storage_owner, "ROOTS", (linked, actual)):
                with self.assertRaisesRegex(RuntimeError, "missing"):
                    storage_owner.entries()

    def test_storage_scan_fails_if_walk_cannot_enumerate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def failed_walk(path, *, followlinks, onerror):
                onerror(PermissionError("synthetic traversal denial"))
                return iter(())

            with patch.object(storage_owner, "ROOTS", (root, root)), patch.object(storage_owner.os, "walk", failed_walk):
                with self.assertRaisesRegex(PermissionError, "traversal denial"):
                    storage_owner.entries()

    def test_storage_scan_rejects_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, codex = root / "data", root / "codex"
            data.mkdir()
            codex.mkdir()
            original = data / "original"
            original.write_text("x", encoding="utf-8")
            os_link = codex / "linked"
            os_link.hardlink_to(original)
            with patch.object(storage_owner, "ROOTS", (data, codex)):
                with self.assertRaisesRegex(RuntimeError, "hard-linked"):
                    storage_owner.entries()

    def test_storage_scan_leaves_private_backup_directory_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data, codex = Path(temporary) / "data", Path(temporary) / "codex"
            backup = data / "backups"
            backup.mkdir(parents=True)
            codex.mkdir()
            (backup / "private.tar.gz").write_text("backup", encoding="utf-8")
            with patch.object(storage_owner, "ROOTS", (data, codex)), patch.object(storage_owner, "EXCLUDED_PATHS", (backup,)):
                paths = storage_owner.entries()
            self.assertNotIn(backup, paths)
            self.assertNotIn(backup / "private.tar.gz", paths)

    def test_idle_check_rejects_active_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "boq.db"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE jobs (status TEXT)")
                connection.execute("INSERT INTO jobs VALUES ('PROCESSING')")
            with patch.object(storage_owner, "DATABASE", database):
                with self.assertRaisesRegex(RuntimeError, "queued/running"):
                    storage_owner.assert_idle()


if __name__ == "__main__":
    unittest.main()
