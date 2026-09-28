"""GOV-C2-128 — inner Step 6: RoutingRecordCompose.

Composes the needs-review ``AdministrativeDocumentAnalysisRequestRouting``. It **approves nothing,
grants nothing, executes nothing** — it proposes one of five routes and shows the accountable owner
what that proposal rests on.

★ The failure mode this node is written against is over-claiming, not under-reporting. ``scope_complete``
means "the card's own declarations are internally complete", and downstream that reads as "cleared to
ingest". So it is produced only when all four proofs hold, no gap was raised and no contradiction was
found — and S-3 re-derives every one of those rather than trusting what is written here (SoT §2-4
出力規律, "肯定判定側の fail-closed").

Every qualifier is carried up with its cited card field. A conclusion that loses its qualifier on the
way to the route is the specific defect the evaluation identified in §2-4 cases 1, 2 and 4.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import (
    ALLOWED_REFERENCE_METADATA,
    CITATION_BASIS,
    NO_DOCUMENT_INTAKE_BASIS,
    REVIEWER_QUESTIONS,
    UNRESOLVED_QUALIFIERS,
    decide_route,
    gap,
)
from src.utils.audit import emit_trace_event

ROUTING_RECORD_KIND = "administrative_document_request_routing"

#: Which output section each qualifier vocabulary feeds, and what the SoT calls the item reference
#: in that section (SoT §4 output list).
_SECTION_OF_QUALIFIER: dict[str, tuple[str, str]] = {
    "phase_qualifier": ("declared_purpose_statements", "purpose_ref"),
    "designation_kind": ("accountable_owner_statements", "owner_ref"),
    "scope_qualifier": ("classification_statements", "classification_ref"),
}


class RoutingRecordComposeNode(FunctionNode):
    """Compose the five-valued routing record (needs-review, cited)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code"):
            emit_trace_event("routing_record_compose.skipped", {"reason": state.get("error_code")}, state)
            return {
                "routing_record": json.dumps(
                    {
                        "status_kind": "out_of_scope",
                        "routing_outcome": None,
                        "routing_basis": [],
                        "evidence_gaps": [],
                        "reviewer_questions": [],
                        "citation_basis": CITATION_BASIS,
                    },
                    ensure_ascii=False,
                ),
                "human_review_required": False,
                "status": AgentStatus.SUCCESS.value,
            }

        card = json.loads(state.get("user_input", "{}") or "{}")
        reconciled = json.loads(state.get("reconciled_fields", "{}") or "{}")
        statements = json.loads(state.get("interpreted_statements", "[]") or "[]")
        citations = json.loads(state.get("citations", "[]") or "[]")

        gaps: list[dict[str, Any]] = list(reconciled.get("gaps", []))
        # An interpretation that found a qualifier worth reporting is itself a gap: the card is not
        # simply "filled in", it is filled in *conditionally*, and the route has to say so.
        gaps += [gap(s["gap_kind"], s["cited_card_field_ref"]) for s in statements if s.get("gap_kind")]
        gaps = _deduplicate(gaps)

        proofs = {
            "required_fields_present": bool(reconciled.get("required_fields_present")),
            "declared_values_within_enum": bool(reconciled.get("declared_values_within_enum")),
            "accountable_owner_authorised": bool(reconciled.get("accountable_owner_authorised")),
            "no_unresolved_qualifier": (
                not card.get("body_content_fields")
                and not any(s["qualifier"] in UNRESOLVED_QUALIFIERS for s in statements)
            ),
        }
        outcome, driving = decide_route({g["gap_kind"] for g in gaps}, proofs)

        basis = [g for g in gaps if g["gap_kind"] in driving]
        if driving and not basis:
            # Undecidable with nothing anchored: name the ambiguity against the card itself rather
            # than publishing a route whose basis points nowhere.
            basis = [gap("statement_ambiguous", "request_card_id")]

        record = {
            "status_kind": ROUTING_RECORD_KIND,
            "request_card_id": card.get("request_card_id"),
            "card_version_ref": card.get("card_version_ref"),
            "routing_template_version": card.get("routing_template_version"),
            "routing_outcome": outcome,
            "routing_basis": basis,
            "evidence_gaps": gaps,
            "contradictions": reconciled.get("contradictions", []),
            "reviewer_questions": _questions(gaps),
            "scope_complete_proofs": proofs,
            "citations": citations,
            "citation_basis": CITATION_BASIS,
            "interpretation_mode": state.get("interpretation_mode", "deterministic_fallback"),
            "no_document_intake_attestation": {
                "administrative_document_body_ingested": False,
                "fields_with_detected_body_content": card.get("body_content_fields", []),
                "allowed_reference_metadata": sorted(ALLOWED_REFERENCE_METADATA),
                "basis": NO_DOCUMENT_INTAKE_BASIS,
            },
            "human_review": {"required": True, "status": "pending_owner_review"},
        }
        record.update(_statement_sections(statements))

        emit_trace_event(
            "routing_record_compose.complete",
            {
                "routing_outcome": outcome,
                "driving_gap_kinds": driving,
                "evidence_gaps": len(gaps),
                "contradictions": len(record["contradictions"]),
                "reviewer_questions": len(record["reviewer_questions"]),
                "scope_complete_proofs": proofs,
            },
            state,
        )
        return {
            "routing_record": json.dumps(record, ensure_ascii=False),
            "evidence_gap_count": len(gaps),
            "human_review_required": True,
            "status": AgentStatus.SUCCESS.value,
        }


def _deduplicate(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per (kind, anchor). Two steps can legitimately observe the same thing."""
    seen: dict[tuple[str, Any], dict[str, Any]] = {}
    for item in gaps:
        seen.setdefault((item["gap_kind"], item["cited_card_field_ref"]), item)
    return list(seen.values())


def _questions(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fixed template questions, one per distinct gap kind, each keeping the gap's anchor.

    Never generated: a reviewer question is a commitment about what the agency will be asked, so it
    is reviewed text, not model output.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in gaps:
        kind = item["gap_kind"]
        if kind in seen or kind not in REVIEWER_QUESTIONS:
            continue
        seen.add(kind)
        out.append(
            {
                "gap_kind": kind,
                "question": REVIEWER_QUESTIONS[kind],
                "cited_card_field_ref": item["cited_card_field_ref"],
            }
        )
    return out


def _statement_sections(statements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split the interpreted statements into the three SoT §4 output sections.

    Each entry keeps its qualifier and its cited card field; S-3 refuses to publish one that lost
    either.
    """
    sections: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in _SECTION_OF_QUALIFIER.values()}
    for item in statements:
        mapping = _SECTION_OF_QUALIFIER.get(item.get("qualifier_key", ""))
        if mapping is None:
            continue
        section, ref_key = mapping
        sections[section].append(
            {
                ref_key: item["item_ref"],
                item["qualifier_key"]: item["qualifier"],
                "evidence_span": item["evidence_span"],
                "cited_card_field_ref": item["cited_card_field_ref"],
            }
        )
    return sections
