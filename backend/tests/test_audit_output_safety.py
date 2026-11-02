from io import BytesIO

import fitz
from openpyxl import Workbook, load_workbook

from app.services.audit_output_safety import (
    contains_sensitive_path,
    output_path_leak_locations,
    workbook_path_leak_locations,
)


def _xlsx(path, rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()


def test_workbook_leak_locations_report_cells_without_the_value(tmp_path):
    path = tmp_path / "report.xlsx"
    _xlsx(path, [["Nguồn", "/home/analyst/secrets/source.pdf"], ["Trang", 21]])
    workbook = load_workbook(path, read_only=True)
    locations = workbook_path_leak_locations(workbook)
    workbook.close()
    assert locations == ["Sheet!B1"]
    assert all("/home/analyst" not in item for item in locations)


def test_output_scanner_covers_workbooks_and_pdfs(tmp_path):
    clean = tmp_path / "clean.xlsx"
    _xlsx(clean, [["Nguồn", "source/input.pdf"]])
    leaked = tmp_path / "leaked.xlsx"
    _xlsx(leaked, [["Nguồn", "/var/tmp/boq/jobs/BOQ-1/input.pdf"]])
    pdf = tmp_path / "annotated.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "ghi chú: /data/jobs/BOQ-1/source.pdf")
    document.set_metadata({"title": "report"})
    document.save(pdf)
    document.close()

    assert output_path_leak_locations([clean]) == []
    leaked_locations = output_path_leak_locations([leaked])
    assert leaked_locations == ["leaked.xlsx:Sheet!B1"]
    assert output_path_leak_locations([pdf]) == ["annotated.pdf:page:1:text"]
    assert contains_sensitive_path("source/input.pdf") is False
    assert contains_sensitive_path("file:///home/x") is True
