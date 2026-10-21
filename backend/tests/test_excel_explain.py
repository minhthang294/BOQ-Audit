from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from conftest import ASGIClient, login
from app.core.config import get_settings
from app.services import excel_explain

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sample_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "BOQ chi tiet"
    source = workbook.create_sheet("BOQ1.5")
    source["A64"] = 45
    source["A65"] = 46
    sheet["A170"] = 45
    sheet["A171"] = 45
    sheet["A172"] = 46
    sheet["W170"] = "=SUBTOTAL(9,INDEX(W:W,MATCH($A170,$A:A,0)+1):INDEX(W:W,MATCH($A170+1,$A:A,0)-1))"
    sheet["W171"] = 100
    sheet["T172"] = 1
    sheet["U172"] = 2
    sheet["V172"] = 3
    sheet["W172"] = "=SUM(T172:V172)"
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def upload(client, filename="boq.xlsx", data=None):
    return client.post(
        "/api/excel/upload",
        files={"file": (filename, data or sample_workbook(), XLSX_MIME)},
    )


def test_upload_preview_and_ownership():
    owner = ASGIClient()
    assert login(owner, "owner", "owner-pass-123").status_code == 200
    admin = ASGIClient()
    assert login(admin, "admin", "admin-pass-123").status_code == 200

    created = upload(owner)
    assert created.status_code == 201, created.text
    body = created.json()
    code = body["code"]
    assert body["filename"] == "boq.xlsx"
    assert [sheet["name"] for sheet in body["sheets"]] == ["BOQ chi tiet", "BOQ1.5"]

    preview = owner.get(
        f"/api/excel/{code}/sheet",
        params={"name": "BOQ chi tiet", "row_start": 170, "max_rows": 1, "max_cols": 23},
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()
    w_column = data["columns"].index("W")
    cell = data["rows"][0][w_column]
    assert cell["ref"] == "W170"
    assert cell["formula"].startswith("=SUBTOTAL(9")
    assert cell["kind"] == "formula"

    assert admin.get(f"/api/excel/{code}").status_code == 404
    assert admin.get(f"/api/excel/{code}/sheet", params={"name": "BOQ chi tiet"}).status_code == 404


def test_delete_and_reupload_validation():
    owner = ASGIClient()
    assert login(owner, "owner", "owner-pass-123").status_code == 200
    created = upload(owner)
    code = created.json()["code"]
    assert owner.delete(f"/api/excel/{code}").status_code == 204
    assert owner.get(f"/api/excel/{code}").status_code == 404
    assert upload(owner, filename="boq.xls").status_code == 415


def test_explain_rejects_oversized_selection():
    owner = ASGIClient()
    assert login(owner, "owner", "owner-pass-123").status_code == 200
    created = upload(owner)
    code = created.json()["code"]
    response = owner.post(
        f"/api/excel/{code}/explain",
        json={"sheet": "BOQ chi tiet", "scope": "range", "selection": "A1:Z100", "question": "", "history": []},
    )
    assert response.status_code == 422
    assert "vượt quá giới hạn" in response.json()["detail"]


def test_build_context_includes_formula_and_references(tmp_path):
    path = get_settings().excel_dir / "XL-UNIT" / "input.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(sample_workbook())
    workbook = SimpleNamespace(file_path=str(path), original_filename="boq.xlsx", code="XL-UNIT")

    context, cells, references, truncated = excel_explain.build_context(workbook, "BOQ chi tiet", "cell", "W170")

    assert context["selection"]["address"] == "BOQ chi tiet!W170"
    assert context["selected_cells"][0]["formula"].startswith("=SUBTOTAL")
    refs = {(cell["sheet"], cell["ref"]) for cell in context["reference_cells"]}
    assert ("BOQ chi tiet", "A170") in refs
    assert ("BOQ chi tiet", "W171") in refs
    assert ("BOQ chi tiet", "T172") in refs
    assert cells >= 3
    assert references >= 3
    assert isinstance(truncated, bool)


def test_selection_parser_rejects_invalid_range():
    with pytest.raises(excel_explain.ExcelExplainError):
        excel_explain.parse_selection("not-a-range", "BOQ chi tiet", "range")
    with pytest.raises(excel_explain.ExcelExplainError):
        excel_explain.parse_selection("A1:Z1000", "BOQ chi tiet", "range")
