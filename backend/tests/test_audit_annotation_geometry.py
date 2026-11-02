import pymupdf
import pytest

from app.services.audit_annotation_geometry import AnnotationGeometryError, pixel_bbox_to_page


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pixel_bbox_round_trips_on_rotated_and_cropped_page(rotation):
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=100)
    page.set_cropbox(pymupdf.Rect(20, 10, 180, 90))
    page.set_rotation(rotation)
    pix = page.get_pixmap(dpi=144, annots=False)
    result = pixel_bbox_to_page(page, dpi=144, render_size=(pix.width, pix.height), bbox_px=(10, 12, 30, 24))
    back_to_render = pymupdf.Rect(result["target_bbox_page"]) * page.rotation_matrix
    assert abs(back_to_render.x0 * pix.width / page.rect.width - 10) < 0.02
    assert abs(back_to_render.y0 * pix.height / page.rect.height - 12) < 0.02
    assert result["rotation"] == rotation
    assert result["cropbox"] == tuple(page.cropbox)
    doc.close()


def test_pixel_bbox_rejects_outside_or_wrong_render_geometry():
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=100)
    with pytest.raises(AnnotationGeometryError):
        pixel_bbox_to_page(page, dpi=144, render_size=(400, 200), bbox_px=(10, 10, 401, 20))
    with pytest.raises(AnnotationGeometryError):
        pixel_bbox_to_page(page, dpi=144, render_size=(200, 200), bbox_px=(10, 10, 20, 20))
    with pytest.raises(AnnotationGeometryError):
        pixel_bbox_to_page(page, dpi=144, render_size=(400, 200), bbox_px=(float("nan"), 10, 20, 20))
    doc.close()
