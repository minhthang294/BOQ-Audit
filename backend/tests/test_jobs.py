from conftest import login
from app.core.config import get_settings
from app.core.database import SessionLocal
from sqlalchemy import text

from app.models.entities import Job, JobStatus


def create_job(client, pdf_bytes, filename="drawing.pdf"):
    return client.post(
        "/api/jobs",
        data={"project_name": "Cầu ABC"},
        files={"file": (filename, pdf_bytes, "application/pdf")},
    )


def test_create_get_and_idor(client, pdf_bytes):
    login(client, "owner", "owner-pass-123")
    created = create_job(client, pdf_bytes)
    assert created.status_code == 201
    code = created.json()["job_code"]
    assert code == "BOQ-000001"
    assert created.json()["status"] == "PROCESSING"
    assert client.get(f"/api/jobs/{code}").status_code == 200
    assert client.get(f"/api/jobs/{code}/input/download").content.startswith(b"%PDF")
    viewed = client.get(f"/api/jobs/{code}/input/view")
    assert viewed.status_code == 200
    assert viewed.headers["content-disposition"].startswith("inline")
    assert viewed.headers["cache-control"] == "private, no-store"

    client.post("/api/auth/logout")
    login(client, "other", "other-pass-123")
    assert client.get(f"/api/jobs/{code}").status_code == 404
    assert client.get(f"/api/jobs/{code}/input/download").status_code == 404
    assert client.get(f"/api/jobs/{code}/input/view").status_code == 404


