import hashlib
from pathlib import Path

import pymupdf
from openpyxl import load_workbook
from io import BytesIO

from conftest import ASGIClient, login
from app.api import admin as admin_api
from app.core.database import SessionLocal
from app.models.entities import AuditAnnotationDraft, AuditAnnotationReviewEvent, AuditExcelReportDraft, AuditExcelReportReviewEvent, AuditRun, Job, JobStatus
from test_jobs import create_job


def test_reviewer_draft_is_persisted_private_and_stale_when_classification_changes(owner_client, monkeypatch):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    pdf = doc.tobytes()
    doc.close()
    code = create_job(owner_client, pdf).json()["job_code"]
    with SessionLocal() as db:
        job = db.query(Job).filter_by(job_code=code).one()
        job.status = JobStatus.REVIEW
        run = AuditRun(job_id=job.id, attempt=1, status=JobStatus.REVIEW.value)
        db.add(run)
        db.commit()
        run_id = run.id
    inventory = {
        "documents": [{"document_id": "pdf", "revision_digest": f"sha256:{hashlib.sha256(pdf).hexdigest()}"}],
        "pages": [{"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": f"sha256:{hashlib.sha256(pdf).hexdigest()}", "pdf_page": 1}],
    }
    issue = {"issue_id": "I-01", "claim": "Sai số lượng", "final_status": "CONFIRMED_ERROR"}
    source = {
        "snapshot_digest": "a" * 64,
        "checkpoint_version": 1,
        "ledger_record_counts": {"issues": 1},
        "issue_final_status_counts": {"CONFIRMED_ERROR": 1},
        "projection": {"issues": [issue]},
        "ledger_records": [{"record_type": "issues", "record_id": "I-01", "check_id": "C-01", "payload": issue, "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": [], "target_ids": []}}],
    }
    classification_digest = ["b" * 64]
    monkeypatch.setattr(admin_api, "_allowed_inventory_reviewers", lambda: {"admin"})
    monkeypatch.setattr(admin_api, "_current_report_source", lambda db, _job: (db.get(AuditRun, run_id), {"pass_no": 3, "inventory": inventory, "ledger": []}, source))
    monkeypatch.setattr(admin_api, "_report_classifications", lambda db, _run_id, _source: {
        "classification_digest": classification_digest[0],
        "decisions": {"I-01": {"severity": "HIGH", "final_status": "CONFIRMED_ERROR", "reason": "Đã kiểm lại nguồn", "reviewer_user_id": 1}},
        "unclassified_issue_count": 0,
    })
    monkeypatch.setattr(admin_api, "calculate_coverage", lambda *_: {"coverage_status": "PARTIAL"})
    mark = {
        "mark_id": "M-01", "issue_id": "I-01", "page_id": "pdf-P00001", "dpi": 144,
        "render_size_px": [1190, 1684], "target_bbox_px": [100, 100, 140, 115],
        "note_bbox_px": [200, 200, 650, 260], "leader_points_px": [[205, 205], [135, 108]],
        "note": "HIGH · I-01 — Sai số lượng.\nKiểm tra bảng kê.",
        "snapshot_digest": source["snapshot_digest"], "classification_digest": classification_digest[0],
    }
    admin_client = ASGIClient()
    assert login(admin_client, "admin", "admin-pass-123").status_code == 200
    source_url = f"/api/admin/jobs/{code}/checkpoint/annotation-source/pdf-P00001?dpi=144"
    assert owner_client.get(source_url).status_code == 403
    source_preview = admin_client.get(source_url)
    assert source_preview.status_code == 200
    assert source_preview.content.startswith(b"\x89PNG")
    assert source_preview.headers["x-render-width"] == "1190"
    assert source_preview.headers["x-audit-snapshot-digest"] == source["snapshot_digest"]
    assert admin_client.get(f"/api/admin/jobs/{code}/checkpoint/annotation-source/missing").status_code == 409
    url = f"/api/admin/jobs/{code}/checkpoint/annotation-drafts"
    assert owner_client.post(url, json={"marks": [mark]}).status_code == 403
    created = admin_client.post(url, json={"marks": [mark]})
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["annotation_qa_status"] == "UNVERIFIED"
    assert payload["is_current"] is True
    assert payload["missing_active_issue_ids"] == []
    with SessionLocal() as db:
        row = db.get(AuditAnnotationDraft, payload["draft_id"])
        assert row is not None
        assert Path(row.file_path).is_file()
        assert hashlib.sha256(Path(row.file_path).read_bytes()).hexdigest() == row.output_pdf_sha256
    download = f"{url}/{payload['draft_id']}/download"
    assert owner_client.get(download).status_code == 403
    assert admin_client.get(download).status_code == 200
    previews = {kind: admin_client.get(f"{url}/{payload['draft_id']}/marks/M-01/preview/{kind}") for kind in ("target", "note", "page")}
    assert all(response.status_code == 200 and response.content.startswith(b"\x89PNG") for response in previews.values())
    review_url = f"{url}/{payload['draft_id']}/marks/M-01/review"
    review = {
        "output_pdf_sha256": payload["output_pdf_sha256"],
        "target_crop_sha256": previews["target"].headers["x-review-image-sha256"],
        "note_crop_sha256": previews["note"].headers["x-review-image-sha256"],
        "page_render_sha256": previews["page"].headers["x-review-image-sha256"],
        "result": "PASS", "reason": "Đã xem crop mục tiêu, ghi chú và toàn trang",
    }
    assert owner_client.post(review_url, json=review).status_code == 403
    assert admin_client.post(review_url, json={**review, "target_crop_sha256": "0" * 64}).status_code == 409
    accepted = admin_client.post(review_url, json=review)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["annotation_qa_status"] == "PASS"
    assert accepted.json()["review_counts"] == {"PASS": 1, "FAIL": 0, "UNVERIFIED": 0}
    assert admin_client.post(review_url, json=review).status_code == 409
    with SessionLocal() as db:
        assert db.query(AuditAnnotationReviewEvent).filter_by(draft_id=payload["draft_id"]).count() == 1
    assert admin_client.get(f"{url}/latest").json()["is_current"] is True
    excel = admin_client.get(f"/api/admin/jobs/{code}/checkpoint/report-draft.xlsx")
    assert excel.status_code == 200, excel.text
    assert excel.headers["x-audit-snapshot-digest"] == source["snapshot_digest"]
    assert excel.headers["x-audit-excel-qa"] == "PASS"
    assert excel.headers["x-audit-excel-sha256"]
    assert excel.headers["x-audit-excel-draft-id"]
    excel_again = admin_client.get(f"/api/admin/jobs/{code}/checkpoint/report-draft.xlsx")
    assert excel_again.headers["x-audit-excel-draft-id"] == excel.headers["x-audit-excel-draft-id"]
    assert excel_again.content == excel.content
    with SessionLocal() as db:
        report_row = db.get(AuditExcelReportDraft, int(excel.headers["x-audit-excel-draft-id"]))
        assert report_row.workbook_sha256 == excel.headers["x-audit-excel-sha256"]
        assert Path(report_row.file_path).is_file()
    report = load_workbook(BytesIO(excel.content))
    assert report.sheetnames[:2] == ["00_Tóm_tắt", "01_Lỗi_kiến_nghị"]
    assert report["10_Đánh_dấu_PDF"]["A2"].value == "M-01"
    annotation_headers = [cell.value for cell in report["10_Đánh_dấu_PDF"][1]]
    assert report["10_Đánh_dấu_PDF"].cell(2, annotation_headers.index("annotation_qa_status") + 1).value == "PASS"
    assert report["10_Đánh_dấu_PDF"].cell(2, annotation_headers.index("severity") + 1).fill.fgColor.rgb.endswith("E53935")
    excel_review_url = f"/api/admin/jobs/{code}/checkpoint/report-drafts/{excel.headers['x-audit-excel-draft-id']}/review"
    review_body = {
        "workbook_sha256": excel.headers["x-audit-excel-sha256"],
        "result": "PASS", "reason": "Đã đối chiếu đầy đủ workbook và nguồn dự toán.",
        "reviewed_sections": ["summary_counts", "ledger_rows", "source_formatting", "severity_colors", "pdf_alignment"],
    }
    assert owner_client.get(excel_review_url).status_code == 403
    assert admin_client.get(excel_review_url).json()["latest_result"] == "UNREVIEWED"
    assert admin_client.post(excel_review_url, json={**review_body, "reviewed_sections": review_body["reviewed_sections"][:-1]}).status_code == 422
    assert admin_client.post(excel_review_url, json={**review_body, "workbook_sha256": "0" * 64}).status_code == 409
    reviewed_excel = admin_client.post(excel_review_url, json=review_body)
    assert reviewed_excel.status_code == 200, reviewed_excel.text
    assert reviewed_excel.json()["latest_result"] == "PASS"
    assert reviewed_excel.json()["reviewed_sections"] == sorted(review_body["reviewed_sections"])
    assert admin_client.post(excel_review_url, json=review_body).status_code == 409
    revised_excel_review = admin_client.post(excel_review_url, json={**review_body, "result": "FAIL", "reason": "Phát hiện nội dung bảng tổng hợp cần kiểm tra lại."})
    assert revised_excel_review.status_code == 200, revised_excel_review.text
    assert revised_excel_review.json()["latest_result"] == "FAIL"
    assert revised_excel_review.json()["revision_no"] == 2
    with SessionLocal() as db:
        assert db.query(AuditExcelReportReviewEvent).filter_by(draft_id=int(excel.headers["x-audit-excel-draft-id"])).count() == 2
    with SessionLocal() as db:
        stored_excel_path = Path(db.get(AuditExcelReportDraft, int(excel.headers["x-audit-excel-draft-id"])).file_path)
    stored_excel_path.write_bytes(stored_excel_path.read_bytes() + b"tamper")
    assert admin_client.get(excel_review_url).json()["is_current"] is False
    repaired_excel = admin_client.get(f"/api/admin/jobs/{code}/checkpoint/report-draft.xlsx")
    assert repaired_excel.status_code == 200
    assert repaired_excel.headers["x-audit-excel-draft-id"] != excel.headers["x-audit-excel-draft-id"]
    assert hashlib.sha256(repaired_excel.content).hexdigest() == repaired_excel.headers["x-audit-excel-sha256"]
    repaired_review_url = f"/api/admin/jobs/{code}/checkpoint/report-drafts/{repaired_excel.headers['x-audit-excel-draft-id']}/review"
    assert admin_client.get(repaired_review_url).json()["latest_result"] == "UNREVIEWED"
    changed_pdf_review = admin_client.post(review_url, json={**review, "result": "FAIL", "reason": "Vị trí mark cần sửa trước khi phát hành."})
    assert changed_pdf_review.status_code == 200, changed_pdf_review.text
    assert admin_client.get(repaired_review_url).json()["is_current"] is False
    assert admin_client.post(repaired_review_url, json={**review_body, "workbook_sha256": repaired_excel.headers["x-audit-excel-sha256"]}).status_code == 409
    classification_digest[0] = "c" * 64
    assert admin_client.get(f"{url}/latest").json()["is_current"] is False
    assert admin_client.get(f"{url}/latest").json()["annotation_qa_status"] == "UNVERIFIED"
    assert admin_client.post(review_url, json=review).status_code == 409
    assert admin_client.post(url, json={"marks": [mark]}).status_code == 409
    classification_digest[0] = "b" * 64
    with SessionLocal() as db:
        path = Path(db.get(AuditAnnotationDraft, payload["draft_id"]).file_path)
    path.write_bytes(path.read_bytes() + b" ")
    assert admin_client.get(f"{url}/latest").json()["is_current"] is False
    assert admin_client.get(download).status_code == 409
