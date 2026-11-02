import pytest

from app.services.audit_batch import BatchOutputError
from app.services.audit_report_source import build_classification_state, build_report_source


def test_report_source_counts_issue_records_from_one_detached_checkpoint_snapshot():
    checkpoint = {
        "run_id": 1, "approval_id": 2, "source_digest": "a" * 64,
        "skill_digest": "b" * 64, "inventory_digest": "c" * 64,
        "batch_plan_digest": "d" * 64, "version": 4, "pass_no": 3,
        "gate_reports": {"1": "e" * 64, "2": "f" * 64}, "ledger": [],
        "batch_results": [], "ledger_records": [
            {"record_type": "issues", "record_id": "I-01", "payload": {"issue_id": "I-01", "final_status": "CONFIRMED_ERROR"}},
            {"record_type": "issues", "record_id": "I-02", "payload": {"issue_id": "I-02", "final_status": "FALSE_POSITIVE"}},
            {"record_type": "issue_candidates", "record_id": "C-01", "payload": {"candidate_id": "C-01"}},
        ],
    }
    source = build_report_source(checkpoint)
    assert source["ledger_record_counts"]["issues"] == 2
    assert source["issue_candidate_count"] == 1
    assert source["issue_final_status_counts"] == {"CONFIRMED_ERROR": 1, "FALSE_POSITIVE": 1}
    assert build_report_source(checkpoint)["snapshot_digest"] == source["snapshot_digest"]
    checkpoint["ledger_records"][0]["payload"]["final_status"] = "UNRESOLVED"
    assert source["projection"]["issues"][0]["final_status"] == "CONFIRMED_ERROR"
    assert build_report_source(checkpoint)["snapshot_digest"] != source["snapshot_digest"]


def test_classification_digest_tracks_effective_reviewer_revision():
    source = {"snapshot_digest": "a" * 64, "projection": {"issues": [{"issue_id": "I-01", "final_status": "CONFIRMED_ERROR"}]}}
    first = {"issue_id": "I-01", "revision_no": 1, "final_status": "CONFIRMED_ERROR", "severity": "HIGH", "reviewer_user_id": 1, "reason": "Đã kiểm lại tờ nguồn"}
    second = {**first, "revision_no": 2, "severity": "MEDIUM", "reason": "Đối chiếu lại mức ảnh hưởng"}
    before = build_classification_state(source, [first])
    after = build_classification_state(source, [first, second])
    assert before["classification_digest"] != after["classification_digest"]
    assert after["decisions"]["I-01"]["severity"] == "MEDIUM"
    assert after["classified_issue_count"] == 1


def test_report_source_counts_only_active_issues_with_pdf_page_refs():
    source = {
        "projection": {"issues": [
            {"issue_id": "pdf-error", "final_status": "CONFIRMED_ERROR"},
            {"issue_id": "sheet-error", "final_status": "UNRESOLVED"},
            {"issue_id": "closed", "final_status": "FALSE_POSITIVE"},
        ]},
        "ledger_records": [
            {"record_type": "issues", "record_id": "pdf-error", "source_refs": {"page_ids": ["drawing-page-1"], "sheet_ids": []}},
            {"record_type": "issues", "record_id": "sheet-error", "source_refs": {"page_ids": [], "sheet_ids": ["s1"]}},
            {"record_type": "issues", "record_id": "closed", "source_refs": {"page_ids": ["drawing-page-1"], "sheet_ids": []}},
        ],
    }
    from app.services.audit_report_source import pdf_mark_required_issue_ids
    assert pdf_mark_required_issue_ids(source) == {"pdf-error"}
    assert pdf_mark_required_issue_ids(source, {"other-page"}) == set()


def _checkpoint_with(records):
    return {
        "run_id": 1, "approval_id": 2, "source_digest": "a" * 64,
        "skill_digest": "b" * 64, "inventory_digest": "c" * 64,
        "batch_plan_digest": "d" * 64, "version": 5, "pass_no": 3,
        "gate_reports": {"1": "e" * 64, "2": "f" * 64}, "ledger": [],
        "batch_results": [], "ledger_records": records,
    }


def test_report_source_accepts_resolved_domain_chain_before_export():
    checkpoint = _checkpoint_with([
        {"record_type": "detail_rows", "record_id": "ROW-1", "payload": {"row_id": "ROW-1"}},
        {"record_type": "calculation_ledger", "record_id": "CALC-1", "payload": {"calc_id": "CALC-1"}},
        {"record_type": "issue_candidates", "record_id": "CAND-1", "payload": {"candidate_id": "CAND-1"}},
        {"record_type": "summary_lineage", "record_id": "SUM-1", "payload": {"summary_row_id": "SUM-1", "source_rows": ["ROW-1"]}},
        {"record_type": "issues", "record_id": "I-01", "source_refs": {"page_ids": ["p1"], "sheet_ids": []},
         "payload": {"issue_id": "I-01", "claim": "synthetic", "final_status": "CONFIRMED_ERROR",
                     "calc_ids": ["CALC-1"], "candidate_ids": ["CAND-1"], "evidence": ["ev-1"]}},
        {"record_type": "pass3_review", "record_id": "REV-1", "payload": {
            "review_id": "REV-1", "calc_id": "CALC-1", "issue_ids": ["I-01"],
        }},
    ])
    source = build_report_source(checkpoint)
    assert source["ledger_record_counts"]["summary_lineage"] == 1
    assert source["pdf_mark_required_issue_count"] == 1


def test_report_source_rejects_dangling_domain_references_before_export():
    checkpoint = _checkpoint_with([
        {"record_type": "detail_rows", "record_id": "ROW-1", "payload": {"row_id": "ROW-1"}},
        {"record_type": "summary_lineage", "record_id": "SUM-1", "payload": {"summary_row_id": "SUM-1", "source_rows": ["ROW-MISSING"]}},
    ])
    with pytest.raises(BatchOutputError, match="summary_lineage.source_rows -> missing detail_rows ROW-MISSING"):
        build_report_source(checkpoint)

    checkpoint = _checkpoint_with([
        {"record_type": "issues", "record_id": "I-01", "payload": {
            "issue_id": "I-01", "final_status": "UNRESOLVED", "calc_ids": ["CALC-MISSING"],
        }},
    ])
    with pytest.raises(BatchOutputError, match="issues.calc_ids -> missing calculation_ledger CALC-MISSING"):
        build_report_source(checkpoint)
