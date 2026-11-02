import copy

import pytest

from app.services.audit_workflow import (
    ALLOWED_TRANSITIONS,
    EXECUTION_STATUSES,
    WorkItemInputError,
    WorkItemTransitionError,
    validate_transition,
    validate_work_items,
    BatchInvocationError,
    apply_ledger_patch,
    run_batches,
    transition_work_item,
    runnable_work_items,
)


def item(check_id, *, pass_no=1, status="PENDING", result="NOT_CHECKED", deps=None, evidence=None, reason=None):
    return {
        "check_id": check_id,
        "document_id": "doc-1",
        "revision_digest": "r1",
        "page_ids": ["p1"],
        "target_ids": [],
        "check_kind": "boq",
        "pass_no": pass_no,
        "dependencies": list(deps or []),
        "applicability_reason": reason,
        "evidence_ids": list(evidence or []),
        "result_status": result,
        "execution_status": status,
    }


def done_item(check_id, *, result="CHECKED", evidence=None, reason=None, **kwargs):
    return item(
        check_id,
        status="DONE",
        result=result,
        evidence=evidence if evidence is not None else ["ev-1"],
        reason=reason,
        **kwargs,
    )


def test_allowed_and_denied_transitions():
    pending = item("a")
    for new_status in ("RUNNING", "BLOCKED", "INVALIDATED"):
        transitioned = transition_work_item(pending, new_status)
        assert transitioned["execution_status"] == new_status
        assert pending["execution_status"] == "PENDING"

    running = item("b", status="RUNNING")
    for new_status in ("DONE", "BLOCKED", "INVALIDATED"):
        kwargs = {}
        if new_status == "DONE":
            kwargs = {"result_status": "CHECKED", "evidence_ids": ["ev"]}
        transitioned = transition_work_item(running, new_status, **kwargs)
        assert transitioned["execution_status"] == new_status

    blocked = item("c", status="BLOCKED")
    for new_status in ("PENDING", "INVALIDATED"):
        transitioned = transition_work_item(blocked, new_status)
        assert transitioned["execution_status"] == new_status

    done = done_item("d")
    transitioned = transition_work_item(done, "INVALIDATED")
    assert transitioned["execution_status"] == "INVALIDATED"
    pending_again = transition_work_item(transitioned, "PENDING")
    assert pending_again["execution_status"] == "PENDING"
    assert pending_again["result_status"] == "NOT_CHECKED"
    assert pending_again["evidence_ids"] == []
    assert pending_again["applicability_reason"] is None

    denied = [
        (old, new)
        for old in EXECUTION_STATUSES
        for new in EXECUTION_STATUSES
        if new not in ALLOWED_TRANSITIONS[old]
    ]
    for old, new in denied:
        with pytest.raises(WorkItemTransitionError):
            base = done_item("x") if old == "DONE" else item("x", status=old)
            transition_work_item(base, new)


def test_done_requires_valid_result_and_evidence():
    base = item("a", status="RUNNING")
    with pytest.raises(WorkItemInputError):
        transition_work_item(base, "DONE")
    with pytest.raises(WorkItemInputError):
        transition_work_item(base, "DONE", result_status="CHECKED", evidence_ids=[])
    with pytest.raises(WorkItemInputError):
        transition_work_item(base, "DONE", result_status="CHECKED", evidence_ids=[""])
    with pytest.raises(WorkItemInputError):
        transition_work_item(
            base, "DONE", result_status="NOT_APPLICABLE", evidence_ids=["ev"]
        )

    ok = transition_work_item(
        base, "DONE", result_status="NOT_APPLICABLE", evidence_ids=["ev"], applicability_reason="not applicable"
    )
    assert ok["result_status"] == "NOT_APPLICABLE"
    assert ok["applicability_reason"] == "not applicable"


def test_immutable_inputs_for_transition_and_scheduler():
    original = item("a")
    other = item("b")
    data = [original, other]
    snapshot = copy.deepcopy(data)

    transition_work_item(original, "RUNNING")
    runnable_work_items(data, 1)

    assert data == snapshot


