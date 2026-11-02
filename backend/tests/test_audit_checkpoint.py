import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.entities import AuditInventoryApproval, AuditRun, AuditRunBatchResult, AuditRunCheckpoint, Job, JobStatus, User
from app.services.audit_quality import ValidationResult, ValidatorReceipt
from app.services.audit_checkpoint import (
    CheckpointConflict,
    CheckpointError,
    create_run_checkpoint,
    build_batch_plan,
    advance_checkpoint_pass,
    finish_checkpoint_batch,
    load_run_checkpoint,
    recover_checkpoint,
    retry_checkpoint_batch,
    start_checkpoint_batch,
)
from app.services.audit_lineage_v4 import calculation_input_id, source_observation_id


@pytest.fixture(autouse=True)
def _allow_test_inventory_reviewer(monkeypatch):
    monkeypatch.setattr(
        "app.services.audit_checkpoint.get_settings",
        lambda: SimpleNamespace(audit_quality_reviewer_email_set={"admin"}),
    )


def _inventory():
    expected = []
    for check_id in ("c1", "c2", "c3"):
        expected.append({
            "check_id": check_id,
            "document_id": "pdf",
            "revision_digest": "sha256:source",
            "page_ids": ["pdf-P00001"],
            "target_ids": [],
            "check_kind": "drawing_review",
            "pass_no": 1,
            "dependencies": [],
        })
    return {
        "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
        "pages": [{"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1}],
        "sheets": [],
        "targets": [],
        "expected_work_items": expected,
    }


def _seed_run(db, inventory=None, source_digest="a" * 64):
    owner = db.scalar(select(User).where(User.email == "owner"))
    job = Job(user_id=owner.id, job_code=f"BOQ-CP-{uuid4().hex[:8]}", project_name="Checkpoint", original_filename="source.pdf", input_file_path="/tmp/source.pdf", status=JobStatus.PROCESSING)
    db.add(job)
    db.flush()
    run = AuditRun(job_id=job.id, attempt=1, status="RUNNING")
    db.add(run)
    approval = AuditInventoryApproval(job_id=job.id, reviewer_user_id=db.scalar(select(User.id).where(User.email == "admin")), source_digest=source_digest, inventory_json=json.dumps(inventory or _inventory()))
    db.add(approval)
    db.flush()
    return run, approval


def _plan():
    return {"1": [["c1"], ["c2"], ["c3"]], "2": [], "3": []}


def _evidence(check_id, evidence_id):
    return {"evidence": [{
        "evidence_id": evidence_id,
        "check_id": check_id,
        "source_file": "drawing.pdf",
        "location": "page 1 / detail D-1",
        "observation": "synthetic observed dimension",
        "method": "read source label",
    }]}


def _ledger_records(checkpoint, check_id):
    if checkpoint["pass_no"] != 1:
        return {"records": []}
    item = next(row for row in checkpoint["inventory"]["expected_work_items"] if row["check_id"] == check_id)
    source_refs = {field: list(item.get(field, [])) for field in ("page_ids", "sheet_ids", "target_ids")}
    return {"records": [{
        "record_type": "run_metadata", "record_id": f"META-{check_id}", "check_id": check_id,
        "source_refs": source_refs,
        "payload": {
            "metadata_id": f"META-{check_id}", "stage": "pass1", "references_read": [],
            "memory_cases_consulted": [], "completed_checks": [],
        },
    }]}


def _create(db, *, source_digest="a" * 64, skill_digest="b" * 64, plan=None):
    run, approval = _seed_run(db, source_digest=source_digest)
    checkpoint = create_run_checkpoint(
        db,
        run_id=run.id,
        approval_id=approval.id,
        current_source_digest=source_digest,
        skill_digest=skill_digest,
        batch_plan=plan if plan is not None else _plan(),
    )
    db.commit()
    return run.id, checkpoint.version


def test_v3_checkpoint_is_history_only_and_cannot_be_resumed():
    with SessionLocal() as db:
        run_id, _ = _create(db)
        row = db.get(AuditRunCheckpoint, run_id)
        row.contract_version = 3
        db.commit()
        with pytest.raises(CheckpointError, match="LEGACY_V3_UNVERIFIED"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)


def test_v3_checkpoint_is_not_imported_into_a_new_v4_attempt():
    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        db.get(AuditRunCheckpoint, first_run.id).contract_version = 3
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        target = create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        assert target.contract_version == 4
        assert target.derived_from_run_id is None
        assert all(item["execution_status"] == "PENDING" for item in load_run_checkpoint(
            db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64,
        )["ledger"])
        assert db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id)) is None


