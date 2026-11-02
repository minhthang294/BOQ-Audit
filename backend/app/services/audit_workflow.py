"""Pure-data work-item state machine and per-pass prerequisite scheduling.

This module contains no database, filesystem, process, or worker integration.
It only validates and transforms in-memory work-item records.
"""

import copy
from typing import Any


class WorkItemInputError(ValueError):
    """A work-item ledger is structurally invalid."""


class WorkItemTransitionError(ValueError):
    """A requested status transition is not allowed."""


class BatchInvocationError(Exception):
    """Typed adapter failure with safe fingerprint and feedback."""

    def __init__(self, fingerprint: Any = "", safe_feedback: Any = ""):
        self.fingerprint = fingerprint if isinstance(fingerprint, str) and fingerprint.strip() else "adapter_error"
        self.safe_feedback = safe_feedback if isinstance(safe_feedback, str) and safe_feedback.strip() else "adapter invocation failed"
        super().__init__(self.safe_feedback)


EXECUTION_STATUSES = frozenset({"PENDING", "RUNNING", "DONE", "BLOCKED", "INVALIDATED"})
RESULT_STATUSES = frozenset({"NOT_CHECKED", "CHECKED", "UNRESOLVED", "NOT_APPLICABLE"})
DONE_RESULT_STATUSES = frozenset({"CHECKED", "UNRESOLVED", "NOT_APPLICABLE"})
VALID_PASSES = frozenset({1, 2, 3})

ALLOWED_TRANSITIONS = {
    "PENDING": frozenset({"RUNNING", "BLOCKED", "INVALIDATED"}),
    "RUNNING": frozenset({"DONE", "BLOCKED", "INVALIDATED"}),
    "BLOCKED": frozenset({"PENDING", "INVALIDATED"}),
    "DONE": frozenset({"INVALIDATED"}),
    "INVALIDATED": frozenset({"PENDING"}),
}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_evidence_ids(value: Any, *, required: bool = False) -> None:
    if value is None:
        if required:
            raise WorkItemInputError("DONE requires non-empty evidence_ids")
        return
    if not isinstance(value, list) or any(not _nonempty_str(item) for item in value):
        raise WorkItemInputError("evidence_ids must be a list of non-empty strings")
    if required and not value:
        raise WorkItemInputError("DONE requires non-empty evidence_ids")


def _validate_done_result(item: dict[str, Any]) -> None:
    result_status = item.get("result_status")
    if result_status not in DONE_RESULT_STATUSES:
        raise WorkItemInputError("DONE requires CHECKED, UNRESOLVED, or NOT_APPLICABLE")
    _validate_evidence_ids(item.get("evidence_ids"), required=True)
    if result_status == "NOT_APPLICABLE":
        if not _nonempty_str(item.get("applicability_reason")):
            raise WorkItemInputError("NOT_APPLICABLE requires applicability_reason")


def validate_work_items(work_items: Any, *, require_dependency_targets: bool = True) -> list[dict[str, Any]]:
    if not isinstance(work_items, list):
        raise WorkItemInputError("work_items must be a list")

    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in work_items:
        if not isinstance(item, dict):
            raise WorkItemInputError("work item must be a dict")

        check_id = item.get("check_id")
        if not _nonempty_str(check_id):
            raise WorkItemInputError("check_id must be a non-empty string")
        if check_id in seen_ids:
            raise WorkItemInputError(f"duplicate check_id: {check_id}")
        seen_ids.add(check_id)

        pass_no = item.get("pass_no")
        if not _is_int(pass_no) or pass_no not in VALID_PASSES:
            raise WorkItemInputError(f"invalid pass_no for {check_id}")

        execution_status = item.get("execution_status")
        if not isinstance(execution_status, str) or execution_status not in EXECUTION_STATUSES:
            raise WorkItemInputError(f"invalid execution_status for {check_id}")

        result_status = item.get("result_status")
        if not isinstance(result_status, str) or result_status not in RESULT_STATUSES:
            raise WorkItemInputError(f"invalid result_status for {check_id}")

        if "dependencies" not in item:
            raise WorkItemInputError(f"missing dependencies for {check_id}")
        dependencies = item.get("dependencies")
        if not isinstance(dependencies, list):
            raise WorkItemInputError(f"dependencies must be a list for {check_id}")
        seen_deps: set[str] = set()
        for dependency in dependencies:
            if not _nonempty_str(dependency):
                raise WorkItemInputError(f"empty dependency for {check_id}")
            if dependency in seen_deps:
                raise WorkItemInputError(f"duplicate dependency for {check_id}: {dependency}")
            seen_deps.add(dependency)

        _validate_evidence_ids(item.get("evidence_ids"), required=False)
        applicability_reason = item.get("applicability_reason")
        if applicability_reason is not None and not isinstance(applicability_reason, str):
            raise WorkItemInputError(f"invalid applicability_reason for {check_id}")

        if execution_status == "DONE":
            _validate_done_result(item)

        normalized.append(item)

    if require_dependency_targets:
        id_set = set(seen_ids)
        for item in normalized:
            for dependency in item.get("dependencies", []):
                if dependency not in id_set:
                    raise WorkItemInputError(
                        f"missing dependency for {item['check_id']}: {dependency}"
                    )

    return normalized


