"""Convert full-page render pixels to unrotated PyMuPDF annotation coordinates."""

import math

import pymupdf


class AnnotationGeometryError(ValueError):
    """An annotation target cannot be located safely on this render."""


def pixel_bbox_to_page(page: pymupdf.Page, *, dpi: int, render_size: tuple[int, int], bbox_px: tuple[float, float, float, float]) -> dict:
    """Bind one pixel bbox to the page crop/rotation used by a full-page render."""
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 600:
        raise AnnotationGeometryError("render DPI must be between 72 and 600")
    if not isinstance(render_size, (list, tuple)) or len(render_size) != 2 or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in render_size):
        raise AnnotationGeometryError("render dimensions are invalid")
    width, height = render_size
    if abs(width - page.rect.width * dpi / 72) > 1.1 or abs(height - page.rect.height * dpi / 72) > 1.1:
        raise AnnotationGeometryError("render dimensions do not match page crop, rotation and DPI")
    if not isinstance(bbox_px, (list, tuple)) or len(bbox_px) != 4 or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in bbox_px):
        raise AnnotationGeometryError("pixel bbox is invalid")
    x0, y0, x1, y1 = bbox_px
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise AnnotationGeometryError("pixel bbox lies outside the rendered page")
    rotated = pymupdf.Rect(x0 * page.rect.width / width, y0 * page.rect.height / height, x1 * page.rect.width / width, y1 * page.rect.height / height)
    unrotated = rotated * page.derotation_matrix
    visible = page.rect * page.derotation_matrix
    if not visible.contains(unrotated):
        raise AnnotationGeometryError("transformed bbox lies outside the visible crop")
    return {
        "page_number": page.number + 1,
        "rotation": page.rotation,
        "cropbox": tuple(page.cropbox),
        "render_size_px": render_size,
        "dpi": dpi,
        "target_bbox_px": bbox_px,
        "target_bbox_page": tuple(round(value, 4) for value in unrotated),
    }
