import copy

from conftest import ASGIClient, login
from app.core.database import SessionLocal
from app.models.entities import AuditInventoryApproval, Job, JobStatus
import fitz



def _user_client(username):
    client = ASGIClient()
    password = "owner-pass-123" if username == "owner" else "admin-pass-123"
    assert login(client, username, password).status_code == 200
    return client




def _valid_pdf_bytes(pages=1):
    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    data = document.tobytes()
    document.close()
    return data


def _three_pass_inventory(scaffold):
    inventory = copy.deepcopy(scaffold)
    revision_digest = inventory["documents"][0]["revision_digest"]
    dependencies = []
    for pass_no in (1, 2, 3):
        check_id = f"PDF-PASS-{pass_no}"
        inventory["expected_work_items"].append({
            "check_id": check_id,
            "document_id": "pdf",
            "revision_digest": revision_digest,
            "page_ids": ["pdf-P00001"],
            "target_ids": [],
            "check_kind": f"PASS_{pass_no}_PAGE_REVIEW",
            "pass_no": pass_no,
            "dependencies": dependencies.copy(),
        })
        dependencies = [check_id]
    return inventory


def test_inventory_approval_requires_admin_and_allowlist():
    owner = _user_client("owner")
    admin = _user_client("admin")
    code = owner.post("/api/jobs", data={"project_name": "Cầu ABC"}, files={"file": ("drawing.pdf", _valid_pdf_bytes(), "application/pdf")}).json()["job_code"]

    assert ASGIClient().get(f"/api/admin/jobs/{code}/inventory").status_code == 401
    assert owner.get(f"/api/admin/jobs/{code}/inventory").status_code == 403

    response = admin.get(f"/api/admin/jobs/{code}/inventory")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["approval"] is None

    scaffold = response.json()["scaffold"]
    denied = admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": scaffold})
    assert denied.status_code == 403


def test_completed_legacy_job_cannot_gain_retrospective_inventory_approval(monkeypatch):
    owner = _user_client("owner")
    admin = _user_client("admin")
    monkeypatch.setattr("app.api.admin._allowed_inventory_reviewers", lambda: {"admin"})
    code = owner.post("/api/jobs", data={"project_name": "Cầu ABC"},
                      files={"file": ("drawing.pdf", _valid_pdf_bytes(), "application/pdf")}).json()["job_code"]
    inventory = _three_pass_inventory(admin.get(f"/api/admin/jobs/{code}/inventory").json()["scaffold"])
    with SessionLocal() as db:
        job = db.query(Job).filter(Job.job_code == code).one()
        job.status = JobStatus.COMPLETED
        db.commit()
    response = admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": inventory})
    assert response.status_code == 409
    with SessionLocal() as db:
        assert db.query(AuditInventoryApproval).count() == 0


def test_inventory_approval_valid_and_stale_after_source_change(monkeypatch):
    owner = _user_client("owner")
    admin = _user_client("admin")
    monkeypatch.setattr("app.api.admin._allowed_inventory_reviewers", lambda: {"admin"})
    code = owner.post("/api/jobs", data={"project_name": "Cầu ABC"}, files={"file": ("drawing.pdf", _valid_pdf_bytes(), "application/pdf")}).json()["job_code"]

    body = admin.get(f"/api/admin/jobs/{code}/inventory").json()
    scaffold = body["scaffold"]
    empty = admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": scaffold})
    assert empty.status_code == 422
    assert empty.json()["detail"] == "Inventory approval requires at least one expected work item in each audit pass"

    partial = _three_pass_inventory(scaffold)
    partial["expected_work_items"].pop()
    rejected_partial = admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": partial})
    assert rejected_partial.status_code == 422

    reviewed_inventory = _three_pass_inventory(scaffold)
    approved = admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": reviewed_inventory})
    assert approved.status_code == 201
    assert approved.headers["cache-control"] == "private, no-store"
    approval = approved.json()
    assert approval["source_digest"] == body["source_digest"]
    assert approval["is_current"] is True
    assert approval["inventory"] == reviewed_inventory

    current = admin.get(f"/api/admin/jobs/{code}/inventory").json()
    assert current["approval"]["is_current"] is True
    assert current["approval"]["inventory"] == reviewed_inventory

    # Change the source bytes so the stored approval becomes stale.
    with SessionLocal() as db:
        job = db.query(Job).filter_by(job_code=code).one()
        path = job.input_file_path
    with open(path, "ab") as source:
        source.write(b"%tampered")
    stale = admin.get(f"/api/admin/jobs/{code}/inventory").json()
    assert stale["approval"]["is_current"] is False
    assert stale["source_digest"] != stale["approval"]["source_digest"]


