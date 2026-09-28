"""GOV-C2-128 — inner Step 5: CitationAnchorRetrieve.

Deterministic anchoring, and the one place the no-document-intake invariant shows up as an *absence*:
**an administrative document is never a citation target here**, because the agent never read one. A
citation anchors on a card field ID, or on a reference the card itself declared and S-1 resolved.

Publishes ``citation_index`` — the **closed set of anchors this run minted**. S-3 checks generated
text against that index, so an anchor invented anywhere downstream (including by a path that bypassed
this node) has nothing to match.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import CARD_FIELD_IDS, CITATION_BASIS
from src.utils.audit import emit_trace_event


class CitationAnchorRetrieveNode(FunctionNode):
    """Anchor each gap and statement to the card field, and mint the closed citation index."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code"):
            emit_trace_event("citation_anchor_retrieve.skipped", {"reason": state.get("error_code")}, state)
            return {}  # upstream degraded — skip guard (inner graph is linear)

        card = json.loads(state.get("user_input", "{}") or "{}")
        reconciled = json.loads(state.get("reconciled_fields", "{}") or "{}")
        statements = json.loads(state.get("interpreted_statements", "[]") or "[]")

        # ── The closed citation index ────────────────────────────────────────
        #
        # Two kinds of anchor, and they need different treatment.
        #
        # A **card field ID** comes from `CARD_FIELD_IDS`, a closed vocabulary this template owns. A
        # caller cannot introduce one, so all twelve are citable — including fields the card left
        # empty. That is not a loophole, it is the requirement: "this required field is absent" is a
        # finding about that field, and restricting the index to fields the card happens to carry
        # would make every missing-field gap unpublishable, which is precisely the finding an agency
        # most needs to receive.
        #
        # A **minted anchor** (`card:<sha8>`, `ref:<sha8>`) is derived from caller data at S-1, so it
        # is the one that has to be pinned to what this run actually produced. S-3 checks every
        # `<kind>:<sha8>` in the rendered envelope against this set, so an anchor invented anywhere
        # downstream — including by a path that bypassed this node — has nothing to match.
        declared = set(CARD_FIELD_IDS)
        minted = {r["cited_source_ref"] for r in card.get("references", []) if r.get("cited_source_ref")}
        if card.get("request_card_id"):
            minted.add(card["request_card_id"])

        index = sorted(declared | minted)
        citations = [
            {
                "cited_card_field_ref": field,
                "reference_label": next(
                    (
                        r.get("reference_label")
                        for r in card.get("references", [])
                        if r["field_id"] == field and r.get("reference_label")
                    ),
                    None,
                ),
                "reference_version": next(
                    (
                        r.get("reference_version")
                        for r in card.get("references", [])
                        if r["field_id"] == field and r.get("reference_version")
                    ),
                    None,
                ),
                "cited_source_ref": next(
                    (
                        r.get("cited_source_ref")
                        for r in card.get("references", [])
                        if r["field_id"] == field and r.get("cited_source_ref")
                    ),
                    None,
                ),
                "citation_basis": CITATION_BASIS,
            }
            for field in sorted(declared)
            if any(g.get("cited_card_field_ref") == field for g in reconciled.get("gaps", []))
            or any(s.get("cited_card_field_ref") == field for s in statements)
        ]

        emit_trace_event(
            "citation_anchor_retrieve.complete",
            {"index_size": len(index), "citations": len(citations), "minted_anchors": len(minted)},
            state,
        )
        return {
            "citations": json.dumps(citations, ensure_ascii=False),
            "citation_index": json.dumps(index, ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
