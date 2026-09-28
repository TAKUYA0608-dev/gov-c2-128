"""GOV-C2-128 — inner domain workflow (SoT §4 Steps 3–6), wrapped by the GraphNode in graph.py.

**Linear with per-node skip guards, deliberately.** ``add_conditional_edges`` does not branch when the
graph is driven through a ``GraphNode``, so every shipped Cat 2 template in this portfolio uses a
linear chain where each node returns ``{}`` early if an upstream step degraded. Reproducing that here
keeps the behaviour identical whether the graph is invoked directly or through the outer agent.

The four steps only ever see the **minimised card** — the single S-2 output schema. That is the
interface at which the no-document-intake invariant is checkable, and ``pre_process`` fails closed
rather than handing anything else down this chain.
"""

from __future__ import annotations

import json
from typing import Any, cast

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from langgraph.graph import END, START

from src.nodes.citation_anchor_retrieve_node import CitationAnchorRetrieveNode
from src.nodes.declared_field_reconcile_node import DeclaredFieldReconcileNode
from src.nodes.request_statement_interpret_node import RequestStatementInterpretNode
from src.nodes.routing_record_compose_node import RoutingRecordComposeNode
from src.schemas.state import State

_SLOTS = (
    "declared_field_reconcile",
    "request_statement_interpret",
    "citation_anchor_retrieve",
    "routing_record_compose",
)


class AdminDocumentRequestBoundaryRoutingWorkflow(BaseGraph):
    """Steps 3 → 4 → 5 → 6: reconcile → interpret → anchor → compose."""

    @property
    def name(self) -> str:
        return "AdminDocumentRequestBoundaryRoutingWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        """No mandatory config: the workflow runs on the seeded lexicon when no LLM is injected."""

    def register_nodes(self) -> None:
        """No super() call — BaseGraph.register_nodes() is abstract; initialize/finalize are the
        outer backbone's concern, handled by AgentBaseGraph in graph.py."""
        llm = (self.config or {}).get("llm")
        self._nodes["declared_field_reconcile"] = DeclaredFieldReconcileNode()
        # SDK how-to: the node that needs the client receives it.
        self._nodes["request_statement_interpret"] = RequestStatementInterpretNode(llm=llm)
        self._nodes["citation_anchor_retrieve"] = CitationAnchorRetrieveNode()
        self._nodes["routing_record_compose"] = RoutingRecordComposeNode()

    def add_edges(self) -> None:
        """Linear topology. Branching is expressed as a per-node skip guard, not as a conditional
        edge: `add_conditional_edges` does not branch when the graph is driven through a GraphNode."""
        self._sg.add_edge(START, _SLOTS[0])
        for current, following in zip(_SLOTS, _SLOTS[1:]):
            self._sg.add_edge(current, following)
        self._sg.add_edge(_SLOTS[-1], END)

    def route(self, state: dict[str, Any]) -> str:
        """Required by the BaseGraph ABC. Never called for this linear topology."""
        return END if state.get("status") == AgentStatus.ERROR.value else _SLOTS[-1]

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "output": state.get("routing_record", "{}"),
            "reconciled_fields": state.get("reconciled_fields", "{}"),
            "interpreted_statements": state.get("interpreted_statements", "[]"),
            "citations": state.get("citations", "[]"),
            "citation_index": state.get("citation_index", "[]"),
            "declared_field_count": state.get("declared_field_count", 0),
            "evidence_gap_count": state.get("evidence_gap_count", 0),
            "contradiction_count": state.get("contradiction_count", 0),
            "human_review_required": state.get("human_review_required", False),
            "human_review_flag": state.get("human_review_flag", False),
            "interpretation_mode": state.get("interpretation_mode", "deterministic_fallback"),
            "error_code": state.get("error_code"),
            "status": state.get("status"),
        }


def parse_routing_record(output: str) -> dict[str, Any]:
    """Small helper so callers do not re-implement the JSON contract."""
    try:
        return cast(dict[str, Any], json.loads(output or "{}"))
    except (ValueError, TypeError):
        return {}