def test_v4_checkpoint_restores_calculation_from_observation_in_an_earlier_batch():
    with SessionLocal() as db:
        run_id, version = _create(db)
        revision = "sha256:source"
        anchor = {"kind": "pdf_bbox", "page_id": "pdf-P00001", "bbox": [1, 2, 10, 20], "rotation": 0}
        observation_id = source_observation_id("pdf", revision, anchor)
        refs = {"page_ids": ["pdf-P00001"], "sheet_ids": [], "target_ids": []}
        observation = {
            "record_type": "source_observations", "record_id": observation_id, "check_id": "c1",
            "source_refs": refs,
            "payload": {
                "observation_id": observation_id, "document_id": "pdf", "revision_digest": revision,
                "observed_value": 12.5, "unit": "m", "anchor": anchor, "evidence_ids": ["ev-c1"],
            },
        }

        version = start_checkpoint_batch(
            db, checkpoint=load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64),
            expected_version=version, batch_ids=["c1"],
        )
        running = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        finish_checkpoint_batch(
            db, checkpoint=running, expected_version=version,
            patch={"items": [{"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-c1"]}]},
            evidence=_evidence("c1", "ev-c1"),
            ledger_records={"records": [observation, *_ledger_records(running, "c1")["records"]]},
            allowed_source_files={"drawing.pdf"},
        )
        db.commit()

        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = start_checkpoint_batch(db, checkpoint=state, expected_version=state["version"], batch_ids=["c2"])
        running = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        calc_id = "CALC-C2"
        calc = {
            "record_type": "calculation_ledger", "record_id": calc_id, "check_id": "c2",
            "source_refs": refs,
            "payload": {
                "calc_id": calc_id, "formula": "12.5", "inputs": [{
                    "input_id": calculation_input_id(calc_id, 0, observation_id),
                    "observation_id": observation_id, "value": 12.5, "unit": "m",
                }],
                "raw_result": 12.5, "unit": "m", "status": "VERIFIED", "source_anchors": ["page 1"],
                "calculation_class": "INDEPENDENT_TAKEOFF", "pass1_result": 12.5, "assumption_ids": [],
            },
        }
        finish_checkpoint_batch(
            db, checkpoint=running, expected_version=version,
            patch={"items": [{"check_id": "c2", "result_status": "CHECKED", "evidence_ids": ["ev-c2"]}]},
            evidence=_evidence("c2", "ev-c2"),
            ledger_records={"records": [calc, *_ledger_records(running, "c2")["records"]]},
            allowed_source_files={"drawing.pdf"},
        )
        db.commit()

        restored = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert [record["record_type"] for record in restored["ledger_records"]].count("source_observations") == 1
        assert [record["record_type"] for record in restored["ledger_records"]].count("calculation_ledger") == 1
        assert restored["ledger_projection"]["calculation_ledger"][0]["inputs"][0]["observation_id"] == observation_id


def _three_pass_inventory():
    inventory = _inventory()
    inventory["expected_work_items"][1]["pass_no"] = 2
    inventory["expected_work_items"][1]["dependencies"] = ["c1"]
    inventory["expected_work_items"][2]["pass_no"] = 3
    inventory["expected_work_items"][2]["dependencies"] = ["c2"]
    return inventory


def _three_pass_checkpoint(db):
    inventory = _three_pass_inventory()
    run, approval = _seed_run(db, inventory=inventory)
    plan = {"1": [["c1"]], "2": [["c2"]], "3": [["c3"]]}
    checkpoint = create_run_checkpoint(db, run_id=run.id, approval_id=approval.id, current_source_digest="a" * 64, skill_digest="b" * 64, batch_plan=plan)
    db.commit()
    return run.id, checkpoint.version


