"""Process-level checks for the single-worker deployment contract.

The deploy contract (docs/ci-cd-gce.md, docker-compose.yml) is one worker per shared
DATA_DIR. These tests spawn real worker processes with an isolated temp database/data
directory and only signal PIDs they created themselves.
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
import app.models.entities  # noqa: F401 - register every table on Base.metadata
from app.models.entities import AuditRun, Job, JobStatus, User, UserRole

BACKEND = Path(__file__).resolve().parents[1]


def _env(tmp_path: Path, database_url: str, fake_codex: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": database_url,
        "DATA_DIR": str(tmp_path / "data"),
        "AUDIT_CHECKPOINT_WORKER_ENABLED": "false",
        "CODEX_COMMAND": f"{sys.executable} {fake_codex}",
        "PYTHONPATH": str(BACKEND),
        "PYTHONUNBUFFERED": "1",
    }


def _spawn(env: dict[str, str], tmp_path: Path, stderr_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=stderr_path.open("ab"),
    )


def _wait_for(predicate, *, timeout=30.0, message: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {message}")


def _read_heartbeat(data_dir: Path) -> dict:
    return json.loads((data_dir / "audit-worker-heartbeat.json").read_text(encoding="utf-8"))


def _run_status(sessions, run_id: int) -> str | None:
    try:
        with sessions() as db:
            run = db.get(AuditRun, run_id)
            return run.status if run is not None else None
    except SQLAlchemyError:
        return None


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="requires POSIX process signals")
def test_worker_lock_blocks_duplicate_process_and_releases_cleanly(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'worker-lock.db'}"
    engine = create_engine(database_url, connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    engine.dispose()
    data_dir = tmp_path / "data"
    lock_path = data_dir / "audit-worker.lock"
    heartbeat_path = data_dir / "audit-worker-heartbeat.json"
    marker = tmp_path / "codex-invoked.marker"
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('invoked', encoding='utf-8')\n",
        encoding="utf-8",
    )
    env = _env(tmp_path, database_url, fake_codex)
    stderr_one = tmp_path / "worker-one.stderr.log"
    stderr_duplicate = tmp_path / "worker-duplicate.stderr.log"
    stderr_after = tmp_path / "worker-after.stderr.log"
    first = duplicate = after = None
    recovery_engine = None

    def health(env: dict[str, str]) -> int:
        return subprocess.run(
            [sys.executable, "-m", "app.worker", "--health"],
            cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        ).returncode

    def lock_is_free() -> bool:
        with lock_path.open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(handle, fcntl.LOCK_UN)
            return True

    try:
        first = _spawn(env, tmp_path, stderr_one)
        heartbeat = _wait_for(
            lambda: heartbeat_path.exists() and _read_heartbeat(data_dir),
            message="first worker heartbeat",
        )
        assert heartbeat["pid"] == first.pid
        owner_instance = lock_path.read_text(encoding="utf-8").strip()
        assert owner_instance == heartbeat["instance"]
        assert health(env) == 0

        with create_engine(database_url, connect_args={"timeout": 30}).connect() as connection:
            for table in ("jobs", "audit_runs", "audit_run_checkpoints", "audit_run_batch_results"):
                assert connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0

        recovery_engine = create_engine(database_url, connect_args={"timeout": 30})
        sessions = sessionmaker(bind=recovery_engine, expire_on_commit=False)
        source = tmp_path / "source.pdf"
        source.write_bytes(b"synthetic source")
        with sessions() as db:
            user = User(name="Synthetic", email="synthetic@example.test", password_hash="synthetic", role=UserRole.CUSTOMER)
            db.add(user)
            db.flush()
            job = Job(
                user_id=user.id, job_code="BOQ-RACE-001", project_name="Synthetic race",
                original_filename="source.pdf", input_file_path=str(source), status=JobStatus.WAITING_FOR_INFO,
            )
            db.add(job)
            db.flush()
            run = AuditRun(job_id=job.id, attempt=1, status="RUNNING")
            db.add(run)
            db.commit()
            job_id, run_id = job.id, run.id

        duplicate = _spawn(env, tmp_path, stderr_duplicate)
        duplicate.wait(timeout=20)

        assert duplicate.returncode == 1
        assert "audit worker lock already held" in stderr_duplicate.read_text(encoding="utf-8")
        assert lock_path.read_text(encoding="utf-8").strip() == owner_instance
        duplicate_heartbeat = _read_heartbeat(data_dir)
        assert duplicate_heartbeat["instance"] == heartbeat["instance"]
        assert duplicate_heartbeat["pid"] == first.pid
        assert health(env) == 0
        assert first.poll() is None
        with sessions() as db:
            assert db.get(Job, job_id).status == JobStatus.WAITING_FOR_INFO
            assert db.get(AuditRun, run_id).status == "RUNNING"
        assert not marker.exists()

        first.send_signal(signal.SIGTERM)
        assert first.wait(timeout=30) in {0, -signal.SIGTERM}
        _wait_for(lock_is_free, message="lock release after worker shutdown")
        assert health(env) == 1

        after = _spawn(env, tmp_path, stderr_after)
        next_heartbeat = _wait_for(
            lambda: heartbeat_path.exists() and _read_heartbeat(data_dir)["pid"] == after.pid and _read_heartbeat(data_dir),
            message="replacement worker heartbeat",
        )
        assert next_heartbeat["instance"] != owner_instance
        assert health(env) == 0
        _wait_for(
            lambda: _run_status(sessions, run_id) == "INTERRUPTED",
            message="replacement worker startup recovery",
        )
        with sessions() as db:
            assert db.get(Job, job_id).status == JobStatus.WAITING_FOR_INFO
            assert db.get(AuditRun, run_id).status == "INTERRUPTED"
            assert db.scalar(text("SELECT COUNT(*) FROM audit_runs")) == 1
            assert db.scalar(text("SELECT COUNT(*) FROM audit_run_checkpoints")) == 0
            assert db.scalar(text("SELECT COUNT(*) FROM audit_run_batch_results")) == 0
        assert not marker.exists()

        after.send_signal(signal.SIGTERM)
        assert after.wait(timeout=30) in {0, -signal.SIGTERM}
    finally:
        for process in (first, duplicate, after):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=15)
        if recovery_engine is not None:
            recovery_engine.dispose()
