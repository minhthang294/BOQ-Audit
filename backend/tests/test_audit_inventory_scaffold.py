import hashlib
from pathlib import Path

import fitz
import openpyxl
import pytest

from app.services.audit_coverage import CoverageInputError, validate_inventory
from app.services.audit_inventory_scaffold import (
    SourceScaffoldError,
    build_source_inventory,
)


def _pdf(path: Path, pages: int = 3) -> Path:
    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    document.save(path)
    document.close()
    return path


def _xlsx(path: Path, sheets=("Sheet1", "Hidden")) -> Path:
    workbook = openpyxl.Workbook()
    workbook.active.title = sheets[0]
    for name in sheets[1:]:
        workbook.create_sheet(name)
    # Hide one sheet to ensure all source sheets are scaffolded.
    if "Hidden" in workbook.sheetnames:
        workbook["Hidden"].sheet_state = "hidden"
    workbook.save(path)
    workbook.close()
    return path


def test_pdf_only_scaffold_is_valid_and_has_no_invented_scope(tmp_path):
    pdf = _pdf(tmp_path / "drawing.pdf", 2)
    inventory = build_source_inventory(pdf_path=pdf)

    assert [d["document_id"] for d in inventory["documents"]] == ["pdf"]
    assert [p["page_id"] for p in inventory["pages"]] == ["pdf-P00001", "pdf-P00002"]
    assert inventory["sheets"] == []
    assert inventory["targets"] == []
    assert inventory["expected_work_items"] == []
    validate_inventory(inventory)


def test_xlsx_only_scaffold_includes_all_sheets(tmp_path):
    xlsx = _xlsx(tmp_path / "estimate.xlsx")
    inventory = build_source_inventory(xlsx_path=xlsx)

    assert [d["document_id"] for d in inventory["documents"]] == ["xlsx"]
    assert [s["sheet_name"] for s in inventory["sheets"]] == ["Sheet1", "Hidden"]
    assert [s["sheet_index"] for s in inventory["sheets"]] == [1, 2]
    assert inventory["pages"] == []
    assert inventory["targets"] == []
    assert inventory["expected_work_items"] == []
    validate_inventory(inventory)


def test_combined_scaffold_is_deterministic(tmp_path):
    pdf = _pdf(tmp_path / "drawing.pdf", 1)
    xlsx = _xlsx(tmp_path / "estimate.xlsx", ("A",))
    first = build_source_inventory(pdf_path=pdf, xlsx_path=xlsx)
    second = build_source_inventory(pdf_path=pdf, xlsx_path=xlsx)
    assert first == second
    assert len(first["documents"]) == 2
    validate_inventory(first)


def test_source_digest_is_bound_and_changes_with_source(tmp_path):
    pdf = _pdf(tmp_path / "drawing.pdf", 1)
    inventory = build_source_inventory(pdf_path=pdf)
    expected = "sha256:" + hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert inventory["documents"][0]["revision_digest"] == expected
    assert inventory["pages"][0]["revision_digest"] == expected

    pdf.write_bytes(pdf.read_bytes() + b"% tampered")
    changed = build_source_inventory(pdf_path=pdf)
    assert changed["documents"][0]["revision_digest"] != expected


def test_rejects_missing_unsupported_and_unreadable_sources(tmp_path):
    missing = tmp_path / "missing.pdf"
    with pytest.raises(SourceScaffoldError):
        build_source_inventory(pdf_path=missing)

    unsupported = tmp_path / "source.txt"
    unsupported.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(SourceScaffoldError):
        build_source_inventory(pdf_path=unsupported)

    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"not a pdf")
    with pytest.raises(SourceScaffoldError):
        build_source_inventory(pdf_path=bad_pdf)

    bad_xlsx = tmp_path / "bad.xlsx"
    bad_xlsx.write_bytes(b"not an xlsx")
    with pytest.raises(SourceScaffoldError):
        build_source_inventory(xlsx_path=bad_xlsx)

    with pytest.raises(SourceScaffoldError):
        build_source_inventory()


def test_does_not_claim_approval_or_completeness(tmp_path):
    pdf = _pdf(tmp_path / "drawing.pdf", 1)
    inventory = build_source_inventory(pdf_path=pdf)
    assert inventory["targets"] == []
    assert inventory["expected_work_items"] == []
    assert "approval" not in inventory
    assert "complete" not in inventory
    assert "coverage" not in inventory


def test_blank_source_paths_are_rejected():
    with pytest.raises(SourceScaffoldError):
        build_source_inventory(pdf_path="   ")
    with pytest.raises(SourceScaffoldError):
        build_source_inventory(xlsx_path="   ")
    with pytest.raises(SourceScaffoldError):
        build_source_inventory(pdf_path=Path("   "))


def test_error_messages_do_not_include_source_paths(tmp_path):
    bad_pdf = tmp_path / "secret-layout" / "bad.pdf"
    bad_pdf.parent.mkdir()
    bad_pdf.write_bytes(b"not a pdf")
    with pytest.raises(SourceScaffoldError) as exc:
        build_source_inventory(pdf_path=bad_pdf)
    assert str(bad_pdf) not in str(exc.value)
    assert "secret-layout" not in str(exc.value)
