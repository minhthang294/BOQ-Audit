"""Render visible, provenance-bound PDF marks for subsequent visual QA."""

import hashlib
import math
from pathlib import Path
from typing import Any

import pymupdf

from app.services.audit_annotation_geometry import AnnotationGeometryError, pixel_bbox_to_page
from app.services.audit_report_source import pdf_mark_required_issue_ids


class AnnotationDraftError(ValueError):
    """A draft mark cannot be safely bound to its source and reviewer decision."""


_COLORS = {
    "CRITICAL": (0.753, 0.000, 0.000),
    "HIGH": (0.898, 0.224, 0.208),
    "MEDIUM": (0.929, 0.490, 0.192),
    "LOW": (0.267, 0.447, 0.769),
    "NOTE": (0.000, 0.651, 0.651),
}
_ACTIVE = {"CONFIRMED_ERROR", "INCONSISTENT", "UNRESOLVED"}
_MAX_REVIEW_PIXELS = 40_000_000


def render_source_page(pdf_bytes: bytes, *, page_number: int, dpi: int) -> tuple[bytes, tuple[int, int]]:
    """Render one approved source page at an exact DPI for pixel mark placement."""
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 600:
        raise AnnotationDraftError("source render DPI must be between 72 and 600")
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        if isinstance(page_number, bool) or not isinstance(page_number, int) or not 1 <= page_number <= len(doc):
            raise AnnotationDraftError("source PDF page is invalid")
        page = doc[page_number - 1]
        width = round(page.rect.width * dpi / 72)
        height = round(page.rect.height * dpi / 72)
        if width * height > _MAX_REVIEW_PIXELS:
            raise AnnotationDraftError("source page render exceeds the safe pixel limit")
        pix = page.get_pixmap(dpi=dpi, annots=False)
        return pix.tobytes("png"), (pix.width, pix.height)


