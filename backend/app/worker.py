"""Single-process audit worker. Run with python -m app.worker."""
import fcntl
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

from app.core.config import get_settings
from app.core.migrations import recover_interrupted_runs
from app.services.audit_runner import claim_job, run_audit_job

logger = logging.getLogger("boq-audit.worker")
STOP = Event()


def _signal(_number, _frame):
    STOP.set()


def _health(heartbeat_path: Path, lock_path: Path) -> int:
    try:
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        instance = lock_path.read_text(encoding="utf-8").strip()
        if heartbeat["instance"] != instance or abs(time.time() - heartbeat["updated"]) > 10:
            return 1
        with lock_path.open("r") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                return 1
        os.kill(heartbeat["pid"], 0)
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        return 1


def main() -> int:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = settings.data_dir / "audit-worker.lock"
    heartbeat_path = settings.data_dir / "audit-worker-heartbeat.json"
    if len(sys.argv) > 1 and sys.argv[1] == "--health":
        return _health(heartbeat_path, lock_path)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.error("audit worker lock already held: %s", lock_path)
            return 1
        instance = uuid4().hex
        lock.seek(0)
        lock.truncate()
        lock.write(instance)
        lock.flush()
        os.fsync(lock.fileno())
        signal.signal(signal.SIGTERM, _signal)
        signal.signal(signal.SIGINT, _signal)

        def heartbeat():
            temporary = heartbeat_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"instance": instance, "pid": os.getpid(), "updated": time.time()}), encoding="utf-8")
            temporary.replace(heartbeat_path)

        heartbeat()
        recover_interrupted_runs()
        while not STOP.is_set():
            heartbeat()
            claim = claim_job()
            if claim:
                run_audit_job(*claim, stop=STOP, heartbeat=heartbeat)
            else:
                STOP.wait(2)
        return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
