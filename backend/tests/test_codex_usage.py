from conftest import login

from app.api import jobs
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


def test_codex_usage_requires_login_and_returns_public_snapshot(client, monkeypatch):
    assert client.get("/api/jobs/codex-usage").status_code == 401
    login(client, "owner", "owner-pass-123")
    monkeypatch.setattr(jobs, "get_codex_usage", lambda: {
        "available": True,
        "plan_type": "plus",
        "ordinary_usage_allowed": True,
        "primary": {"used_percent": 37, "remaining_percent": 63, "resets_at": 1_790_746_376, "window_duration_minutes": 300},
        "secondary": None,
        "checked_at": "2026-09-30T10:00:00+00:00",
    })
    response = client.get("/api/jobs/codex-usage")
    assert response.status_code == 200
    assert response.json()["primary"]["remaining_percent"] == 63
