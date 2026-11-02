"""Regression: concurrent pytest processes must not share the test database root."""
import json
import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND = TESTS_DIR.parent
_PROBE = (
    "import json, conftest; "
    "print(json.dumps({'data': str(conftest.TEST_DATA), 'db': conftest.os.environ['DATABASE_URL']}))"
)


def _spawn_probe() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _PROBE],
        cwd=TESTS_DIR,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_pytest_processes_get_distinct_temporary_roots():
    first = _spawn_probe()
    second = _spawn_probe()
    first_out, first_err = first.communicate(timeout=180)
    second_out, second_err = second.communicate(timeout=180)
    assert first.returncode == 0, first_err
    assert second.returncode == 0, second_err
    left = json.loads(first_out.strip().splitlines()[-1])
    right = json.loads(second_out.strip().splitlines()[-1])

    assert left["data"] != right["data"]
    assert left["db"] == f"sqlite:///{left['data']}/test.db"
    assert right["db"] == f"sqlite:///{right['data']}/test.db"
    assert Path(left["data"]).name.startswith("boq-audit-tests-")
    assert not Path(left["data"]).exists()
    assert not Path(right["data"]).exists()
