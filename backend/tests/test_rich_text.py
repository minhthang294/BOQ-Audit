"""Regression: rich-text sanitizer strips attributes/tags and blocks script/style content."""
from app.core.rich_text import sanitize_rich_text
from app.schemas.api import AdminUpdateJob

UNSAFE = (
    '<script>alert(1)</script><p onclick="evil()">Ghi <strong>chú</strong>'
    '<img src=x onerror=evil()><a href="javascript:evil()">link</a></p>'
    '<style>p{}</style><div><u>ok</u></div>'
)
SAFE = "<p>Ghi <strong>chú</strong>link</p><div><u>ok</u></div>"


def test_sanitize_rich_text_strips_attributes_and_unsafe_tags():
    assert sanitize_rich_text(UNSAFE) == SAFE
    assert "script" not in sanitize_rich_text("<script>x</script>")
    assert sanitize_rich_text("<p title='x' style='color:red'>ok</p>") == "<p>ok</p>"


def test_admin_update_job_sanitizes_customer_notes():
    assert AdminUpdateJob(customer_notes=UNSAFE).customer_notes == SAFE
    assert AdminUpdateJob(customer_notes=None).customer_notes is None
