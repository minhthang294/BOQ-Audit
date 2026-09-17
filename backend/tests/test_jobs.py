from conftest import login
from app.core.config import get_settings


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


def test_complete_requires_both_outputs(admin_client, pdf_bytes):
    admin_client.post("/api/auth/logout")
    login(admin_client, "owner", "owner-pass-123")
    code = create_job(admin_client, pdf_bytes).json()["job_code"]
    admin_client.post("/api/auth/logout")
    login(admin_client, "admin", "admin-pass-123")
    assert admin_client.post(f"/api/admin/jobs/{code}/complete").status_code == 409


def test_upload_security(owner_client, pdf_bytes):
    traversal = create_job(owner_client, pdf_bytes, "../../secret.pdf")
    assert traversal.status_code == 201
    assert traversal.json()["original_filename"] == "secret.pdf"
    wrong_ext = create_job(owner_client, pdf_bytes, "malware.exe")
    assert wrong_ext.status_code == 415
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
