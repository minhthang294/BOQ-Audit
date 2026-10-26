import json
from uuid import uuid4

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import (
    AuditQualityReview,
    AuditQualityReviewEvent,
    AuditRun,
    Job,
    JobOutput,
    JobStatus,
    OutputType,
    utcnow,
)
from app.services import audit_quality as quality
from app.services import audit_quality_invalidation as invalidation
from test_jobs import create_job


def _job_id(code: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(Job.id).where(Job.job_code == code))


def _set_status(code: str, status: JobStatus) -> None:
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        job.status = status
        db.commit()


def _create_review(
    code: str,
    *,
    attempt: int = 1,
    event_type: str | None = None,
    run_id: int | None = None,
) -> tuple[int, int]:
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        if run_id is None:
            run = AuditRun(
                job_id=job.id,
                attempt=attempt,
                status="COMPLETED",
                started_at=utcnow(),
                ended_at=utcnow(),
                duration_seconds=1.0,
            )
            db.add(run)
            db.flush()
        else:
            run = db.get(AuditRun, run_id)
        review = AuditQualityReview(
            run_id=run.id,
            reviewer_user_id=job.user_id,
            snapshot_id=f"snap-{uuid4().hex}",
            stage="release",
            controller_status="PASS",
            candidate_release_status=quality.RELEASE_READY_STATUS,
            quality_aggregate=json.dumps({"open_record_count": 0, "blocking_checks": []}),
            source_digest="a" * 64,
            skill_digest=None,
            input_digest="c" * 64,
            receipt_digests=json.dumps({"release": "f" * 64}),
            output_manifest_digest="d" * 64,
            customer_fields_digest="e" * 64,
            output_manifest="[]",
        )
        db.add(review)
        db.flush()
        if event_type is not None:
            db.add(
                AuditQualityReviewEvent(
                    review_id=review.id,
                    event_type=event_type,
                    actor_id="1",
                    reason="seed",
                )
            )
        db.commit()
        return review.id, run.id


def _current_review_id(code: str) -> int:
    with SessionLocal() as db:
        review = invalidation.latest_review(db, _job_id(code))
        assert review is not None
        return review.id


def _events(code: str) -> list[AuditQualityReviewEvent]:
    review_id = _current_review_id(code)
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(AuditQualityReviewEvent)
                .where(AuditQualityReviewEvent.review_id == review_id)
                .order_by(AuditQualityReviewEvent.event_id)
            )
        )


def _add_attempt_output(
    code: str,
    *,
    name: str = "BOQ_Audit_Report.xlsx",
    file_type: OutputType = OutputType.EXCEL_REPORT,
    content: bytes = b"attempt",
) -> tuple[object, int]:
    path = get_settings().jobs_dir / code / "attempts" / "1" / "output" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        output = JobOutput(
            job_id=job.id,
            file_type=file_type,
            original_filename=name,
            stored_filename=name,
            file_path=str(path),
            file_size=len(content),
        )
        db.add(output)
        db.commit()
        db.refresh(output)
        return path, output.id


def _add_required_outputs(code: str, pdf_bytes: bytes) -> None:
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        for name, file_type, content in (
            ("BOQ_Audit_Report.xlsx", OutputType.EXCEL_REPORT, b"PK\x03\x04report"),
            ("Annotated_Original.pdf", OutputType.ANNOTATED_PDF, pdf_bytes),
        ):
            path = get_settings().jobs_dir / code / "output" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            db.add(
                JobOutput(
                    job_id=job.id,
                    file_type=file_type,
                    original_filename=name,
                    stored_filename=name,
                    file_path=str(path),
                    file_size=len(content),
                )
            )
        db.commit()


