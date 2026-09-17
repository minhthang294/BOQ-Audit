import os
import asyncio
from pathlib import Path

import httpx2 as httpx
import pytest

TEST_DATA = Path("/tmp/boq-audit-tests")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{TEST_DATA / 'test.db'}",
        "DATA_DIR": str(TEST_DATA),
        "BACKEND_SECRET_KEY": "test-secret-key-with-enough-entropy",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "admin-pass-123",
        "DEMO_USERNAME": "owner",
        "DEMO_PASSWORD": "owner-pass-123",
        "MAX_UPLOAD_MB": "1",
    }
)

from app.auth.security import hash_password
from app.core.database import Base, SessionLocal, engine
from app.main import app
from app.models.entities import User, UserRole


@pytest.fixture(autouse=True)
def clean_database():
    TEST_DATA.mkdir(parents=True, exist_ok=True)
    Base.metadata.drop_all(engine)
    jobs_dir = TEST_DATA / "jobs"
    if jobs_dir.exists():
        for path in sorted(jobs_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add_all(
            [
                User(name="Admin", email="admin", password_hash=hash_password("admin-pass-123"), role=UserRole.ADMIN),
                User(name="Owner", email="owner", password_hash=hash_password("owner-pass-123"), role=UserRole.CUSTOMER),
                User(name="Other", email="other", password_hash=hash_password("other-pass-123"), role=UserRole.CUSTOMER),
            ]
        )
        db.commit()
    yield


@pytest.fixture
def client():
    return ASGIClient()


class ASGIClient:
    """Synchronous facade over HTTPX ASGITransport, avoiding TestClient's Python 3.14 portal issue."""

    def __init__(self):
        self.cookies = httpx.Cookies()

    def request(self, method: str, url: str, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver", cookies=self.cookies) as client:
                response = await client.request(method, url, **kwargs)
                self.cookies.update(response.cookies)
                return response

        return asyncio.run(send())

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request("DELETE", url, **kwargs)


def login(client: ASGIClient, username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.fixture
def owner_client(client):
    assert login(client, "owner", "owner-pass-123").status_code == 200
    return client


@pytest.fixture
def admin_client(client):
    assert login(client, "admin", "admin-pass-123").status_code == 200
    return client


@pytest.fixture
def pdf_bytes():
    return b"%PDF-1.4\n% minimal test pdf\n%%EOF"