def validate_transition(old_status: Any, new_status: Any) -> None:
    if (
        not isinstance(old_status, str)
        or not isinstance(new_status, str)
        or old_status not in EXECUTION_STATUSES
        or new_status not in EXECUTION_STATUSES
    ):
        raise WorkItemTransitionError("invalid execution status")
    if new_status not in ALLOWED_TRANSITIONS[old_status]:
        raise WorkItemTransitionError(f"transition not allowed: {old_status} -> {new_status}")


def transition_work_item(
    item: Any,
    new_status: Any,
    *,
    result_status: Any = None,
    evidence_ids: Any = None,
    applicability_reason: Any = None,
) -> dict[str, Any]:
    validate_work_items([item], require_dependency_targets=False)

    old_status = item["execution_status"]
    validate_transition(old_status, new_status)
    if new_status != "DONE" and (
        result_status is not None
        or evidence_ids is not None
        or applicability_reason is not None
    ):
        raise WorkItemInputError("result, evidence, and applicability can only be set on DONE")

    new_item = copy.deepcopy(item)
    new_item["execution_status"] = new_status
    if old_status == "INVALIDATED" and new_status == "PENDING":
        new_item.update(result_status="NOT_CHECKED", evidence_ids=[], applicability_reason=None)

    if result_status is not None:
        if not isinstance(result_status, str) or result_status not in RESULT_STATUSES:
            raise WorkItemInputError("invalid result_status")
        new_item["result_status"] = result_status
    if evidence_ids is not None:
        _validate_evidence_ids(evidence_ids, required=False)
        new_item["evidence_ids"] = list(evidence_ids) if isinstance(evidence_ids, list) else evidence_ids
    if applicability_reason is not None:
        if not isinstance(applicability_reason, str):
            raise WorkItemInputError("invalid applicability_reason")
        new_item["applicability_reason"] = applicability_reason
    if new_status == "DONE":
        if new_item.get("result_status") != "NOT_APPLICABLE":
            new_item["applicability_reason"] = None
        _validate_done_result(new_item)

    return new_item


def _done_with_valid_result(item: dict[str, Any]) -> bool:
    if item.get("execution_status") != "DONE":
        return False
    try:
        _validate_done_result(item)
    except WorkItemInputError:
        return False
    return True


def runnable_work_items(work_items: Any, pass_no: Any) -> list[str]:
    validated = validate_work_items(work_items)
    if not _is_int(pass_no) or pass_no not in VALID_PASSES:
        raise WorkItemInputError("pass_no must be 1, 2, or 3")

    by_id = {item["check_id"]: item for item in validated}
    runnable: list[str] = []
    for item in validated:
        if item["pass_no"] != pass_no:
            continue
        if item["execution_status"] != "PENDING":
            continue
        dependencies = item.get("dependencies", [])
        if all(
            dep in by_id and _done_with_valid_result(by_id[dep])
            for dep in dependencies
        ):
            runnable.append(item["check_id"])
    return runnable



def _validate_batch_ids(batch_check_ids: Any) -> list[str]:
    if not isinstance(batch_check_ids, list) or isinstance(batch_check_ids, str):
        raise WorkItemInputError("batch_check_ids must be a list")
    batch_ids: list[str] = []
    seen: set[str] = set()
    for batch_id in batch_check_ids:
        if not _nonempty_str(batch_id):
            raise WorkItemInputError("batch IDs must be non-empty strings")
        if batch_id in seen:
            raise WorkItemInputError(f"duplicate batch ID: {batch_id}")
        seen.add(batch_id)
        batch_ids.append(batch_id)
    if not batch_ids:
        raise WorkItemInputError("batch must not be empty")
    return batch_ids


