import json

from conftest import ASGIClient, login
from app.core.database import SessionLocal
from app.models.entities import AuditQualityReview, AuditQualityReviewEvent, AuditRun, Job, utcnow
from test_jobs import create_job


def _user_client(username):
    client = ASGIClient()
    password = "owner-pass-123" if username == "owner" else "admin-pass-123"
    assert login(client, username, password).status_code == 200
    return client


def _add_review(code, *, attempt=1, aggregate=None, event_type=None):
    with SessionLocal() as db:
        job = db.query(Job).filter_by(job_code=code).one()
        run = AuditRun(job_id=job.id, attempt=attempt, status="COMPLETED", started_at=utcnow(), ended_at=utcnow(), duration_seconds=1)
        db.add(run)
        db.flush()
        review = AuditQualityReview(
            run_id=run.id,
            reviewer_user_id=None,
            snapshot_id=f"snapshot-{attempt}",
            stage="release",
            controller_status="PASS",
            candidate_release_status="READY_FOR_ENGINEER_REVIEW",
            quality_aggregate=json.dumps(aggregate if aggregate is not None else {}),
            source_digest="a" * 64,
            skill_digest=None,
            input_digest="b" * 64,
            receipt_digests="{}",
            output_manifest_digest="c" * 64,
            customer_fields_digest="d" * 64,
            output_manifest="[]",
        )
        db.add(review)
        db.flush()
        if event_type:
            db.add(AuditQualityReviewEvent(review_id=review.id, event_type=event_type, actor_id="system", reason="test"))
        db.commit()
        return review.id


def test_admin_quality_api_requires_admin_and_reports_no_review(pdf_bytes):
    owner = _user_client("owner")
    admin = _user_client("admin")
    code = create_job(owner, pdf_bytes).json()["job_code"]

    assert ASGIClient().get(f"/api/admin/jobs/{code}/quality").status_code == 401
    forbidden = owner.get(f"/api/admin/jobs/{code}/quality")
    assert forbidden.status_code == 403

    response = admin.get(f"/api/admin/jobs/{code}/quality")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["freshness_status"] == "NOT_REVIEWED"
    assert response.json()["quality"] is None
    assert response.json()["coverage_status"] == "UNAVAILABLE"


def test_admin_quality_api_returns_latest_safe_summary_and_invalidation(pdf_bytes):
    owner = _user_client("owner")
    admin = _user_client("admin")
    code = create_job(owner, pdf_bytes).json()["job_code"]
    _add_review(code, aggregate={
        "validator_exit_code": 0,
        "open_record_count": 2,
        "open_issue_count": 1,
        "blocking_checks": ["PASS2_INCOMPLETE"],
        "critical_errors": None,
        "warnings": None,
        "error_code": None,
        "missing_outputs": [],
        "raw_evidence": "must not be exposed",
    }, event_type="QUALITY_INVALIDATED")

    response = admin.get(f"/api/admin/jobs/{code}/quality")

    assert response.status_code == 200
    body = response.json()
    assert body["attempt"] == 1
    assert body["freshness_status"] == "INVALIDATED"
    assert body["invalidation_event_type"] == "QUALITY_INVALIDATED"
    assert body["quality"]["advisory"] is True
    assert body["quality"]["enforcing"] is False
    assert body["quality"]["open_record_count"] == 2
    assert body["quality"]["blocking_checks"] == ["PASS2_INCOMPLETE"]
    assert body["coverage"] is None
    assert "raw_evidence" not in response.text
    assert "source_digest" not in response.text


def test_admin_quality_api_uses_latest_attempt_and_tolerates_invalid_aggregate(pdf_bytes):
    owner = _user_client("owner")
    admin = _user_client("admin")
    code = create_job(owner, pdf_bytes).json()["job_code"]
    _add_review(code, attempt=1, aggregate={"open_record_count": 7})
    _add_review(code, attempt=2, aggregate="malformed")

    response = admin.get(f"/api/admin/jobs/{code}/quality")

    assert response.status_code == 200
    assert response.json()["attempt"] == 2
    assert response.json()["quality"]["open_record_count"] is None
    assert response.json()["freshness_status"] == "NOT_VERIFIED"


def test_admin_quality_api_returns_404_for_unknown_job():
    admin = _user_client("admin")
    assert admin.get("/api/admin/jobs/BOQ-999999/quality").status_code == 404
