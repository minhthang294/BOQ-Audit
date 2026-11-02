"""Check or migrate persistent BOQ/Codex storage ownership to UID/GID 10001."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import stat
import sys

OWNER = 10001
GROUP = 10001
ROOTS = (Path("/data"), Path("/codex-home"))
EXCLUDED_PATHS = (Path("/data/backups"), Path("/codex-home/tmp"))
DATABASE = Path("/data/database/boq.db")


def entries() -> list[Path]:
    found: list[Path] = []
    for root in ROOTS:
        try:
            root_info = root.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError(f"Required storage directory is missing: {root}") from exc
        if not stat.S_ISDIR(root_info.st_mode):
            raise RuntimeError(f"Required storage directory is missing: {root}")
        found.append(root)
        for directory, names, files in os.walk(root, followlinks=False, onerror=_raise_walk_error):
            base = Path(directory)
            names[:] = [name for name in names if base / name not in EXCLUDED_PATHS]
            for name in names + files:
                path = base / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise RuntimeError(f"Refusing symlink in persistent storage: {path}")
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise RuntimeError(f"Refusing hard-linked file in persistent storage: {path}")
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise RuntimeError(f"Refusing special file in persistent storage: {path}")
                found.append(path)
    return found


def _raise_walk_error(error: OSError) -> None:
    raise error


def assert_idle() -> None:
    database = DATABASE
    if not database.exists():
        return
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "jobs" in tables:
            active = connection.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('SUBMITTED', 'PROCESSING')"
            ).fetchone()[0]
            if active:
                raise RuntimeError(f"Refusing ownership migration: {active} queued/running job(s)")
        if "audit_runs" in tables:
            active = connection.execute("SELECT count(*) FROM audit_runs WHERE status='RUNNING'").fetchone()[0]
            if active:
                raise RuntimeError(f"Refusing ownership migration: {active} running audit(s)")
    finally:
        connection.close()


def run(apply: bool, check_idle: bool) -> int:
    paths = entries()
    if check_idle:
        assert_idle()
    mismatches = [path for path in paths if (path.lstat().st_uid, path.lstat().st_gid) != (OWNER, GROUP)]
    if apply:
        for path in mismatches:
            os.chown(path, OWNER, GROUP, follow_symlinks=False)
        paths = entries()
        mismatches = [path for path in paths if (path.lstat().st_uid, path.lstat().st_gid) != (OWNER, GROUP)]
    if mismatches:
        print(f"Ownership mismatch: {len(mismatches)} path(s); expected {OWNER}:{GROUP}.", file=sys.stderr)
        return 1
    print(f"Storage ownership OK: {len(paths)} path(s) owned by {OWNER}:{GROUP}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify ownership without changing files")
    mode.add_argument("--apply", action="store_true", help="change ownership to 10001:10001")
    parser.add_argument("--assert-idle", action="store_true", help="also reject active DB jobs/audits")
    args = parser.parse_args()
    try:
        return run(args.apply, args.assert_idle)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"Storage owner preflight failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
