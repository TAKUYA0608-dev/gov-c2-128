"""AgentCore Platform v1.0"""

# ─────────────────────────────────────────────────────────────────────────────
# Template Category (Cat) — choose ONE based on your Cat judgment:
#
# Cat 1 — Single technical capability (use-case-agnostic)
#   Purpose : Delivers one reusable, domain-independent capability.
#             The same template can be dropped into any project unchanged.
#   Examples: TextSummarizer, EmbeddingGenerator, LanguageDetector,
#             SentimentAnalyzer, KeywordExtractor
#   Parent  : AgentBaseGraph
#   Pipeline: START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max 3)
#                                          pre_process
#   See src/examples/graph_cat1_sample.py for a full example.
#
# Cat 2 — Multi-step domain workflow (job-to-be-done)
#   Purpose : Orchestrates multiple steps to accomplish a specific business
#             outcome. The template name describes the outcome, not the
#             individual capabilities it uses.
#   Examples: InvoiceTriageAgent, ContractGapDetectionAgent,
#             ResumeScreeningAgent, SupportTicketResolutionAgent
#   Parent  : AgentBaseGraph (outer graph) + GraphNode in the `main` slot
#             wrapping an inner BaseGraph/AgentBaseGraph (domain workflow)
#   Pipeline: Same fixed 5-node backbone as Cat 1; domain complexity is
#             encapsulated inside DomainWorkflowGraphNode.get_subgraph().
#   Layout  : src/graph/graph.py                 ← outer graph (this file)
#             src/graph/domain_workflow_graph.py ← inner graph
#   See src/examples/graph_cat2_sample.py and src/examples/domain_workflow_graph_sample.py for examples.
#
# Cat 3 — Autonomous think→act→observe loop (self-directed)
#   Purpose : Runs an LLM-driven loop that decides its own next action,
#             executes tools, observes results, and terminates when the task
#             is complete or a budget/iteration ceiling is hit.
#   Examples: ResearchAgent, AutonomousCodeReviewAgent,
#             DataExplorationAgent, MultiStepPlannerAgent
#   Parent  : AutonomousBaseGraph
#   Pipeline: START → initialize → think ⇄ act → finalize → END
#   Config  : budget_usd and llm are required in config.yaml.
#   See src/examples/graph_cat3_sample.py for a full example.
#
# ── How to choose ─────────────────────────────────────────────────────────────
#
#   Ask: "Does this template solve a specific business problem end-to-end?"
#     No  → Cat 1 (generic capability)
#     Yes → "Does it need an autonomous reasoning loop to decide its steps?"
#       No  → Cat 2 (fixed multi-step workflow)
#       Yes → Cat 3 (LLM-driven loop)
#
# framework.* imports are unchanged; agent-local imports use the src. prefix.
# ─────────────────────────────────────────────────────────────────────────────

from typing import Any, ClassVar, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.trust_level import TrustLevel