def test_direct_dependency_states():
    a = item("a")
    b = item("b", deps=["a"])
    c = item("c")

    # Pending prerequisite blocks b.
    assert runnable_work_items([a, b, c], 1) == ["a", "c"]

    # DONE with CHECKED unblocks b.
    a_done = done_item("a")
    assert runnable_work_items([a_done, b, c], 1) == ["b", "c"]

    # UNRESOLVED is handled but still a valid prerequisite.
    a_unresolved = done_item("a", result="UNRESOLVED")
    assert runnable_work_items([a_unresolved, b], 1) == ["b"]

    # BLOCKED prerequisite does not unblock.
    a_blocked = item("a", status="BLOCKED")
    assert runnable_work_items([a_blocked, b], 1) == []

    # Invalid DONE (missing evidence) makes input invalid.
    a_bad = item("a", status="DONE", result="CHECKED", evidence=[])
    with pytest.raises(WorkItemInputError):
        runnable_work_items([a_bad, b], 1)


def test_unrelated_item_remains_runnable():
    a = item("a")
    b = item("b", deps=["a"])
    unrelated = item("c")
    # b blocked by a, but c has no dependency and is still runnable.
    assert runnable_work_items([a, b, unrelated], 1) == ["a", "c"]


def test_pass_filtering():
    a1 = item("a", pass_no=1)
    a2 = item("b", pass_no=2)
    a3 = item("c", pass_no=3)
    assert runnable_work_items([a1, a2, a3], 2) == ["b"]
    assert runnable_work_items([a1, a2, a3], 3) == ["c"]


def test_duplicate_missing_malformed_identifiers():
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a"), item("a")])
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a", deps=["missing"])])
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("", )])
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a", pass_no=4)])
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a", status="UNKNOWN")])
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a", result="UNKNOWN")])
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a", deps=["a", "a"])])
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a", deps=[123])])
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a", deps=[""])])


def test_missing_dependencies_field_is_invalid():
    base = item("a")
    base.pop("dependencies")
    with pytest.raises(WorkItemInputError):
        validate_work_items([base])


def test_malformed_status_values_raise_documented_errors():
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a", status=[])])
    with pytest.raises(WorkItemInputError):
        validate_work_items([item("a", result={})])
    with pytest.raises(WorkItemTransitionError):
        validate_transition([], "RUNNING")
    with pytest.raises(WorkItemTransitionError):
        validate_transition("PENDING", {})
    with pytest.raises(WorkItemTransitionError):
        transition_work_item(item("a"), [])


def test_cycle_is_not_runnable():
    a = item("a", deps=["b"])
    b = item("b", deps=["a"])
    assert runnable_work_items([a, b], 1) == []


def test_done_unresolved_not_clear_for_coverage_semantics():
    # UNRESOLVED is a valid prerequisite but remains distinct.
    a = done_item("a", result="UNRESOLVED")
    b = item("b", deps=["a"])
    assert runnable_work_items([a, b], 1) == ["b"]


def patch_item(check_id, result="CHECKED", evidence=None, reason=None):
    item = {"check_id": check_id, "result_status": result, "evidence_ids": list(evidence or ["ev"])}
    if reason is not None:
        item["applicability_reason"] = reason
    return item


def test_valid_multi_item_patch_merge():
    ledger = [
        item("a", status="RUNNING"),
        item("b", status="RUNNING"),
        item("c"),
    ]
    snapshot = copy.deepcopy(ledger)
    patch = {"items": [patch_item("a"), patch_item("b", result="UNRESOLVED", evidence=["ev-b"])]}
    result = apply_ledger_patch(ledger, ["a", "b"], patch)

    assert [x["check_id"] for x in result] == ["a", "b", "c"]
    assert result[0]["execution_status"] == "DONE"
    assert result[0]["result_status"] == "CHECKED"
    assert result[1]["result_status"] == "UNRESOLVED"
    assert result[2]["execution_status"] == "PENDING"
    assert ledger == snapshot


def test_patch_rejects_missing_extra_duplicate_out_of_scope_ids():
    ledger = [item("a", status="RUNNING"), item("b", status="RUNNING")]

    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a", "b"], {"items": [patch_item("a")]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a", "b"], {"items": [patch_item("a"), patch_item("b"), patch_item("a")]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a", "b"], {"items": [patch_item("a"), patch_item("c")]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a", "a"], {"items": [patch_item("a"), patch_item("a")]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["z"], {"items": [patch_item("z")]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, [], {"items": []})


def test_patch_rejects_non_running_items():
    ledger = [item("a", status="PENDING"), item("b", status="RUNNING")]
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a", "b"], {"items": [patch_item("a"), patch_item("b")]})
    ledger2 = [item("a", status="DONE", result="CHECKED", evidence=["ev"]), item("b", status="RUNNING")]
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger2, ["a", "b"], {"items": [patch_item("a"), patch_item("b")]})
    ledger3 = [item("a", status="BLOCKED"), item("b", status="RUNNING")]
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger3, ["a", "b"], {"items": [patch_item("a"), patch_item("b")]})
    ledger4 = [item("a", status="INVALIDATED"), item("b", status="RUNNING")]
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger4, ["a", "b"], {"items": [patch_item("a"), patch_item("b")]})


