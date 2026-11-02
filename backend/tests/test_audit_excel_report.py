from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from app.services.audit_excel_report import _SHEETS, ReportExportError, build_excel_report_draft, validate_excel_report_draft


def test_report_draft_keeps_source_sheets_styles_and_all_ledger_fields(tmp_path):
    estimate = tmp_path / "estimate.xlsx"
    source_workbook = Workbook()
    original = source_workbook.active
    original.title = "Công trình"
    original["A1"] = "=2+3"
    original["A1"].font = Font(name="Arial", size=14, bold=True)
    original["A1"].fill = PatternFill("solid", fgColor="00FF00")
    original.row_dimensions[2].hidden = True
    original.column_dimensions["B"].hidden = True
    source_workbook.save(estimate)
    issue = {"issue_id": "I-01", "final_status": "CONFIRMED_ERROR", "claim": '=HYPERLINK("https://invalid.example", "click")', "extra_evidence": {"a": 1}}
    source = {
        "snapshot_digest": "a" * 64, "checkpoint_version": 7,
        "projection": {"issues": [issue]},
        "ledger_records": [{"record_type": "issues", "record_id": "I-01", "check_id": "C-01", "source_refs": {"page_ids": ["pdf-P00001"], "sheet_ids": ["xlsx-S00001"], "target_ids": []}, "payload": issue}],
        "ledger_record_counts": {"issues": 1, "page_ledger": 0},
        "issue_final_status_counts": {"CONFIRMED_ERROR": 1},
    }
    decision = {"issue_id": "I-01", "final_status": "CONFIRMED_ERROR", "severity": "HIGH", "reason": "Đã kiểm lại nguồn", "reviewer_user_id": 1}
    classifications = {"classification_digest": "b" * 64, "decisions": {"I-01": decision}, "unclassified_issue_count": 0}
    output = build_excel_report_draft(source=source, classifications=classifications, source_workbook=estimate)
    report = load_workbook(BytesIO(output))
    assert report.sheetnames[:13] == [
        "00_Tóm_tắt", "01_Lỗi_kiến_nghị", "02_Bản_đồ_trang", "03_Dòng_BOQ",
        "04_Liên_kết_tổng_hợp", "05_Tính_toán", "06_Giả_định", "07_Rà_soát_Pass2",
        "08_Rà_soát_Pass3", "09_Kiểm_tra_lùi", "10_Đánh_dấu_PDF", "11_Cổng_độ_phủ", "12_Cách_đọc",
    ]
    assert report.sheetnames[-1] == "Công trình"
    assert report["Công trình"]["A1"].value == "=2+3"
    assert report["Công trình"]["A1"].font.sz == 14
    assert report["Công trình"]["A1"].fill.fgColor.rgb.endswith("00FF00")
    assert report["Công trình"].row_dimensions[2].hidden
    assert report["Công trình"].column_dimensions["B"].hidden
    issues = report["01_Lỗi_kiến_nghị"]
    headings = [cell.value for cell in issues[1]]
    values = {headings[index]: cell.value for index, cell in enumerate(issues[2])}
    assert values["reviewer_severity"] == "HIGH"
    assert issues.cell(2, headings.index("reviewer_severity") + 1).fill.fgColor.rgb.endswith("E53935")
    assert values["claim"].startswith("=HYPERLINK")
    assert issues.cell(2, headings.index("claim") + 1).data_type == "s"
    assert values["extra_evidence"] == '{"a": 1}'
    assert report["00_Tóm_tắt"]["B2"].value == "BẢN NHÁP — CHƯA DUYỆT KỸ SƯ"
    source["ledger_record_counts"] = {"issues": 1, "page_ledger": 0}
    qa = validate_excel_report_draft(output, source=source, classifications=classifications, source_workbook=estimate)
    assert qa["status"] == "PASS", [check for check in qa["checks"] if check["status"] == "FAIL"]
    assert qa["workbook_sha256"]


