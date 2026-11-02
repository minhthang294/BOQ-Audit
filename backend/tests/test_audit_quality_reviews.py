import json

import pytest
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core import migrations
from app.core.database import Base
from app.models.entities import (
    AuditQualityReview,
    AuditQualityReviewEvent,
    AuditRun,
    Job,
    JobStatus,
    User,
    UserRole,
)

NEW_TABLES = {"audit_quality_reviews", "audit_quality_review_events"}


def _review_values(
    run_id: int,
    snapshot_id: str,
    reviewer_user_id: int | None = None,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "reviewer_user_id": reviewer_user_id,
        "snapshot_id": snapshot_id,
        "stage": "release",
        "controller_status": "PASS",
        "candidate_release_status": "READY_FOR_ENGINEER_REVIEW",
        "quality_aggregate": json.dumps({"open_record_count": 0, "blocking_checks": []}),
        "source_digest": "a" * 64,
        "skill_digest": "b" * 64,
        "input_digest": "c" * 64,
        "receipt_digests": json.dumps(
            {
                "pass1": "d" * 64,
                "pass2": "d" * 64,
                "pass3": "d" * 64,
                "release": "d" * 64,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        "output_manifest_digest": "e" * 64,
        "customer_fields_digest": "f" * 64,
        "output_manifest": json.dumps(
            [
                {
                    "output_id": 1,
                    "file_type": "EXCEL_REPORT",
                    "path": "output/BOQ_Audit_Report.xlsx",
                    "size": 10,
                    "sha256": "1" * 64,
                }
            ]
        ),
    }


def test_startup_migration_adds_quality_tables_idempotently(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'quality.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        legacy_tables = [
            table for name, table in Base.metadata.tables.items() if name not in NEW_TABLES
        ]
        Base.metadata.create_all(engine, tables=legacy_tables)
        assert NEW_TABLES.isdisjoint(inspect(engine).get_table_names())

        with Session(engine) as db:
            user = User(
                name="Reviewer",
                email="reviewer@example.com",
                password_hash="hash",
                role=UserRole.ADMIN,
            )
            job = Job(
                user=user,
                project_name="Quality review",
                original_filename="input.pdf",
                input_file_path="input.pdf",
                status=JobStatus.REVIEW,
            )
            run = AuditRun(job=job, attempt=1, status="COMPLETED")
            db.add(run)
            db.commit()
            user_id = user.id
            job_id = job.id
            run_id = run.id

        monkeypatch.setattr(migrations, "engine", engine)
        monkeypatch.setattr(migrations, "SessionLocal", sessionmaker(bind=engine))

        migrations.migrate_and_recover()

        with Session(engine) as db:
            review = AuditQualityReview(**_review_values(run_id, "snapshot-1", user_id))
            db.add(review)
            db.flush()

            error_review_values = _review_values(run_id, "snapshot-error", user_id)
            error_review_values["skill_digest"] = None
            error_review_values["controller_status"] = "ERROR"
            error_review_values["candidate_release_status"] = None
            error_review_values["quality_aggregate"] = json.dumps(
                {"open_record_count": None, "blocking_checks": []}
            )
            error_review = AuditQualityReview(**error_review_values)
            db.add(error_review)
            db.flush()

            review_event = AuditQualityReviewEvent(
                review_id=review.id,
                event_type="SIGNOFF_ACCEPTED",
                actor_id=str(user_id),
                reason="Reviewed",
            )
            system_event = AuditQualityReviewEvent(
                review_id=review.id,
                event_type="REVOKED",
                actor_id="SYSTEM",
                reason="System mutation",
            )
            db.add_all([review_event, system_event])
            db.commit()
            review_id = review.id
            error_review_id = error_review.id
            review_event_id = review_event.event_id
            system_event_id = system_event.event_id

        migrations.migrate_and_recover()

        assert review_id is not None
        assert error_review_id is not None
        assert review_event_id is not None
        assert system_event_id > review_event_id

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert NEW_TABLES <= tables

        unique_constraints = {
            (item["name"], tuple(item["column_names"]))
            for item in inspector.get_unique_constraints("audit_quality_reviews")
        }
        assert unique_constraints == {
            ("uq_audit_quality_reviews_run_snapshot", ("run_id", "snapshot_id")),
            ("uq_audit_quality_reviews_snapshot_id", ("snapshot_id",)),
        }

        review_foreign_keys = {
            (tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
            for item in inspector.get_foreign_keys("audit_quality_reviews")
        }
        assert review_foreign_keys == {
            (("reviewer_user_id",), "users", ("id",)),
            (("run_id",), "audit_runs", ("id",)),
        }

        event_foreign_keys = {
            (tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
            for item in inspector.get_foreign_keys("audit_quality_review_events")
        }
        assert event_foreign_keys == {(("review_id",), "audit_quality_reviews", ("id",))}

        review_columns = {
            column["name"]: column
            for column in inspector.get_columns("audit_quality_reviews")
        }
        assert review_columns["skill_digest"]["nullable"] is True

        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(User)) == 1
            assert db.scalar(select(func.count()).select_from(Job)) == 1
            assert db.scalar(select(func.count()).select_from(AuditRun)) == 1
            assert db.scalar(select(func.count()).select_from(AuditQualityReview)) == 2
            assert db.scalar(select(func.count()).select_from(AuditQualityReviewEvent)) == 2

            stored_job = db.get(Job, job_id)
            stored_review = db.get(AuditQualityReview, review_id)
            stored_error_review = db.get(AuditQualityReview, error_review_id)
            stored_event = db.get(AuditQualityReviewEvent, review_event_id)
            stored_system_event = db.get(AuditQualityReviewEvent, system_event_id)
            assert stored_job is not None
            assert stored_job.status == JobStatus.REVIEW
            assert stored_review is not None
            assert stored_review.run_id == run_id
            assert stored_review.reviewer_user_id == user_id
            assert stored_review.snapshot_id == "snapshot-1"
            assert stored_error_review is not None
            assert stored_error_review.snapshot_id == "snapshot-error"
            assert stored_error_review.controller_status == "ERROR"
            assert stored_error_review.candidate_release_status is None
            assert stored_error_review.skill_digest is None
            assert stored_event is not None
            assert stored_event.review_id == review_id
            assert stored_event.actor_id == str(user_id)
            assert stored_event.reason == "Reviewed"
            assert stored_system_event is not None
            assert stored_system_event.actor_id == "SYSTEM"

            second_run = AuditRun(job_id=job_id, attempt=2, status="COMPLETED")
            db.add(second_run)
            db.flush()

            db.add(AuditQualityReview(**_review_values(second_run.id, "snapshot-1")))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()

            db.add(AuditQualityReview(**_review_values(999_999, "snapshot-2")))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()

            db.add(AuditQualityReview(**_review_values(run_id, "snapshot-3", 999_999)))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()

            db.add(
                AuditQualityReviewEvent(
                    review_id=999_999,
                    event_type="SIGNOFF_ACCEPTED",
                    actor_id=str(user_id),
                    reason="Invalid review",
                )
            )
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
    finally:
        engine.dispose()


def test_startup_migration_adds_dependency_invalidation_columns_to_legacy_revision_table(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'batch-revisions.db'}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE audit_run_batch_revisions"))
            connection.execute(text("""
                CREATE TABLE audit_run_batch_revisions (
                    id INTEGER PRIMARY KEY,
                    run_id INTEGER NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
                    base_result_id INTEGER NOT NULL REFERENCES audit_run_batch_results(id) ON DELETE CASCADE,
                    gate_id INTEGER NOT NULL REFERENCES audit_run_stage_gates(id),
                    revision_no INTEGER NOT NULL,
                    blocker_fingerprint VARCHAR(64) NOT NULL,
                    patch_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    records_json TEXT NOT NULL,
                    patch_digest VARCHAR(64) NOT NULL,
                    evidence_digest VARCHAR(64) NOT NULL,
                    records_digest VARCHAR(64) NOT NULL,
                    checkpoint_version INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    CONSTRAINT uq_audit_batch_revision_number UNIQUE (base_result_id, revision_no)
                )
            """))
        monkeypatch.setattr(migrations, "engine", engine)
        monkeypatch.setattr(migrations, "SessionLocal", sessionmaker(bind=engine))
        migrations.migrate_and_recover()
        migrations.migrate_and_recover()
        columns = {column["name"]: column for column in inspect(engine).get_columns("audit_run_batch_revisions")}
        assert columns["invalidated_check_ids_json"]["nullable"] is False
        assert columns["invalidation_digest"]["nullable"] is True
    finally:
        engine.dispose()


def test_startup_migration_adds_cross_attempt_provenance_columns_to_legacy_checkpoints(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-checkpoints.db'}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE audit_run_batch_ledgers"))
            connection.execute(text("DROP TABLE audit_run_batch_revisions"))
            connection.execute(text("DROP TABLE audit_run_batch_results"))
            connection.execute(text("DROP TABLE audit_run_checkpoints"))
            connection.execute(text("""
                CREATE TABLE audit_run_checkpoints (
                    run_id INTEGER PRIMARY KEY,
                    approval_id INTEGER NOT NULL,
                    source_digest VARCHAR(64) NOT NULL,
                    skill_digest VARCHAR(64) NOT NULL,
                    inventory_digest VARCHAR(64) NOT NULL,
                    batch_plan_digest VARCHAR(64) NOT NULL,
                    inventory_json TEXT NOT NULL,
                    batch_plan_json TEXT NOT NULL,
                    ledger_json TEXT NOT NULL,
                    lesson_snapshot_json TEXT,
                    lesson_snapshot_digest VARCHAR(64),
                    gate_reports_json TEXT NOT NULL,
                    pass_no INTEGER NOT NULL,
                    active_batch_ids_json TEXT NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE audit_run_batch_results (
                    id INTEGER PRIMARY KEY,
                    run_id INTEGER NOT NULL,
                    pass_no INTEGER NOT NULL,
                    batch_key VARCHAR(64) NOT NULL,
                    check_ids_json TEXT NOT NULL,
                    source_files_json TEXT NOT NULL,
                    patch_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    patch_digest VARCHAR(64) NOT NULL,
                    evidence_digest VARCHAR(64) NOT NULL,
                    checkpoint_version INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    CONSTRAINT uq_audit_run_batch_results_run_key UNIQUE (run_id, batch_key)
                )
            """))
            connection.execute(text("""
                INSERT INTO audit_run_checkpoints VALUES (
                    17, 23, :source, :skill, :inventory_digest, :plan_digest,
                    '{"expected_work_items":[]}', '{"1":[],"2":[],"3":[]}', '[]',
                    '{"project_scope":"legacy"}', :lesson_digest, '{}', 1, '[]', 'READY', 4,
                    '2026-10-29 00:00:00'
                )
            """), {
                "source": "a" * 64, "skill": "b" * 64,
                "inventory_digest": "c" * 64, "plan_digest": "d" * 64,
                "lesson_digest": "e" * 64,
            })
            connection.execute(text("""
                INSERT INTO audit_run_batch_results VALUES (
                    31, 17, 1, :batch_key, '["legacy-check"]', '["drawing.pdf"]',
                    :patch_json, '{"evidence":[]}', :patch_digest, :evidence_digest,
                    4, '2026-10-29 00:00:00'
                )
            """), {
                "batch_key": "f" * 64, "patch_digest": "1" * 64,
                "evidence_digest": "2" * 64, "patch_json": '{"legacy":true}',
            })

        checkpoint_before = {column["name"] for column in inspect(engine).get_columns("audit_run_checkpoints")}
        result_before = {column["name"] for column in inspect(engine).get_columns("audit_run_batch_results")}
        assert "checker_digest" not in checkpoint_before
        assert "derived_from_run_id" not in checkpoint_before | result_before

        monkeypatch.setattr(migrations, "engine", engine)
        monkeypatch.setattr(migrations, "SessionLocal", sessionmaker(bind=engine))
        migrations.migrate_and_recover()
        migrations.migrate_and_recover()

        inspector = inspect(engine)
        checkpoint_columns = {column["name"]: column for column in inspector.get_columns("audit_run_checkpoints")}
        result_columns = {column["name"]: column for column in inspector.get_columns("audit_run_batch_results")}
        assert checkpoint_columns["checker_digest"]["nullable"] is True
        assert checkpoint_columns["derived_from_run_id"]["nullable"] is True
        assert result_columns["derived_from_run_id"]["nullable"] is True
        with engine.connect() as connection:
            checkpoint = connection.execute(text("SELECT * FROM audit_run_checkpoints WHERE run_id=17")).mappings().one()
            result = connection.execute(text("SELECT * FROM audit_run_batch_results WHERE id=31")).mappings().one()
            assert checkpoint["inventory_json"] == '{"expected_work_items":[]}'
            assert checkpoint["lesson_snapshot_json"] == '{"project_scope":"legacy"}'
            assert checkpoint["version"] == 4
            assert checkpoint["checker_digest"] is None
            assert checkpoint["derived_from_run_id"] is None
            assert result["patch_json"] == '{"legacy":true}'
            assert result["checkpoint_version"] == 4
            assert result["derived_from_run_id"] is None
            assert connection.execute(text("SELECT COUNT(*) FROM audit_run_checkpoints")).scalar_one() == 1
            assert connection.execute(text("SELECT COUNT(*) FROM audit_run_batch_results")).scalar_one() == 1
    finally:
        engine.dispose()