def _completed_pass1_attempt(db):
    run, approval = _seed_run(db)
    plan = _plan()
    create_run_checkpoint(
        db, run_id=run.id, approval_id=approval.id,
        current_source_digest="a" * 64, skill_digest="b" * 64, batch_plan=plan,
    )
    state = load_run_checkpoint(db, run_id=run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
    version = start_checkpoint_batch(db, checkpoint=state, expected_version=state["version"], batch_ids=["c1"])
    running = load_run_checkpoint(db, run_id=run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
    finish_checkpoint_batch(
        db, checkpoint=running, expected_version=version,
        patch={"items": [{"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-c1"]}]},
        evidence=_evidence("c1", "ev-c1"), ledger_records=_ledger_records(running, "c1"),
        allowed_source_files={"drawing.pdf"},
    )
    run.status = "INTERRUPTED"
    db.flush()
    return run, approval, plan


def _passed_gate(tmp_path, *, run_id, pass_no):
    receipt = ValidatorReceipt(
        schema_version=1,
        run_id=run_id,
        snapshot_id=f"snap-{pass_no}",
        stage=f"pass{pass_no}",
        source_digest="a" * 64,
        skill_digest="b" * 64,
        input_digest="c" * 64,
        validator_exit_code=0,
        candidate_release_status="METADATA_STAGE_COMPLETE",
        blocking_checks=(),
        open_record_count=0,
        validated_at=datetime.now(timezone.utc).isoformat(),
        controller_status="PASS",
    )
    receipt_path = tmp_path / f"pass{pass_no}.json"
    receipt_path.write_text(json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return ValidationResult(receipt=receipt, receipt_path=receipt_path)


def _complete_first_pass_item(db, run_id, version):
    state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
    version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c1"])
    db.commit()
    state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
    version = finish_checkpoint_batch(db, checkpoint=state, expected_version=version, patch={"items": [{"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-c1"]}]}, evidence=_evidence("c1", "ev-c1"), ledger_records=_ledger_records(state, "c1"), allowed_source_files={"drawing.pdf"})
    db.commit()
    return version


def test_checkpoint_create_binds_approval_inventory_and_exact_batch_plan():
    with SessionLocal() as db:
        run_id, version = _create(db)
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
    assert version == 1
    assert state["inventory_digest"]
    assert state["lesson_snapshot"]["project_scope"]
    assert state["lesson_snapshot_digest"]
    assert [item["execution_status"] for item in state["ledger"]] == ["PENDING"] * 3
    assert state["status"] == "READY"
    assert "gate" not in state


def test_checkpoint_refuses_legacy_approval_missing_pass1_source_page():
    inventory = _inventory()
    inventory["pages"].append({
        "page_id": "pdf-P00002", "document_id": "pdf",
        "revision_digest": "sha256:source", "pdf_page": 2,
    })
    with SessionLocal() as db:
        run, approval = _seed_run(db, inventory=inventory)
        with pytest.raises(CheckpointError, match="approved inventory is invalid"):
            create_run_checkpoint(
                db, run_id=run.id, approval_id=approval.id,
                current_source_digest="a" * 64, skill_digest="b" * 64,
                batch_plan=_plan(),
            )


def test_checkpoint_rejects_tampered_lesson_snapshot():
    with SessionLocal() as db:
        run_id, _ = _create(db)
        row = db.get(AuditRunCheckpoint, run_id)
        row.lesson_snapshot_json = row.lesson_snapshot_json.replace('"project_scope":"', '"project_scope":"tampered-')
        db.commit()
        with pytest.raises(CheckpointError, match="lesson selection snapshot is invalid"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)


def test_batch_plan_is_deterministic_bounded_and_dependency_ordered():
    inventory = _inventory()
    inventory["expected_work_items"].extend([
        {
            "check_id": f"independent-{index}",
            "document_id": "pdf",
            "revision_digest": "sha256:source",
            "page_ids": ["pdf-P00001"],
            "target_ids": [],
            "check_kind": "drawing_review",
            "pass_no": 1,
            "dependencies": [],
        }
        for index in range(5)
    ])
    inventory["expected_work_items"].append({
        "check_id": "dependent",
        "document_id": "pdf",
        "revision_digest": "sha256:source",
        "page_ids": ["pdf-P00001"],
        "target_ids": [],
        "check_kind": "cross_check",
        "pass_no": 2,
        "dependencies": ["c1", "c2", "c3", *[f"independent-{index}" for index in range(5)]],
    })
    batches = build_batch_plan(inventory, max_batch_size=2)
    flattened = [check_id for stage in batches.values() for batch in stage for check_id in batch]
    assert set(flattened) == {item["check_id"] for item in inventory["expected_work_items"]}
    assert len(flattened) == len(set(flattened))
    assert all(len(batch) <= 2 for stage in batches.values() for batch in stage)
    assert batches == build_batch_plan(inventory, max_batch_size=2)
    assert batches["2"] == [["dependent"]]


def test_batch_plan_rejects_cycles_and_invalid_batch_size():
    inventory = _inventory()
    inventory["expected_work_items"][0]["dependencies"] = ["c2"]
    inventory["expected_work_items"][1]["dependencies"] = ["c1"]
    with pytest.raises(CheckpointError, match="unschedulable dependency cycle"):
        build_batch_plan(inventory)
    with pytest.raises(CheckpointError, match="max_batch_size"):
        build_batch_plan(_inventory(), max_batch_size=0)


@pytest.mark.parametrize("source,skill,plan", [
    ("c" * 64, "b" * 64, _plan()),
    ("a" * 64, "b" * 64, {"1": [["c1"], ["c1"]], "2": [], "3": []}),
    ("a" * 64, "b" * 64, {"1": [["c1"]], "2": [], "3": []}),
])
def test_checkpoint_rejects_stale_digests_and_incomplete_or_duplicate_batches(source, skill, plan):
    with SessionLocal() as db:
        run, approval = _seed_run(db)
        with pytest.raises(CheckpointError):
            create_run_checkpoint(db, run_id=run.id, approval_id=approval.id, current_source_digest=source, skill_digest=skill, batch_plan=plan)


def test_checkpoint_rejects_skill_source_drift_and_duplicate_create():
    with SessionLocal() as db:
        run_id, _ = _create(db)
        with pytest.raises(CheckpointError, match="source changed"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="c" * 64, current_skill_digest="b" * 64)
        with pytest.raises(CheckpointError, match="skill changed"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="c" * 64)
        run = db.get(AuditRun, run_id)
        approval = db.scalar(select(AuditInventoryApproval).where(AuditInventoryApproval.job_id == run.job_id))
        with pytest.raises(CheckpointError, match="already exists"):
            create_run_checkpoint(db, run_id=run_id, approval_id=approval.id, current_source_digest="a" * 64, skill_digest="b" * 64, batch_plan=_plan())


def test_checkpoint_load_rejects_latest_approval_source_tampering():
    with SessionLocal() as db:
        run_id, _ = _create(db)
        run = db.get(AuditRun, run_id)
        approval = db.scalar(select(AuditInventoryApproval).where(AuditInventoryApproval.job_id == run.job_id))
        approval.source_digest = "c" * 64
        db.commit()
        with pytest.raises(CheckpointError, match="approval source changed"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)

    with SessionLocal() as db:
        run_id, _ = _create(db)
        run = db.get(AuditRun, run_id)
        approval = db.scalar(select(AuditInventoryApproval).where(AuditInventoryApproval.job_id == run.job_id))
        tampered = _inventory()
        tampered["expected_work_items"].pop()
        approval.inventory_json = json.dumps(tampered)
        db.commit()
        with pytest.raises(CheckpointError, match="approved inventory changed"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)


def test_checkpoint_refuses_resume_after_reviewer_allowlist_revocation(monkeypatch):
    with SessionLocal() as db:
        run_id, _ = _create(db)
        monkeypatch.setattr(
            "app.services.audit_checkpoint.get_settings",
            lambda: SimpleNamespace(audit_quality_reviewer_email_set=set()),
        )
        with pytest.raises(CheckpointError, match="reviewer is not currently allowlisted"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)


def test_checkpoint_requires_latest_approval_and_latest_running_attempt():
    with SessionLocal() as db:
        run, approval = _seed_run(db)
        db.add(AuditInventoryApproval(job_id=run.job_id, reviewer_user_id=approval.reviewer_user_id, source_digest=approval.source_digest, inventory_json=approval.inventory_json))
        db.flush()
        with pytest.raises(CheckpointError, match="latest inventory approval"):
            create_run_checkpoint(db, run_id=run.id, approval_id=approval.id, current_source_digest="a" * 64, skill_digest="b" * 64, batch_plan=_plan())

        latest_approval = db.scalar(select(AuditInventoryApproval).where(AuditInventoryApproval.job_id == run.job_id).order_by(AuditInventoryApproval.id.desc()))
        newer_run = AuditRun(job_id=run.job_id, attempt=2, status="RUNNING")
        db.add(newer_run)
        db.flush()
        with pytest.raises(CheckpointError, match="latest audit attempt"):
            create_run_checkpoint(db, run_id=run.id, approval_id=latest_approval.id, current_source_digest="a" * 64, skill_digest="b" * 64, batch_plan=_plan())


def test_checkpoint_concurrent_create_conflict_is_typed(monkeypatch):
    with SessionLocal() as db:
        run_id, _ = _create(db)
        run = db.get(AuditRun, run_id)
        approval = db.scalar(select(AuditInventoryApproval).where(AuditInventoryApproval.job_id == run.job_id))
        original_get = db.get

        def hide_existing_checkpoint(entity, ident, **kwargs):
            if entity is AuditRunCheckpoint:
                return None
            return original_get(entity, ident, **kwargs)

        monkeypatch.setattr(db, "get", hide_existing_checkpoint)
        with pytest.raises(CheckpointError, match="already exists"):
            create_run_checkpoint(db, run_id=run_id, approval_id=approval.id, current_source_digest="a" * 64, skill_digest="b" * 64, batch_plan=_plan())
        db.rollback()


def test_checkpoint_recovery_preserves_done_batch_and_blocks_only_active_batch():
    with SessionLocal() as db:
        run_id, version = _create(db)
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c1"])
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = finish_checkpoint_batch(db, checkpoint=state, expected_version=version, patch={"items": [{"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-c1"]}]}, evidence=_evidence("c1", "ev-c1"), ledger_records=_ledger_records(state, "c1"), allowed_source_files={"drawing.pdf"})
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c2"])
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = recover_checkpoint(db, checkpoint=state, expected_version=version)
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert version == 5
        assert {item["check_id"]: item["execution_status"] for item in state["ledger"]} == {"c1": "DONE", "c2": "BLOCKED", "c3": "PENDING"}
        assert state["active_batch_ids"] == []
        assert state["status"] == "INTERRUPTED"
        version = retry_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c2"])
        db.commit()
        assert version == 6