from src.graph.domain_workflow_graph import AdminDocumentRequestBoundaryRoutingWorkflow
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class AdminDocumentRequestBoundaryRoutingWorkflowGraphNode(GraphNode):
    """Cat 2 `main` slot — wraps the inner domain workflow.

    ★ Defined here, beside the outer graph, and **never under ``src/nodes/``**. PB-6
    (`test_pb_invoke_order`) instantiates every class under `src/nodes/` and calls it with a bare
    state; a GraphNode resolves an ``InvocationContext`` from that state, so one placed there fails
    the boundary test with `KeyError: 'session_id'`.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL
    error_strategy: ClassVar[str] = "propagate"

    # ★ Class-level cache, keyed on the identity of the injected llm client.
    #
    # A plain ClassVar cache is wrong on its own once an llm can be injected: the first construction
    # wins, so an agent configured WITH a client can be handed the deterministic subgraph an earlier
    # construction cached. Here that is a correctness bug rather than a performance one — the
    # interpretation step behaves differently in the two modes and the envelope discloses which ran.
    # Keying on the client, and holding it alongside the workflow so identity is re-checked rather
    # than just hashed, avoids that.
    _subgraph: ClassVar[dict[int, tuple[Any, AdminDocumentRequestBoundaryRoutingWorkflow]]] = {}
    _subgraph_capacity: ClassVar[int] = 8

    def __init__(self, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm

    def get_subgraph(self) -> AdminDocumentRequestBoundaryRoutingWorkflow:
        key = id(self._llm)
        cached = AdminDocumentRequestBoundaryRoutingWorkflowGraphNode._subgraph.get(key)
        if cached is not None and cached[0] is self._llm:
            return cached[1]
        workflow = AdminDocumentRequestBoundaryRoutingWorkflow(config=self._parent_config())
        entries = list(AdminDocumentRequestBoundaryRoutingWorkflowGraphNode._subgraph.items())
        if len(entries) >= AdminDocumentRequestBoundaryRoutingWorkflowGraphNode._subgraph_capacity:
            entries = entries[1:]
        AdminDocumentRequestBoundaryRoutingWorkflowGraphNode._subgraph = dict(entries + [(key, (self._llm, workflow))])
        return workflow

    def _parent_config(self) -> dict[str, Any]:
        """Forward the parent's llm to the inner graph (SDK how-to: compose-agents-graphnode)."""
        return {"llm": self._llm}

    def extract_input(self, state: dict[str, Any]) -> str:
        """The inner graph reads the S-1/S-2 output — the minimised card — never the caller's raw body.

        This is the interface at which the no-document-intake invariant holds: Steps 3–6 are written
        against the minimised schema and are never handed anything else.
        """
        return cast(str, state.get("validated_input", state.get("user_input", "{}")))

    def merge_output(self, state: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
        merged = {
            "routing_record": output.get("output", "{}"),
            "reconciled_fields": output.get("reconciled_fields", "{}"),
            "interpreted_statements": output.get("interpreted_statements", "[]"),
            "citations": output.get("citations", "[]"),
            "citation_index": output.get("citation_index", "[]"),
            "declared_field_count": output.get("declared_field_count", 0),
            "evidence_gap_count": output.get("evidence_gap_count", 0),
            "contradiction_count": output.get("contradiction_count", 0),
            "human_review_required": output.get("human_review_required", False),
            "human_review_flag": output.get("human_review_flag", False),
            "interpretation_mode": output.get("interpretation_mode", "deterministic_fallback"),
            "status": output.get("status"),
        }
        # Outer error_code wins: an outer rejection (injection / oversize / schema violation) is the
        # reason to report, not whatever the inner workflow concluded from an already-discarded body.
        merged["error_code"] = state.get("error_code") or output.get("error_code")
        return merged


class Graph(AgentBaseGraph):
    """Outer fixed 5-slot pipeline; domain complexity lives in the GraphNode above."""

    @property
    def name(self) -> str:
        return "AdminDocumentRequestBoundaryRouterAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode
        self._nodes["pre_process"] = PreProcessNode()
        # SDK how-to: the node that needs the client receives it.
        self._nodes["main"] = AdminDocumentRequestBoundaryRoutingWorkflowGraphNode(llm=(self.config or {}).get("llm"))
        self._nodes["post_process"] = PostProcessNode()

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        """Framework default, plus the guarantee that a success is never empty.

        The Marketplace runner rejects a successful invocation whose output is
        missing — verified on a deployed Pod — and a degraded run
        (SUCCESS + error_code) produces no artefact for the framework default
        to surface. Report the degradation instead: this states what happened,
        it does not invent an answer.

        Only on SUCCESS. A request refused by the framework's S-2 gate (status
        ERROR) must keep publishing nothing — answering a hostile input with a
        notice would undo the refusal, and the runner treats a non-success
        invocation as a failure regardless, so there is nothing to rescue.
        """
        out: dict[str, Any] = super().get_output(state)
        if not out.get("output") and str(state.get("status", "")).lower().endswith("success"):
            code = state.get("error_code") or "NO_CONTENT"
            out["output"] = (
                "This request could not be completed "
                f"(error_code={code}). No content was produced; "
                "see error_code and error_log for the degradation cause."
            )
        return out


# Cat 1/2 registry alias: config/agent.yaml resolves `class: "<AgentClass>"` against this module.
AdminDocumentRequestBoundaryRouterAgent = Graph
