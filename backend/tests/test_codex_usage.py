from conftest import login
from sqlalchemy import select

from app.api import jobs
from app.core.database import SessionLocal
from app.models.entities import Job, JobStatus, User
from app.services.codex_usage import _public_snapshot


def test_public_snapshot_converts_used_to_remaining_and_drops_account_metadata():
    result = _public_snapshot({
        "accountId": "must-not-be-exposed",
        "ordinaryUsageAllowed": True,
        "rateLimits": {
            "planType": "plus",
            "primary": {"usedPercent": 37, "windowDurationMins": 300, "resetsAt": 1_790_746_376},
            "secondary": {"usedPercent": 16, "windowDurationMins": 10_080, "resetsAt": 1_791_278_445},
        },
    })
    assert result["primary"]["remaining_percent"] == 63
    assert result["secondary"]["remaining_percent"] == 84
    assert result["plan_type"] == "plus"
    assert "accountId" not in result


def test_provider_usage_is_admin_only_and_customer_capacity_is_neutral(client, monkeypatch):
    assert client.get("/api/jobs/codex-usage").status_code == 401
    login(client, "owner", "owner-pass-123")
    calls = 0

    def fake_usage():
        nonlocal calls
        calls += 1
        return {
            "available": True,
            "plan_type": "plus",
            "ordinary_usage_allowed": True,
            "primary": {"used_percent": 37, "remaining_percent": 63, "resets_at": 1_790_746_376, "window_duration_minutes": 300},
            "secondary": None,
            "checked_at": "2026-09-30T10:00:00+00:00",
        }

    monkeypatch.setattr(jobs, "get_codex_usage", fake_usage)
    assert client.get("/api/jobs/codex-usage").status_code == 403
    capacity = client.get("/api/jobs/ai-capacity")
    assert capacity.status_code == 200
    assert set(capacity.json()) == {"available", "ready", "checked_at"}
    assert capacity.json()["available"] is False
    assert calls == 0

    assert login(client, "admin", "admin-pass-123").status_code == 200
    assert client.get("/api/jobs/codex-usage").json()["available"] is False
    assert calls == 0

    with SessionLocal() as db:
        owner_id = db.scalar(select(User.id).where(User.email == "owner"))
        db.add(Job(user_id=owner_id, job_code="BOQ-000001", project_name="Cầu ABC", original_filename="drawing.pdf", input_file_path="/tmp/drawing.pdf", status=JobStatus.PROCESSING))
        db.commit()

    capacity = client.get("/api/jobs/ai-capacity")
    assert capacity.json()["available"] is True
    assert calls == 1
    assert "plus" not in capacity.text
    response = client.get("/api/jobs/codex-usage")
    assert response.status_code == 200
    assert response.json()["primary"]["remaining_percent"] == 63
    assert calls == 2

    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.job_code == "BOQ-000001"))
        job.status = JobStatus.COMPLETED
        db.commit()
    assert client.get("/api/jobs/ai-capacity").json()["available"] is False
    assert client.get("/api/jobs/codex-usage").json()["available"] is False
    assert calls == 2