def render_annotation_draft(
    *,
    pdf_path: Path,
    inventory: dict[str, Any],
    source: dict[str, Any],
    classifications: dict[str, Any],
    marks: list[dict[str, Any]],
    font_path: Path,
) -> tuple[bytes, dict[str, Any]]:
    """Return PDF bytes and an all-UNVERIFIED QA ledger; never assert visual correctness."""
    documents = [item for item in inventory["documents"] if item["document_id"] == "pdf"]
    pdf_bytes = pdf_path.read_bytes()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    if len(documents) != 1 or f"sha256:{pdf_sha256}" != documents[0]["revision_digest"]:
        raise AnnotationDraftError("PDF bytes differ from the approved inventory revision")
    if not font_path.is_file():
        raise AnnotationDraftError("Vietnamese annotation font is unavailable")
    issues = {row["issue_id"]: row for row in source["projection"]["issues"]}
    pages = {row["page_id"]: row for row in inventory["pages"] if row["document_id"] == "pdf"}
    pdf_issue_ids = pdf_mark_required_issue_ids(source, set(pages))
    if not isinstance(marks, list) or (pdf_issue_ids and not marks):
        raise AnnotationDraftError("active issues require exact annotation marks")

    qa: list[dict[str, Any]] = []
    seen: set[str] = set()
    marked_issues: set[str] = set()
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for mark in marks:
            if not isinstance(mark, dict) or set(mark) != {
                "mark_id", "issue_id", "page_id", "dpi", "render_size_px", "target_bbox_px",
                "note_bbox_px", "leader_points_px", "note", "snapshot_digest", "classification_digest",
            }:
                raise AnnotationDraftError("mark fields are incomplete or unexpected")
            issue_id, page_id, mark_id = mark["issue_id"], mark["page_id"], mark["mark_id"]
            if not isinstance(issue_id, str) or issue_id not in issues or not isinstance(mark_id, str) or not mark_id.strip() or mark_id in seen:
                raise AnnotationDraftError("issue or mark ID is missing, unknown or repeated")
            seen.add(mark_id)
            marked_issues.add(issue_id)
            decision = classifications["decisions"].get(issue_id)
            severity = decision["severity"] if decision else None
            if issues[issue_id]["final_status"] not in _ACTIVE or severity not in _COLORS:
                raise AnnotationDraftError("only reviewer-classified active issues can be marked")
            if mark["snapshot_digest"] != source["snapshot_digest"] or mark["classification_digest"] != classifications["classification_digest"]:
                raise AnnotationDraftError("mark belongs to a stale ledger or classification revision")
            refs = [row["source_refs"]["page_ids"] for row in source["ledger_records"] if row["record_type"] == "issues" and row["record_id"] == issue_id]
            if len(refs) != 1 or page_id not in refs[0] or page_id not in pages:
                raise AnnotationDraftError("mark page is not an approved source reference for this issue")
            page_number = pages[page_id]["pdf_page"]
            if isinstance(page_number, bool) or not isinstance(page_number, int) or not 1 <= page_number <= len(doc):
                raise AnnotationDraftError("approved PDF page is invalid")
            page = doc[page_number - 1]
            dpi, size = mark["dpi"], mark["render_size_px"]
            try:
                target = pixel_bbox_to_page(page, dpi=dpi, render_size=size, bbox_px=mark["target_bbox_px"])
                note = pixel_bbox_to_page(page, dpi=dpi, render_size=size, bbox_px=mark["note_bbox_px"])
            except AnnotationGeometryError as exc:
                raise AnnotationDraftError(str(exc)) from exc
            points_px = mark["leader_points_px"]
            if not isinstance(points_px, (list, tuple)) or not 2 <= len(points_px) <= 5:
                raise AnnotationDraftError("leader must have 2 to 5 points")
            points = []
            for point in points_px:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise AnnotationDraftError("leader point is invalid")
                try:
                    pixel_bbox_to_page(page, dpi=dpi, render_size=size, bbox_px=(point[0], point[1], point[0] + 0.01, point[1] + 0.01))
                except (AnnotationGeometryError, TypeError) as exc:
                    raise AnnotationDraftError("leader point lies outside the render") from exc
                points.append(pymupdf.Point(point[0] * page.rect.width / size[0], point[1] * page.rect.height / size[1]) * page.derotation_matrix)
            target_rect, note_rect = pymupdf.Rect(target["target_bbox_page"]), pymupdf.Rect(note["target_bbox_page"])
            if not note_rect.contains(points[0]) or not target_rect.contains(points[-1]):
                raise AnnotationDraftError("leader must start in the note and end in the target")
            message = mark["note"]
            if not isinstance(message, str) or not message.startswith(f"{severity} · {issue_id} — ") or not 1 <= len(message.splitlines()) <= 2 or len(message) > 220:
                raise AnnotationDraftError("visible note must name severity and issue in at most two short lines")
            if target_rect.intersects(note_rect):
                raise AnnotationDraftError("note overlaps its marked target")
            inset = note_rect + (3, 2, -3, -2)
            font = pymupdf.Font(fontfile=str(font_path))
            line_width = inset.width if page.rotation in {0, 180} else inset.height
            if any(font.text_length(line, fontsize=8) > line_width for line in message.splitlines()):
                raise AnnotationDraftError("visible note line does not fit its chosen box")
            color = _COLORS[severity]
            fontname = "boqvietnamese"
            page.insert_font(fontname=fontname, fontfile=str(font_path))
            page.draw_rect(target_rect, color=color, width=0.7, overlay=True)
            page.draw_polyline(points, color=color, width=0.7, overlay=True)
            page.draw_rect(note_rect, color=color, fill=(1, 1, 1), width=0.7, overlay=True)
            if page.insert_textbox(inset, message, fontname=fontname, fontsize=8, color=color, rotate=page.rotation, overlay=True) < 0:
                raise AnnotationDraftError("visible note does not fit its chosen box")
            qa.append({
                "mark_id": mark_id, "issue_id": issue_id, "severity": severity, "page_id": page_id,
                "lineage_ids": list(issues[issue_id].get("lineage_ids", [])),
                "pdf_page": page_number, "source_pdf_sha256": pdf_sha256,
                "snapshot_digest": source["snapshot_digest"],
                "classification_digest": classifications["classification_digest"],
                "target": target, "note_bbox_px": mark["note_bbox_px"],
                "note_bbox_page": note["target_bbox_page"], "leader_points_px": points_px,
                "leader_points_page": [tuple(point) for point in points], "note": message,
                "annotation_qa_status": "UNVERIFIED", "crop_review": None,
            })
        output = doc.tobytes(garbage=4, deflate=True)
    return output, {
        "source_pdf_sha256": pdf_sha256,
        "output_pdf_sha256": hashlib.sha256(output).hexdigest(),
        "snapshot_digest": source["snapshot_digest"],
        "classification_digest": classifications["classification_digest"],
        "annotation_qa_status": "UNVERIFIED",
        "missing_active_issue_ids": sorted(pdf_issue_ids - marked_issues),
        "marks": qa,
    }


