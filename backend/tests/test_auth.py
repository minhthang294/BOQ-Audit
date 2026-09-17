from conftest import login


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


def test_unauthenticated_access_rejected(client):
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/admin/jobs").status_code == 401


def test_customer_cannot_call_admin_api(owner_client):
    assert owner_client.get("/api/admin/jobs").status_code == 403
