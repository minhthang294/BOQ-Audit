"""Deterministic identities and source-anchor checks for contract v4."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


_CELL_RANGE = re.compile(r"^([A-Z]{1,3}[1-9][0-9]*)(?::([A-Z]{1,3}[1-9][0-9]*))?$")


def _stable_id(prefix: str, value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{prefix}-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


def source_observation_id(document_id: str, revision_digest: str, anchor: dict[str, Any]) -> str:
    return _stable_id("OBS", [document_id, revision_digest, anchor])


def estimate_line_id(document_id: str, revision_digest: str, sheet_id: str, cell_range: str) -> str:
    return _stable_id("LINE", [document_id, revision_digest, sheet_id, cell_range])


def estimate_line_group_id(line_ids: list[str]) -> str:
    return _stable_id("GROUP", sorted(line_ids))


def calculation_input_id(calc_id: str, index: int, observation_id: str) -> str:
    return _stable_id("IN", [calc_id, index, observation_id])


def boq_lineage_id(payload: dict[str, Any]) -> str:
    return _stable_id("LINK", {
        key: sorted(payload[key])
        for key in ("estimate_line_ids", "detail_row_ids", "summary_row_ids", "calc_ids")
    })


def validate_xlsx_cell_range(value: Any) -> tuple[int, int, int, int]:
    match = _CELL_RANGE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("XLSX range must use canonical A1 notation")

    def column_number(reference: str) -> int:
        column = re.match(r"[A-Z]+", reference).group()
        return sum((ord(char) - ord("A") + 1) * 26**index for index, char in enumerate(reversed(column)))

    start_ref, end_ref = match.group(1), match.group(2) or match.group(1)
    start_col, end_col = column_number(start_ref), column_number(end_ref)
    start_row = int(re.search(r"[1-9][0-9]*$", start_ref).group())
    end_row = int(re.search(r"[1-9][0-9]*$", end_ref).group())
    if end_col < start_col or end_row < start_row:
        raise ValueError("XLSX range end must not precede its start")
    return start_col, start_row, end_col, end_row


def validate_anchor(anchor: Any, *, page_ids: set[str], sheet_ids: set[str]) -> None:
    if not isinstance(anchor, dict):
        raise ValueError("source observation anchor must be an object")
    if anchor.get("kind") == "pdf_bbox":
        if set(anchor) != {"kind", "page_id", "bbox", "rotation"} or anchor["page_id"] not in page_ids:
            raise ValueError("PDF observation anchor is outside the assigned page scope")
        bbox = anchor["bbox"]
        if (
            not isinstance(bbox, list) or len(bbox) != 4
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in bbox)
            or bbox[0] < 0 or bbox[1] < 0 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]
        ):
            raise ValueError("PDF observation bbox must be finite, ordered, and non-negative")
        rotation = anchor["rotation"]
        if isinstance(rotation, bool) or rotation not in {0, 90, 180, 270}:
            raise ValueError("PDF observation rotation must be 0, 90, 180, or 270")
        return
    if anchor.get("kind") == "xlsx_range":
        if set(anchor) != {"kind", "sheet_id", "range", "row_span"} or anchor["sheet_id"] not in sheet_ids:
            raise ValueError("XLSX observation anchor is outside the assigned sheet scope")
        span = anchor["row_span"]
        try:
            _, start, _, end = validate_xlsx_cell_range(anchor["range"])
        except ValueError as exc:
            raise ValueError("XLSX observation A1 range must be ordered and match a positive row_span") from exc
        if isinstance(span, bool) or not isinstance(span, int) or span < 1:
            raise ValueError("XLSX observation range must be canonical A1 notation with a positive row_span")
        if span != end - start + 1:
            raise ValueError("XLSX observation row_span does not match its A1 range")
        return
    raise ValueError("source observation anchor kind is unsupported")