def test_checkpoint_persists_batch_evidence_atomically_and_detects_tampering():
    with SessionLocal() as db:
        run_id, version = _create(db)
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c1"])
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = finish_checkpoint_batch(
            db,
            checkpoint=state,
            expected_version=version,
            patch={"items": [{"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-c1"]}]},
            evidence=_evidence("c1", "ev-c1"),
            ledger_records=_ledger_records(state, "c1"),
            allowed_source_files={"drawing.pdf"},
        )
        db.commit()
        row = db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == run_id))
        assert row.run_id == run_id
        assert row.checkpoint_version == version
        assert row.patch_digest
        assert row.evidence_digest
        assert load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)["ledger"][0]["execution_status"] == "DONE"
        row.evidence_json = '{"evidence":[]}'
        db.commit()
        with pytest.raises(CheckpointError, match="batch result digest or scope mismatch"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)


def test_new_attempt_imports_only_unchanged_pass_one_batches_with_provenance():
    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        target = create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        imported = load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert target.derived_from_run_id == first_run.id
        assert imported["derived_from_run_id"] == first_run.id
        assert imported["ledger"][0]["execution_status"] == "DONE"
        assert imported["ledger"][1]["execution_status"] == "PENDING"
        copied = db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id))
        assert copied is not None and copied.derived_from_run_id == first_run.id