def test_patch_rejects_unknown_keys_and_scope_rewrites():
    ledger = [item("a", status="RUNNING")]
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": ["ev"], "execution_status": "DONE"}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": ["ev"], "pass_no": 2}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": ["ev"], "dependencies": []}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": ["ev"], "document_id": "x"}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"extra": []})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": "not-a-list"})


def test_patch_rejects_invalid_result_evidence_and_na_reason():
    ledger = [item("a", status="RUNNING")]
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "DONE", "evidence_ids": ["ev"]}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": []}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": [""]}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": ["ev", "ev"]}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "NOT_APPLICABLE", "evidence_ids": ["ev"]}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "NOT_APPLICABLE", "evidence_ids": ["ev"], "applicability_reason": ""}]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "NOT_APPLICABLE", "evidence_ids": ["ev"], "applicability_reason": 123}]})
    ok = apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "NOT_APPLICABLE", "evidence_ids": ["ev"], "applicability_reason": "reason"}]})
    assert ok[0]["result_status"] == "NOT_APPLICABLE"
    assert ok[0]["applicability_reason"] == "reason"


def test_patch_invalid_member_does_not_partially_mutate():
    ledger = [item("a", status="RUNNING"), item("b", status="RUNNING")]
    snapshot = copy.deepcopy(ledger)
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(
            ledger,
            ["a", "b"],
            {"items": [patch_item("a"), {"check_id": "b", "result_status": "CHECKED", "evidence_ids": []}]},
        )
    assert ledger == snapshot
    assert all(x["execution_status"] == "RUNNING" for x in ledger)


def test_patch_preserves_order_and_non_batch_fields():
    ledger = [
        item("a", status="RUNNING"),
        item("z"),
        item("b", status="RUNNING"),
    ]
    original_z = copy.deepcopy(ledger[1])
    result = apply_ledger_patch(ledger, ["b", "a"], {"items": [patch_item("b"), patch_item("a")]})
    assert [x["check_id"] for x in result] == ["a", "z", "b"]
    assert result[0]["execution_status"] == "DONE"
    assert result[2]["execution_status"] == "DONE"
    assert result[1] == original_z


def test_patch_requires_list_batch_and_object_patch():
    ledger = [item("a", status="RUNNING")]
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ("a",), {"items": [patch_item("a")]})
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], [patch_item("a")])