def test_excel_draft_qa_detects_wrong_ledger_count_and_formula_in_audit_sheet():
    source = {"snapshot_digest": "a" * 64, "checkpoint_version": 1, "projection": {"issues": []}, "ledger_records": [], "ledger_record_counts": {}, "issue_final_status_counts": {}}
    classifications = {"classification_digest": "b" * 64, "decisions": {}, "unclassified_issue_count": 0}
    output = build_excel_report_draft(source=source, classifications=classifications)
    source["ledger_record_counts"] = {"issues": 1}
    qa = validate_excel_report_draft(output, source=source, classifications=classifications)
    assert qa["status"] == "FAIL"
    assert {check["name"] for check in qa["checks"] if check["status"] == "FAIL"} == {"ledger_rows:issues", "summary_values"}
    source["ledger_record_counts"] = {}
    issue = {"issue_id": "I-01", "final_status": "CONFIRMED_ERROR", "claim": "original claim"}
    issue_record = {
        "record_type": "issues", "record_id": "I-01", "check_id": "C-01",
        "source_refs": {"page_ids": [], "sheet_ids": [], "target_ids": []}, "payload": issue,
    }
    report_source = {
        "snapshot_digest": "c" * 64, "checkpoint_version": 2, "projection": {"issues": [issue]},
        "ledger_records": [issue_record], "ledger_record_counts": {"issues": 1},
        "issue_final_status_counts": {"CONFIRMED_ERROR": 1},
    }
    report_classifications = {"classification_digest": "d" * 64, "decisions": {}, "unclassified_issue_count": 1}
    coverage = {"coverage_status": "INCOMPLETE", "missing_check_ids": ["C-01"], "work_item_status_counts": {"PENDING:NOT_CHECKED": 1}}
    output = build_excel_report_draft(source=report_source, classifications=report_classifications, coverage=coverage)
    qa = validate_excel_report_draft(output, source=report_source, classifications=report_classifications, coverage=coverage)
    assert qa["status"] == "PASS", [check for check in qa["checks"] if check["status"] == "FAIL"]
    workbook = load_workbook(BytesIO(output))
    workbook["00_Tóm_tắt"]["A1"] = "=1+1"
    corrupted = BytesIO()
    workbook.save(corrupted)
    qa = validate_excel_report_draft(corrupted.getvalue(), source=report_source, classifications=report_classifications, coverage=coverage)
    assert qa["status"] == "FAIL"
    assert {check["name"] for check in qa["checks"] if check["status"] == "FAIL"} == {"no_formulas_in_audit_sheets", "summary_values"}

    workbook = load_workbook(BytesIO(output))
    sheet = workbook["01_Lỗi_kiến_nghị"]
    claim_column = next(cell.column for cell in sheet[1] if cell.value == "claim")
    sheet.cell(2, claim_column).value = "tampered ledger value"
    corrupted = BytesIO()
    workbook.save(corrupted)
    qa = validate_excel_report_draft(corrupted.getvalue(), source=report_source, classifications=report_classifications, coverage=coverage)
    assert qa["status"] == "FAIL"
    assert {check["name"] for check in qa["checks"] if check["status"] == "FAIL"} == {"ledger_values:issues"}

    workbook = load_workbook(BytesIO(output))
    workbook["00_Tóm_tắt"]["B3"] = "stale snapshot"
    corrupted = BytesIO()
    workbook.save(corrupted)
    qa = validate_excel_report_draft(corrupted.getvalue(), source=report_source, classifications=report_classifications, coverage=coverage)
    assert {check["name"] for check in qa["checks"] if check["status"] == "FAIL"} >= {"summary_values", "snapshot_digest"}

    workbook = load_workbook(BytesIO(output))
    workbook["11_Cổng_độ_phủ"]["B2"] = "COMPLETE"
    corrupted = BytesIO()
    workbook.save(corrupted)
    qa = validate_excel_report_draft(corrupted.getvalue(), source=report_source, classifications=report_classifications, coverage=coverage)
    assert qa["status"] == "FAIL"
    assert {check["name"] for check in qa["checks"] if check["status"] == "FAIL"} == {"coverage_values"}


def test_report_draft_rejects_stale_annotations_and_reserved_source_sheet(tmp_path):
    source = {"snapshot_digest": "a" * 64, "checkpoint_version": 1, "projection": {"issues": []}, "ledger_records": [], "ledger_record_counts": {}, "issue_final_status_counts": {}}
    classifications = {"classification_digest": "b" * 64, "decisions": {}, "unclassified_issue_count": 0}
    with pytest.raises(ReportExportError, match="different report revision"):
        build_excel_report_draft(source=source, classifications=classifications, annotation_qa={"snapshot_digest": "c" * 64, "classification_digest": "b" * 64})
    estimate = tmp_path / "reserved.xlsx"
    workbook = Workbook()
    workbook.active.title = "01_Lỗi_kiến_nghị"
    workbook.save(estimate)
    with pytest.raises(ReportExportError, match="reserved audit"):
        build_excel_report_draft(source=source, classifications=classifications, source_workbook=estimate)