def test_invalidation_appends_revoked_for_accepted_signoff(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _set_status(code, JobStatus.COMPLETED)
    _create_review(code, event_type=invalidation.SIGNOFF_ACCEPTED)
    job_id = _job_id(code)

    with SessionLocal() as db:
        event = invalidation.record_mutation_invalidation(db, job_id, 42, "unit mutation")
        repeated = invalidation.record_mutation_invalidation(
            db, job_id, 42, "repeated mutation"
        )
        assert event is not None
        event_details = (event.event_type, event.actor_id, event.reason)
        db.commit()
        assert event_details == (invalidation.REVOKED, "42", "unit mutation")
        assert repeated is None

    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.COMPLETED
    assert [event.event_type for event in _events(code)] == [
        invalidation.SIGNOFF_ACCEPTED,
        invalidation.REVOKED,
    ]


def test_invalidation_is_idempotent_without_accepted_signoff(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _set_status(code, JobStatus.REVIEW)
    _create_review(code)
    job_id = _job_id(code)

    with SessionLocal() as db:
        first = invalidation.record_mutation_invalidation(db, job_id, "owner", "first")
        second = invalidation.record_mutation_invalidation(db, job_id, "owner", "second")
        assert first is not None
        first_type = first.event_type
        assert second is None
        db.commit()
    assert first_type == invalidation.QUALITY_INVALIDATED
    assert [event.event_type for event in _events(code)] == [invalidation.QUALITY_INVALIDATED]


def test_invalidation_uses_highest_review_on_newest_run(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _set_status(code, JobStatus.REVIEW)
    old_review_id, _ = _create_review(
        code, attempt=1, event_type=invalidation.SIGNOFF_ACCEPTED
    )
    lower_review_id, newest_run_id = _create_review(code, attempt=2)
    highest_review_id, _ = _create_review(code, attempt=2, run_id=newest_run_id)
    job_id = _job_id(code)

    with SessionLocal() as db:
        event = invalidation.record_mutation_invalidation(db, job_id, "admin", "newest")
        db.commit()
        assert event is not None
        assert event.review_id == highest_review_id

    assert highest_review_id > lower_review_id > old_review_id
    assert [event.event_type for event in _events(code)] == [
        invalidation.QUALITY_INVALIDATED
    ]
    with SessionLocal() as db:
        old_events = list(
            db.scalars(
                select(AuditQualityReviewEvent).where(
                    AuditQualityReviewEvent.review_id == old_review_id
                )
            )
        )
        lower_events = list(
            db.scalars(
                select(AuditQualityReviewEvent).where(
                    AuditQualityReviewEvent.review_id == lower_review_id
                )
            )
        )
    assert [event.event_type for event in old_events] == [invalidation.SIGNOFF_ACCEPTED]
    assert lower_events == []


def test_admin_patch_invalidates_only_real_binding_changes(
    owner_client, admin_client, pdf_bytes
):
    real_changes = [
        {"status": "WAITING_FOR_INFO"},
        {"critical_errors": 3},
        {"warnings": 4},
        {"customer_notes": "Ghi chú khách hàng"},
    ]
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    for attempt, payload in enumerate(real_changes, start=1):
        _set_status(code, JobStatus.REVIEW)
        review_id, _ = _create_review(
            code, attempt=attempt, event_type=invalidation.SIGNOFF_ACCEPTED
        )
        response = admin_client.patch(f"/api/admin/jobs/{code}", json=payload)
        assert response.status_code == 200
        with SessionLocal() as db:
            event = db.scalar(
                select(AuditQualityReviewEvent)
                .where(AuditQualityReviewEvent.review_id == review_id)
                .order_by(AuditQualityReviewEvent.event_id.desc())
                .limit(1)
            )
            assert event is not None
            assert event.event_type == invalidation.REVOKED

    _set_status(code, JobStatus.REVIEW)
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        job.critical_errors = 0
        job.warnings = 0
        job.customer_notes = None
        db.commit()
    review_id, _ = _create_review(
        code,
        attempt=len(real_changes) + 1,
        event_type=invalidation.SIGNOFF_ACCEPTED,
    )
    assert admin_client.patch(
        f"/api/admin/jobs/{code}",
        json={"critical_errors": 0, "warnings": 0, "customer_notes": None},
    ).status_code == 200
    assert admin_client.patch(
        f"/api/admin/jobs/{code}", json={"admin_notes": "ghi chú nội bộ"}
    ).status_code == 200
    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(AuditQualityReviewEvent)
                .where(AuditQualityReviewEvent.review_id == review_id)
                .order_by(AuditQualityReviewEvent.event_id)
            )
        )
        assert [event.event_type for event in events] == [invalidation.SIGNOFF_ACCEPTED]


def test_admin_output_upload_invalidates_and_preserves_attempt_blob(
    owner_client, admin_client, pdf_bytes
):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _set_status(code, JobStatus.REVIEW)
    _create_review(code, event_type=invalidation.SIGNOFF_ACCEPTED)
    attempt_path, old_output_id = _add_attempt_output(code)

    response = admin_client.post(
        f"/api/admin/jobs/{code}/outputs",
        data={"file_type": "EXCEL_REPORT"},
        files={
            "file": (
                "report.xlsx",
                b"PK\x03\x04new",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 201
    assert attempt_path.read_bytes() == b"attempt"
    with SessionLocal() as db:
        assert db.get(JobOutput, old_output_id) is None
    assert [event.event_type for event in _events(code)][-1] == invalidation.REVOKED


def test_admin_output_delete_invalidates_and_preserves_attempt_blob(
    owner_client, admin_client, pdf_bytes
):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _set_status(code, JobStatus.REVIEW)
    _create_review(code, event_type=invalidation.SIGNOFF_ACCEPTED)
    attempt_path, output_id = _add_attempt_output(code)

    response = admin_client.delete(f"/api/admin/jobs/{code}/outputs/{output_id}")

    assert response.status_code == 200
    assert attempt_path.read_bytes() == b"attempt"
    with SessionLocal() as db:
        assert db.get(JobOutput, output_id) is None
    assert [event.event_type for event in _events(code)][-1] == invalidation.REVOKED


def test_customer_rename_invalidates_only_real_change(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _set_status(code, JobStatus.REVIEW)
    _create_review(code, event_type=invalidation.SIGNOFF_ACCEPTED)

    same = owner_client.patch(f"/api/jobs/{code}", json={"project_name": "Cầu ABC"})
    assert same.status_code == 200
    assert [event.event_type for event in _events(code)] == [
        invalidation.SIGNOFF_ACCEPTED
    ]

    changed = owner_client.patch(
        f"/api/jobs/{code}", json={"project_name": "Cầu đã đổi tên"}
    )
    assert changed.status_code == 200
    assert changed.json()["status"] == "REVIEW"
    assert [event.event_type for event in _events(code)][-1] == invalidation.REVOKED


def test_customer_retry_invalidates_and_preserves_attempt_blob(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _set_status(code, JobStatus.FAILED)
    _create_review(code, event_type=invalidation.SIGNOFF_ACCEPTED)
    attempt_path, _ = _add_attempt_output(code)

    response = owner_client.post(f"/api/jobs/{code}/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "SUBMITTED"
    assert attempt_path.read_bytes() == b"attempt"
    with SessionLocal() as db:
        assert db.get(Job, _job_id(code)).outputs == []
    assert [event.event_type for event in _events(code)][-1] == invalidation.REVOKED


def test_complete_is_not_blocked_by_quality_review(owner_client, admin_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _set_status(code, JobStatus.REVIEW)
    _create_review(code, event_type=invalidation.SIGNOFF_ACCEPTED)
    _add_required_outputs(code, pdf_bytes)

    response = admin_client.post(f"/api/admin/jobs/{code}/complete")

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert [event.event_type for event in _events(code)] == [
        invalidation.SIGNOFF_ACCEPTED
    ]


def test_reviewed_jobs_are_not_hard_deletable(owner_client, admin_client, pdf_bytes):
    admin_code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _create_review(admin_code)
    admin_directory = get_settings().jobs_dir / admin_code
    assert admin_client.delete(f"/api/admin/jobs/{admin_code}").status_code == 409
    assert admin_directory.is_dir()

    customer_code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _create_review(customer_code)
    customer_directory = get_settings().jobs_dir / customer_code
    assert owner_client.delete(f"/api/jobs/{customer_code}").status_code == 409
    assert customer_directory.is_dir()


def test_legacy_jobs_without_review_remain_readable_and_deletable(
    owner_client, admin_client, pdf_bytes
):
    customer_code = create_job(owner_client, pdf_bytes).json()["job_code"]
    _set_status(customer_code, JobStatus.COMPLETED)
    assert owner_client.get(f"/api/jobs/{customer_code}").status_code == 200
    customer_directory = get_settings().jobs_dir / customer_code
    assert owner_client.delete(f"/api/jobs/{customer_code}").status_code == 200
    assert not customer_directory.exists()

    admin_code = create_job(owner_client, pdf_bytes).json()["job_code"]
    admin_directory = get_settings().jobs_dir / admin_code
    assert admin_client.delete(f"/api/admin/jobs/{admin_code}").status_code == 200
    assert not admin_directory.exists()