def test_patch_rejects_applicability_reason_for_non_na():
    ledger = [item("a", status="RUNNING")]
    with pytest.raises(WorkItemInputError):
        apply_ledger_patch(ledger, ["a"], {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": ["ev"], "applicability_reason": "reason"}]})


def test_patch_result_is_deeply_detached():
    ledger = [item("a", status="RUNNING")]
    ledger[0]["page_ids"] = ["p1"]
    result = apply_ledger_patch(ledger, ["a"], {"items": [patch_item("a")]})
    result[0]["page_ids"].append("p2")
    result[0]["dependencies"].append("x")
    assert ledger[0]["page_ids"] == ["p1"]
    assert ledger[0]["dependencies"] == []
    assert result[0]["page_ids"] is not ledger[0]["page_ids"]


def test_patch_clears_stale_applicability_reason_on_checked():
    ledger = [item("a", status="RUNNING", reason="stale NA explanation")]
    result = apply_ledger_patch(ledger, ["a"], {"items": [patch_item("a", result="CHECKED")]})
    assert result[0]["result_status"] == "CHECKED"
    assert result[0]["applicability_reason"] is None


def invoke_ok(batch_ids, timeout_seconds, fingerprint, feedback):
    return {"items": [{"check_id": cid, "result_status": "CHECKED", "evidence_ids": ["ev"]} for cid in batch_ids]}


def test_q04c_independent_batch_failure_and_completion():
    ledger = [item("a", status="PENDING"), item("b", status="PENDING"), item("c", status="PENDING"), item("d", status="PENDING")]
    # a depends on nothing; b depends on a; c,d independent batch
    ledger[1]["dependencies"] = ["a"]
    # Make b runnable only after a done, but batch [b] initially not ready, so use batches [a], [c,d]
    batches = [["a"], ["c", "d"]]

    def invoke(batch_ids, timeout, fp, fb):
        if batch_ids == ["a"]:
            raise BatchInvocationError("fp-a", "safe feedback a")
        return invoke_ok(batch_ids, timeout, fp, fb)

    # First batch fails all 3 attempts -> BLOCKED; second batch still runs.
    result = run_batches(ledger, 1, batches, invoke, 5, 10)
    assert result["budget_exhausted"] is False
    assert result["done_batches"] == [["c", "d"]]
    assert result["blocked_batches"] == [["a"]]
    assert result["unrun_batches"] == []
    by_id = {x["check_id"]: x for x in result["ledger"]}
    assert by_id["a"]["execution_status"] == "BLOCKED"
    assert by_id["c"]["execution_status"] == "DONE"
    assert by_id["d"]["execution_status"] == "DONE"
    assert result["attempt_count"] == 4  # 3 failed attempts for a + 1 success for c,d


def test_q04c_repeated_fingerprint_does_not_exceed_two_repairs():
    ledger = [item("a", status="PENDING")]
    calls = []

    def invoke(batch_ids, timeout, fp, fb):
        calls.append((batch_ids, timeout, fp, fb))
        raise BatchInvocationError("same-fp", "same feedback")

    result = run_batches(ledger, 1, [["a"]], invoke, 7, 10)
    assert len(calls) == 3
    assert [c[2] for c in calls] == ["", "same-fp", "same-fp"]
    assert result["attempt_count"] == 3
    assert result["blocked_batches"] == [["a"]]


def test_q04c_timeout_passed_unchanged():
    ledger = [item("a", status="PENDING")]
    seen = []

    def invoke(batch_ids, timeout, fp, fb):
        seen.append(timeout)
        raise BatchInvocationError("fp", "feedback")

    run_batches(ledger, 1, [["a"]], invoke, 11, 10)
    assert seen == [11, 11, 11]


def test_q04c_budget_exhaustion_returns_unrun_items():
    ledger = [item("a", status="PENDING"), item("b", status="PENDING")]
    calls = []

    def invoke(batch_ids, timeout, fp, fb):
        calls.append(batch_ids)
        raise BatchInvocationError("fp", "feedback")

    # Budget allows only 2 calls: first batch consumes 2? Actually 3 needed, so budget exhausted mid-first.
    result = run_batches(ledger, 1, [["a"], ["b"]], invoke, 5, 2)
    assert result["budget_exhausted"] is True
    assert result["attempt_count"] == 2
    assert result["blocked_batches"] == [["a"]]
    assert result["unrun_batches"] == [["b"]]
    assert result["unrun_items"] == ["b"]
    assert len(calls) == 2


def test_q04c_callback_and_patch_failures_do_not_leave_running():
    ledger = [item("a", status="PENDING")]
    # Patch invalid on every call
    def invoke(batch_ids, timeout, fp, fb):
        return {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": []}]}

    result = run_batches(ledger, 1, [["a"]], invoke, 5, 10)
    assert result["blocked_batches"] == [["a"]]
    assert result["ledger"][0]["execution_status"] == "BLOCKED"
    assert result["ledger"][0]["execution_status"] != "RUNNING"

    # Unknown exception also fails closed
    ledger2 = [item("b", status="PENDING")]
    def invoke2(batch_ids, timeout, fp, fb):
        raise RuntimeError("boom")
    result2 = run_batches(ledger2, 1, [["b"]], invoke2, 5, 10)
    assert result2["blocked_batches"] == [["b"]]
    assert result2["ledger"][0]["execution_status"] == "BLOCKED"


def test_q04c_patch_validation_does_not_commit_partial():
    ledger = [item("a", status="PENDING"), item("b", status="PENDING")]

    def invoke(batch_ids, timeout, fp, fb):
        return {"items": [{"check_id": "a", "result_status": "CHECKED", "evidence_ids": ["ev"]}, {"check_id": "b", "result_status": "CHECKED", "evidence_ids": []}]}

    result = run_batches(ledger, 1, [["a", "b"]], invoke, 5, 10)
    assert result["blocked_batches"] == [["a", "b"]]
    assert all(x["execution_status"] == "BLOCKED" for x in result["ledger"])


def test_q04c_unready_batch_rejected():
    ledger = [item("a", status="PENDING"), item("b", status="PENDING", deps=["a"])]
    with pytest.raises(WorkItemInputError):
        run_batches(ledger, 1, [["b"]], lambda *a: {}, 5, 10)
    with pytest.raises(WorkItemInputError):
        run_batches(ledger, 1, [["a"], ["a"]], lambda *a: {}, 5, 10)
    with pytest.raises(WorkItemInputError):
        run_batches(ledger, 1, [[]], lambda *a: {}, 5, 10)
    with pytest.raises(WorkItemInputError):
        run_batches(ledger, 1, [["missing"]], lambda *a: {}, 5, 10)


def test_q04c_no_global_completion_flag():
    ledger = [item("a", status="PENDING")]
    result = run_batches(ledger, 1, [["a"]], invoke_ok, 5, 10)
    assert "global_stage_complete" not in result
    assert "stage_complete" not in result
    assert "published" not in result
    assert result["done_batches"] == [["a"]]


def test_q04c_exact_budget_success_leaves_later_batch_unrun():
    ledger = [item("a", status="PENDING"), item("b", status="PENDING")]
    result = run_batches(ledger, 1, [["a"], ["b"]], invoke_ok, 5, 1)
    assert result["budget_exhausted"] is True
    assert result["done_batches"] == [["a"]]
    assert result["unrun_batches"] == [["b"]]
    assert result["unrun_items"] == ["b"]
    assert {x["check_id"]: x["execution_status"] for x in result["ledger"]} == {"a": "DONE", "b": "PENDING"}


def test_q04c_skips_batch_directly_dependent_on_blocked_batch():
    ledger = [item("a", status="PENDING"), item("b", status="PENDING", deps=["a"]), item("c", status="PENDING")]

    def invoke(batch_ids, timeout, fingerprint, feedback):
        if batch_ids == ["a"]:
            raise BatchInvocationError("fp-a", "safe feedback a")
        return invoke_ok(batch_ids, timeout, fingerprint, feedback)

    result = run_batches(ledger, 1, [["a"], ["b"], ["c"]], invoke, 5, 10)
    assert result["blocked_batches"] == [["a"]]
    assert result["done_batches"] == [["c"]]
    assert result["unrun_batches"] == [["b"]]
    assert result["unrun_items"] == ["b"]
    assert {x["check_id"]: x["execution_status"] for x in result["ledger"]} == {
        "a": "BLOCKED",
        "b": "PENDING",
        "c": "DONE",
    }


def test_q04c_rejects_initial_unready_batch():
    ledger = [item("a", status="PENDING"), item("b", status="PENDING", deps=["a"])]
    with pytest.raises(WorkItemInputError):
        run_batches(ledger, 1, [["b"]], lambda *args: {}, 5, 10)


def test_q04c_exact_budget_single_successful_batch_is_not_exhausted():
    ledger = [item("a", status="PENDING")]
    result = run_batches(ledger, 1, [["a"]], invoke_ok, 5, 1)
    assert result["budget_exhausted"] is False
    assert result["done_batches"] == [["a"]]
    assert result["unrun_batches"] == []
    assert result["attempt_count"] == 1


def test_q04c_transitive_dependency_skip():
    ledger = [
        item("a", status="PENDING"),
        item("b", status="PENDING", deps=["a"]),
        item("c", status="PENDING", deps=["b"]),
        item("d", status="PENDING"),
    ]

    def invoke(batch_ids, timeout, fingerprint, feedback):
        if batch_ids == ["a"]:
            raise BatchInvocationError("fp-a", "safe feedback a")
        return invoke_ok(batch_ids, timeout, fingerprint, feedback)

    result = run_batches(ledger, 1, [["a"], ["b"], ["c"], ["d"]], invoke, 5, 10)
    assert result["blocked_batches"] == [["a"]]
    assert result["unrun_batches"] == [["b"], ["c"]]
    assert result["done_batches"] == [["d"]]
    assert result["unrun_items"] == ["b", "c"]
    assert {x["check_id"]: x["execution_status"] for x in result["ledger"]} == {
        "a": "BLOCKED",
        "b": "PENDING",
        "c": "PENDING",
        "d": "DONE",
    }
