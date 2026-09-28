"""GOV-C2-128 — inner Step 4: RequestStatementInterpret. **This is the Agent value** (SoT §2-3).

The deterministic core one step earlier can say a field is filled, an enum is valid, a role is in the
authority table. It cannot say whether "当面は要約のみ、統計的な傾向分析は別途" is one declared purpose or
two, whether "課長補佐 (当面代行)" is a designation or the absence of one, whether "別表2 のとおり" is a
delegation or a blank, or whether "昨年度と同一区分・ただし不開示情報を含む写しを追加" changed the scope.
Those four readings are the SoT §2-4 cases, and each is a place where the deterministic answer is
wrong in a specific direction — two of them by *over*-claiming completeness, which is the failure that
transfers straight into an ingest decision.

**Containment, and what the model is shown.** The prompt is built by ``build_llm_view`` from the
minimised card and nothing else. A field whose text was dropped at S-2 contributes its ``field_id``
and nothing more, so an excerpt — or anything derived from one — has no path to the model. The card's
free text is framed as quoted data, evidence only, never instructions. This is a layer of its own,
tested separately from S-2 and from S-3.

**What comes back is constrained, not trusted.** Only `{field_id, taxonomy_key, qualifier}` triples
inside the approved sets survive ``validate_interpretations``; the evidence span is re-derived from
the minimised card rather than taken from the model, so a model that echoed text back cannot
reintroduce it. Anything rejected raises ``human_review_flag`` — it is never echoed.

**With no client configured** the node runs the SoT §12-A seeded lexicon and the envelope says
``interpretation_mode: deterministic_fallback``. Nothing degrades silently.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import (
    build_llm_view,
    interpret_statements,
    validate_interpretations,
)
from src.utils.audit import emit_trace_event


class RequestStatementInterpretNode(FunctionNode):
    """Classify the card's declarations into the approved taxonomy, with their qualifiers."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code"):
            emit_trace_event("request_statement_interpret.skipped", {"reason": state.get("error_code")}, state)
            return {}  # upstream degraded — skip guard (inner graph is linear)

        card = json.loads(state.get("user_input", "{}") or "{}")
        view = build_llm_view(card)
        statements, mode, rejected = self._interpret(card, view, state)

        emit_trace_event(
            "request_statement_interpret.complete",
            {
                "interpretation_mode": mode,
                "statements": len(statements),
                "rejected_interpretations": rejected,
                "contaminated_field_ids": view["contaminated_field_ids"],
                # Taxonomy keys and field IDs only — never a declaration or a span.
                "gap_kinds": sorted({s["gap_kind"] for s in statements if s["gap_kind"]}),
            },
            state,
        )
        return {
            "interpreted_statements": json.dumps(statements, ensure_ascii=False),
            "interpretation_mode": mode,
            "human_review_flag": rejected > 0,
            "status": AgentStatus.SUCCESS.value,
        }

    def _interpret(
        self, card: dict[str, Any], view: dict[str, Any], state: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str, int]:
        """Run the model when one is configured; fall back to the seeded lexicon otherwise."""
        if self._llm is None:
            return interpret_statements(card), "deterministic_fallback", 0

        emit_trace_event("request_statement_interpret.llm_call", {"statement_count": len(view["statements"])}, state)
        try:
            raw = self._llm.complete(json.dumps(view, ensure_ascii=False))
            kept, rejected = validate_interpretations(json.loads(raw), card)
        except Exception as exc:  # noqa: BLE001 - any client/parse failure degrades, never crashes
            # A model that failed or answered off-schema must not silently thin the record. The
            # seeded lexicon runs, the mode is disclosed, and the reason is audited without echoing
            # anything the model produced.
            emit_trace_event("request_statement_interpret.llm_output_rejected", {"reason": type(exc).__name__}, state)
            return interpret_statements(card), "deterministic_fallback", 1
        if not kept:
            emit_trace_event(
                "request_statement_interpret.llm_output_rejected", {"reason": "no_admissible_interpretation"}, state
            )
            return interpret_statements(card), "deterministic_fallback", max(rejected, 1)
        return kept, "llm", rejected
