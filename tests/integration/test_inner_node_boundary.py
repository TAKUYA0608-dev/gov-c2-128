"""The inner workflow must run through the framework node boundary, not around it.

Review 2026-08-05 (High): the inner graph was driven by a hand-rolled `run_linear()` helper
that called each `FunctionNode.execute()` directly. That skips `BaseNode.__call__` — the S-1 trust
gate, the S-2/S-3 hooks and the framework's own lifecycle/S-4 events — so the inner nodes' declared
`required_trust_level = VERIFIED_EXTERNAL` was not enforced on the path production actually takes.
The helper and the `GraphNode.execute()` override that called it are both gone; the framework's
`GraphNode.execute()` drives the subgraph.

The outer-path test could not have caught this: it asserts on the published envelope, which is
identical either way. This asserts on the traversal itself.
"""

import pytest

from framework.nodes.base_node import BaseNode

from tests.integration import test_end_to_end as e2e

#: Read from the inner graph rather than hardcoded, so a step added later is covered automatically.
def _inner_node_names() -> set[str]:
    from src.graph.domain_workflow_graph import AdminDocumentRequestBoundaryRoutingWorkflow
    workflow = AdminDocumentRequestBoundaryRoutingWorkflow(config={})
    workflow.register_nodes()
    return {type(n).__name__ for n in workflow._nodes.values()}


@pytest.fixture
def traversed(monkeypatch):
    seen: list[str] = []
    original = BaseNode.__call__

    def spy(self, state, *args, **kwargs):
        seen.append(type(self).__name__)
        return original(self, state, *args, **kwargs)

    monkeypatch.setattr(BaseNode, "__call__", spy)
    e2e._invoke(e2e._card())
    return seen


class TestInnerNodesCrossTheFrameworkBoundary:
    def test_every_inner_node_goes_through_call(self, traversed):
        inner = _inner_node_names()
        assert inner, "no inner nodes registered — the expectation would be vacuous"
        missing = inner - set(traversed)
        assert not missing, (
            f"inner nodes that never crossed BaseNode.__call__: {sorted(missing)}. "
            "The subgraph is being driven around the framework boundary again.")

    def test_the_outer_backbone_still_runs(self, traversed):
        """Guards against a change that satisfies the check above by breaking the outer path."""
        assert {"PreProcessNode", "PostProcessNode"} <= set(traversed)