def _validate_patch_item(patch_item: Any) -> dict[str, Any]:
    if not isinstance(patch_item, dict):
        raise WorkItemInputError("patch item must be an object")
    allowed = {"check_id", "result_status", "evidence_ids", "applicability_reason"}
    if not set(patch_item).issubset(allowed):
        raise WorkItemInputError("patch item contains unknown keys")
    if not {"check_id", "result_status", "evidence_ids"} <= set(patch_item):
        raise WorkItemInputError("patch item is missing required keys")

    check_id = patch_item["check_id"]
    if not _nonempty_str(check_id):
        raise WorkItemInputError("patch check_id must be a non-empty string")

    result_status = patch_item["result_status"]
    if not isinstance(result_status, str) or result_status not in DONE_RESULT_STATUSES:
        raise WorkItemInputError("invalid patch result_status")

    evidence_ids = patch_item["evidence_ids"]
    if not isinstance(evidence_ids, list) or not evidence_ids:
        raise WorkItemInputError("patch evidence_ids must be a non-empty list")
    if any(not _nonempty_str(item) for item in evidence_ids):
        raise WorkItemInputError("patch evidence_ids must contain non-empty strings")
    if len(set(evidence_ids)) != len(evidence_ids):
        raise WorkItemInputError("patch evidence_ids must be unique")

    if "applicability_reason" in patch_item:
        reason = patch_item["applicability_reason"]
        if not _nonempty_str(reason):
            raise WorkItemInputError("applicability_reason must be a non-empty string")
        if result_status != "NOT_APPLICABLE":
            raise WorkItemInputError("applicability_reason is only allowed for NOT_APPLICABLE")
    if result_status == "NOT_APPLICABLE" and "applicability_reason" not in patch_item:
        raise WorkItemInputError("NOT_APPLICABLE requires applicability_reason")

    return {
        "check_id": check_id,
        "result_status": result_status,
        "evidence_ids": list(evidence_ids),
        "applicability_reason": patch_item.get("applicability_reason"),
        "has_reason": "applicability_reason" in patch_item,
    }


def apply_ledger_patch(ledger: Any, batch_check_ids: Any, patch: Any) -> list[dict[str, Any]]:
    """All-or-nothing pure transform from RUNNING batch items to DONE items."""
    validated_ledger = validate_work_items(ledger)
    batch_ids = _validate_batch_ids(batch_check_ids)

    if not isinstance(patch, dict):
        raise WorkItemInputError("patch must be an object")
    if set(patch.keys()) != {"items"}:
        raise WorkItemInputError("patch must contain only items")
    patch_items_raw = patch["items"]
    if not isinstance(patch_items_raw, list):
        raise WorkItemInputError("patch items must be a list")

    validated_patch_items = [_validate_patch_item(item) for item in patch_items_raw]

    patch_ids = [item["check_id"] for item in validated_patch_items]
    if len(set(patch_ids)) != len(patch_ids):
        raise WorkItemInputError("patch contains duplicate check_id")
    if set(patch_ids) != set(batch_ids):
        raise WorkItemInputError("patch IDs must exactly match batch IDs")

    ledger_by_id = {item["check_id"]: item for item in validated_ledger}
    for batch_id in batch_ids:
        ledger_item = ledger_by_id.get(batch_id)
        if ledger_item is None:
            raise WorkItemInputError(f"batch ID is not in ledger: {batch_id}")
        if ledger_item["execution_status"] != "RUNNING":
            raise WorkItemInputError(f"batch item is not RUNNING: {batch_id}")

    patch_by_id = {item["check_id"]: item for item in validated_patch_items}

    # Validate every transition before producing output.
    transitioned_by_id: dict[str, dict[str, Any]] = {}
    for batch_id in batch_ids:
        ledger_item = ledger_by_id[batch_id]
        patch_item = patch_by_id[batch_id]
        kwargs = {
            "result_status": patch_item["result_status"],
            "evidence_ids": patch_item["evidence_ids"],
        }
        if patch_item["has_reason"]:
            kwargs["applicability_reason"] = patch_item["applicability_reason"]
        transitioned_by_id[batch_id] = transition_work_item(
            ledger_item, "DONE", **kwargs
        )

    result: list[dict[str, Any]] = []
    for original in validated_ledger:
        check_id = original["check_id"]
        if check_id in transitioned_by_id:
            result.append(transitioned_by_id[check_id])
        else:
            result.append(copy.deepcopy(original))

    validate_work_items(result)
    return result