def test_cross_attempt_import_is_idempotent():
    from app.services.audit_checkpoint import _import_prior_pass_one

    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        target = create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        result_count = len(list(db.scalars(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id))))
        version = target.version

        _import_prior_pass_one(db, source_run_id=first_run.id, target_run_id=second_run.id)

        imported = load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert len(list(db.scalars(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id)))) == result_count == 1
        assert imported["version"] == version
        assert imported["ledger"][0]["execution_status"] == "DONE"


def test_cross_attempt_batch_provenance_must_match_checkpoint():
    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        copied = db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id))
        assert copied is not None and copied.derived_from_run_id == first_run.id
        copied.derived_from_run_id = second_run.id
        db.flush()

        with pytest.raises(CheckpointError, match="batch import provenance does not match the checkpoint"):
            load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)


def test_new_attempt_does_not_import_when_checker_digest_differs():
    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        db.get(AuditRunCheckpoint, first_run.id).checker_digest = "0" * 64
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        target = create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        current = load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert target.derived_from_run_id is None
        assert all(item["execution_status"] == "PENDING" for item in current["ledger"])
        assert db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id)) is None


def test_failed_cross_attempt_batch_import_rolls_back_partial_checkpoint(monkeypatch):
    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()

        def fail_import(*args, **kwargs):
            raise CheckpointError("simulated interrupted import")

        monkeypatch.setattr("app.services.audit_checkpoint.finish_checkpoint_batch", fail_import)
        create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        current = load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert current["status"] == "READY"
        assert current["derived_from_run_id"] is None
        assert all(item["execution_status"] == "PENDING" for item in current["ledger"])
        assert db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id)) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_digest", "0" * 64),
        ("skill_digest", "0" * 64),
        ("checker_digest", "0" * 64),
        ("inventory_digest", "0" * 64),
        ("batch_plan_digest", "0" * 64),
        ("lesson_snapshot_digest", "0" * 64),
    ],
)
def test_cross_attempt_import_requires_every_checkpoint_binding_to_match(field, value):
    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        source_row = db.get(AuditRunCheckpoint, first_run.id)
        setattr(source_row, field, value)
        db.flush()
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        target = create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        current = load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert target.derived_from_run_id is None
        assert current["derived_from_run_id"] is None
        assert all(item["execution_status"] == "PENDING" for item in current["ledger"])
        assert db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id)) is None


def test_cross_attempt_import_rejects_reapproved_inventory_for_the_same_source():
    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        reapplied = AuditInventoryApproval(
            job_id=first_run.job_id,
            reviewer_user_id=db.scalar(select(User.id).where(User.email == "admin")),
            source_digest="a" * 64,
            inventory_json=approval.inventory_json,
        )
        db.add(reapplied)
        db.flush()
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        target = create_run_checkpoint(
            db, run_id=second_run.id, approval_id=reapplied.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        current = load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert target.derived_from_run_id is None
        assert current["approval_id"] == reapplied.id
        assert all(item["execution_status"] == "PENDING" for item in current["ledger"])
        assert db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id)) is None


def test_cross_attempt_import_skips_a_source_attempt_that_never_stopped_cleanly():
    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        first_run.status = "RUNNING"
        db.flush()
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        target = create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        current = load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert target.derived_from_run_id is None
        assert all(item["execution_status"] == "PENDING" for item in current["ledger"])
        assert db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id)) is None


def test_cross_attempt_import_requires_an_older_source_attempt():
    from app.services.audit_checkpoint import _import_prior_pass_one

    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        first_before = load_run_checkpoint(db, run_id=first_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)

        _import_prior_pass_one(db, source_run_id=second_run.id, target_run_id=first_run.id)

        first_after = load_run_checkpoint(db, run_id=first_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert first_after["version"] == first_before["version"]
        assert first_after["derived_from_run_id"] is None
        assert len(list(db.scalars(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == first_run.id)))) == 1


def test_cross_attempt_import_rejects_conflicting_target_provenance():
    from app.services.audit_checkpoint import _import_prior_pass_one

    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        target = create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64, batch_plan=plan,
        )
        target.derived_from_run_id = second_run.id
        db.flush()

        _import_prior_pass_one(db, source_run_id=first_run.id, target_run_id=second_run.id)

        assert target.derived_from_run_id == second_run.id
        assert db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id)) is None


