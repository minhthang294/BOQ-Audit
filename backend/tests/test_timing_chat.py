from datetime import timedelta, timezone

from conftest import ASGIClient, login
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.migrations import migrate_and_recover
from app.models.entities import AuditRun, ChatConversation, ChatMessage, CustomerActiveSession, Job, JobOutput, JobStatus, OutputType, utcnow
from app.services import audit_runner, project_chat
from test_jobs import create_job


def test_run_history_retry_and_actual_worker_start(owner_client, pdf_bytes, monkeypatch):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        job_id = job.id
        job.created_at = utcnow() - timedelta(minutes=10)
        assert job.started_at is None
        db.commit()
    clock = iter([10, 15, 20, 27])
    monkeypatch.setattr(audit_runner, "monotonic", lambda: next(clock))
    def finish(job_id):
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED if len(job.audit_runs) == 1 else JobStatus.COMPLETED
            job.completed_at = utcnow()
            db.commit()
    monkeypatch.setattr(audit_runner, "_run_audit_job", finish)
    audit_runner.run_audit_job(job_id)
    first = owner_client.get(f"/api/jobs/{code}").json()
    assert first["ai_processing_seconds"] == 5
    assert first["turnaround_seconds"] >= 600
    assert first["audit_runs"][0]["started_at"] != first["created_at"]
    assert owner_client.post(f"/api/jobs/{code}/retry").status_code == 200
    audit_runner.run_audit_job(job_id)
    result = owner_client.get(f"/api/jobs/{code}").json()
    assert result["ai_processing_seconds"] == 12
    assert result["ai_timing_complete"]
    assert len(result["audit_runs"]) == 2
    assert result["current_attempt_seconds"] is None
    assert result["turnaround_seconds"] == owner_client.get(f"/api/jobs/{code}").json()["turnaround_seconds"]


def test_worker_exception_is_timed(owner_client, pdf_bytes, monkeypatch):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job_id = db.scalar(select(Job.id).where(Job.job_code == code))
    def fail(_):
        raise RuntimeError("simulated worker failure")
    monkeypatch.setattr(audit_runner, "_run_audit_job", fail)
    audit_runner.run_audit_job(job_id)
    job = owner_client.get(f"/api/jobs/{code}").json()
    assert job["status"] == "FAILED"
    assert job["audit_runs"][0]["status"] == "FAILED"
    assert job["ai_processing_seconds"] is not None