def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _copy_ledger(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [copy.deepcopy(item) for item in ledger]


def _batch_ids(batch: list[Any]) -> list[str]:
    if not isinstance(batch, list) or not batch:
        raise WorkItemInputError("batch must be a non-empty list")
    seen: set[str] = set()
    result: list[str] = []
    for item in batch:
        if not _nonempty_str(item):
            raise WorkItemInputError("batch IDs must be non-empty strings")
        if item in seen:
            raise WorkItemInputError(f"duplicate batch ID: {item}")
        seen.add(item)
        result.append(item)
    return result


def _validate_batches_structure(batches: Any) -> list[list[str]]:
    if not isinstance(batches, list):
        raise WorkItemInputError("batches must be a list")
    validated: list[list[str]] = []
    seen_all: set[str] = set()
    for batch in batches:
        ids = _batch_ids(batch)
        for batch_id in ids:
            if batch_id in seen_all:
                raise WorkItemInputError(f"overlapping batch ID: {batch_id}")
            seen_all.add(batch_id)
        validated.append(ids)
    return validated


def _transition_batch(current: list[dict[str, Any]], batch_ids: list[str], new_status: str) -> list[dict[str, Any]]:
    batch_id_set = set(batch_ids)
    result: list[dict[str, Any]] = []
    for original in current:
        if original["check_id"] in batch_id_set:
            result.append(transition_work_item(original, new_status))
        else:
            result.append(copy.deepcopy(original))
    validate_work_items(result)
    return result


def run_batches(
    ledger: Any,
    pass_no: Any,
    batches: Any,
    invoke: Any,
    timeout_seconds: Any,
    total_call_budget: Any,
) -> dict[str, Any]:
    """Sequential bounded orchestration over an injected adapter."""
    validated_ledger = validate_work_items(ledger)
    if not _is_int(pass_no) or pass_no not in VALID_PASSES:
        raise WorkItemInputError("pass_no must be 1, 2, or 3")
    if not callable(invoke):
        raise WorkItemInputError("invoke must be callable")
    if not _is_number(timeout_seconds) or timeout_seconds <= 0:
        raise WorkItemInputError("timeout_seconds must be positive")
    if not _is_int(total_call_budget) or total_call_budget <= 0:
        raise WorkItemInputError("total_call_budget must be a positive integer")

    validated_batches = _validate_batches_structure(batches)
    current = _copy_ledger(validated_ledger)

    total_calls = 0
    budget_exhausted = False
    done_batches: list[list[str]] = []
    blocked_batches: list[list[str]] = []
    unrun_batches: list[list[str]] = []
    unrun_items: list[str] = []

    blocked_ids: set[str] = set()
    skipped_ids: set[str] = set()
    for batch in validated_batches:
        if budget_exhausted or total_calls >= total_call_budget:
            budget_exhausted = True
            unrun_batches.append(list(batch))
            unrun_items.extend(batch)
            continue

        by_id = {item["check_id"]: item for item in current}
        for batch_id in batch:
            ledger_item = by_id.get(batch_id)
            if ledger_item is None:
                raise WorkItemInputError(f"batch ID is not in ledger: {batch_id}")
            if ledger_item["pass_no"] != pass_no:
                raise WorkItemInputError(f"batch item has wrong pass: {batch_id}")
            if ledger_item["execution_status"] != "PENDING":
                raise WorkItemInputError(f"batch item is not PENDING: {batch_id}")

        if any(
            dependency in blocked_ids or dependency in skipped_ids
            for batch_id in batch
            for dependency in by_id[batch_id].get("dependencies", [])
        ):
            unrun_batches.append(list(batch))
            unrun_items.extend(batch)
            skipped_ids.update(batch)
            continue

        runnable_ids = set(runnable_work_items(current, pass_no))
        for batch_id in batch:
            if batch_id not in runnable_ids:
                raise WorkItemInputError(f"batch item is not runnable: {batch_id}")

        current = _transition_batch(current, batch, "RUNNING")

        failed = True
        fingerprint = ""
        feedback = ""
        for _attempt in range(3):
            if total_calls >= total_call_budget:
                budget_exhausted = True
                break
            total_calls += 1
            try:
                patch = invoke(list(batch), timeout_seconds, fingerprint, feedback)
            except BatchInvocationError as exc:
                fingerprint = exc.fingerprint
                feedback = exc.safe_feedback
                continue
            except Exception:
                fingerprint = "adapter_error"
                feedback = "adapter invocation failed"
                continue

            try:
                current = apply_ledger_patch(current, list(batch), patch)
                failed = False
                break
            except WorkItemInputError as exc:
                fingerprint = "patch_validation_error"
                feedback = str(exc)
                continue

        if failed:
            current = _transition_batch(current, batch, "BLOCKED")
            blocked_batches.append(list(batch))
            blocked_ids.update(batch)
        else:
            done_batches.append(list(batch))

    result = {
        "ledger": current,
        "attempt_count": total_calls,
        "done_batches": done_batches,
        "blocked_batches": blocked_batches,
        "unrun_batches": unrun_batches,
        "unrun_items": unrun_items,
        "budget_exhausted": budget_exhausted,
    }
    return result


__all__ = [
    "WorkItemInputError",
    "WorkItemTransitionError",
    "EXECUTION_STATUSES",
    "RESULT_STATUSES",
    "DONE_RESULT_STATUSES",
    "VALID_PASSES",
    "ALLOWED_TRANSITIONS",
    "validate_work_items",
    "validate_transition",
    "transition_work_item",
    "runnable_work_items",
    "apply_ledger_patch",
    "run_batches",
    "BatchInvocationError",
]