def test_admin_workflow_and_customer_download(client, pdf_bytes):
    login(client, "owner", "owner-pass-123")
    code = create_job(client, pdf_bytes).json()["job_code"]
    client.post("/api/auth/logout")
    login(client, "admin", "admin-pass-123")
    assert client.get(f"/api/admin/jobs/{code}/input/download").status_code == 200
    updated = client.patch(f"/api/admin/jobs/{code}", json={"status": "PROCESSING", "critical_errors": 7, "warnings": 11})
    assert updated.status_code == 200
    assert updated.json()["started_at"] is not None
    excel = client.post(
        f"/api/admin/jobs/{code}/outputs",
        data={"file_type": "EXCEL_REPORT"},
        files={"file": ("report.xlsx", b"PK\x03\x04fake xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    annotated = client.post(
        f"/api/admin/jobs/{code}/outputs",
        data={"file_type": "ANNOTATED_PDF"},
        files={"file": ("annotated.pdf", pdf_bytes, "application/pdf")},
    )
    assert excel.status_code == 201
    assert annotated.status_code == 201
    completed = client.post(f"/api/admin/jobs/{code}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"

    output_id = excel.json()["id"]
    client.post("/api/auth/logout")
    login(client, "owner", "owner-pass-123")
    result = client.get(f"/api/jobs/{code}")
    assert result.json()["critical_errors"] == 7
    assert client.get(f"/api/jobs/{code}/outputs/{output_id}/download").content.startswith(b"PK")
    annotated_id = annotated.json()["id"]
    viewed = client.get(f"/api/jobs/{code}/outputs/{annotated_id}/view")
    assert viewed.status_code == 200
    assert viewed.content.startswith(b"%PDF")
    assert viewed.headers["content-disposition"].startswith("inline")
    assert client.get(f"/api/jobs/{code}/outputs/{output_id}/view").status_code == 404


def test_customer_outputs_hidden_until_completed_and_cross_customer_idor(client, pdf_bytes):
    login(client, "owner", "owner-pass-123")
    code = create_job(client, pdf_bytes).json()["job_code"]
    client.post("/api/auth/logout")
    login(client, "admin", "admin-pass-123")
    excel = client.post(
        f"/api/admin/jobs/{code}/outputs",
        data={"file_type": "EXCEL_REPORT"},
        files={"file": ("report.xlsx", b"PK\x03\x04fake xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    annotated = client.post(
        f"/api/admin/jobs/{code}/outputs",
        data={"file_type": "ANNOTATED_PDF"},
        files={"file": ("annotated.pdf", pdf_bytes, "application/pdf")},
    ).json()

    # Admin can inspect draft outputs through the authorized output endpoints.
    assert client.get(f"/api/jobs/{code}/outputs").status_code == 200
    assert len(client.get(f"/api/jobs/{code}/outputs").json()) == 2
    assert client.get(f"/api/jobs/{code}/outputs/{excel['id']}/download").status_code == 200

    for draft_status in ("PROCESSING", "REVIEW"):
        assert client.patch(f"/api/admin/jobs/{code}", json={"status": draft_status}).status_code == 200
        client.post("/api/auth/logout")
        login(client, "owner", "owner-pass-123")
        assert client.get(f"/api/jobs/{code}").json()["outputs"] == []
        assert client.get(f"/api/jobs/{code}/outputs").json() == []
        assert client.get(f"/api/jobs/{code}/outputs/{excel['id']}/download").status_code == 404
        assert client.get(f"/api/jobs/{code}/outputs/{annotated['id']}/view").status_code == 404
        client.post("/api/auth/logout")
        login(client, "admin", "admin-pass-123")

    assert client.post(f"/api/admin/jobs/{code}/complete").status_code == 200
    client.post("/api/auth/logout")
    login(client, "owner", "owner-pass-123")
    assert len(client.get(f"/api/jobs/{code}").json()["outputs"]) == 2
    assert len(client.get(f"/api/jobs/{code}/outputs").json()) == 2
    assert client.get(f"/api/jobs/{code}/outputs/{excel['id']}/download").content.startswith(b"PK")
    assert client.get(f"/api/jobs/{code}/outputs/{annotated['id']}/download").content.startswith(b"%PDF")

    # Reopening a completed job immediately revokes customer output access.
    client.post("/api/auth/logout")
    login(client, "admin", "admin-pass-123")
    assert client.patch(f"/api/admin/jobs/{code}", json={"status": "REVIEW"}).status_code == 200
    client.post("/api/auth/logout")
    login(client, "owner", "owner-pass-123")
    assert client.get(f"/api/jobs/{code}").json()["outputs"] == []
    assert client.get(f"/api/jobs/{code}/outputs/{excel['id']}/download").status_code == 404
    client.post("/api/auth/logout")
    login(client, "admin", "admin-pass-123")
    assert client.post(f"/api/admin/jobs/{code}/complete").status_code == 200

    client.post("/api/auth/logout")
    login(client, "other", "other-pass-123")
    assert client.get(f"/api/jobs/{code}").status_code == 404
    assert client.get(f"/api/jobs/{code}/input/download").status_code == 404
    assert client.get(f"/api/jobs/{code}/outputs/{excel['id']}/download").status_code == 404


def test_complete_requires_both_outputs(admin_client, pdf_bytes):
    admin_client.post("/api/auth/logout")
    login(admin_client, "owner", "owner-pass-123")
    code = create_job(admin_client, pdf_bytes).json()["job_code"]
    admin_client.post("/api/auth/logout")
    login(admin_client, "admin", "admin-pass-123")
    assert admin_client.post(f"/api/admin/jobs/{code}/complete").status_code == 409


def test_only_admin_can_delete_job_and_files(client, pdf_bytes):
    login(client, "owner", "owner-pass-123")
    code = create_job(client, pdf_bytes).json()["job_code"]
    job_directory = get_settings().jobs_dir / code
    assert job_directory.is_dir()
    assert client.delete(f"/api/admin/jobs/{code}").status_code == 403

    client.post("/api/auth/logout")
    login(client, "admin", "admin-pass-123")
    deleted = client.delete(f"/api/admin/jobs/{code}")
    assert deleted.status_code == 200
    assert not job_directory.exists()
    assert client.get(f"/api/admin/jobs/{code}").status_code == 404


def test_customer_can_only_have_two_active_jobs(owner_client, pdf_bytes):
    first = create_job(owner_client, pdf_bytes)
    second = create_job(owner_client, pdf_bytes)
    assert first.status_code == 201
    assert second.status_code == 201

    blocked = create_job(owner_client, pdf_bytes)
    assert blocked.status_code == 409
    assert "2 hồ sơ" in blocked.json()["detail"]

    with SessionLocal() as db:
        job = db.query(Job).filter(Job.job_code == first.json()["job_code"]).one()
        job.status = JobStatus.COMPLETED
        db.commit()

    allowed_again = create_job(owner_client, pdf_bytes)
    assert allowed_again.status_code == 201


def test_upload_security(owner_client, pdf_bytes):
    traversal = create_job(owner_client, pdf_bytes, "../../secret.pdf")
    assert traversal.status_code == 201
    assert traversal.json()["original_filename"] == "secret.pdf"
    wrong_ext = create_job(owner_client, pdf_bytes, "malware.exe")
    assert wrong_ext.status_code == 415
    wrong_mime = owner_client.post(
        "/api/jobs",
        data={"project_name": "Cầu ABC"},
        files={"file": ("drawing.pdf", pdf_bytes, "text/plain")},
    )
    assert wrong_mime.status_code == 415
    fake_pdf = create_job(owner_client, b"not a pdf", "fake.pdf")
    assert fake_pdf.status_code == 415
    settings = get_settings()
    original_limit = settings.max_upload_mb
    settings.max_upload_mb = 0
    try:
        too_large = create_job(owner_client, b"%PDF-too-large-for-test-limit", "large.pdf")
        assert too_large.status_code == 413
    finally:
        settings.max_upload_mb = original_limit
    with SessionLocal() as db:
        # Invalid/oversized uploads are removed; only the valid traversal-name
        # upload remains.
        assert db.query(Job).count() == 1


def test_sqlite_pragmas_and_health(client):
    with SessionLocal() as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert db.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert db.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "healthy", "database": "ok", "storage": "ok"}
