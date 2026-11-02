import hashlib
from pathlib import Path

import pymupdf
import pytest

from app.services.audit_annotation_draft import AnnotationDraftError, render_annotation_draft, render_annotation_review_images, render_source_page, summarize_annotation_reviews


FONT = next((path for path in (
    Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    Path("/usr/share/fonts/noto/NotoSans-Regular.ttf"),
) if path.is_file()), Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"))


@pytest.fixture
def draft_inputs(tmp_path):
    pdf_path = tmp_path / "source.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=120)
    page.set_cropbox(pymupdf.Rect(10, 10, 190, 110))
    page.set_rotation(90)
    doc.save(pdf_path)
    doc.close()
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    inventory = {
        "documents": [{"document_id": "pdf", "revision_digest": f"sha256:{digest}"}],
        "pages": [{"page_id": "pdf-P00001", "document_id": "pdf", "revision_digest": f"sha256:{digest}", "pdf_page": 1}],
    }
    source = {
        "snapshot_digest": "a" * 64,
        "projection": {"issues": [
            {"issue_id": "I-01", "final_status": "CONFIRMED_ERROR"},
            {"issue_id": "I-02", "final_status": "FALSE_POSITIVE"},
        ]},
        "ledger_records": [{"record_type": "issues", "record_id": issue_id, "source_refs": {"page_ids": ["pdf-P00001"]}} for issue_id in ("I-01", "I-02")],
    }
    classifications = {"classification_digest": "b" * 64, "decisions": {"I-01": {"severity": "HIGH"}, "I-02": {"severity": None}}}
    with pymupdf.open(pdf_path) as doc:
        pix = doc[0].get_pixmap(dpi=144)
    mark = {
        "mark_id": "M-01", "issue_id": "I-01", "page_id": "pdf-P00001", "dpi": 144,
        "render_size_px": [pix.width, pix.height],
        "target_bbox_px": [20, 20, 55, 32],
        "note_bbox_px": [55, 45, 200, 330],
        "leader_points_px": [[57, 47], [55, 28]],
        "note": "HIGH · I-01 — Sai.\nKiểm tra.",
        "snapshot_digest": "a" * 64, "classification_digest": "b" * 64,
    }
    return pdf_path, inventory, source, classifications, mark


def _render(inputs, marks):
    pdf_path, inventory, source, classifications, _ = inputs
    return render_annotation_draft(pdf_path=pdf_path, inventory=inventory, source=source, classifications=classifications, marks=marks, font_path=FONT)


def test_draft_has_visible_note_and_unverified_qa_on_rotated_crop(draft_inputs):
    if not FONT.exists():
        pytest.skip("Noto Sans is unavailable")
    output, qa = _render(draft_inputs, [draft_inputs[-1]])
    assert qa["annotation_qa_status"] == "UNVERIFIED"
    assert qa["missing_active_issue_ids"] == []
    assert qa["marks"][0]["mark_id"] == "M-01"
    assert qa["marks"][0]["target"]["rotation"] == 90
    assert qa["output_pdf_sha256"] == hashlib.sha256(output).hexdigest()
    assert qa["source_pdf_sha256"] == hashlib.sha256(draft_inputs[0].read_bytes()).hexdigest()
    with pymupdf.open(stream=output, filetype="pdf") as doc:
        assert len(doc) == 1
        assert doc[0].first_annot is None
        assert "Kiểm tra." in doc[0].get_text()
        assert len(doc[0].get_drawings()) >= 3


@pytest.mark.parametrize("change", [
    {"snapshot_digest": "c" * 64},
    {"issue_id": "I-02"},
    {"page_id": "pdf-P00002"},
    {"render_size_px": [1, 1]},
    {"target_bbox_px": [0, 0, 999, 999]},
    {"leader_points_px": [[0, 0], [0, 0]]},
    {"note": "HIGH · I-01 — " + "x" * 300},
    {"note": "HIGH · I-01 — " + "x" * 80},
])
def test_draft_rejects_stale_or_unbound_marks(draft_inputs, change):
    with pytest.raises(AnnotationDraftError):
        _render(draft_inputs, [{**draft_inputs[-1], **change}])


def test_draft_rejects_changed_source_pdf(draft_inputs):
    pdf_path = draft_inputs[0]
    pdf_path.write_bytes(pdf_path.read_bytes() + b" ")
    with pytest.raises(AnnotationDraftError, match="PDF bytes differ"):
        _render(draft_inputs, [draft_inputs[-1]])


def test_no_active_issues_allows_unmarked_pdf_but_never_auto_passes(draft_inputs):
    if not FONT.exists():
        pytest.skip("Noto Sans is unavailable")
    pdf_path, inventory, source, classifications, _ = draft_inputs
    source["projection"]["issues"] = [{"issue_id": "I-02", "final_status": "FALSE_POSITIVE"}]
    source["ledger_records"] = [row for row in source["ledger_records"] if row["record_id"] == "I-02"]
    output, qa = render_annotation_draft(
        pdf_path=pdf_path, inventory=inventory, source=source, classifications=classifications,
        marks=[], font_path=FONT,
    )
    assert qa["marks"] == []
    assert qa["missing_active_issue_ids"] == []
    assert qa["annotation_qa_status"] == "UNVERIFIED"
    assert summarize_annotation_reviews(qa, []) ["annotation_qa_status"] == "UNVERIFIED"
    with pymupdf.open(stream=output, filetype="pdf") as doc:
        assert len(doc) == 1


def test_active_issue_cannot_have_unmarked_pdf(draft_inputs):
    if not FONT.exists():
        pytest.skip("Noto Sans is unavailable")
    with pytest.raises(AnnotationDraftError, match="active issues require exact annotation marks"):
        _render(draft_inputs, [])


def test_sheet_only_active_issue_stays_in_excel_without_fake_pdf_mark(draft_inputs):
    if not FONT.exists():
        pytest.skip("Noto Sans is unavailable")
    _pdf_path, _inventory, source, classifications, mark = draft_inputs
    source["projection"]["issues"] = [{"issue_id": "I-01", "final_status": "CONFIRMED_ERROR"}]
    source["ledger_records"] = [{"record_type": "issues", "record_id": "I-01", "source_refs": {"page_ids": [], "sheet_ids": ["s1"]}}]
    output, qa = _render(draft_inputs, [])
    assert output.startswith(b"%PDF")
    assert qa["missing_active_issue_ids"] == []
    assert qa["marks"] == []
    assert summarize_annotation_reviews(qa, [])["annotation_qa_status"] == "UNVERIFIED"
    with pytest.raises(AnnotationDraftError, match="mark page is not an approved source reference"):
        _render(draft_inputs, [mark])
    source["ledger_records"][0]["source_refs"]["page_ids"] = ["pdf-P00001"]
    with pytest.raises(AnnotationDraftError, match="active issues require exact annotation marks"):
        _render(draft_inputs, [])


def test_draft_allows_multiple_targets_for_one_issue_but_rejects_repeated_mark_id(draft_inputs):
    first = draft_inputs[-1]
    second = {**first, "mark_id": "M-02", "target_bbox_px": [21, 34, 56, 43], "leader_points_px": [[57, 47], [55, 38]]}
    _, qa = _render(draft_inputs, [first, second])
    assert len(qa["marks"]) == 2
    assert qa["missing_active_issue_ids"] == []
    with pytest.raises(AnnotationDraftError, match="repeated"):
        _render(draft_inputs, [first, {**second, "mark_id": "M-01"}])


def test_review_images_and_latest_event_remain_bound_to_final_pdf(draft_inputs):
    output, qa = _render(draft_inputs, [draft_inputs[-1]])
    images = render_annotation_review_images(output, qa["marks"][0])
    assert set(images) == {"target", "note", "page"}
    for image in images.values():
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert summarize_annotation_reviews(qa, [])["annotation_qa_status"] == "UNVERIFIED"
    event = {"mark_id": "M-01", "revision_no": 1, "result": "PASS", "output_pdf_sha256": qa["output_pdf_sha256"]}
    assert summarize_annotation_reviews(qa, [event])["annotation_qa_status"] == "PASS"
    assert summarize_annotation_reviews(qa, [event, {**event, "revision_no": 2, "result": "FAIL"}])["annotation_qa_status"] == "FAIL"
    with pytest.raises(AnnotationDraftError, match="does not match"):
        summarize_annotation_reviews(qa, [{**event, "output_pdf_sha256": "0" * 64}])
    qa["missing_active_issue_ids"] = ["I-03"]
    assert summarize_annotation_reviews(qa, [event])["annotation_qa_status"] == "UNVERIFIED"


def test_source_render_uses_exact_rotated_crop_and_rejects_invalid_page(draft_inputs):
    image, size = render_source_page(draft_inputs[0].read_bytes(), page_number=1, dpi=144)
    assert image.startswith(b"\x89PNG")
    assert size == tuple(draft_inputs[-1]["render_size_px"])
    with pytest.raises(AnnotationDraftError, match="page is invalid"):
        render_source_page(draft_inputs[0].read_bytes(), page_number=2, dpi=144)
    large = pymupdf.open()
    large.new_page(width=10000, height=10000)
    with pytest.raises(AnnotationDraftError, match="pixel limit"):
        render_source_page(large.tobytes(), page_number=1, dpi=300)
    large.close()
