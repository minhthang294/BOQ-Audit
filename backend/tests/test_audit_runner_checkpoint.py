import json
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import (
    AuditInventoryApproval,
    AuditQualityReview,
    AuditQualityReviewEvent,
    AuditRunBatchLedger,
    AuditRun,
    AuditRunBatchResult,
    AuditRunBatchRevision,
    AuditRunGateRepairRequest,
    AuditRunCheckpoint,
    AuditRunStageGate,
    Job,
    JobStatus,
    User,
    UserRole,
)
from app.services import audit_checkpoint, audit_runner
from app.services.audit_report_source import build_report_source
from app.services.audit_stage_gate import StageGateInputError
from app import worker
from app.services.audit_quality import ManifestEntry, ValidationResult, ValidatorReceipt


class CheckpointRunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'runner-checkpoint.db'}")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"synthetic drawing source")
        self.skill = self.root / "skills" / "boq-audit" / "SKILL.md"
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text("synthetic skill", encoding="utf-8")
        self.settings = SimpleNamespace(
            jobs_dir=self.root / "jobs",
            data_dir=self.root / "data",
            boq_audit_skill_path=self.skill,
            audit_checkpoint_worker_enabled=True,
        )
        self.runner_settings = patch.object(audit_runner, "get_settings", return_value=self.settings)
        self.checkpoint_settings = patch.object(
            audit_checkpoint,
            "get_settings",
            return_value=SimpleNamespace(audit_quality_reviewer_email_set={"reviewer@example.com"}),
        )
        self.session_patch = patch.object(audit_runner, "SessionLocal", self.sessions)
        self.worker_settings = patch.object(worker, "get_settings", return_value=self.settings)
        self.bundle_patch = patch.object(
            audit_runner.quality,
            "verify_pinned_bundle",
            return_value=SimpleNamespace(digest=audit_runner.quality.TRUSTED_SKILL_DIGEST),
        )
        self.runner_settings.start(); self.checkpoint_settings.start(); self.session_patch.start(); self.bundle_patch.start(); self.worker_settings.start()
        self.user, self.job, self.run = self._seed()

    def tearDown(self):
        self.worker_settings.stop(); self.bundle_patch.stop(); self.session_patch.stop(); self.checkpoint_settings.stop(); self.runner_settings.stop()
        self.engine.dispose()
        self.temporary.cleanup()

    def _seed(self):
        inventory = {
            "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
            "pages": [{"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1}],
            "sheets": [],
            "targets": [],
            "expected_work_items": [{
                "check_id": "p1-page-1", "document_id": "pdf", "revision_digest": "sha256:source",
                "page_ids": ["pdf-P00001"], "target_ids": [], "check_kind": "drawing_review",
                "pass_no": 1, "dependencies": [],
            }],
        }
        with self.sessions() as db:
            user = User(name="Reviewer", email="reviewer@example.com", password_hash="hash", role=UserRole.ADMIN)
            db.add(user); db.flush()
            job = Job(
                user_id=user.id, job_code="BOQ-RUN-1", project_name="Synthetic", original_filename="source.pdf",
                input_file_path=str(self.source), status=JobStatus.PROCESSING,
            )
            db.add(job); db.flush()
            run = AuditRun(job_id=job.id, attempt=1, status="RUNNING")
            db.add(run); db.flush()
            source_digest = audit_runner._source_digest(job)
            approval = AuditInventoryApproval(
                job_id=job.id, reviewer_user_id=user.id, source_digest=source_digest,
                inventory_json=json.dumps(inventory, sort_keys=True),
            )
            db.add(approval); db.commit()
            return user.id, job.id, run.id

    @staticmethod
    def _result():
        patch_doc = {"items": [{"check_id": "p1-page-1", "result_status": "CHECKED", "evidence_ids": ["ev-1"]}]}
        evidence = {"evidence": [{
            "evidence_id": "ev-1", "check_id": "p1-page-1", "source_file": "source/input.pdf",
            "location": "PDF page 1 / synthetic detail", "observation": "dimension label present", "method": "read source",
        }]}
        ledger_records = {"records": [{
            "record_type": "page_ledger", "record_id": "pdf-P00001", "check_id": "p1-page-1",
            "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": [], "target_ids": []},
            "payload": {"page_id": "pdf-P00001", "source_pdf": "source/input.pdf", "pdf_page": 1, "status": "VERIFIED", "evidence_anchors": ["PDF page 1"]},
        }, {
            "record_type": "run_metadata", "record_id": "META-p1-page-1", "check_id": "p1-page-1",
            "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": [], "target_ids": []},
            "payload": {"metadata_id": "META-p1-page-1", "stage": "pass1", "references_read": [], "memory_cases_consulted": [], "completed_checks": []},
        }]}
        return patch_doc, evidence, ledger_records

    def _fake_batch(self, job, attempt, **kwargs):
        self.assertEqual(job.job_code, "BOQ-RUN-1")
        self.assertEqual(attempt, 1)
        self.assertEqual(kwargs["pass_no"], 1)
        self.assertEqual(kwargs["batch_ids"], ["p1-page-1"])
        self.assertEqual(kwargs["source_files"], {"source/input.pdf": str(self.source)})
        return 0, False, "synthetic completion", self._result()

    def _dependency_batch(self, job, attempt, **kwargs):
        check_id = kwargs["batch_ids"][0]
        page_no = 1 if check_id == "root" else 2
        page_id = f"pdf-P{page_no:05d}"
        evidence_id = f"ev-{check_id}"
        return 0, False, "synthetic completion", (
            {"items": [{"check_id": check_id, "result_status": "CHECKED", "evidence_ids": [evidence_id]}]},
            {"evidence": [{
                "evidence_id": evidence_id, "check_id": check_id, "source_file": "source/input.pdf",
                "location": f"PDF page {page_no}", "observation": f"observation {check_id}", "method": "read source",
            }]},
            {"records": [{
                "record_type": "page_ledger", "record_id": page_id, "check_id": check_id,
                "source_refs": {"page_ids": [page_id], "sheet_ids": [], "target_ids": []},
                "payload": {"page_id": page_id, "source_pdf": "source/input.pdf", "pdf_page": page_no,
                            "status": "VERIFIED", "evidence_anchors": [f"PDF page {page_no}"]},
            }, {
                "record_type": "run_metadata", "record_id": f"META-{check_id}", "check_id": check_id,
                "source_refs": {"page_ids": [page_id], "sheet_ids": [], "target_ids": []},
                "payload": {"metadata_id": f"META-{check_id}", "stage": "pass1", "references_read": [],
                            "memory_cases_consulted": [], "completed_checks": []},
            }]},
        )

    def _gate_result(self, status="BLOCKED"):
        with self.sessions() as db:
            source_digest = audit_runner._source_digest(db.get(Job, self.job))
        snapshot_id = f"gate-{uuid4().hex}"
        receipt = ValidatorReceipt(
            schema_version=1, run_id=self.run, snapshot_id=snapshot_id, stage="pass1",
            source_digest=source_digest, skill_digest=None if status == "ERROR" else audit_runner.quality.TRUSTED_SKILL_DIGEST,
            input_digest="c" * 64, validator_exit_code=0 if status == "PASS" else 2 if status == "BLOCKED" else None,
            candidate_release_status=audit_runner.quality.STAGE_COMPLETE_STATUS if status == "PASS" else "BLOCKED" if status == "BLOCKED" else None,
            blocking_checks=("Mandatory reference files were not recorded as read",) if status == "BLOCKED" else (),
            open_record_count=0 if status == "PASS" else 1 if status == "BLOCKED" else None,
            validated_at=datetime.now(timezone.utc).isoformat(), controller_status=status,
        )
        receipt_path = self.root / f"{snapshot_id}.json"
        receipt_path.write_text(json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return ValidationResult(receipt=receipt, receipt_path=receipt_path)

    def test_batch_execution_commits_patch_evidence_and_checkpoint_cas(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            result = audit_runner.execute_next_checkpoint_batch(self.job, self.run)

        self.assertEqual(result["status"], "BATCH_DONE")
        with self.sessions() as db:
            checkpoint = db.get(AuditRunCheckpoint, self.run)
            batch_results = list(db.scalars(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == self.run)))
            state = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=audit_runner._source_digest(db.get(Job, self.job)),
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
        self.assertEqual(checkpoint.status, "READY")
        self.assertEqual(checkpoint.version, 3)
        self.assertEqual(len(batch_results), 1)
        self.assertEqual(state["ledger"][0]["execution_status"], "DONE")
        self.assertEqual(state["batch_results"][0]["evidence"]["evidence"][0]["evidence_id"], "ev-1")
        self.assertEqual(state["ledger_records"][0]["payload"]["page_id"], "pdf-P00001")
        self.assertEqual(state["ledger_projection"]["page_ledger"][0]["page_id"], "pdf-P00001")

    def test_worker_claim_imports_unchanged_pass_one_batch_and_keeps_old_attempt_immutable(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "BATCH_DONE")
        with self.sessions() as db:
            source_digest = audit_runner._source_digest(db.get(Job, self.job))
        with self.sessions() as db:
            first_checkpoint = db.get(AuditRunCheckpoint, self.run)
            first_version = first_checkpoint.version
            first_result = db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == self.run))
            first_patch = first_result.patch_json
            first_evidence = first_result.evidence_json
            db.get(AuditRun, self.run).status = "INTERRUPTED"
            job = db.get(Job, self.job)
            job.status = JobStatus.SUBMITTED
            job.completed_at = None
            db.commit()

        claimed = audit_runner.claim_job(self.job)

        self.assertIsNotNone(claimed)
        claimed_job, second_run = claimed
        self.assertEqual(claimed_job, self.job)
        with self.sessions() as db:
            second_run_row = db.get(AuditRun, second_run)
            second_checkpoint = db.get(AuditRunCheckpoint, second_run)
            imported = db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == second_run))
            state = audit_checkpoint.load_run_checkpoint(
                db, run_id=second_run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            old_checkpoint = db.get(AuditRunCheckpoint, self.run)
            old_result = db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == self.run))
            self.assertEqual(second_run_row.attempt, 2)
            self.assertEqual(second_checkpoint.derived_from_run_id, self.run)
            self.assertIsNotNone(imported)
            self.assertEqual(imported.derived_from_run_id, self.run)
            self.assertEqual(state["derived_from_run_id"], self.run)
            self.assertEqual(state["ledger"][0]["execution_status"], "DONE")
            self.assertEqual(len(state["batch_results"]), 1)
            self.assertEqual(old_checkpoint.version, first_version)
            self.assertIsNone(old_checkpoint.derived_from_run_id)
            self.assertEqual(old_result.patch_json, first_patch)
            self.assertEqual(old_result.evidence_json, first_evidence)
            self.assertIsNone(old_result.derived_from_run_id)

    def _dangling_reference_batch(self, job, attempt, **kwargs):
        patch_doc, evidence, records = self._result()
        records["records"].append({
            "record_type": "summary_lineage", "record_id": "SUM-1", "check_id": "p1-page-1",
            "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": [], "target_ids": []},
            "payload": {
                "summary_row_id": "SUM-1", "source_rows": ["ROW-MISSING"], "reported_total": 1,
                "source_anchor": "PDF page 1 / synthetic summary", "status": "VERIFIED",
                "name_spec_match": True, "unit_match": True, "scope_match": True,
            },
        })
        return 0, False, "synthetic completion", (patch_doc, evidence, records)

    def test_gate_records_dangling_domain_reference_without_advancing_pass(self):
        attempt_root = self.settings.jobs_dir / "BOQ-RUN-1" / "attempts" / "1"
        attempt_root.mkdir(parents=True, exist_ok=True)
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._dangling_reference_batch):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "BATCH_DONE")

        result = audit_runner.execute_next_checkpoint_batch(self.job, self.run)

        self.assertEqual(result["status"], "PASS_GATE_BLOCKED")
        self.assertEqual(result["error_code"], "STAGE_LEDGER_REFERENCE")
        with self.sessions() as db:
            checkpoint = db.get(AuditRunCheckpoint, self.run)
            gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run))
        self.assertEqual(checkpoint.pass_no, 1)
        self.assertEqual(gate.error_code, "STAGE_LEDGER_REFERENCE")
        self.assertFalse(gate.advanced)
        self.assertEqual(json.loads(gate.receipt_json)["controller_status"], "ERROR")

    def _seed_two_item_dependency_inventory(self):
        inventory = {
            "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
            "pages": [
                {"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1},
                {"page_id": "pdf-P00002", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 2},
            ],
            "sheets": [], "targets": [],
            "expected_work_items": [
                {"check_id": "root", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["pdf-P00001"],
                 "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
                {"check_id": "child", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["pdf-P00002"],
                 "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": ["root"]},
            ],
        }
        with self.sessions() as db:
            approval = db.scalar(select(AuditInventoryApproval).where(AuditInventoryApproval.job_id == self.job))
            approval.inventory_json = json.dumps(inventory, sort_keys=True)
            db.commit()

    def _start_child_batch_after_root(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._dependency_batch):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "BATCH_DONE")
        with self.sessions() as db:
            db.execute(__import__("sqlalchemy").text("BEGIN IMMEDIATE"))
            source_digest = audit_runner._source_digest(db.get(Job, self.job))
            checkpoint = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            audit_checkpoint.start_checkpoint_batch(
                db, checkpoint=checkpoint, expected_version=checkpoint["version"], batch_ids=["child"],
            )
            db.commit()
            return source_digest, checkpoint["version"] + 1

    def test_restart_recovers_active_batch_without_losing_committed_batch(self):
        self._seed_two_item_dependency_inventory()
        source_digest, interrupted_version = self._start_child_batch_after_root()

        result = audit_runner.execute_next_checkpoint_batch(self.job, self.run)

        self.assertEqual(result["status"], "RECOVERED_INTERRUPTED")
        self.assertEqual(result["check_ids"], ["child"])
        with self.sessions() as db:
            state = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            self.assertEqual(state["status"], "INTERRUPTED")
            self.assertEqual(state["version"], interrupted_version + 1)
            self.assertEqual({item["check_id"]: item["execution_status"] for item in state["ledger"]}, {"root": "DONE", "child": "BLOCKED"})
            self.assertEqual(len(state["batch_results"]), 1)
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "INTERRUPTED")
            version = audit_checkpoint.retry_checkpoint_batch(
                db, checkpoint=state, expected_version=state["version"], batch_ids=["child"],
            )
            db.commit()
            self.assertEqual(version, state["version"] + 1)

        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._dependency_batch):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "BATCH_DONE")
        with self.sessions() as db:
            resumed = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
        self.assertEqual({item["check_id"]: item["execution_status"] for item in resumed["ledger"]}, {"root": "DONE", "child": "DONE"})
        self.assertEqual(len(resumed["batch_results"]), 2)
        self.assertIsNone(resumed["derived_from_run_id"])

    def test_superseded_attempt_imports_only_committed_batches_after_interruption(self):
        self._seed_two_item_dependency_inventory()
        source_digest, interrupted_version = self._start_child_batch_after_root()
        with self.sessions() as db:
            db.execute(__import__("sqlalchemy").text("BEGIN IMMEDIATE"))
            checkpoint = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            audit_checkpoint.recover_checkpoint(db, checkpoint=checkpoint, expected_version=checkpoint["version"])
            db.get(AuditRun, self.run).status = "INTERRUPTED"
            job = db.get(Job, self.job)
            job.status = JobStatus.SUBMITTED
            job.completed_at = None
            source_patch = db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == self.run)).patch_json
            db.commit()

        claimed = audit_runner.claim_job(self.job)

        self.assertIsNotNone(claimed)
        _claimed_job, second_run = claimed
        with self.sessions() as db:
            state = audit_checkpoint.load_run_checkpoint(
                db, run_id=second_run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            self.assertEqual({item["check_id"]: item["execution_status"] for item in state["ledger"]}, {"root": "DONE", "child": "PENDING"})
            self.assertEqual(state["derived_from_run_id"], self.run)
            self.assertEqual(len(state["batch_results"]), 1)
            old = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            self.assertEqual(old["version"], interrupted_version + 1)
            self.assertEqual({item["check_id"]: item["execution_status"] for item in old["ledger"]}, {"root": "DONE", "child": "BLOCKED"})
            self.assertEqual(db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == self.run)).patch_json, source_patch)

    def test_completed_pass_runs_trusted_gate_and_stays_blocked_on_missing_contract_evidence(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        validation = self._gate_result()
        receipt = validation.receipt
        with patch.object(audit_runner, "_validate_checkpoint_stage", return_value=validation):
            result = audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        self.assertEqual(result["status"], "PASS_GATE_BLOCKED")
        self.assertEqual(result["blocking_checks"], list(receipt.blocking_checks))
        with self.sessions() as db:
            checkpoint = db.get(AuditRunCheckpoint, self.run)
            gates = list(db.scalars(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run)))
        self.assertEqual(checkpoint.pass_no, 1)
        self.assertEqual(len(gates), 1)
        self.assertFalse(gates[0].advanced)
        self.assertEqual(json.loads(gates[0].receipt_json)["controller_status"], "BLOCKED")

    def test_stage_source_binding_error_is_persisted_as_gate_receipt(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "BATCH_DONE")
        attempt_root = self.settings.jobs_dir / "BOQ-RUN-1" / "attempts" / "1"
        attempt_root.mkdir(parents=True, exist_ok=True)
        with patch.object(audit_runner, "write_checkpoint_stage_inputs", side_effect=StageGateInputError("synthetic source drift")):
            result = audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        self.assertEqual(result["status"], "PASS_GATE_BLOCKED")
        self.assertEqual(result["error_code"], "STAGE_SOURCE_BINDING")
        with self.sessions() as db:
            gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run))
        receipt = json.loads(gate.receipt_json)
        self.assertEqual(gate.error_code, "STAGE_SOURCE_BINDING")
        self.assertEqual(receipt["controller_status"], "ERROR")
        self.assertIsNone(receipt["validator_exit_code"])
        self.assertEqual(gate.receipt_digest, hashlib.sha256(gate.receipt_json.encode()).hexdigest())
        snapshot = self.settings.data_dir / "controller" / "snapshots" / gate.snapshot_id
        diagnostic = json.loads((snapshot / "work" / "controller_preflight_error.json").read_text())
        self.assertEqual(diagnostic["error_code"], "STAGE_SOURCE_BINDING")
        entries = [ManifestEntry(**row) for row in json.loads((snapshot / "manifest.json").read_text()) if row["path"].startswith("work/")]
        self.assertEqual(receipt["input_digest"], audit_runner.quality.canonical_manifest_digest(entries))

    def test_blocked_gate_revision_preserves_base_and_replaces_effective_batch_atomically(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        with patch.object(audit_runner, "_validate_checkpoint_stage", return_value=self._gate_result()):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "PASS_GATE_BLOCKED")
        with self.sessions() as db:
            db.execute(__import__("sqlalchemy").text("BEGIN IMMEDIATE"))
            source_digest = audit_runner._source_digest(db.get(Job, self.job))
            checkpoint = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run))
            patch_doc, evidence, records = self._result()
            with self.assertRaisesRegex(audit_checkpoint.CheckpointError, "did not change"):
                audit_checkpoint.append_checkpoint_batch_revision(
                    db, checkpoint=checkpoint, expected_version=checkpoint["version"], gate_id=gate.id,
                    batch_ids=["p1-page-1"], patch=patch_doc, evidence=evidence,
                    ledger_records=records, current_source_digest=source_digest,
                    current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
                )
            self.assertEqual(db.scalar(select(__import__("sqlalchemy").func.count()).select_from(AuditRunBatchRevision)), 0)
            evidence["evidence"][0]["observation"] = "source rechecked after gate blocker"
            records["records"][0]["payload"]["evidence_anchors"] = ["PDF page 1 / rechecked"]
            version = audit_checkpoint.append_checkpoint_batch_revision(
                db, checkpoint=checkpoint, expected_version=checkpoint["version"], gate_id=gate.id,
                batch_ids=["p1-page-1"], patch=patch_doc, evidence=evidence,
                ledger_records=records, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            db.commit()
            current = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            base = db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == self.run))
            revision = db.scalar(select(AuditRunBatchRevision).where(AuditRunBatchRevision.run_id == self.run))
            self.assertEqual(version, checkpoint["version"] + 1)
            self.assertEqual(current["batch_results"][0]["revision_no"], 1)
            self.assertEqual(current["batch_results"][0]["evidence"]["evidence"][0]["observation"], "source rechecked after gate blocker")
            self.assertEqual(json.loads(base.evidence_json)["evidence"][0]["observation"], "dimension label present")
            self.assertEqual(revision.gate_id, gate.id)
            with self.assertRaises(audit_checkpoint.CheckpointConflict):
                audit_checkpoint.append_checkpoint_batch_revision(
                    db, checkpoint=checkpoint, expected_version=checkpoint["version"], gate_id=gate.id,
                    batch_ids=["p1-page-1"], patch=patch_doc, evidence=evidence,
                    ledger_records=records, current_source_digest=source_digest,
                    current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
                )
            db.rollback()
            with patch.object(audit_runner, "_validate_checkpoint_stage", return_value=self._gate_result()):
                self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "PASS_GATE_BLOCKED")
            db.execute(__import__("sqlalchemy").text("BEGIN IMMEDIATE"))
            current = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            next_gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run).order_by(AuditRunStageGate.id.desc()))
            evidence["evidence"][0]["observation"] = "source rechecked after second gate blocker"
            records["records"][0]["payload"]["evidence_anchors"] = ["PDF page 1 / rechecked again"]
            audit_checkpoint.append_checkpoint_batch_revision(
                db, checkpoint=current, expected_version=current["version"], gate_id=next_gate.id,
                batch_ids=["p1-page-1"], patch=patch_doc, evidence=evidence,
                ledger_records=records, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            db.commit()
            current = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            self.assertEqual(current["batch_results"][0]["revision_no"], 2)
            with self.assertRaisesRegex(audit_checkpoint.CheckpointError, "budget"):
                audit_checkpoint.append_checkpoint_batch_revision(
                    db, checkpoint=current, expected_version=current["version"], gate_id=next_gate.id,
                    batch_ids=["p1-page-1"], patch=patch_doc, evidence=evidence,
                    ledger_records=records, current_source_digest=source_digest,
                    current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
                )
            revision.evidence_digest = "0" * 64
            db.commit()
            with self.assertRaises(audit_checkpoint.CheckpointError):
                audit_checkpoint.load_run_checkpoint(
                    db, run_id=self.run, current_source_digest=source_digest,
                    current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
                )

    def test_repair_invalidates_dependent_batch_and_worker_appends_its_rerun(self):
        inventory = {
            "documents": [{"document_id": "pdf", "revision_digest": "sha256:source"}],
            "pages": [
                {"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 1},
                {"page_id": "pdf-P00002", "document_id": "pdf", "revision_digest": "sha256:source", "pdf_page": 2},
            ],
            "sheets": [], "targets": [],
            "expected_work_items": [
                {"check_id": "root", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["pdf-P00001"],
                 "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": []},
                {"check_id": "child", "document_id": "pdf", "revision_digest": "sha256:source", "page_ids": ["pdf-P00002"],
                 "target_ids": [], "check_kind": "drawing_review", "pass_no": 1, "dependencies": ["root"]},
            ],
        }
        with self.sessions() as db:
            approval = db.scalar(select(AuditInventoryApproval).where(AuditInventoryApproval.job_id == self.job))
            approval.inventory_json = json.dumps(inventory, sort_keys=True)
            db.commit()

        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._dependency_batch):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "BATCH_DONE")
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "BATCH_DONE")
        with patch.object(audit_runner, "_validate_checkpoint_stage", return_value=self._gate_result()):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "PASS_GATE_BLOCKED")

        with self.sessions() as db:
            db.execute(__import__("sqlalchemy").text("BEGIN IMMEDIATE"))
            source_digest = audit_runner._source_digest(db.get(Job, self.job))
            checkpoint = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            self.assertEqual(audit_checkpoint._dependent_result_check_ids(checkpoint, ["root"], next(item["base_result_id"] for item in checkpoint["batch_results"] if item["check_ids"] == ["root"])), ["child"])
            gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run).order_by(AuditRunStageGate.id.desc()))
            patch_doc, evidence, records = self._dependency_batch(None, None, batch_ids=["root"])[3]
            evidence["evidence"][0]["observation"] = "root rechecked after gate blocker"
            records["records"][0]["payload"]["evidence_anchors"] = ["PDF page 1 / rechecked"]
            audit_checkpoint.append_checkpoint_batch_revision(
                db, checkpoint=checkpoint, expected_version=checkpoint["version"], gate_id=gate.id,
                batch_ids=["root"], patch=patch_doc, evidence=evidence, ledger_records=records,
                current_source_digest=source_digest, current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            db.commit()
            invalidated = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=source_digest,
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
        self.assertEqual(invalidated["ledger"][1]["execution_status"], "PENDING")
        self.assertTrue(next(item for item in invalidated["batch_results"] if item["check_ids"] == ["child"])["stale"])
        self.assertEqual([record["record_id"] for record in invalidated["ledger_records"] if record["record_type"] == "page_ledger"], ["pdf-P00001"])

        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._dependency_batch):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "BATCH_DONE")
        with self.sessions() as db:
            current = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=audit_runner._source_digest(db.get(Job, self.job)),
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
        child = next(item for item in current["batch_results"] if item["check_ids"] == ["child"])
        self.assertFalse(child["stale"])
        self.assertEqual(child["revision_no"], 1)
        self.assertEqual(current["ledger"][1]["execution_status"], "DONE")
        with self.sessions() as db:
            root_result = next(item for item in current["batch_results"] if item["check_ids"] == ["root"])
            root_revision = db.scalar(select(AuditRunBatchRevision).where(
                AuditRunBatchRevision.base_result_id == root_result["base_result_id"],
            ))
            self.assertEqual(json.loads(root_revision.invalidated_check_ids_json), ["child"])
            valid_digest = root_revision.invalidation_digest
            root_revision.invalidation_digest = "0" * 64
            db.commit()
            with self.assertRaisesRegex(audit_checkpoint.CheckpointError, "invalidation"):
                audit_checkpoint.load_run_checkpoint(
                    db, run_id=self.run, current_source_digest=audit_runner._source_digest(db.get(Job, self.job)),
                    current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
                )
            root_revision.invalidation_digest = valid_digest
            db.commit()

    def test_worker_claims_reviewer_repair_and_commits_revision_on_same_run(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        with patch.object(audit_runner, "_validate_checkpoint_stage", return_value=self._gate_result()):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "PASS_GATE_BLOCKED")
        with self.sessions() as db:
            job = db.get(Job, self.job)
            job.status = JobStatus.WAITING_FOR_INFO
            db.get(AuditRun, self.run).status = JobStatus.WAITING_FOR_INFO.value
            checkpoint = db.get(AuditRunCheckpoint, self.run)
            base = db.scalar(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == self.run))
            gate = db.scalar(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run))
            request = AuditRunGateRepairRequest(
                run_id=self.run, base_result_id=base.id, gate_id=gate.id,
                batch_ids_json='["p1-page-1"]', source_digest=checkpoint.source_digest,
                skill_digest=checkpoint.skill_digest, checkpoint_version=checkpoint.version,
                attempt_no=1, status="REQUESTED",
            )
            db.add(request); db.commit()
            request_id = request.id
            review = AuditQualityReview(
                run_id=self.run, reviewer_user_id=self.user, snapshot_id="pre-repair-signoff",
                stage="release", controller_status="PASS", candidate_release_status="READY_FOR_ENGINEER_REVIEW",
                quality_aggregate="{}", source_digest=checkpoint.source_digest,
                skill_digest=checkpoint.skill_digest, input_digest="a" * 64,
                receipt_digests="{}", output_manifest_digest="b" * 64,
                customer_fields_digest="c" * 64, output_manifest="[]",
            )
            db.add(review); db.flush()
            db.add(AuditQualityReviewEvent(review_id=review.id, event_type="SIGNOFF_ACCEPTED", actor_id=str(self.user), reason="synthetic signoff"))
            db.commit()
            review_id = review.id
            before = audit_checkpoint.load_run_checkpoint(db, run_id=self.run, current_source_digest=audit_runner._source_digest(job), current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST)
            source_before = build_report_source(before)
            self.assertEqual(source_before["ledger_record_counts"]["page_ledger"], 1)
        with patch.object(worker, "SessionLocal", self.sessions):
            self.settings.audit_checkpoint_worker_enabled = False
            self.assertIsNone(worker._claim_repair_request())
            with self.sessions() as db:
                self.assertEqual(db.get(AuditRunGateRepairRequest, request_id).status, "REQUESTED")
                self.assertEqual(db.get(Job, self.job).status, JobStatus.WAITING_FOR_INFO)
            self.settings.audit_checkpoint_worker_enabled = True
            self.assertEqual(worker._claim_repair_request(), request_id)
        with patch.object(audit_runner, "_execute_codex_batch", return_value=(1, False, "synthetic failure", None)):
            self.assertFalse(audit_runner.run_checkpoint_repair_request(request_id))
        with self.sessions() as db:
            self.assertEqual(db.get(AuditRunGateRepairRequest, request_id).status, "FAILED")
            self.assertEqual(db.get(Job, self.job).status, JobStatus.WAITING_FOR_INFO)
            self.assertEqual(list(db.scalars(select(AuditRunBatchRevision).where(AuditRunBatchRevision.run_id == self.run))), [])
            self.assertEqual([event.event_type for event in db.scalars(select(AuditQualityReviewEvent).where(AuditQualityReviewEvent.review_id == review_id))], ["SIGNOFF_ACCEPTED"])
            prior = db.get(AuditRunGateRepairRequest, request_id)
            retry = AuditRunGateRepairRequest(
                run_id=self.run, base_result_id=prior.base_result_id, gate_id=prior.gate_id,
                batch_ids_json=prior.batch_ids_json, source_digest=prior.source_digest,
                skill_digest=prior.skill_digest, checkpoint_version=prior.checkpoint_version,
                attempt_no=2, status="REQUESTED",
            )
            db.add(retry); db.commit()
            request_id = retry.id
        with patch.object(worker, "SessionLocal", self.sessions):
            self.assertEqual(worker._claim_repair_request(), request_id)
        def repaired_batch(*_args, **_kwargs):
            patch_doc, evidence, records = self._result()
            evidence["evidence"][0]["observation"] = "reopened source during repair"
            records["records"][0]["payload"]["evidence_anchors"] = ["PDF page 1 / repair"]
            return 0, False, "", (patch_doc, evidence, records)
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=repaired_batch):
            self.assertTrue(audit_runner.run_checkpoint_repair_request(request_id))
        with self.sessions() as db:
            request = db.get(AuditRunGateRepairRequest, request_id)
            job = db.get(Job, self.job)
            state = audit_checkpoint.load_run_checkpoint(
                db, run_id=self.run, current_source_digest=audit_runner._source_digest(job),
                current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
            )
            self.assertEqual(request.status, "COMMITTED")
            self.assertEqual(job.status, JobStatus.PROCESSING)
            self.assertEqual(db.get(AuditRun, self.run).status, "RUNNING")
            self.assertEqual(state["batch_results"][0]["revision_no"], 1)
            self.assertEqual(state["batch_results"][0]["evidence"]["evidence"][0]["observation"], "reopened source during repair")
            self.assertNotEqual(build_report_source(state)["snapshot_digest"], source_before["snapshot_digest"])
            self.assertEqual([event.event_type for event in db.scalars(select(AuditQualityReviewEvent).where(AuditQualityReviewEvent.review_id == review_id).order_by(AuditQualityReviewEvent.event_id))], ["SIGNOFF_ACCEPTED", "REVOKED"])
        with patch.object(audit_runner, "_validate_checkpoint_stage", return_value=self._gate_result("PASS")):
            self.assertEqual(audit_runner.execute_next_checkpoint_batch(self.job, self.run)["status"], "PASS_ADVANCED")
        with self.sessions() as db:
            self.assertEqual(db.get(AuditRunCheckpoint, self.run).pass_no, 2)
            self.assertEqual(len(list(db.scalars(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run)))), 2)

    def test_pass_gate_history_commits_with_checkpoint_advance(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        validation = self._gate_result("PASS")
        with patch.object(audit_runner, "_validate_checkpoint_stage", return_value=validation):
            result = audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        self.assertEqual(result["status"], "PASS_ADVANCED")
        with self.sessions() as db:
            checkpoint = db.get(AuditRunCheckpoint, self.run)
            gates = list(db.scalars(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run)))
        self.assertEqual(checkpoint.pass_no, 2)
        self.assertEqual(len(gates), 1)
        self.assertTrue(gates[0].advanced)
        self.assertEqual(json.loads(checkpoint.gate_reports_json)["1"], gates[0].receipt_digest)

    def test_validator_error_receipt_is_kept_without_advancing(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        validation = self._gate_result("ERROR")
        with patch.object(audit_runner, "_validate_checkpoint_stage", return_value=validation):
            result = audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        self.assertEqual(result["status"], "PASS_GATE_BLOCKED")
        with self.sessions() as db:
            checkpoint = db.get(AuditRunCheckpoint, self.run)
            gates = list(db.scalars(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run)))
        self.assertEqual(checkpoint.pass_no, 1)
        self.assertEqual(len(gates), 1)
        self.assertEqual(json.loads(gates[0].receipt_json)["controller_status"], "ERROR")

    def test_gate_history_failure_rolls_back_pass_advance(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        validation = self._gate_result("PASS")
        with patch.object(audit_runner, "_validate_checkpoint_stage", return_value=validation), patch.object(
            audit_runner, "record_checkpoint_gate", side_effect=RuntimeError("history write failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "history write failed"):
                audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        with self.sessions() as db:
            checkpoint = db.get(AuditRunCheckpoint, self.run)
            gates = list(db.scalars(select(AuditRunStageGate).where(AuditRunStageGate.run_id == self.run)))
        self.assertEqual(checkpoint.pass_no, 1)
        self.assertEqual(json.loads(checkpoint.gate_reports_json), {})
        self.assertEqual(gates, [])

    def test_checkpoint_load_rejects_tampered_ledger_record(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        with self.sessions() as db:
            record = db.scalar(select(AuditRunBatchLedger).where(AuditRunBatchLedger.run_id == self.run))
            record.records_json = '[{"page_id":"tampered"}]'
            db.commit()
            with self.assertRaises(audit_checkpoint.CheckpointError):
                audit_checkpoint.load_run_checkpoint(
                    db, run_id=self.run, current_source_digest=audit_runner._source_digest(db.get(Job, self.job)),
                    current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
                )

    def test_checkpoint_load_rejects_missing_batch_ledger_manifest(self):
        with patch.object(audit_runner, "_execute_codex_batch", side_effect=self._fake_batch):
            audit_runner.execute_next_checkpoint_batch(self.job, self.run)
        with self.sessions() as db:
            record = db.scalar(select(AuditRunBatchLedger).where(AuditRunBatchLedger.run_id == self.run))
            db.delete(record)
            db.commit()
            with self.assertRaises(audit_checkpoint.CheckpointError):
                audit_checkpoint.load_run_checkpoint(
                    db, run_id=self.run, current_source_digest=audit_runner._source_digest(db.get(Job, self.job)),
                    current_skill_digest=audit_runner.quality.TRUSTED_SKILL_DIGEST,
                )

    def test_source_drift_during_batch_recovers_active_items_without_saving_result(self):
        def change_source(job, attempt, **kwargs):
            self.source.write_bytes(b"changed while batch was running")
            return 0, False, "synthetic completion", self._result()

        with patch.object(audit_runner, "_execute_codex_batch", side_effect=change_source):
            result = audit_runner.execute_next_checkpoint_batch(self.job, self.run)

        self.assertEqual(result["status"], "BATCH_FAILED")
        with self.sessions() as db:
            checkpoint = db.get(AuditRunCheckpoint, self.run)
            batch_count = len(list(db.scalars(select(AuditRunBatchResult).where(AuditRunBatchResult.run_id == self.run))))
        self.assertEqual(checkpoint.status, "INTERRUPTED")
        self.assertEqual(json.loads(checkpoint.ledger_json)[0]["execution_status"], "BLOCKED")
        self.assertEqual(batch_count, 0)


if __name__ == "__main__":
    unittest.main()