def test_legacy_checkpoint_without_checker_digest_is_not_resumed_or_imported():
    with SessionLocal() as db:
        first_run, approval, plan = _completed_pass1_attempt(db)
        db.get(AuditRunCheckpoint, first_run.id).checker_digest = None
        db.flush()
        with pytest.raises(CheckpointError, match="audit checker or dependency configuration changed"):
            load_run_checkpoint(db, run_id=first_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        target = create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=plan, reuse_from_run_id=first_run.id,
        )
        current = load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert target.derived_from_run_id is None
        assert all(item["execution_status"] == "PENDING" for item in current["ledger"])


def _dependency_inventory():
    inventory = _inventory()
    inventory["expected_work_items"] = [
        {
            "check_id": "d1", "document_id": "pdf", "revision_digest": "sha256:source",
            "page_ids": ["pdf-P00001"], "target_ids": [], "check_kind": "drawing_review",
            "pass_no": 1, "dependencies": [],
        },
        {
            "check_id": "d2", "document_id": "pdf", "revision_digest": "sha256:source",
            "page_ids": ["pdf-P00001"], "target_ids": [], "check_kind": "drawing_review",
            "pass_no": 1, "dependencies": ["d1"],
        },
        {
            "check_id": "ind", "document_id": "pdf", "revision_digest": "sha256:source",
            "page_ids": ["pdf-P00001"], "target_ids": [], "check_kind": "drawing_review",
            "pass_no": 1, "dependencies": [],
        },
    ]
    return inventory


_DEPENDENCY_PLAN = {"1": [["d1"], ["d2"], ["ind"]], "2": [], "3": []}


def _complete_source_item(db, run_id, check_id):
    state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
    version = start_checkpoint_batch(db, checkpoint=state, expected_version=state["version"], batch_ids=[check_id])
    running = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
    finish_checkpoint_batch(
        db, checkpoint=running, expected_version=version,
        patch={"items": [{"check_id": check_id, "result_status": "CHECKED", "evidence_ids": [f"ev-{check_id}"]}]},
        evidence=_evidence(check_id, f"ev-{check_id}"),
        ledger_records=_ledger_records(running, check_id),
        allowed_source_files={"drawing.pdf"},
    )
    db.flush()


def _completed_dependency_attempt(db):
    run, approval = _seed_run(db, inventory=_dependency_inventory())
    create_run_checkpoint(
        db, run_id=run.id, approval_id=approval.id,
        current_source_digest="a" * 64, skill_digest="b" * 64, batch_plan=_DEPENDENCY_PLAN,
    )
    for check_id in ("d1", "d2", "ind"):
        _complete_source_item(db, run.id, check_id)
    run.status = "INTERRUPTED"
    db.flush()
    return run, approval


def test_cross_attempt_import_follows_dependency_edges():
    with SessionLocal() as db:
        first_run, approval = _completed_dependency_attempt(db)
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64,
            batch_plan=_DEPENDENCY_PLAN, reuse_from_run_id=first_run.id,
        )
        imported = load_run_checkpoint(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        statuses = {item["check_id"]: item["execution_status"] for item in imported["ledger"]}
        assert statuses == {"d1": "DONE", "d2": "DONE", "ind": "DONE"}
        assert imported["derived_from_run_id"] == first_run.id
        assert len(list(db.scalars(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run.id)))) == 3


def test_cross_attempt_import_converges_when_candidates_arrive_dependents_first(monkeypatch):
    from app.services.audit_checkpoint import _import_prior_pass_one
    from app.services import audit_checkpoint

    with SessionLocal() as db:
        first_run, approval = _completed_dependency_attempt(db)
        second_run = AuditRun(job_id=first_run.job_id, attempt=2, status="RUNNING")
        db.add(second_run)
        db.flush()
        create_run_checkpoint(
            db, run_id=second_run.id, approval_id=approval.id,
            current_source_digest="a" * 64, skill_digest="b" * 64, batch_plan=_DEPENDENCY_PLAN,
        )
        real_load = audit_checkpoint.load_run_checkpoint

        def reversed_source_order(session, *, run_id, current_source_digest, current_skill_digest):
            state = real_load(
                session, run_id=run_id,
                current_source_digest=current_source_digest, current_skill_digest=current_skill_digest,
            )
            if run_id == first_run.id:
                state = {**state, "batch_results": list(reversed(state["batch_results"]))}
            return state

        monkeypatch.setattr(audit_checkpoint, "load_run_checkpoint", reversed_source_order)
        _import_prior_pass_one(db, source_run_id=first_run.id, target_run_id=second_run.id)
        monkeypatch.setattr(audit_checkpoint, "load_run_checkpoint", real_load)

        imported = real_load(db, run_id=second_run.id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        statuses = {item["check_id"]: item["execution_status"] for item in imported["ledger"]}
        assert statuses == {"d1": "DONE", "d2": "DONE", "ind": "DONE"}
        assert imported["derived_from_run_id"] == first_run.id


def test_checkpoint_batch_patch_rejection_leaves_checkpoint_running_without_result():
    with SessionLocal() as db:
        run_id, version = _create(db)
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c1"])
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        with pytest.raises(CheckpointError, match="batch patch or evidence is invalid"):
            finish_checkpoint_batch(
                db,
                checkpoint=state,
                expected_version=version,
                patch={"items": [{"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-c1"]}]},
                evidence={"evidence": []},
                allowed_source_files={"drawing.pdf"},
            )
        db.rollback()
        current = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert current["status"] == "RUNNING"
        assert db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == run_id)) is None


