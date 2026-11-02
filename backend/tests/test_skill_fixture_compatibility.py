"""Regression: bundled v6.4 calculation-ledger fixtures match the controller contract."""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.audit_batch import BatchOutputError
from app.services.audit_batch_ledgers import validate_batch_ledger_records

REPO = Path(__file__).resolve().parents[2]
SKILL_TESTS = REPO / ".orchestration" / "skill" / "tests"
FIXTURE_FILES = ("test_scripts.py", "test_workflow.py")


def _fixture_input_literals() -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    for name in FIXTURE_FILES:
        tree = ast.parse((SKILL_TESTS / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "inputs":
                    found.append((name, ast.literal_eval(value)))
    return found


def _inventory() -> dict:
    return {
        "documents": [{"document_id": "pdf", "revision_digest": "sha256:r1"}],
        "pages": [{"page_id": "p1", "document_id": "pdf", "revision_digest": "sha256:r1", "pdf_page": 1}],
        "sheets": [],
        "targets": [],
        "expected_work_items": [{
            "check_id": "check-p1", "document_id": "pdf", "revision_digest": "sha256:r1",
            "page_ids": ["p1"], "target_ids": [], "check_kind": "drawing_review",
            "pass_no": 1, "dependencies": [],
        }],
    }


def _batch(inputs: object) -> list[dict]:
    refs = {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []}
    return [
        {
            "record_type": "calculation_ledger", "record_id": "CALC-1", "check_id": "check-p1",
            "source_refs": refs,
            "payload": {
                "calc_id": "CALC-1", "formula": "1 * 2", "inputs": inputs,
                "source_anchors": ["page p1"], "raw_result": 2, "unit": "cái",
                "pass1_result": 2, "status": "VERIFIED", "calculation_class": "INDEPENDENT_TAKEOFF",
            },
        },
        {
            "record_type": "run_metadata", "record_id": "META-check-p1", "check_id": "check-p1",
            "source_refs": refs,
            "payload": {
                "metadata_id": "META-check-p1", "stage": "pass1",
                "references_read": [], "memory_cases_consulted": [], "completed_checks": [],
            },
        },
    ]


def _validate(records: list[dict]) -> None:
    validate_batch_ledger_records(
        {"records": records}, inventory=_inventory(),
        assigned_check_ids=["check-p1"], pass_no=1, allowed_source_files={"drawing.pdf"},
    )


def test_skill_calculation_inputs_are_nonempty_object_arrays():
    fixtures = _fixture_input_literals()
    assert {name for name, _ in fixtures} == set(FIXTURE_FILES)
    for name, inputs in fixtures:
        assert isinstance(inputs, list) and inputs, name
        assert all(isinstance(item, dict) for item in inputs), name
        _validate(_batch(inputs))


def test_controller_still_rejects_legacy_input_shapes():
    for inputs in ({"n": 1}, [1], []):
        with pytest.raises(BatchOutputError):
            _validate(_batch(inputs))


def test_pinned_skill_validator_accepts_corrected_fixture():
    if not SKILL_TESTS.is_dir():
        pytest.skip("pinned skill tests are not extracted in this workspace")
    result = subprocess.run(
        [
            sys.executable, "-m", "unittest", "-v",
            "test_workflow.WorkflowTests.test_calculation_inputs_follow_schema_object_array",
        ],
        cwd=SKILL_TESTS, capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