def render_annotation_review_images(pdf_bytes: bytes, mark: dict[str, Any]) -> dict[str, bytes]:
    """Render the exact final PDF page and tight target/note crops for human review."""
    target = mark["target"]
    width, height = target["render_size_px"]
    if width * height > _MAX_REVIEW_PIXELS:
        raise AnnotationDraftError("annotation review render exceeds the safe pixel limit")
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_no = mark["pdf_page"]
        if isinstance(page_no, bool) or not isinstance(page_no, int) or not 1 <= page_no <= len(doc):
            raise AnnotationDraftError("annotation review page is invalid")
        pix = doc[page_no - 1].get_pixmap(dpi=target["dpi"], annots=False)
        if (pix.width, pix.height) != (width, height):
            raise AnnotationDraftError("annotation review render differs from recorded geometry")
        images = {"page": pix.tobytes("png")}
        for kind, bbox in (("target", target["target_bbox_px"]), ("note", mark["note_bbox_px"])):
            x0, y0, x1, y1 = bbox
            if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                raise AnnotationDraftError("annotation review crop lies outside the render")
            margin = 80 if kind == "target" else 40
            clip = pymupdf.IRect(
                max(0, math.floor(x0) - margin), max(0, math.floor(y0) - margin),
                min(width, math.ceil(x1) + margin), min(height, math.ceil(y1) + margin),
            )
            images[kind] = pymupdf.Pixmap(pix, pix.width, pix.height, clip).tobytes("png")
        return images


def summarize_annotation_reviews(qa: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Use only the latest explicit reviewer event for each mark of this PDF draft."""
    mark_ids = {mark["mark_id"] for mark in qa["marks"]}
    if len(mark_ids) != len(qa["marks"]):
        raise AnnotationDraftError("annotation QA ledger has duplicate mark IDs")
    latest: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, int]] = set()
    for event in events:
        mark_id, revision = event["mark_id"], event["revision_no"]
        if (
            mark_id not in mark_ids or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1
            or (mark_id, revision) in seen or event["output_pdf_sha256"] != qa["output_pdf_sha256"]
            or event["result"] not in {"PASS", "FAIL"}
        ):
            raise AnnotationDraftError("annotation review event does not match this PDF draft")
        seen.add((mark_id, revision))
        if mark_id not in latest or revision > latest[mark_id]["revision_no"]:
            latest[mark_id] = event
    counts = {"PASS": 0, "FAIL": 0, "UNVERIFIED": 0}
    for mark_id in mark_ids:
        counts[latest[mark_id]["result"] if mark_id in latest else "UNVERIFIED"] += 1
    status = "FAIL" if counts["FAIL"] else "PASS" if mark_ids and counts["PASS"] == len(mark_ids) and not qa["missing_active_issue_ids"] else "UNVERIFIED"
    return {"annotation_qa_status": status, "counts": counts, "latest_reviews": latest}
