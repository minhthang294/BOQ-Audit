from app.api import lessons as lesson_api
from app.core.database import SessionLocal
from app.models.entities import User, UserRole
from app.services.audit_lessons import LessonStore
from conftest import ASGIClient, login


def _add_admin(email="reviewer@example.test", name="Lesson Reviewer", password="reviewer-pass-123"):
    from app.auth.security import hash_password

    with SessionLocal() as db:
        reviewer = User(
            name=name, email=email,
            password_hash=hash_password(password), role=UserRole.ADMIN,
        )
        db.add(reviewer)
        db.commit()


def test_private_lesson_candidate_review_and_visibility(tmp_path, monkeypatch):
    _add_admin()
    _add_admin("other-admin@example.test", "Other Admin", "other-admin-pass-123")
    monkeypatch.setattr(lesson_api, "_store", lambda: LessonStore(tmp_path / "lessons"))
    monkeypatch.setattr(lesson_api, "_reviewer_allowlist", lambda: {"reviewer@example.test"})
    creator = ASGIClient()
    assert login(creator, "admin", "admin-pass-123").status_code == 200
    candidate_response = creator.post("/api/admin/lessons", json={
        "error_class": "MISSING_DIMENSION",
        "applicability_check_kinds": ["DETAIL_RECONCILIATION"],
        "trigger_conditions": ["A dimension chain has a gap"],
        "required_checks": ["Recheck the source and recalculate"],
        "counterexamples": ["A later revision supplies the dimension"],
        "source_case_ids": ["case-001"],
        "evidence_refs": ["run-1/page-2"],
        "project_scope": "project-1",
        "designated_reviewer_email": "reviewer@example.test",
    })
    assert candidate_response.status_code == 201, candidate_response.text
    lesson = candidate_response.json()
    assert lesson["status"] == "CANDIDATE"
    assert lesson["visibility_scope"] == "private"
    assert lesson["reviewer_id"] is None

    assert creator.get("/api/admin/lessons").json()["total"] == 1
    other_admin = ASGIClient()
    assert login(other_admin, "other-admin@example.test", "other-admin-pass-123").status_code == 200
    assert other_admin.get(f"/api/admin/lessons/{lesson['lesson_id']}/versions/1").status_code == 404
    assert other_admin.get("/api/admin/lessons").json()["total"] == 0

    reviewer = ASGIClient()
    assert login(reviewer, "reviewer@example.test", "reviewer-pass-123").status_code == 200
    reviewed = reviewer.post(f"/api/admin/lessons/{lesson['lesson_id']}/versions/1/review", json={
        "result": "VERIFIED", "reason": "Đã đối chiếu hồ sơ nguồn và xác nhận bài học.",
        "evidence_refs": ["checked-run-1/page-2"],
    })
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "VERIFIED"
    assert reviewed.json()["reviewer_id"] == "reviewer@example.test"
    assert reviewer.get("/api/admin/lessons?status=VERIFIED").json()["total"] == 1
    assert LessonStore(tmp_path / "lessons").select_current(lesson["lesson_id"])["version"] == 1


def test_lesson_review_requires_configured_active_admin_and_designated_reviewer(admin_client, tmp_path, monkeypatch):
    monkeypatch.setattr(lesson_api, "_store", lambda: LessonStore(tmp_path / "lessons"))
    monkeypatch.setattr(lesson_api, "_reviewer_allowlist", lambda: {"reviewer@example.test"})
    candidate = admin_client.post("/api/admin/lessons", json={
        "error_class": "MISREAD_SCALE", "trigger_conditions": ["Scale is ambiguous"],
        "required_checks": ["Verify printed scale"], "applicability_check_kinds": ["SCALE_CHECK"], "designated_reviewer_email": "reviewer@example.test",
    })
    assert candidate.status_code == 422
    assert "đang hoạt động" in candidate.json()["detail"]

    _add_admin()
    candidate = admin_client.post("/api/admin/lessons", json={
        "error_class": "MISREAD_SCALE", "trigger_conditions": ["Scale is ambiguous"],
        "required_checks": ["Verify printed scale"], "applicability_check_kinds": ["SCALE_CHECK"], "designated_reviewer_email": "reviewer@example.test",
    })
    assert candidate.status_code == 201, candidate.text
    lesson_id = candidate.json()["lesson_id"]
    denied = admin_client.post(f"/api/admin/lessons/{lesson_id}/versions/1/review", json={
        "result": "VERIFIED", "reason": "Attempt by a non-designated engineer.", "evidence_refs": ["review-check-1"],
    })
    assert denied.status_code == 403
    reviewer = ASGIClient()
    assert login(reviewer, "reviewer@example.test", "reviewer-pass-123").status_code == 200
    incomplete = reviewer.post(f"/api/admin/lessons/{lesson_id}/versions/1/review", json={
        "result": "VERIFIED", "reason": "Too short", "evidence_refs": [],
    })
    assert incomplete.status_code == 422