def test_restart_recovery_and_existing_session_migration(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        job.status = JobStatus.PROCESSING
        db.add(AuditRun(job=job, attempt=1, started_at=utcnow() - timedelta(hours=1)))
        conversation = ChatConversation(job_id=job.id, user_id=job.user_id, context_version=project_chat.context_version(job))
        db.add(conversation)
        db.flush()
        db.add(ChatMessage(job_id=job.id, role="user", content="test", status="PENDING", context_version=conversation.context_version))
        db.query(CustomerActiveSession).delete()
        db.commit()
    migrate_and_recover()
    migrate_and_recover()
    assert owner_client.get("/api/auth/me").status_code == 200
    job = owner_client.get(f"/api/jobs/{code}").json()
    assert job["status"] == "FAILED"
    assert job["audit_runs"][0]["status"] == "INTERRUPTED"
    assert not job["ai_timing_complete"]
    assert job["audit_runs"][0]["duration_seconds"] is None
    with SessionLocal() as db:
        assert db.query(ChatMessage).one().status == "FAILED"


def test_review_and_reopen_keep_execution_time_separate(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        job.status = JobStatus.COMPLETED
        job.completed_at = utcnow() - timedelta(minutes=5)
        db.add(AuditRun(job=job, attempt=1, started_at=utcnow() - timedelta(minutes=10), ended_at=utcnow() - timedelta(minutes=9), duration_seconds=60, status="COMPLETED"))
        db.commit()
    admin = ASGIClient()
    login(admin, "admin", "admin-pass-123")
    reopened = admin.patch(f"/api/admin/jobs/{code}", json={"status": "REVIEW"}).json()
    assert reopened["completed_at"] is None
    assert reopened["ai_processing_seconds"] == 60
    assert reopened["current_attempt_seconds"] is None


def test_chat_history_ownership_context_and_metadata(owner_client, pdf_bytes, monkeypatch):
    from app.api import chat
    monkeypatch.setattr(chat.settings, "chat_gateway_token", "test-gateway-token-32-characters-long")
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    seen = []
    def answer(job, thread_id, version, history, message):
        seen.append((project_chat.public_context(job), thread_id, history))
        return {"thread_id": "private-thread", "text": "Hồ sơ đang chờ xử lý."}
    monkeypatch.setattr(chat, "gateway_reply", answer)
    response = owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": "Tiến độ thế nào?"})
    assert response.status_code == 200
    assert len(response.json()["messages"]) == 2
    assert "private-thread" not in response.text
    assert "thread_id" not in response.text
    assert owner_client.get(f"/api/jobs/{code}/chat").json()["messages"] == response.json()["messages"]
    assert "summary" not in seen[0][0]
    assert owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": "Tiếp tục", "thread_id": "another-user"}).status_code == 422
    assert owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": " "}).status_code == 422
    other = ASGIClient()
    login(other, "other", "other-pass-123")
    assert other.get(f"/api/jobs/{code}/chat").status_code == 404
    assert other.post(f"/api/jobs/{code}/chat/messages", json={"message": "Hi"}).status_code == 404
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        job.status = JobStatus.REVIEW
        job.customer_notes = "Draft must stay private"
        job.admin_notes = "Secret internal notes"
        db.commit()
    assert owner_client.get(f"/api/jobs/{code}/chat").json()["messages"] == []
    assert owner_client.get(f"/api/jobs/{code}").json()["customer_notes"] is None
    assert owner_client.patch(f"/api/jobs/{code}", json={"project_name": "New"}).json()["customer_notes"] is None
    assert owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": "Nội dung?"}).status_code == 200
    assert seen[-1][1] is None
    assert "Draft" not in str(seen[-1]) and "Secret" not in str(seen[-1])


def test_chat_failure_busy_limits_and_disable(owner_client, pdf_bytes, monkeypatch):
    from app.api import chat
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    monkeypatch.setattr(chat.settings, "chat_gateway_token", "")
    assert not owner_client.get(f"/api/jobs/{code}/chat").json()["enabled"]
    assert owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": "Hi"}).status_code == 503
    monkeypatch.setattr(chat.settings, "chat_gateway_token", "test-gateway-token-32-characters-long")
    def fail(*args):
        raise TimeoutError("provider-secret-and-private-path")
    monkeypatch.setattr(chat, "gateway_reply", fail)
    failed = owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": "Hi"})
    assert failed.status_code == 503
    assert "provider-secret" not in failed.text
    history = owner_client.get(f"/api/jobs/{code}/chat").json()
    assert history["messages"][0]["status"] == "FAILED"
    assert not history["busy"]
    chat.turn_lock.acquire()
    try:
        assert owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": "Hi"}).status_code == 409
    finally:
        chat.turn_lock.release()
    monkeypatch.setattr(chat.limiter, "limit", 1)
    assert owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": "Hi"}).status_code == 429


def test_completed_chat_context_includes_only_approved_evidence(owner_client, pdf_bytes):
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        job.status = JobStatus.COMPLETED
        job.customer_notes = "<p>Approved result</p>"
        job.admin_notes = "secret internal notes"
        result = project_chat.report_context(job)
        assert result["summary"] == "Approved result"
        assert "secret" not in str(result)
        assert "not available" in result["evidence_scope"]
        # A replacement report can reuse SQLite's last ID and the same filename.
        output = JobOutput(job=job, file_type=OutputType.ANNOTATED_PDF, original_filename="report.pdf", stored_filename="report.pdf", file_path="/not-used/report.pdf", file_size=100)
        db.add(output)
        db.flush()
        previous_version = project_chat.context_version(job)
        output.created_at += timedelta(seconds=1)
        assert project_chat.context_version(job) != previous_version


def test_chat_rechecks_session_after_slow_reply(owner_client, pdf_bytes, monkeypatch):
    from app.api import chat
    monkeypatch.setattr(chat.settings, "chat_gateway_token", "test-gateway-token-32-characters-long")
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    replacement = ASGIClient()
    def replace(*args):
        assert login(replacement, "owner", "owner-pass-123").status_code == 200
        return {"thread_id": "private-thread", "text": "Result"}
    monkeypatch.setattr(chat, "gateway_reply", replace)
    response = owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": "Hi"})
    assert response.status_code == 401
    assert response.headers["x-session-reason"] == "replaced"
    result = replacement.get(f"/api/jobs/{code}/chat").json()
    assert not result["busy"]
    assert result["messages"][0]["status"] == "FAILED"


def test_chat_discards_answer_when_project_is_reopened(owner_client, pdf_bytes, monkeypatch):
    from app.api import chat
    monkeypatch.setattr(chat.settings, "chat_gateway_token", "test-gateway-token-32-characters-long")
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == code))
        job.status = JobStatus.COMPLETED
        job.customer_notes = "Approved result"
        db.commit()
    def reopen(*args):
        with SessionLocal() as db:
            job = db.scalar(select(Job).where(Job.job_code == code))
            job.status = JobStatus.REVIEW
            job.completed_at = None
            db.commit()
        return {"thread_id": "private-thread", "text": "Previously approved result"}
    monkeypatch.setattr(chat, "gateway_reply", reopen)
    response = owner_client.post(f"/api/jobs/{code}/chat/messages", json={"message": "Giải thích kết quả"})
    assert response.status_code == 409
    assert "Previously approved" not in response.text
    assert owner_client.get(f"/api/jobs/{code}/chat").json()["messages"] == []
