"""Build a reviewer-only Excel draft from one verified ledger snapshot."""

import json
from copy import copy
from io import BytesIO
from pathlib import Path
from typing import Any
import hashlib
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.services.audit_output_safety import workbook_path_leak_locations


class ReportExportError(ValueError):
    """A report cannot be bound to the current verified inputs."""


_SHEETS = {
    "issues": "01_Lỗi_kiến_nghị",
    "page_ledger": "02_Bản_đồ_trang",
    "detail_rows": "03_Dòng_BOQ",
    "summary_lineage": "04_Liên_kết_tổng_hợp",
    "calculation_ledger": "05_Tính_toán",
    "assumption_ledger": "06_Giả_định",
    "pass2_review": "07_Rà_soát_Pass2",
    "pass3_review": "08_Rà_soát_Pass3",
    "regression_checks": "09_Kiểm_tra_lùi",
    "issue_candidates": "13_Ứng_viên_lỗi",
    "physical_instance_ledger": "14_Cấu_kiện",
    "run_metadata": "15_Metadata",
    "source_observations": "16_Quan_sát_nguồn",
    "boq_lineage": "17_Truy_vết_BOQ",
}
_AUDIT_SHEETS = [
    "00_Tóm_tắt", "01_Lỗi_kiến_nghị", "02_Bản_đồ_trang", "03_Dòng_BOQ",
    "04_Liên_kết_tổng_hợp", "05_Tính_toán", "06_Giả_định",
    "07_Rà_soát_Pass2", "08_Rà_soát_Pass3", "09_Kiểm_tra_lùi",
    "10_Đánh_dấu_PDF", "11_Cổng_độ_phủ", "12_Cách_đọc",
    "13_Ứng_viên_lỗi", "14_Cấu_kiện", "15_Metadata", "16_Quan_sát_nguồn", "17_Truy_vết_BOQ",
]
_SEVERITY_FILLS = {
    "CRITICAL": "C00000", "HIGH": "E53935", "MEDIUM": "ED7D31",
    "LOW": "4472C4", "NOTE": "00A6A6",
}


def _cell_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(text) > 32767 or any(ord(char) < 32 and char not in "\t\n\r" for char in text):
        raise ReportExportError("ledger cell cannot be represented safely in Excel")
    return text


