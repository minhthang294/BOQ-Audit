"""Detect absolute server paths that must not reach customer-visible outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

SENSITIVE_ABSOLUTE_PREFIXES = (
    "/home/",
    "/root/",
    "/var/tmp/",
    "/tmp/",
    "/data/",
    "/opt/",
    "/srv/",
    "file://",
)


def contains_sensitive_path(value: str) -> bool:
    return any(prefix in value for prefix in SENSITIVE_ABSOLUTE_PREFIXES)


def workbook_path_leak_locations(workbook, worksheets=None) -> list[str]:
    locations: list[str] = []
    for sheet in (worksheets if worksheets is not None else workbook.worksheets):
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and contains_sensitive_path(cell.value):
                    locations.append(f"{sheet.title}!{cell.coordinate}")
    return locations


def _pdf_path_leak_locations(path: Path) -> list[str]:
    import fitz

    locations: list[str] = []
    document = fitz.open(path)
    try:
        for name, value in (document.metadata or {}).items():
            if isinstance(value, str) and contains_sensitive_path(value):
                locations.append(f"metadata:{name}")
        for index, page in enumerate(document, start=1):
            if contains_sensitive_path(page.get_text()):
                locations.append(f"page:{index}:text")
            for annotation in page.annots() or []:
                if contains_sensitive_path(annotation.info.get("content", "")):
                    locations.append(f"page:{index}:annotation")
    finally:
        document.close()
    return locations


def output_path_leak_locations(paths: Iterable[Path]) -> list[str]:
    """Return sheet/page locations with absolute server paths; never returns the value itself."""
    locations: list[str] = []
    for path in paths:
        suffix = path.suffix.lower()
        try:
            if suffix in {".xlsx", ".xls"}:
                from openpyxl import load_workbook

                workbook = load_workbook(path, read_only=True, data_only=False)
                try:
                    locations.extend(f"{path.name}:{location}" for location in workbook_path_leak_locations(workbook))
                finally:
                    workbook.close()
            elif suffix == ".pdf":
                locations.extend(f"{path.name}:{location}" for location in _pdf_path_leak_locations(path))
        except Exception:
            # Leak detection is advisory to output validity; unreadable files are handled by the quality gate.
            continue
    return locations