def test_inventory_approval_rejects_pass1_missing_a_source_page(monkeypatch):
    owner = _user_client("owner")
    admin = _user_client("admin")
    monkeypatch.setattr("app.api.admin._allowed_inventory_reviewers", lambda: {"admin"})
    code = owner.post("/api/jobs", data={"project_name": "Cầu ABC"}, files={"file": ("drawing.pdf", _valid_pdf_bytes(pages=2), "application/pdf")}).json()["job_code"]
    scaffold = admin.get(f"/api/admin/jobs/{code}/inventory").json()["scaffold"]

    response = admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": _three_pass_inventory(scaffold)})

    assert response.status_code == 422
    assert "Pass 1 work items do not cover every source page" in response.json()["detail"]


def test_inventory_approval_rejects_scope_changes_and_malformed_inventory(monkeypatch):
    owner = _user_client("owner")
    admin = _user_client("admin")
    monkeypatch.setattr("app.api.admin._allowed_inventory_reviewers", lambda: {"admin"})
    code = owner.post("/api/jobs", data={"project_name": "Cầu ABC"}, files={"file": ("drawing.pdf", _valid_pdf_bytes(), "application/pdf")}).json()["job_code"]
    scaffold = admin.get(f"/api/admin/jobs/{code}/inventory").json()["scaffold"]

    missing_page = __import__("copy").deepcopy(scaffold)
    missing_page["pages"] = []
    assert admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": missing_page}).status_code == 422

    extra_page = __import__("copy").deepcopy(scaffold)
    extra_page["pages"].append({"page_id": "pdf-P99999", "document_id": "pdf", "revision_digest": extra_page["pages"][0]["revision_digest"], "pdf_page": 999})
    assert admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": extra_page}).status_code == 422

    changed_digest = __import__("copy").deepcopy(scaffold)
    changed_digest["documents"][0]["revision_digest"] = "sha256:tampered"
    assert admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": changed_digest}).status_code == 422

    malformed = __import__("copy").deepcopy(scaffold)
    malformed["documents"] = "not-a-list"
    assert admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": malformed}).status_code == 422


def test_inventory_approval_is_append_only_history(monkeypatch):
    owner = _user_client("owner")
    admin = _user_client("admin")
    monkeypatch.setattr("app.api.admin._allowed_inventory_reviewers", lambda: {"admin"})
    code = owner.post("/api/jobs", data={"project_name": "Cầu ABC"}, files={"file": ("drawing.pdf", _valid_pdf_bytes(), "application/pdf")}).json()["job_code"]
    scaffold = admin.get(f"/api/admin/jobs/{code}/inventory").json()["scaffold"]

    reviewed_inventory = _three_pass_inventory(scaffold)
    first = admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": reviewed_inventory}).json()
    second = admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": reviewed_inventory}).json()
    assert second["approved_at"] != first["approved_at"]

    with SessionLocal() as db:
        job = db.query(Job).filter_by(job_code=code).one()
        approvals = db.query(AuditInventoryApproval).filter_by(job_id=job.id).all()
        assert len(approvals) == 2

    current = admin.get(f"/api/admin/jobs/{code}/inventory").json()
    assert current["approval"]["approved_at"] == second["approved_at"]


def test_inventory_approval_rejects_source_change_during_review(monkeypatch):
    owner = _user_client("owner")
    admin = _user_client("admin")
    monkeypatch.setattr("app.api.admin._allowed_inventory_reviewers", lambda: {"admin"})
    code = owner.post("/api/jobs", data={"project_name": "Cầu ABC"}, files={"file": ("drawing.pdf", _valid_pdf_bytes(), "application/pdf")}).json()["job_code"]
    scaffold = admin.get(f"/api/admin/jobs/{code}/inventory").json()["scaffold"]

    values = iter(["a" * 64, "a" * 64, "b" * 64])
    monkeypatch.setattr("app.api.admin._source_digest", lambda _job: next(values))
    response = admin.post(f"/api/admin/jobs/{code}/inventory", json={"inventory": _three_pass_inventory(scaffold)})

    assert response.status_code == 409
    assert response.json()["detail"] == "Source files changed; reload the inventory before approval"
