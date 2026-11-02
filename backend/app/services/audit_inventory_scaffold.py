"""Controller-owned source scaffolding for Q03e.

This module only derives candidate inventory records from source files. It never
creates targets or expected work items, never approves inventory, and never
claims that the source inventory is complete.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

import fitz
import openpyxl

from app.services.audit_coverage import CoverageInputError


class SourceScaffoldError(CoverageInputError):
    """A source file could not be read into candidate inventory records."""


def _copy_and_hash(source: Path, destination: Path, kind: str) -> str:
    digest = hashlib.sha256()
    try:
        with source.open("rb") as src, destination.open("wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
    except (OSError, ValueError) as exc:
        raise SourceScaffoldError(f"unreadable {kind} source") from exc
    return f"sha256:{digest.hexdigest()}"


def _as_path(value: Any) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        if str(value).strip():
            return value
        raise SourceScaffoldError("source path must be a non-empty path")
    if isinstance(value, str):
        if value.strip():
            return Path(value)
        raise SourceScaffoldError("source path must be a non-empty string or Path")
    raise SourceScaffoldError("source path must be a non-empty string or Path")


def _pdf_page_count(path: Path) -> int:
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise SourceScaffoldError("unreadable PDF source") from exc
    try:
        if getattr(document, "is_encrypted", False) and not document.authenticate(""):
            raise SourceScaffoldError("encrypted PDF requires a password")
        return int(document.page_count)
    except SourceScaffoldError:
        raise
    except Exception as exc:
        raise SourceScaffoldError("unreadable PDF source") from exc
    finally:
        try:
            document.close()
        except Exception:
            pass


def _xlsx_sheet_names(path: Path) -> list[str]:
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    except Exception as exc:
        raise SourceScaffoldError("unreadable XLSX source") from exc
    try:
        return [str(name) for name in workbook.sheetnames]
    except Exception as exc:
        raise SourceScaffoldError("unreadable XLSX source") from exc
    finally:
        try:
            workbook.close()
        except Exception:
            pass


def build_source_inventory(pdf_path=None, xlsx_path=None):
    """Build a deterministic candidate inventory from source files."""
    pdf_source = _as_path(pdf_path)
    xlsx_source = _as_path(xlsx_path)

    if pdf_source is None and xlsx_source is None:
        raise SourceScaffoldError("at least one source file is required")

    documents: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    sheets: list[dict[str, Any]] = []

    if pdf_source is not None:
        if not pdf_source.is_file() or pdf_source.suffix.lower() != ".pdf":
            raise SourceScaffoldError("invalid PDF source")
        with tempfile.TemporaryDirectory(prefix="boq-scaffold-") as temporary:
            snapshot = Path(temporary) / "source.pdf"
            digest = _copy_and_hash(pdf_source, snapshot, "PDF")
            page_count = _pdf_page_count(snapshot)
            document_id = "pdf"
            documents.append({"document_id": document_id, "revision_digest": digest})
            for page_number in range(1, page_count + 1):
                pages.append(
                    {
                        "page_id": f"{document_id}-P{page_number:05d}",
                        "document_id": document_id,
                        "revision_digest": digest,
                        "pdf_page": page_number,
                    }
                )

    if xlsx_source is not None:
        if not xlsx_source.is_file() or xlsx_source.suffix.lower() != ".xlsx":
            raise SourceScaffoldError("invalid XLSX source")
        with tempfile.TemporaryDirectory(prefix="boq-scaffold-") as temporary:
            snapshot = Path(temporary) / "source.xlsx"
            digest = _copy_and_hash(xlsx_source, snapshot, "XLSX")
            sheet_names = _xlsx_sheet_names(snapshot)
            document_id = "xlsx"
            documents.append({"document_id": document_id, "revision_digest": digest})
            for sheet_index, sheet_name in enumerate(sheet_names, start=1):
                sheets.append(
                    {
                        "sheet_id": f"{document_id}-S{sheet_index:05d}",
                        "document_id": document_id,
                        "revision_digest": digest,
                        "sheet_index": sheet_index,
                        "sheet_name": sheet_name,
                    }
                )

    return {
        "documents": documents,
        "pages": pages,
        "sheets": sheets,
        "targets": [],
        "expected_work_items": [],
    }


__all__ = ["SourceScaffoldError", "build_source_inventory"]
