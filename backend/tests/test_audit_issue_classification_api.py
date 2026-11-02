from conftest import ASGIClient, login
from app.api import admin as admin_api
from app.core.database import SessionLocal
from app.models.entities import AuditIssueClassification, AuditQualityReview, AuditQualityReviewEvent, AuditRun, Job, JobStatus
from test_jobs import create_job


def test_issue_classification_is_reviewer_only_and_bound_to_current_snapshot(owner_client, pdf_bytes, monkeypatch):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job = db.query(Job).filter_by(job_code=code).one()
        job.status = JobStatus.REVIEW
        run = AuditRun(job_id=job.id, attempt=1, status=JobStatus.REVIEW.value)
        db.add(run); db.flush()
        review = AuditQualityReview(
            run_id=run.id, reviewer_user_id=None, snapshot_id="prior-issue-signoff", stage="release",
            controller_status="PASS", candidate_release_status="READY_FOR_ENGINEER_REVIEW",
            quality_aggregate="{}", source_digest="a" * 64, skill_digest="b" * 64,
            input_digest="c" * 64, receipt_digests="{}", output_manifest_digest="d" * 64,
            customer_fields_digest="e" * 64, output_manifest="[]",
        )
        db.add(review); db.flush()
        db.add(AuditQualityReviewEvent(review_id=review.id, event_type="SIGNOFF_ACCEPTED", actor_id="reviewer", reason="synthetic"))
        db.commit()
        run_id = run.id
        review_id = review.id
    admin = ASGIClient()
    assert login(admin, "admin", "admin-pass-123").status_code == 200
    monkeypatch.setattr(admin_api, "_allowed_inventory_reviewers", lambda: {"admin"})
    digest = ["a" * 64]
    issue = {
        "record_type": "issues", "record_id": "I-01",
        "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": []},
        "payload": {"issue_id": "I-01", "claim": "Sai số lượng tại tờ 1", "final_status": "CONFIRMED_ERROR"},
    }
    closed_issue = {
        **issue, "record_id": "I-02",
        "payload": {"issue_id": "I-02", "claim": "Giả thuyết sai đã đóng", "final_status": "FALSE_POSITIVE"},
    }
    monkeypatch.setattr(admin_api, "_current_report_source", lambda db, _job: (
        db.get(AuditRun, run_id), {"pass_no": 3},
        {"snapshot_digest": digest[0], "ledger_records": [issue, closed_issue], "projection": {"issues": [issue["payload"], closed_issue["payload"]]}},
    ))
    list_url = f"/api/admin/jobs/{code}/checkpoint/issues"
    post_url = f"{list_url}/I-01/classification"
    body = {"snapshot_digest": digest[0], "final_status": "CONFIRMED_ERROR", "severity": "HIGH", "reason": "Đã kiểm lại số lượng tại tờ 1"}
    assert owner_client.get(list_url).status_code == 403
    assert owner_client.post(post_url, json=body).status_code == 403
    assert admin.get(f"{list_url}?offset=1&limit=1").json()["issues"][0]["issue_id"] == "I-02"
    initial = admin.get(list_url).json()
    assert initial["issues"][0]["classified"] is False
    assert admin.post(post_url, json={**body, "severity": None}).status_code == 409
    assert admin.post(post_url, json={**body, "final_status": "FALSE_POSITIVE"}).status_code == 409
    assert admin.post(post_url, json=body).status_code == 200
    assert admin.post(post_url, json=body).status_code == 409
    assert admin.get(list_url).json()["issues"][0]["severity"] == "HIGH"
    assert admin.get(list_url).json()["classification_digest"] != initial["classification_digest"]
    assert admin.post(post_url, json={**body, "severity": "MEDIUM", "reason": "Đối chiếu lại nguồn và hạ mức độ"}).status_code == 200
    assert admin.get(list_url).json()["issues"][0]["severity"] == "MEDIUM"
    closed_url = f"{list_url}/I-02/classification"
    closed_body = {"snapshot_digest": digest[0], "final_status": "FALSE_POSITIVE", "severity": None, "reason": "Đã đối chiếu nguồn và bác bỏ giả thuyết"}
    assert admin.post(closed_url, json={**closed_body, "severity": "LOW"}).status_code == 409
    assert admin.post(closed_url, json=closed_body).status_code == 200
    with SessionLocal() as db:
        assert [row.revision_no for row in db.query(AuditIssueClassification).filter_by(run_id=run_id, issue_id="I-01").order_by(AuditIssueClassification.id)] == [1, 2]
        assert [event.event_type for event in db.query(AuditQualityReviewEvent).filter_by(review_id=review_id).order_by(AuditQualityReviewEvent.event_id)] == ["SIGNOFF_ACCEPTED", "REVOKED"]
    digest[0] = "b" * 64
    assert admin.get(list_url).json()["issues"][0]["classified"] is False
    assert admin.post(post_url, json=body).status_code == 409
