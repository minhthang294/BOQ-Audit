from conftest import ASGIClient, login
from app.core.database import SessionLocal
from app.models.entities import CustomerActiveSession, RateLimitEvent, UserSession


def test_login_success_and_me(client):
    response = login(client, "owner", "owner-pass-123")
    assert response.status_code == 200
    assert response.json()["role"] == "CUSTOMER"
    assert response.json()["username"] == "owner"
    assert "email" not in response.json()
    assert "session" in response.cookies
    assert client.get("/api/auth/me").status_code == 200


def test_login_wrong_password(client):
    response = login(client, "owner", "wrong")
    assert response.status_code == 401
    assert "password_hash" not in response.text


def test_login_rate_limit_after_failed_attempts(client):
    for _ in range(5):
        assert login(client, "owner", "wrong-password").status_code == 401
    limited = login(client, "owner", "wrong-password")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    with SessionLocal() as db:
        assert db.query(RateLimitEvent).filter(RateLimitEvent.scope == "login").count() == 5


def test_logout_revokes_copied_jwt(client):
    response = login(client, "owner", "owner-pass-123")
    assert response.status_code == 200
    copied_token = client.cookies.get("session")
    with SessionLocal() as db:
        assert db.query(UserSession).count() == 1

    assert client.post("/api/auth/logout").status_code == 200
    replay = ASGIClient()
    replay.cookies.set("session", copied_token)
    assert replay.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        assert db.query(UserSession).count() == 0



def test_unauthenticated_access_rejected(client):
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/admin/jobs").status_code == 401


def test_customer_cannot_call_admin_api(owner_client):
    assert owner_client.get("/api/admin/jobs").status_code == 403


def test_second_customer_login_replaces_first_but_failed_login_does_not(owner_client, pdf_bytes):
    from test_jobs import create_job
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    second = ASGIClient()
    assert login(second, "owner", "wrong").status_code == 401
    assert owner_client.get("/api/auth/me").status_code == 200
    assert login(second, "owner", "owner-pass-123").status_code == 200
    rejected = owner_client.get("/api/auth/me")
    assert rejected.status_code == 401
    assert rejected.headers["x-session-reason"] == "replaced"
    for url in ("/api/jobs", f"/api/jobs/{code}/input/download", f"/api/jobs/{code}/chat"):
        assert owner_client.get(url).status_code == 401
    assert create_job(owner_client, pdf_bytes).status_code == 401
    assert second.get("/api/auth/me").status_code == 200
    tab = ASGIClient()
    tab.cookies.set("session", second.cookies.get("session"))
    assert tab.get("/api/auth/me").status_code == 200


def test_concurrent_customer_logins_leave_one_valid_session():
    from concurrent.futures import ThreadPoolExecutor
    clients = [ASGIClient(), ASGIClient()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda c: login(c, "owner", "owner-pass-123"), clients))
    assert [r.status_code for r in responses] == [200, 200]
    assert sorted(c.get("/api/auth/me").status_code for c in clients) == [200, 401]
    with SessionLocal() as db:
        assert db.query(CustomerActiveSession).count() == 1
        assert db.query(UserSession).count() == 1


def test_admin_sessions_and_password_revocation(client, monkeypatch):
    import sys
    from app.cli import main
    other = ASGIClient()
    assert login(client, "admin", "admin-pass-123").status_code == 200
    assert login(other, "admin", "admin-pass-123").status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    monkeypatch.setattr(sys, "argv", ["cli", "set-password", "--username", "admin", "--password", "new-admin-pass-123"])
    main()
    assert client.get("/api/auth/me").status_code == 401
    assert other.get("/api/auth/me").status_code == 401


def test_account_limit_is_shared_across_source_ips(client, monkeypatch):
    from app.api.auth import login_limiter
    # Clear only the IP bucket to simulate distributed sources hitting one account.
    for _ in range(5):
        assert login(client, "owner", "wrong").status_code == 401
        login_limiter.reset()
    assert login(client, "owner", "wrong").status_code == 429