def test_annotation_sheet_values_and_severity_style_are_bound_to_pdf_qa_snapshot():
    source = {"snapshot_digest": "a" * 64, "checkpoint_version": 1, "projection": {"issues": []}, "ledger_records": [], "ledger_record_counts": {}, "issue_final_status_counts": {}}
    classifications = {"classification_digest": "b" * 64, "decisions": {}, "unclassified_issue_count": 0}
    mark = {
        "mark_id": "M-01", "issue_id": "I-01", "severity": "HIGH", "pdf_page": 1,
        "annotation_qa_status": "UNVERIFIED", "note": "Kiểm tra chi tiết gối.",
        "snapshot_digest": source["snapshot_digest"], "classification_digest": classifications["classification_digest"],
        "target": {"target_bbox_px": [100, 100, 120, 120]}, "crop_review": None,
    }
    annotation_qa = {
        "snapshot_digest": source["snapshot_digest"], "classification_digest": classifications["classification_digest"],
        "output_pdf_sha256": "c" * 64, "annotation_qa_status": "UNVERIFIED", "marks": [mark],
    }
    output = build_excel_report_draft(source=source, classifications=classifications, annotation_qa=annotation_qa)
    qa = validate_excel_report_draft(output, source=source, classifications=classifications, annotation_qa=annotation_qa)
    assert qa["status"] == "PASS", [check for check in qa["checks"] if check["status"] == "FAIL"]

    workbook = load_workbook(BytesIO(output))
    sheet = workbook["10_Đánh_dấu_PDF"]
    headers = [cell.value for cell in sheet[1]]
    sheet.cell(2, headers.index("annotation_qa_status") + 1).value = "PASS"
    corrupted = BytesIO()
    workbook.save(corrupted)
    qa = validate_excel_report_draft(corrupted.getvalue(), source=source, classifications=classifications, annotation_qa=annotation_qa)
    assert {check["name"] for check in qa["checks"] if check["status"] == "FAIL"} == {"annotation_values"}

    workbook = load_workbook(BytesIO(output))
    sheet = workbook["10_Đánh_dấu_PDF"]
    sheet.cell(2, headers.index("severity") + 1).fill = PatternFill()
    corrupted = BytesIO()
    workbook.save(corrupted)
    qa = validate_excel_report_draft(corrupted.getvalue(), source=source, classifications=classifications, annotation_qa=annotation_qa)
    assert {check["name"] for check in qa["checks"] if check["status"] == "FAIL"} == {"severity_colors"}


def test_report_guide_text_is_verified_by_export_qa():
    source = {"snapshot_digest": "a" * 64, "checkpoint_version": 1, "projection": {"issues": []}, "ledger_records": [], "ledger_record_counts": {}, "issue_final_status_counts": {}}
    classifications = {"classification_digest": "b" * 64, "decisions": {}, "unclassified_issue_count": 0}
    output = build_excel_report_draft(source=source, classifications=classifications)
    workbook = load_workbook(BytesIO(output))
    workbook["12_Cách_đọc"]["B5"] = "Đã phát hành chính thức"
    corrupted = BytesIO()
    workbook.save(corrupted)
    qa = validate_excel_report_draft(corrupted.getvalue(), source=source, classifications=classifications)
    assert qa["status"] == "FAIL"
    assert {check["name"] for check in qa["checks"] if check["status"] == "FAIL"} == {"guide_values"}


