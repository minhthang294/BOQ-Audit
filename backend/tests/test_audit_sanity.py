import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.audit_sanity import check_detail_rows


class AuditSanityTests(unittest.TestCase):
    def test_flags_fraction_duplicate_and_missing_stt(self):
        result = check_detail_rows([
            {"row_id": "r1", "unit": "cái", "reported_quantity": 1.5, "section_id": "s", "stt": 1},
            {"row_id": "r2", "unit": "m", "reported_quantity": 2, "section_id": "s", "stt": 1},
            {"row_id": "r3", "unit": "m", "reported_quantity": 2, "section_id": "s", "stt": 3},
        ])
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual({item["anomaly"] for item in result["anomalies"]}, {"DISCRETE_FRACTION", "STT_DUPLICATE", "STT_GAP"})
        self.assertEqual(result["anomalies"][-1]["missing"], [2])

    def test_accepts_integer_quantity_and_consecutive_stt(self):
        result = check_detail_rows([
            {"row_id": "r1", "unit": "bộ", "reported_quantity": "2", "section_id": "s", "stt": 1},
            {"row_id": "r2", "unit": "m", "reported_quantity": 2, "section_id": "s", "stt": 2},
        ])
        self.assertEqual(result["status"], "NO_ANOMALIES")
        self.assertEqual(result["checked_rows"], 2)
        self.assertEqual(len(result["input_digest"]), 64)

    def test_bad_input_is_unavailable(self):
        self.assertEqual(check_detail_rows(None)["status"], "UNAVAILABLE")
        self.assertEqual(check_detail_rows([{"row_id": "r", "value": float("nan")}])["status"], "UNAVAILABLE")

    def test_flags_missing_stt_and_empty_rows_are_not_a_clean_pass(self):
        result = check_detail_rows([{"row_id": "r1", "section_id": "s", "stt": 1}, {"row_id": "r2", "section_id": "s"}])
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertIn("STT_MISSING", {item["anomaly"] for item in result["anomalies"]})
        self.assertEqual(check_detail_rows([])["status"], "NO_DETAIL_ROWS")


if __name__ == "__main__":
    unittest.main()