def test_checkpoint_rejects_pass1_batch_without_run_metadata_before_persisting():
    with SessionLocal() as db:
        run_id, version = _create(db)
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c1"])
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        with pytest.raises(CheckpointError, match="batch patch or evidence is invalid"):
            finish_checkpoint_batch(
                db, checkpoint=state, expected_version=version,
                patch={"items": [{"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-c1"]}]},
                evidence=_evidence("c1", "ev-c1"), allowed_source_files={"drawing.pdf"},
            )
        db.rollback()
        current = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert current["status"] == "RUNNING"
        assert db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == run_id)) is None


def test_checkpoint_compare_and_swap_rejects_stale_writer():
    with SessionLocal() as db:
        run_id, version = _create(db)
        first = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        second = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        start_checkpoint_batch(db, checkpoint=first, expected_version=version, batch_ids=["c1"])
        db.commit()
        with pytest.raises(CheckpointConflict):
            start_checkpoint_batch(db, checkpoint=second, expected_version=version, batch_ids=["c1"])
        db.rollback()
        row = db.get(AuditRunCheckpoint, run_id)
        assert row.version == 2


def test_checkpoint_rejects_changed_batch_plan_and_replaced_approval():
    with SessionLocal() as db:
        run_id, _ = _create(db)
        row = db.get(AuditRunCheckpoint, run_id)
        row.batch_plan_json = json.dumps({"1": [["c1", "c2"], ["c3"]], "2": [], "3": []})
        db.commit()
        with pytest.raises(CheckpointError, match="batch plan checkpoint digest"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)

    with SessionLocal() as db:
        run_id, _ = _create(db)
        run = db.get(AuditRun, run_id)
        original = db.scalar(select(AuditInventoryApproval).where(AuditInventoryApproval.job_id == run.job_id))
        db.add(AuditInventoryApproval(job_id=run.job_id, reviewer_user_id=original.reviewer_user_id, source_digest=original.source_digest, inventory_json=original.inventory_json))
        db.commit()
        with pytest.raises(CheckpointError, match="approval changed"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)


def test_checkpoint_reloads_concurrent_version_and_rejects_partial_or_tampered_ledger():
    with SessionLocal() as db:
        run_id, _ = _create(db)
    with SessionLocal() as first, SessionLocal() as second:
        stale = load_run_checkpoint(first, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        current = load_run_checkpoint(second, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        start_checkpoint_batch(second, checkpoint=current, expected_version=current["version"], batch_ids=["c1"])
        second.commit()
        refreshed = load_run_checkpoint(first, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert stale["version"] == 1
        assert refreshed["version"] == 2
        assert refreshed["status"] == "RUNNING"

    with SessionLocal() as db:
        run_id, _ = _create(db)
        row = db.get(AuditRunCheckpoint, run_id)
        ledger = json.loads(row.ledger_json)
        row.ledger_json = json.dumps(ledger[:-1])
        db.commit()
        with pytest.raises(CheckpointError):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)


def test_checkpoint_does_not_accept_pending_result_or_evidence_mutation():
    with SessionLocal() as db:
        run_id, version = _create(db)
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c1"])
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = finish_checkpoint_batch(db, checkpoint=state, expected_version=version, patch={"items": [{"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-1"]}]}, evidence=_evidence("c1", "ev-1"), ledger_records=_ledger_records(state, "c1"), allowed_source_files={"drawing.pdf"})
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        flipped = [dict(item) for item in state["ledger"]]
        flipped[0]["result_status"] = "UNRESOLVED"
        from app.services.audit_checkpoint import _store_state
        with pytest.raises(CheckpointError):
            _store_state(db, checkpoint=state, expected_version=version, ledger=flipped, active_batch_ids=[], status="RUNNING")

        pending = [dict(item) for item in state["ledger"]]
        pending[1]["result_status"] = "CHECKED"
        pending[1]["evidence_ids"] = ["ev-pending"]
        with pytest.raises(CheckpointError):
            _store_state(db, checkpoint=state, expected_version=version, ledger=pending, active_batch_ids=[], status="RUNNING")


def test_checkpoint_status_transition_cannot_leave_running_items_idle():
    with SessionLocal() as db:
        run_id, version = _create(db)
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c1"])
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        from app.services.audit_checkpoint import _store_state
        with pytest.raises(CheckpointError):
            _store_state(db, checkpoint=state, expected_version=version, ledger=state["ledger"], active_batch_ids=["c1"], status="READY")


def test_checkpoint_advances_only_through_matching_pass_receipt(tmp_path):
    with SessionLocal() as db:
        run_id, version = _three_pass_checkpoint(db)
        for pass_no, check_id in ((1, "c1"), (2, "c2"), (3, "c3")):
            state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
            version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=[check_id])
            db.commit()
            state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
            version = finish_checkpoint_batch(db, checkpoint=state, expected_version=version, patch={"items": [{"check_id": check_id, "result_status": "CHECKED", "evidence_ids": [f"ev-{check_id}"]}]}, evidence=_evidence(check_id, f"ev-{check_id}"), ledger_records=_ledger_records(state, check_id), allowed_source_files={"drawing.pdf"})
            db.commit()
            if pass_no < 3:
                state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
                with pytest.raises(CheckpointError):
                    advance_checkpoint_pass(db, checkpoint=state, expected_version=version, next_pass=pass_no + 1, gate_result=_passed_gate(tmp_path, run_id=run_id, pass_no=pass_no + 1))
                version = advance_checkpoint_pass(db, checkpoint=state, expected_version=version, next_pass=pass_no + 1, gate_result=_passed_gate(tmp_path, run_id=run_id, pass_no=pass_no))
                db.commit()
        final = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        assert final["pass_no"] == 3
        assert set(final["gate_reports"]) == {"1", "2"}
        assert final["status"] == "READY"


