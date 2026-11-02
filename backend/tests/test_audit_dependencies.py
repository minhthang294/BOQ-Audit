import pytest

from app.services.audit_dependencies import DependencyError, affected_nodes
from app.services.audit_checkpoint import CheckpointError, validate_repair_dependency_scope


def test_correction_invalidates_transitive_dependents_but_preserves_unrelated_branch():
    graph = {
        "source-A": [], "value-A": ["source-A"], "CALC-A": ["value-A"],
        "detail-A": ["CALC-A"], "summary-A": ["detail-A"],
        "BOQ-A": ["summary-A"], "issue-A": ["BOQ-A"],
        "report-A": ["issue-A"], "annotation-A": ["issue-A"],
        "source-B": [], "detail-B": ["source-B"], "report-B": ["detail-B"],
    }
    assert affected_nodes(graph, ["source-A"]) == set(graph) - {"source-B", "detail-B", "report-B"}
    assert affected_nodes(graph, ["CALC-A"]) == {"CALC-A", "detail-A", "summary-A", "BOQ-A", "issue-A", "report-A", "annotation-A"}


@pytest.mark.parametrize("graph,changed", [
    ({"A": ["missing"]}, ["A"]),
    ({"A": ["B"], "B": ["A"]}, ["A"]),
    ({"A": []}, ["missing"]),
    ({"A": []}, []),
])
def test_incomplete_or_cyclic_graph_fails_closed(graph, changed):
    with pytest.raises(DependencyError):
        affected_nodes(graph, changed)


def test_long_dependency_chain_does_not_require_recursive_traversal():
    graph = {f"node-{index}": [f"node-{index - 1}"] if index else [] for index in range(1200)}
    assert len(affected_nodes(graph, ["node-0"])) == 1200


def test_repair_dependency_scope_returns_downstream_closure_and_excludes_independent_branch():
    checkpoint = {
        "inventory": {"expected_work_items": [
            {"check_id": "source-A", "dependencies": []},
            {"check_id": "source-B", "dependencies": []},
            {"check_id": "calc-A", "dependencies": ["source-A"]},
            {"check_id": "detail-A", "dependencies": ["calc-A"]},
        ]},
        "batch_results": [
            {"check_ids": ["source-A"]},
            {"check_ids": ["source-B"]},
        ],
    }
    affected = validate_repair_dependency_scope(checkpoint, ["source-A"])
    assert affected == {"source-A", "calc-A", "detail-A"}
    assert "source-B" not in affected
    checkpoint["batch_results"].append({"check_ids": ["calc-A"]})
    assert validate_repair_dependency_scope(checkpoint, ["source-A"]) == affected


@pytest.mark.parametrize("work_items", [
    [{"check_id": "a", "dependencies": ["missing"]}],
    [{"check_id": "a", "dependencies": ["b"]}, {"check_id": "b", "dependencies": ["a"]}],
])
def test_repair_dependency_scope_fails_closed_on_invalid_graph(work_items):
    with pytest.raises(CheckpointError, match="dependency graph"):
        validate_repair_dependency_scope({"inventory": {"expected_work_items": work_items}, "batch_results": []}, ["a"])
