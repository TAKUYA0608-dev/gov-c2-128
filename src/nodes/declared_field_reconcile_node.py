"""GOV-C2-128 — inner Step 3: DeclaredFieldReconcile.

Presence, enum validity, authority-model lookup and field anchoring are deterministic — this is the
Tool-equivalent core the SoT §2-1 asks to be separable, and it lives in ``services`` so it can be
lifted out unchanged if the Tool re-classification in §2-4 is ever taken.

What this node owns is the discipline around that core: it **flags, never infers**. An unreconcilable
value is reported as a gap anchored on its card field; it is not repaired, defaulted, or guessed.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import reconcile_declared_fields
from src.utils.audit import emit_trace_event


class DeclaredFieldReconcileNode(FunctionNode):
    """Reconcile the card's declared fields against the approved enums and authority model."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code"):
            # Skipping is a decision the audit trail has to show. Returning silently leaves a
            # degraded run with no domain event on this step at all (S-4 gap).
            emit_trace_event("declared_field_reconcile.skipped", {"reason": state.get("error_code")}, state)
            return {}  # upstream degraded — skip guard (inner graph is linear)

        card = json.loads(state.get("user_input", "{}") or "{}")
        if not card.get("declared_values") and not card.get("statements"):
            emit_trace_event("declared_field_reconcile.out_of_scope", {"reason": "no_declared_content"}, state)
            return {
                "reconciled_fields": "{}",
                "declared_field_count": 0,
                "error_code": "NO_REQUEST_CARD",
                "status": AgentStatus.SUCCESS.value,
            }

        result = reconcile_declared_fields(card)
        emit_trace_event(
            "declared_field_reconcile.complete",
            {
                "declared_fields": result["declared_field_count"],
                "gaps": len(result["gaps"]),
                "contradictions": len(result["contradictions"]),
                "required_fields_present": result["required_fields_present"],
                "declared_values_within_enum": result["declared_values_within_enum"],
                "accountable_owner_authorised": result["accountable_owner_authorised"],
                # Kinds and field IDs only — never a declared value.
                "gap_kinds": sorted({g["gap_kind"] for g in result["gaps"]}),
            },
            state,
        )
        return {
            "reconciled_fields": json.dumps(result, ensure_ascii=False),
            "declared_field_count": result["declared_field_count"],
            "contradiction_count": len(result["contradictions"]),
            "status": AgentStatus.SUCCESS.value,
        }