@pytest.mark.parametrize("changes", [
    {"schema_version": 999},
    {"blocking_checks": ("blocked",)},
    {"open_record_count": 1},
])
def test_checkpoint_rejects_invalid_success_receipt_semantics(tmp_path, changes):
    from dataclasses import replace
    with SessionLocal() as db:
        run_id, version = _three_pass_checkpoint(db)
        version = _complete_first_pass_item(db, run_id, version)
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        gate = _passed_gate(tmp_path, run_id=run_id, pass_no=1)
        gate = replace(gate, receipt=replace(gate.receipt, **changes))
        with pytest.raises(CheckpointError, match="stage gate did not pass"):
            advance_checkpoint_pass(db, checkpoint=state, expected_version=version, next_pass=2, gate_result=gate)


def test_checkpoint_rejects_duplicate_gate_and_aggregate_size_overflow(tmp_path):
    from app.services.audit_checkpoint import _MAX_CHECKPOINT_BYTES, _check_total_size
    with pytest.raises(CheckpointError, match="total size limit"):
        _check_total_size("x" * (_MAX_CHECKPOINT_BYTES // 2), "y" * (_MAX_CHECKPOINT_BYTES // 2 + 1))
    with SessionLocal() as db:
        run_id, version = _three_pass_checkpoint(db)
        version = _complete_first_pass_item(db, run_id, version)
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        state["gate_reports"]["1"] = "d" * 64
        with pytest.raises(CheckpointError, match="already has a gate"):
            advance_checkpoint_pass(db, checkpoint=state, expected_version=version, next_pass=2, gate_result=_passed_gate(tmp_path, run_id=run_id, pass_no=1))


def test_checkpoint_load_rejects_gate_history_inconsistent_with_pass():
    with SessionLocal() as db:
        run_id, _ = _create(db)
        row = db.get(AuditRunCheckpoint, run_id)
        row.gate_reports_json = json.dumps({"1": "d" * 64})
        db.commit()
        with pytest.raises(CheckpointError, match="gate history does not match"):
            load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)


def test_checkpoint_rejects_scope_rewrite_and_bad_patch_without_partial_update():
    with SessionLocal() as db:
        run_id, version = _create(db, plan={"1": [["c1", "c2"], ["c3"]], "2": [], "3": []})
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        version = start_checkpoint_batch(db, checkpoint=state, expected_version=version, batch_ids=["c1", "c2"])
        db.commit()
        state = load_run_checkpoint(db, run_id=run_id, current_source_digest="a" * 64, current_skill_digest="b" * 64)
        bad_patch = {"items": [
            {"check_id": "c1", "result_status": "CHECKED", "evidence_ids": ["ev-1"]},
            {"check_id": "c2", "result_status": "NOT_CHECKED", "evidence_ids": []},
        ]}
        with pytest.raises(CheckpointError, match="batch patch or evidence is invalid"):
            finish_checkpoint_batch(db, checkpoint=state, expected_version=version, patch=bad_patch, evidence={"evidence": []}, allowed_source_files={"drawing.pdf"})
        changed = [dict(item) for item in state["ledger"]]
        changed[0]["page_ids"] = []
        with pytest.raises(CheckpointError):
            from app.services.audit_checkpoint import _store_state
            _store_state(db, checkpoint=state, expected_version=version, ledger=changed, active_batch_ids=["c1", "c2"], status="RUNNING")
        db.rollback()
        row = db.get(AuditRunCheckpoint, run_id)
        assert row.version == version
