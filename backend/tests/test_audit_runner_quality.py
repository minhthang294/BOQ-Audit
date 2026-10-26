import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.models.entities import AuditQualityReview, AuditRun, Job, JobStatus, OutputType, User, UserRole, utcnow
from app.services import audit_quality as quality
from app.services import audit_runner


def _database(tmp_path: Path) -> tuple[object, sessionmaker]:
    engine = create_engine(f"sqlite:///{tmp_path / 'runner-quality.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def _seed_processing_job(engine, tmp_path: Path, *, attempts: int = 1) -> tuple[int, list[int]]:
    with Session(engine) as db:
        user = User(name="Admin", email="admin@example.com", password_hash="hash", role=UserRole.ADMIN)
        job = Job(
            user=user,
            job_code="BOQ-000001",
            project_name="Quality review",
            original_filename="input.pdf",
            input_file_path=str(tmp_path / "input.pdf"),
            status=JobStatus.PROCESSING,
        )
        db.add(job)
        db.flush()
        run_ids = []
        for attempt in range(1, attempts + 1):
            run = AuditRun(job=job, attempt=attempt, status="RUNNING", started_at=utcnow())
            db.add(run)
            db.flush()
            run_ids.append(run.id)
        db.commit()
        return job.id, run_ids


def _outputs(tmp_path: Path, attempt: int = 1) -> list[tuple[Path, OutputType]]:
    output_dir = tmp_path / "jobs" / "BOQ-000001" / "attempts" / str(attempt) / "output"
    output_dir.mkdir(parents=True)
    files = [
        ("BOQ_Audit_Report.xlsx", b"report", OutputType.EXCEL_REPORT),
        ("Annotated_Original.pdf", b"%PDF-1.4\n", OutputType.ANNOTATED_PDF),
    ]
    outputs = []
    for name, content, kind in files:
        path = output_dir / name
        path.write_bytes(content)
        outputs.append((path, kind))
    return outputs


def _artifact(
    tmp_path: Path,
    run_id: int,
    outputs: list[tuple[Path, OutputType]],
    *,
    controller_status: str = "PASS",
    error_code: str | None = None,
    error_message: str | None = None,
) -> audit_runner._QualityArtifact:
    manifest = tuple(
        quality.ManifestEntry(
            f"output/{path.name}",
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
        )
        for path, _kind in outputs
    )
    candidate_status = quality.RELEASE_READY_STATUS if controller_status == "PASS" else None
    receipt = quality.ValidatorReceipt(
        schema_version=1,
        run_id=run_id,
        snapshot_id="snap-test",
        stage="release",
        source_digest="a" * 64,
        skill_digest="b" * 64,
        input_digest="c" * 64,
        validator_exit_code=0,
        candidate_release_status=candidate_status,
        blocking_checks=(),
        open_record_count=0 if controller_status == "PASS" else None,
        validated_at="2026-10-26T00:00:00Z",
        controller_status=controller_status,
    )
    return audit_runner._QualityArtifact(
        snapshot=SimpleNamespace(snapshot_id="snap-test", manifest=manifest),
        validation=SimpleNamespace(
            receipt=receipt,
            controller_status=controller_status,
            receipt_path=tmp_path / "release-receipt.json",
            report=(
                {"open_issue_count": 0, "warning": "metadata only"}
                if controller_status == "PASS"
                else None
            ),
            error_code=error_code,
            error_message=error_message,
        ),
        source_digest="a" * 64,
        receipt_digest="f" * 64,
    )


def _configure(monkeypatch, engine, tmp_path: Path) -> None:
    monkeypatch.setattr(audit_runner, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(
        audit_runner,
        "get_settings",
        lambda: SimpleNamespace(jobs_dir=tmp_path / "jobs", data_dir=tmp_path / "data"),
    )


@pytest.mark.parametrize("controller_status", ["PASS", "ERROR"])
def test_finish_persists_one_advisory_release_review_for_current_run(
    tmp_path: Path, monkeypatch, controller_status: str
) -> None:
    engine, session_factory = _database(tmp_path)
    job_id, (run_id,) = _seed_processing_job(engine, tmp_path)
    outputs = _outputs(tmp_path)
    artifact = _artifact(
        tmp_path,
        run_id,
        outputs,
        controller_status=controller_status,
        error_code="REPORT_INVALID" if controller_status == "ERROR" else None,
        error_message="fresh report missing" if controller_status == "ERROR" else None,
    )
    _configure(monkeypatch, engine, tmp_path)
    message = "Lỗi nghiêm trọng: 1\nCảnh báo/cần lưu ý: 2"

    assert audit_runner._finish(job_id, run_id, 0.0, 0, message, outputs, quality_artifact=artifact)

    with session_factory() as db:
        job = db.get(Job, job_id)
        reviews = list(db.scalars(select(AuditQualityReview)))
        assert job is not None
        assert job.status == JobStatus.REVIEW
        assert len(reviews) == 1
        review = reviews[0]
        assert review.run_id == run_id
        assert review.reviewer_user_id is None
        assert review.stage == "release"
        assert review.controller_status == controller_status
        assert review.candidate_release_status == (
            quality.RELEASE_READY_STATUS if controller_status == "PASS" else None
        )
        assert review.source_digest == "a" * 64
        assert json.loads(review.receipt_digests) == {"release": "f" * 64}
        assert json.loads(review.quality_aggregate) == {
            "advisory": True,
            "blocking_checks": [],
            "critical_errors": 1,
            "enforcing": False,
            "error_code": "REPORT_INVALID" if controller_status == "ERROR" else None,
            "error_reason": "fresh report missing" if controller_status == "ERROR" else None,
            "missing_outputs": [],
            "open_issue_count": 0 if controller_status == "PASS" else None,
            "open_record_count": 0 if controller_status == "PASS" else None,
            "returncode": 0,
            "trusted": False,
            "validator_exit_code": 0,
            "warning": "metadata only" if controller_status == "PASS" else None,
            "warnings": 2,
        }
        manifest = json.loads(review.output_manifest)
        assert {item["output_id"] for item in manifest} == {output.id for output in job.outputs}
        assert {item["file_type"] for item in manifest} == {
            OutputType.EXCEL_REPORT.value,
            OutputType.ANNOTATED_PDF.value,
        }
        assert review.output_manifest_digest == audit_runner._canonical_digest(manifest)
        assert review.customer_fields_digest == audit_runner._canonical_digest(
            audit_runner._customer_fields_payload(job)
        )


def test_finish_marks_review_error_when_output_changes_after_snapshot(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = _database(tmp_path)
    job_id, (run_id,) = _seed_processing_job(engine, tmp_path)
    outputs = _outputs(tmp_path)
    artifact = _artifact(tmp_path, run_id, outputs)
    changed_path = outputs[0][0]
    original = changed_path.read_bytes()
    changed_path.write_bytes(b"X" * len(original))
    assert changed_path.read_bytes() != original
    _configure(monkeypatch, engine, tmp_path)

    assert audit_runner._finish(job_id, run_id, 0.0, 0, "", outputs, quality_artifact=artifact)

    with session_factory() as db:
        job = db.get(Job, job_id)
        review = db.scalar(select(AuditQualityReview))
        assert job is not None
        assert job.status == JobStatus.REVIEW
        assert job.completed_at is None
        assert review is not None
        assert review.controller_status == "ERROR"
        assert review.candidate_release_status is None
        aggregate = json.loads(review.quality_aggregate)
        assert aggregate["advisory"] is True
        assert aggregate["trusted"] is False
        assert aggregate["enforcing"] is False
        assert aggregate["error_code"] == "OUTPUT_INTEGRITY"
        assert "output/BOQ_Audit_Report.xlsx" in aggregate["error_reason"]


def test_finish_does_not_persist_review_for_stale_run(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = _database(tmp_path)
    job_id, (stale_run_id, _newest_run_id) = _seed_processing_job(engine, tmp_path, attempts=2)
    outputs = _outputs(tmp_path)
    artifact = _artifact(tmp_path, stale_run_id, outputs)
    _configure(monkeypatch, engine, tmp_path)

    assert not audit_runner._finish(job_id, stale_run_id, 0.0, 0, "", outputs, quality_artifact=artifact)

    with session_factory() as db:
        assert db.get(Job, job_id).status == JobStatus.PROCESSING
        assert list(db.scalars(select(AuditQualityReview))) == []


def test_runner_finalizes_when_quality_preparation_fails(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = _database(tmp_path)
    job_id, (run_id,) = _seed_processing_job(engine, tmp_path)
    outputs = _outputs(tmp_path)
    _configure(monkeypatch, engine, tmp_path)
    monkeypatch.setattr(audit_runner, "_execute", lambda *_: (0, False, "", outputs))

    def fail_preparation(*_args, **_kwargs):
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(audit_runner, "_prepare_quality_artifact", fail_preparation)

    audit_runner.run_audit_job(job_id, run_id)

    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.REVIEW
        assert len(job.outputs) == 2
        assert list(db.scalars(select(AuditQualityReview))) == []


def test_runner_finalizes_when_adapter_returns_error_review(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = _database(tmp_path)
    job_id, (run_id,) = _seed_processing_job(engine, tmp_path)
    outputs = _outputs(tmp_path)
    artifact = _artifact(
        tmp_path,
        run_id,
        outputs,
        controller_status="ERROR",
        error_code="REPORT_INVALID",
        error_message="fresh report missing",
    )
    _configure(monkeypatch, engine, tmp_path)
    monkeypatch.setattr(audit_runner, "_execute", lambda *_: (0, False, "", outputs))
    monkeypatch.setattr(audit_runner, "_prepare_quality_artifact", lambda *_: artifact)

    audit_runner.run_audit_job(job_id, run_id)

    with session_factory() as db:
        job = db.get(Job, job_id)
        review = db.scalar(select(AuditQualityReview))
        assert job is not None
        assert job.status == JobStatus.REVIEW
        assert review is not None
        assert review.controller_status == "ERROR"
        assert json.loads(review.quality_aggregate)["error_reason"] == "fresh report missing"


def test_source_digest_is_deterministic_and_includes_optional_sources(tmp_path: Path) -> None:
    input_bytes = b"%PDF-1.4\ninput"
    estimate_bytes = b"estimate"
    narrative_bytes = b"narrative"
    input_path = tmp_path / "input.pdf"
    estimate_path = tmp_path / "estimate.xlsx"
    narrative_path = tmp_path / "narrative.docx"
    input_path.write_bytes(input_bytes)
    estimate_path.write_bytes(estimate_bytes)
    narrative_path.write_bytes(narrative_bytes)
    job = SimpleNamespace(
        input_file_path=str(input_path),
        estimate_input=SimpleNamespace(file_path=str(estimate_path)),
        narrative_input=SimpleNamespace(file_path=str(narrative_path)),
    )
    expected = quality.canonical_manifest_digest(
        [
            quality.ManifestEntry(
                "source/input.pdf",
                hashlib.sha256(input_bytes).hexdigest(),
                len(input_bytes),
            ),
            quality.ManifestEntry(
                "source/estimate",
                hashlib.sha256(estimate_bytes).hexdigest(),
                len(estimate_bytes),
            ),
            quality.ManifestEntry(
                "source/narrative",
                hashlib.sha256(narrative_bytes).hexdigest(),
                len(narrative_bytes),
            ),
        ]
    )

    assert audit_runner._source_digest(job) == expected
    assert audit_runner._source_digest(job) == expected

    input_path.write_bytes(b"%PDF-1.4\nchanged")
    assert audit_runner._source_digest(job) != expected


def test_bundled_manifest_matches_tracked_source() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert audit_runner._BUNDLED_SKILL_MANIFEST.read_bytes() == (
        repo_root / "docs/plans/BOQ_AUDIT_SKILL_MANIFEST.json"
    ).read_bytes()


def test_quality_artifact_uses_contract_controller_root(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "input.pdf"
    source.write_bytes(b"source")
    settings = SimpleNamespace(
        jobs_dir=tmp_path / "jobs",
        data_dir=tmp_path / "data",
        boq_audit_skill_path=tmp_path / "skills" / "boq-audit" / "SKILL.md",
    )
    snapshot = SimpleNamespace(snapshot_id="snap-controller", manifest=())
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_bytes(b"{}")
    validation = SimpleNamespace(receipt_path=receipt_path)
    captured = {}
    monkeypatch.setattr(audit_runner, "get_settings", lambda: settings)

    def create_snapshot(source_attempt, controller_root, **kwargs):
        captured["controller_root"] = controller_root
        return snapshot

    def validate_snapshot(*args, **kwargs):
        captured["validation_kwargs"] = kwargs
        return validation

    monkeypatch.setattr(quality, "create_snapshot", create_snapshot)
    monkeypatch.setattr(quality, "validate_snapshot", validate_snapshot)
    job = SimpleNamespace(
        job_code="BOQ-000001",
        input_file_path=str(source),
        estimate_input=None,
        narrative_input=None,
    )

    artifact = audit_runner._prepare_quality_artifact(job, 1, 42)

    assert captured["controller_root"] == settings.data_dir / "controller"
    assert captured["validation_kwargs"]["stage"] == "release"
    assert captured["validation_kwargs"]["run_id"] == 42
    assert artifact.receipt_digest == hashlib.sha256(b"{}").hexdigest()