def _write_table(sheet, rows: list[dict[str, Any]], first_columns: list[str]) -> None:
    columns = list(dict.fromkeys([*first_columns, *(key for row in rows for key in row)]))
    sheet.append(columns)
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.font = Font(name="Aptos", size=11, color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 30
    for row in rows:
        sheet.append([_cell_value(row.get(key)) for key in columns])
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"  # Model text beginning with '=' must remain text, never an Excel formula.
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.font = Font(name="Aptos", size=10)
        severity_column = "reviewer_severity" if "reviewer_severity" in columns else "severity" if "severity" in columns else None
        if severity_column:
            cell = sheet.cell(sheet.max_row, columns.index(severity_column) + 1)
            color = _SEVERITY_FILLS.get(cell.value)
            if color:
                cell.fill = PatternFill("solid", fgColor=color)
                cell.font = Font(name="Aptos", size=10, color="FFFFFF", bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, column in enumerate(columns, start=1):
        length = max([len(column), *(len(str(row.get(column, ""))) for row in rows[:100])], default=12)
        sheet.column_dimensions[get_column_letter(index)].width = min(55, max(14, length + 2))


def _group_report_rows(source: dict[str, Any], classifications: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    issues = {row["issue_id"]: row for row in source["projection"]["issues"]}
    decisions = classifications["decisions"]
    if any(
        issue_id not in issues or decision["final_status"] != issues[issue_id]["final_status"]
        or (decision["severity"] is None) != (decision["final_status"] in {"FALSE_POSITIVE", "CLOSED_WITH_EXPLANATION"})
        or (decision["severity"] is not None and decision["severity"] not in _SEVERITY_FILLS)
        for issue_id, decision in decisions.items()
    ):
        raise ReportExportError("reviewer classifications differ from the ledger snapshot")
    grouped: dict[str, list[dict[str, Any]]] = {kind: [] for kind in _SHEETS}
    for record in source["ledger_records"]:
        kind = record["record_type"]
        if kind not in grouped:
            raise ReportExportError(f"unmapped ledger record type: {kind}")
        refs = record["source_refs"]
        row = {
            "record_id": record["record_id"], "check_id": record["check_id"],
            "source_page_ids": refs["page_ids"], "source_sheet_ids": refs["sheet_ids"],
            "source_target_ids": refs["target_ids"],
        }
        if kind == "issues":
            decision = decisions.get(record["record_id"])
            row.update({
                "reviewer_severity": decision["severity"] if decision else None,
                "review_reason": decision["reason"] if decision else None,
                "reviewer_user_id": decision["reviewer_user_id"] if decision else None,
            })
        if set(row) & set(record["payload"]):
            raise ReportExportError("model payload collides with controller or reviewer report fields")
        row.update(record["payload"])
        grouped[kind].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda item: item["record_id"])
    return grouped


def _summary_rows(source: dict[str, Any], classifications: dict[str, Any], annotation_qa: dict[str, Any] | None, source_sheet_count: int) -> list[dict[str, Any]]:
    rows = [
        {"Trường": "Trạng thái phát hành", "Giá trị": "BẢN NHÁP — CHƯA DUYỆT KỸ SƯ"},
        {"Trường": "Ledger snapshot SHA-256", "Giá trị": source["snapshot_digest"]},
        {"Trường": "Classification SHA-256", "Giá trị": classifications["classification_digest"]},
        {"Trường": "Checkpoint version", "Giá trị": source["checkpoint_version"]},
        {"Trường": "Số sheet dự toán nguồn", "Giá trị": source_sheet_count},
        {"Trường": "Issue chưa phân cấp", "Giá trị": classifications["unclassified_issue_count"]},
        {"Trường": "PDF annotation QA", "Giá trị": annotation_qa["annotation_qa_status"] if annotation_qa else "UNAVAILABLE"},
        {"Trường": "Ký duyệt kỹ sư", "Giá trị": "PENDING"},
    ]
    rows += [{"Trường": f"Bản ghi {kind}", "Giá trị": count} for kind, count in sorted(source["ledger_record_counts"].items())]
    rows += [{"Trường": f"Issue status {status}", "Giá trị": count} for status, count in sorted(source["issue_final_status_counts"].items())]
    return rows


def _key_value_rows(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"Trường": key, "Giá trị": value} for key, value in sorted(values.items())]


def _guide_rows() -> list[dict[str, str]]:
    return [
        {"Mục": "Nguồn", "Nội dung": "Các sheet dự toán gốc được giữ sau các sheet audit; không sửa dữ liệu nguồn để xác nhận lỗi."},
        {"Mục": "Màu", "Nội dung": "CRITICAL/HIGH đỏ, MEDIUM cam, LOW xanh dương, NOTE xanh ngọc; ô không màu chưa được reviewer phân cấp."},
        {"Mục": "Số lượng", "Nội dung": "Các số trên trang tóm tắt là số bản ghi ledger, chưa khử trùng lặp lỗi gốc."},
        {"Mục": "Phát hành", "Nội dung": "Excel này là bản nháp. QA đánh dấu PDF và kỹ sư duyệt là cổng riêng."},
    ]


def build_excel_report_draft(
    *, source: dict[str, Any], classifications: dict[str, Any],
    source_workbook: Path | None = None,
    annotation_qa: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
) -> bytes:
    """Keep source estimate sheets and append all ledger records as an unpublishable draft."""
    grouped = _group_report_rows(source, classifications)
    if annotation_qa is not None and (
        annotation_qa["snapshot_digest"] != source["snapshot_digest"]
        or annotation_qa["classification_digest"] != classifications["classification_digest"]
    ):
        raise ReportExportError("PDF annotation draft belongs to a different report revision")
    workbook = load_workbook(source_workbook, keep_links=True) if source_workbook is not None else Workbook()
    if source_workbook is None:
        workbook.remove(workbook.active)
    original_names = list(workbook.sheetnames)
    if set(original_names) & set(_AUDIT_SHEETS):
        raise ReportExportError("source estimate workbook already contains reserved audit sheet names")
    summary = workbook.create_sheet("00_Tóm_tắt", 0)
    _write_table(summary, _summary_rows(source, classifications, annotation_qa, len(original_names)), ["Trường", "Giá trị"])
    for kind, title in _SHEETS.items():
        sheet = workbook.create_sheet(title, _AUDIT_SHEETS.index(title))
        _write_table(sheet, grouped[kind], ["record_id", "reviewer_severity", "final_status", "claim"] if kind == "issues" else ["record_id", "check_id"])
    annotations = workbook.create_sheet("10_Đánh_dấu_PDF", _AUDIT_SHEETS.index("10_Đánh_dấu_PDF"))
    mark_rows = annotation_qa["marks"] if annotation_qa else []
    _write_table(annotations, mark_rows, ["mark_id", "issue_id", "severity", "pdf_page", "annotation_qa_status"])
    gate = workbook.create_sheet("11_Cổng_độ_phủ", _AUDIT_SHEETS.index("11_Cổng_độ_phủ"))
    _write_table(gate, _key_value_rows(coverage or {"coverage_status": "UNAVAILABLE"}), ["Trường", "Giá trị"])
    guide = workbook.create_sheet("12_Cách_đọc", _AUDIT_SHEETS.index("12_Cách_đọc"))
    _write_table(guide, _guide_rows(), ["Mục", "Nội dung"])
    for title in original_names:
        workbook.move_sheet(workbook[title], offset=len(workbook.sheetnames))
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def validate_excel_report_draft(
    workbook_bytes: bytes, *, source: dict[str, Any], classifications: dict[str, Any],
    source_workbook: Path | None = None, annotation_qa: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check the generated draft's structure, ledger counts, formulas and source-sheet fidelity."""
    result = {
        "status": "FAIL", "workbook_sha256": hashlib.sha256(workbook_bytes).hexdigest(),
        "snapshot_digest": source["snapshot_digest"],
        "classification_digest": classifications["classification_digest"],
        "annotation_pdf_sha256": annotation_qa.get("output_pdf_sha256") if annotation_qa else None,
        "annotation_qa_digest": hashlib.sha256(json.dumps(annotation_qa, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if annotation_qa else None,
        "checks": [],
        "limitations": ["QA cấu trúc và round-trip không xác nhận nội dung kỹ thuật; openpyxl không chứng minh bảo toàn mọi extension Excel không hỗ trợ."],
    }

    def check(name: str, passed: bool, detail: str) -> None:
        result["checks"].append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})

    def style(cell):
        return (cell.number_format, *(copy(getattr(cell, name)) for name in ("font", "fill", "border", "alignment", "protection")))

    expected_groups = _group_report_rows(source, classifications)
    try:
        workbook = load_workbook(BytesIO(workbook_bytes), data_only=False, keep_links=True)
        source_book = load_workbook(source_workbook, data_only=False, keep_links=True) if source_workbook else None
        expected_names = [*_AUDIT_SHEETS, *(source_book.sheetnames if source_book else [])]
        check("sheet_names_and_order", workbook.sheetnames == expected_names, "Tên và thứ tự sheet phải khớp contract và nguồn dự toán.")
        expected_counts = source["ledger_record_counts"]
        for kind, title in _SHEETS.items():
            sheet = workbook[title]
            expected = expected_counts.get(kind, 0)
            check(f"ledger_rows:{kind}", sheet.max_row - 1 == expected, f"expected={expected}; actual={sheet.max_row - 1}")
            first_columns = ["record_id", "reviewer_severity", "final_status", "claim"] if kind == "issues" else ["record_id", "check_id"]
            expected_columns = list(dict.fromkeys([*first_columns, *(key for row in expected_groups[kind] for key in row)]))
            actual_columns = [sheet.cell(1, column).value for column in range(1, sheet.max_column + 1)]
            expected_rows = [[_cell_value(row.get(key)) for key in expected_columns] for row in expected_groups[kind]]
            actual_rows = [[sheet.cell(row, column).value for column in range(1, sheet.max_column + 1)] for row in range(2, sheet.max_row + 1)]
            check(f"ledger_values:{kind}", actual_columns == expected_columns and actual_rows == expected_rows,
                  "Header and cell values must match the bound ledger projection.")
        summary = workbook["00_Tóm_tắt"]
        summary_values = {summary.cell(row, 1).value: summary.cell(row, 2).value for row in range(2, summary.max_row + 1)}
        expected_summary = _summary_rows(source, classifications, annotation_qa, len(source_book.sheetnames) if source_book else 0)
        actual_summary = [{"Trường": summary.cell(row, 1).value, "Giá trị": summary.cell(row, 2).value} for row in range(2, summary.max_row + 1)]
        check("summary_values", [summary.cell(1, column).value for column in range(1, summary.max_column + 1)] == ["Trường", "Giá trị"]
              and actual_summary == [{key: _cell_value(value) for key, value in row.items()} for row in expected_summary],
              "Summary values must match the bound report inputs.")
        check("snapshot_digest", summary_values.get("Ledger snapshot SHA-256") == source["snapshot_digest"], "Digest workbook phải trùng snapshot hiện hành.")
        check("classification_digest", summary_values.get("Classification SHA-256") == classifications["classification_digest"], "Digest phân cấp phải trùng revision reviewer hiện hành.")
        check("annotation_qa_summary", summary_values.get("PDF annotation QA") == (annotation_qa["annotation_qa_status"] if annotation_qa else "UNAVAILABLE"), "Trạng thái PDF trong summary phải trùng QA snapshot.")
        coverage_sheet = workbook["11_Cổng_độ_phủ"]
        expected_coverage = _key_value_rows(coverage or {"coverage_status": "UNAVAILABLE"})
        actual_coverage = [{"Trường": coverage_sheet.cell(row, 1).value, "Giá trị": coverage_sheet.cell(row, 2).value} for row in range(2, coverage_sheet.max_row + 1)]
        check("coverage_values", [coverage_sheet.cell(1, column).value for column in range(1, coverage_sheet.max_column + 1)] == ["Trường", "Giá trị"]
              and actual_coverage == [{key: _cell_value(value) for key, value in row.items()} for row in expected_coverage],
              "Coverage values must match the approved inventory and checkpoint ledger.")
        guide_sheet = workbook["12_Cách_đọc"]
        expected_guide = _guide_rows()
        actual_guide = [{"Mục": guide_sheet.cell(row, 1).value, "Nội dung": guide_sheet.cell(row, 2).value} for row in range(2, guide_sheet.max_row + 1)]
        check("guide_values", [guide_sheet.cell(1, column).value for column in range(1, guide_sheet.max_column + 1)] == ["Mục", "Nội dung"]
              and actual_guide == expected_guide, "Guide text must match the report's draft, severity, and source rules.")
        annotation_sheet = workbook["10_Đánh_dấu_PDF"]
        expected_marks = annotation_qa["marks"] if annotation_qa else []
        annotation_columns = ["mark_id", "issue_id", "severity", "pdf_page", "annotation_qa_status"]
        annotation_columns = list(dict.fromkeys([*annotation_columns, *(key for row in expected_marks for key in row)]))
        actual_annotation_columns = [annotation_sheet.cell(1, column).value for column in range(1, annotation_sheet.max_column + 1)]
        expected_mark_rows = [[_cell_value(row.get(key)) for key in annotation_columns] for row in expected_marks]
        actual_mark_rows = [[annotation_sheet.cell(row, column).value for column in range(1, annotation_sheet.max_column + 1)] for row in range(2, annotation_sheet.max_row + 1)]
        check("annotation_values", actual_annotation_columns == annotation_columns and actual_mark_rows == expected_mark_rows,
              "Annotation rows must match the current PDF QA snapshot.")
        severity_cells = True
        for title, rows, columns, severity_column in (
            ("01_Lỗi_kiến_nghị", expected_groups["issues"], ["record_id", "reviewer_severity", "final_status", "claim"], "reviewer_severity"),
            ("10_Đánh_dấu_PDF", expected_marks, annotation_columns, "severity"),
        ):
            sheet = workbook[title]
            for row_no, expected_row in enumerate(rows, start=2):
                severity = expected_row.get(severity_column)
                color = _SEVERITY_FILLS.get(severity)
                if color:
                    cell = sheet.cell(row_no, columns.index(severity_column) + 1)
                    if not (cell.fill.fgColor.type == "rgb" and (cell.fill.fgColor.rgb or "").endswith(color)
                            and cell.font.bold and cell.font.color and cell.font.color.type == "rgb"
                            and (cell.font.color.rgb or "").endswith("FFFFFF")):
                        severity_cells = False
                        break
            if not severity_cells:
                break
        check("severity_colors", severity_cells, "Reviewer severity and PDF mark colors must match the severity palette.")
        formula_cells = [f"{sheet.title}!{cell.coordinate}" for sheet in workbook.worksheets[:len(_AUDIT_SHEETS)] for row in sheet.iter_rows() for cell in row if cell.data_type == "f"]
        check("no_formulas_in_audit_sheets", not formula_cells, "Audit sheets không được thực thi công thức từ dữ liệu ledger." if not formula_cells else ", ".join(formula_cells[:10]))
        leak_locations = workbook_path_leak_locations(workbook, workbook.worksheets[:len(_AUDIT_SHEETS)])
        check("no_absolute_paths_in_audit_sheets", not leak_locations,
              "Audit sheets không được chứa đường dẫn hệ thống tuyệt đối." if not leak_locations else ", ".join(leak_locations[:10]))
        audit_sheets = workbook.worksheets[:len(_AUDIT_SHEETS)]
        check("audit_sheet_format", all(sheet.sheet_state == "visible" and sheet.freeze_panes == "A2" and sheet.cell(1, 1).fill.fgColor.rgb in {"0017365D", "17365D"} for sheet in audit_sheets), "Audit sheets phải hiện, freeze hàng tiêu đề và có style header.")
        if source_book:
            fidelity = True
            difference = ""
            for source_sheet in source_book.worksheets:
                copied = workbook[source_sheet.title]
                if (source_sheet.sheet_state, list(source_sheet.merged_cells.ranges), source_sheet.freeze_panes) != (copied.sheet_state, list(copied.merged_cells.ranges), copied.freeze_panes):
                    fidelity, difference = False, f"{source_sheet.title}: sheet state/merged/freeze"
                    break
                for row_index in range(1, max(source_sheet.max_row, copied.max_row) + 1):
                    if source_sheet.row_dimensions[row_index].hidden != copied.row_dimensions[row_index].hidden or source_sheet.row_dimensions[row_index].height != copied.row_dimensions[row_index].height:
                        fidelity, difference = False, f"{source_sheet.title}: row {row_index} dimensions"
                        break
                if not fidelity:
                    break
                for row_index in range(1, max(source_sheet.max_row, copied.max_row) + 1):
                    for column_index in range(1, max(source_sheet.max_column, copied.max_column) + 1):
                        left, right = source_sheet.cell(row_index, column_index), copied.cell(row_index, column_index)
                        if (left.value, left.data_type, style(left)) != (right.value, right.data_type, style(right)):
                            fidelity, difference = False, f"{source_sheet.title}!{left.coordinate}: value/style"
                            break
                    if not fidelity:
                        break
                if not fidelity:
                    break
                for key in set(source_sheet.column_dimensions) | set(copied.column_dimensions):
                    left, right = source_sheet.column_dimensions[key], copied.column_dimensions[key]
                    if (left.width, left.hidden, left.min, left.max) != (right.width, right.hidden, right.min, right.max):
                        fidelity, difference = False, f"{source_sheet.title}: column {key} dimensions"
                        break
                if not fidelity:
                    break
            check("source_sheet_values_and_formats", fidelity, difference or "Nguồn giữ nguyên giá trị, công thức, style, kích thước, ẩn dòng/cột và vùng gộp.")
        else:
            check("source_sheet_values_and_formats", True, "Không có workbook dự toán nguồn.")
    except (KeyError, OSError, ValueError, TypeError, BadZipFile) as exc:
        check("workbook_readable", False, type(exc).__name__)
    result["status"] = "PASS" if all(item["status"] == "PASS" for item in result["checks"]) else "FAIL"
    return result
