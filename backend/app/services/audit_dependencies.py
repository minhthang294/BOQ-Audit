"""Deterministic dependency traversal for audit correction invalidation."""

from collections.abc import Mapping, Sequence


class DependencyError(ValueError):
    """The dependency graph cannot support a scoped correction."""


def affected_nodes(dependencies: Mapping[str, Sequence[str]], changed_ids: Sequence[str]) -> set[str]:
    """Return changed nodes and every downstream node, rejecting incomplete graphs."""
    if not isinstance(dependencies, Mapping) or not dependencies:
        raise DependencyError("dependency graph is empty or invalid")
    if any(not isinstance(node, str) or not node.strip() for node in dependencies):
        raise DependencyError("dependency node is invalid")
    reverse: dict[str, set[str]] = {node: set() for node in dependencies}
    indegree: dict[str, int] = {}
    for node, parents in dependencies.items():
        if not isinstance(parents, (list, tuple, set)) or any(not isinstance(parent, str) or parent not in reverse for parent in parents):
            raise DependencyError(f"missing or invalid dependency for {node}")
        indegree[node] = len(set(parents))
        for parent in parents:
            reverse[parent].add(node)
    ready = [node for node, count in indegree.items() if count == 0]
    seen = 0
    while ready:
        seen += 1
        for child in reverse[ready.pop()]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if seen != len(dependencies):
        raise DependencyError("dependency graph contains a cycle")
    if not isinstance(changed_ids, (list, tuple, set)) or not changed_ids:
        raise DependencyError("changed IDs are required")
    if any(not isinstance(node, str) for node in changed_ids):
        raise DependencyError("changed ID is invalid")
    affected = set(changed_ids)
    if any(not isinstance(node, str) or node not in reverse for node in affected):
        raise DependencyError("changed ID is absent from dependency graph")
    pending = list(affected)
    while pending:
        for dependent in reverse[pending.pop()]:
            if dependent not in affected:
                affected.add(dependent)
                pending.append(dependent)
    return affected