def test_every_ledger_type_round_trips_to_its_report_sheet():
    id_fields = {
        "issues": "issue_id", "page_ledger": "page_id", "detail_rows": "row_id",
        "summary_lineage": "summary_row_id", "calculation_ledger": "calc_id",
        "assumption_ledger": "assumption_id", "pass2_review": "review_id",
        "pass3_review": "review_id", "regression_checks": "case_id",
        "issue_candidates": "candidate_id", "physical_instance_ledger": "instance_id",
        "run_metadata": "metadata_id", "source_observations": "observation_id",
        "boq_lineage": "lineage_id",
    }
    records = []
    for kind, id_field in id_fields.items():
        record_id = "issue-1" if kind == "issues" else f"{kind}-1"
        payload = {id_field: record_id, "report_parity_probe": f"value:{kind}"}
        if kind == "issues":
            payload.update(final_status="CONFIRMED_ERROR", claim="Sai khối lượng tại vị trí đối chiếu")
        records.append({
            "record_type": kind, "record_id": record_id, "check_id": "check-1",
            "source_refs": {"page_ids": ["p1"], "sheet_ids": [], "target_ids": ["t1"]},
            "payload": payload,
        })
    issue = records[0]["payload"]
    source = {
        "snapshot_digest": "a" * 64, "checkpoint_version": 3,
        "projection": {"issues": [issue]}, "ledger_records": records,
        "ledger_record_counts": {kind: 1 for kind in id_fields},
        "issue_final_status_counts": {"CONFIRMED_ERROR": 1},
    }
    classifications = {
        "classification_digest": "b" * 64,
        "decisions": {"issue-1": {
            "final_status": "CONFIRMED_ERROR", "severity": "HIGH",
            "reason": "Đã kiểm tra vị trí và mức ảnh hưởng", "reviewer_user_id": 7,
        }},
        "unclassified_issue_count": 0,
    }

    output = build_excel_report_draft(source=source, classifications=classifications)
    qa = validate_excel_report_draft(output, source=source, classifications=classifications)
    assert qa["status"] == "PASS", [check for check in qa["checks"] if check["status"] == "FAIL"]
    report = load_workbook(BytesIO(output), data_only=False)
    for kind, title in _SHEETS.items():
        sheet = report[title]
        headers = [cell.value for cell in sheet[1]]
        row = {headers[index]: cell.value for index, cell in enumerate(sheet[2])}
        assert sheet.max_row == 2
        assert row["report_parity_probe"] == f"value:{kind}"


def test_excel_draft_qa_rejects_absolute_paths_in_audit_sheets():
    issue = {"issue_id": "I-01", "final_status": "CONFIRMED_ERROR", "claim": "Nguồn: /home/analyst/private/source.pdf"}
    record = {"record_type": "issues", "record_id": "I-01", "check_id": "C-01", "source_refs": {"page_ids": [], "sheet_ids": [], "target_ids": []}, "payload": issue}
    source = {
        "snapshot_digest": "a" * 64, "checkpoint_version": 1,
        "projection": {"issues": [issue]}, "ledger_records": [record],
        "ledger_record_counts": {"issues": 1}, "issue_final_status_counts": {"CONFIRMED_ERROR": 1},
    }
    classifications = {
        "classification_digest": "b" * 64,
        "decisions": {"I-01": {
            "final_status": "CONFIRMED_ERROR", "severity": "HIGH",
            "reason": "Đã kiểm tra nguồn", "reviewer_user_id": 7,
        }},
        "unclassified_issue_count": 0,
    }

    output = build_excel_report_draft(source=source, classifications=classifications)
    qa = validate_excel_report_draft(output, source=source, classifications=classifications)

    assert qa["status"] == "FAIL"
    assert {check["name"] for check in qa["checks"] if check["status"] == "FAIL"} == {"no_absolute_paths_in_audit_sheets"}


def test_report_draft_rejects_model_field_spoofing_and_excel_truncation():
    issue = {"issue_id": "I-01", "final_status": "CONFIRMED_ERROR", "claim": "Sai khối lượng", "reviewer_severity": "LOW"}
    record = {"record_type": "issues", "record_id": "I-01", "check_id": "C-01", "source_refs": {"page_ids": [], "sheet_ids": [], "target_ids": []}, "payload": issue}
    source = {"snapshot_digest": "a" * 64, "checkpoint_version": 1, "projection": {"issues": [issue]}, "ledger_records": [record], "ledger_record_counts": {"issues": 1}, "issue_final_status_counts": {"CONFIRMED_ERROR": 1}}
    classifications = {"classification_digest": "b" * 64, "decisions": {}, "unclassified_issue_count": 1}
    with pytest.raises(ReportExportError, match="collides"):
        build_excel_report_draft(source=source, classifications=classifications)
    record["payload"] = {**issue, "claim": "x" * 32768}
    del record["payload"]["reviewer_severity"]
    with pytest.raises(ReportExportError, match="safely"):
        build_excel_report_draft(source=source, classifications=classifications)
    record["payload"]["claim"] = "bad\x00text"
    with pytest.raises(ReportExportError, match="safely"):
        build_excel_report_draft(source=source, classifications=classifications)
