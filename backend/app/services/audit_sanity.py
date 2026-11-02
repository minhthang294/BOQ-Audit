"""Deterministic advisory checks over persisted Pass 1 detail rows."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

DISCRETE_UNITS = {"cái", "bộ", "biển", "lỗ", "cọc", "gối", "khe", "thanh", "chiếc"}


def check_detail_rows(rows: Any) -> dict[str, Any]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return {"status": "UNAVAILABLE", "checked_rows": 0, "input_digest": None, "anomalies": [], "advisory": True}
    try:
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return {"status": "UNAVAILABLE", "checked_rows": 0, "input_digest": None, "anomalies": [], "advisory": True}

    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    anomalies = []
    sections: dict[str, list[tuple[str, int | None]]] = {}
    for index, row in enumerate(rows):
        row_id = row.get("row_id", f"INDEX-{index}")
        unit = str(row.get("unit", "")).strip().lower()
        quantity = row.get("reported_quantity")
        if unit in DISCRETE_UNITS and quantity is not None and quantity != "":
            try:
                value = Decimal(str(quantity).replace(",", "."))
                if not value.is_finite():
                    raise InvalidOperation
                if value != value.to_integral_value():
                    anomalies.append({"row_id": row_id, "anomaly": "DISCRETE_FRACTION", "observed": quantity, "unit": unit})
            except (InvalidOperation, ValueError):
                anomalies.append({"row_id": row_id, "anomaly": "NOT_NUMERIC", "observed": quantity, "unit": unit})

        section = row.get("section_id")
        stt = row.get("stt")
        if section is not None:
            section_rows = sections.setdefault(str(section), [])
            if stt is None:
                section_rows.append((str(row_id), None))
                anomalies.append({"row_id": row_id, "anomaly": "STT_MISSING", "section_id": section})
                continue
            try:
                observed = int(stt)
                if isinstance(stt, bool) or str(stt).strip() != str(observed) or observed < 1:
                    raise ValueError
                section_rows.append((str(row_id), observed))
            except (TypeError, ValueError):
                anomalies.append({"row_id": row_id, "anomaly": "STT_NOT_INTEGER", "section_id": section, "observed": stt})

    for section, values in sections.items():
        numbers = [number for _, number in values if number is not None]
        duplicates = {number for number, count in Counter(numbers).items() if count > 1}
        missing = sorted(set(range(1, len(values) + 1)) - set(numbers))
        for row_id, number in values:
            if number in duplicates:
                anomalies.append({"row_id": row_id, "anomaly": "STT_DUPLICATE", "section_id": section, "observed": number})
        if missing:
            anomalies.append({"row_id": section, "anomaly": "STT_GAP", "section_id": section, "missing": missing})

    return {
        "status": "NO_DETAIL_ROWS" if not rows else "REVIEW_REQUIRED" if anomalies else "NO_ANOMALIES",
        "checked_rows": len(rows),
        "input_digest": digest,
        "anomalies": anomalies,
        "advisory": True,
    }
