from conftest import ASGIClient, login
from test_jobs import create_job


def test_customer_management_permissions(client, owner_client):
    anonymous = ASGIClient()
    for method, path, kwargs in [
        ("GET", "/api/admin/users", {}),
        ("POST", "/api/admin/users", {"json": {"name": "New", "username": "new", "password": "new-password-123"}}),
        ("PATCH", "/api/admin/users/2", {"json": {"is_active": False}}),
        ("DELETE", "/api/admin/users/2", {}),
    ]:
        assert anonymous.request(method, path, **kwargs).status_code == 401
        assert owner_client.request(method, path, **kwargs).status_code == 403


def test_account_lifecycle_and_password_revocation(admin_client):
    path = "/api/admin/users"
    payload = {"name": "  New User  ", "username": "  NEWUSER  ", "password": "first-password-123"}
    created = admin_client.post(path, json=payload)
    assert created.status_code == 201
    user = created.json()
    assert user["name"] == "New User" and user["username"] == "newuser"
    assert user["role"] == "CUSTOMER" and user["job_count"] == 0
    assert "password" not in created.text and "hash" not in created.text
    assert admin_client.post(path, json=payload).status_code == 409
    assert admin_client.post(path, json={**payload, "role": "ADMIN"}).status_code == 422
    assert admin_client.post(path, json={**payload, "password": "short"}).status_code == 422
    target = f"{path}/{user['id']}"
    customer = ASGIClient()
    assert login(customer, "newuser", payload["password"]).status_code == 200
    assert admin_client.patch(target, json={"name": "Renamed"}).status_code == 200
    assert customer.get("/api/auth/me").status_code == 200
    assert admin_client.patch(target, json={"password": "second-password-123", "username": "renamed"}).status_code == 200
    assert customer.get("/api/auth/me").status_code == 401
    assert login(customer, "renamed", payload["password"]).status_code == 401
    assert login(customer, "renamed", "second-password-123").status_code == 200
    assert admin_client.patch(target, json={"is_active": False}).status_code == 200
    assert customer.get("/api/auth/me").status_code == 401
    assert login(customer, "renamed", "second-password-123").status_code == 401
    assert admin_client.patch(target, json={"is_active": True}).status_code == 200
    assert customer.get("/api/auth/me").status_code == 401
    assert login(customer, "renamed", "second-password-123").status_code == 200
    assert admin_client.delete(target).status_code == 204
    assert customer.get("/api/auth/me").status_code == 401
    assert all(item["id"] != user["id"] for item in admin_client.get(path).json())
    assert admin_client.delete(target).status_code == 404


def test_validation_admin_protection_and_project_preservation(admin_client, pdf_bytes):
    owner_client = ASGIClient()
    assert login(owner_client, "owner", "owner-pass-123").status_code == 200
    path = "/api/admin/users"
    users = admin_client.get(path).json()
    assert len(users) == 2 and all(user["role"] == "CUSTOMER" for user in users)
    owner = next(user for user in users if user["username"] == "owner")
    target = f"{path}/{owner['id']}"
    for payload in [{"name": "   "}, {"username": "has space"}, {"password": "short"},
                    {"password": None}, {"name": None}, {"username": None}, {"is_active": None}, {"role": "ADMIN"}]:
        assert admin_client.patch(target, json=payload).status_code == 422
    assert admin_client.patch(target, json={"username": "OTHER"}).status_code == 409
    admin_id = admin_client.get("/api/auth/me").json()["id"]
    assert admin_client.patch(f"{path}/{admin_id}", json={"is_active": False}).status_code == 404
    assert admin_client.delete(f"{path}/{admin_id}").status_code == 404
    assert admin_client.patch(f"{path}/9999", json={"name": "Missing"}).status_code == 404
    code = create_job(owner_client, pdf_bytes).json()["job_code"]
    assert admin_client.delete(target).status_code == 409
    assert next(user for user in admin_client.get(path).json() if user["id"] == owner["id"])["job_count"] == 1
    assert owner_client.get(f"/api/jobs/{code}/input/download").content == pdf_bytes
    assert admin_client.patch(target, json={"is_active": False}).status_code == 200
    assert owner_client.get(f"/api/jobs/{code}").status_code == 401
    assert admin_client.get(f"/api/admin/jobs/{code}/input/download").content == pdf_bytes
