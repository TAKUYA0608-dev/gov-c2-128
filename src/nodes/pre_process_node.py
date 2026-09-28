"""GOV-C2-128 — pre_process: SoT §4 Step 1 (RequestCardIngest, S-1) + Step 2 (S-2 minimisation).

Three layers run here, deliberately kept distinct — the SoT is explicit that they are not the same
thing, and conflating them is what lets a free-text declaration act as an instruction:

1. **S-1 — structural validation.** Required fields, NFKC normalisation, size cap. Injection
   detection is *not* attributed to S-1.
2. **S-2 — minimisation (pre-LLM).** Requester / accountable-owner personal data is dropped, caller
   identifiers are tokenised, and — the invariant this template exists for — **administrative-document
   body content is dropped**, leaving only the field ID and a detection reason. All of it happens
   **before any LLM call**, so no downstream node ever sees it. This is minimisation, not containment.
3. **pre-LLM containment (a separate layer).** Free-text declarations are treated as *quoted data*: an
   instruction-shaped declaration is quarantined and never acted on. Independent of S-2.

**The output is checked against the single S-2 schema before it leaves.** ``minimised_card_violation``
is run on this node's own product, and a violation degrades the run rather than publishing an object
Steps 3–7 were promised they would never see.

**Provenance is resolved exactly once, here.** Downstream nodes receive anchors, never raw labels, so
a caller value shaped like an internal surrogate cannot re-enter the pipeline.

Degraded paths return ``SUCCESS + error_code`` and discard the offending body — never ``ERROR``, which
would skip ``post_process`` (S-3/S-4) in the production framework.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import (
    CARD_FIELD_IDS,
    DECLARED_VALUE_FIELDS,
    ID_TOKENISE_FIELDS,
    PII_DROP_FIELDS,
    QUARANTINE_SENTINEL,
    STATEMENT_FIELDS,
    contains_injection_marker,
    document_body_signal,
    minimised_card_violation,
    nfkc,
    opaque_id,
    redact,
    resolve_provenance,
)
from src.utils.audit import emit_trace_event

_MAX_INPUT = 400_000

#: Caller-supplied provenance CLAIMS on a declared reference. Every one is resolved through
#: ``resolve_provenance`` here at S-1 — **including ``cited_source_ref``**, the field name the routing
#: record itself uses downstream. Accepting that verbatim would let a caller mint a citation anchor by
#: choosing a key name.
_PROVENANCE_KEYS = ("source", "cited_source_ref", "reference_source")


def _empty_card() -> dict[str, Any]:
    """A fresh minimised card with nothing declared.

    A factory rather than a module constant: a shared constant would only be safe as long as nobody
    ever mutated the nested containers, and `dict(CONST)` is a shallow copy — the lists and dicts
    would still be the module's. That is a cross-request bug waiting for the first caller who edits
    one in place, so the shape is rebuilt each time instead.
    """
    return {
        "request_card_id": None,
        "card_version_ref": None,
        "routing_template_version": None,
        "declared_values": {},
        "statements": [],
        "references": [],
        "body_content_fields": [],
    }


class PreProcessNode(FunctionNode):
    """S-1 structural validation + S-2 minimisation + pre-LLM containment."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2 hook. **Must return state and must never raise** (SDK 1.0.0).

        Raising here, or returning ``None``, sets state to ``None`` in the production framework and
        crashes every downstream node. Rejection is surfaced through ``error_code`` in ``execute``.
        """
        return dict(state)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = state.get("user_input", "") or ""
        input_context = state.get("input_context", {}) or {}  # read-only [C1]
        enriched = {
            "source": "AdminDocumentRequestBoundaryRouterAgent",
            "channel": input_context.get("channel", "unknown"),
        }

        if len(raw) > _MAX_INPUT:
            return self._degraded("INPUT_TOO_LONG", enriched, state, drop_input=True)
        if not raw.strip():
            return self._degraded("INPUT_REJECTED", enriched, state, fmt="empty")

        card, fmt = self._parse(nfkc(raw))

        # Whole-request rejection applies to an *instruction surface* only — a body that is not a
        # structured card, so the request itself is the instruction. It must NOT apply to a structured
        # card: a legitimate request card can carry an instruction-shaped string inside 目的欄 or 備考欄,
        # and rejecting the card for that discards the declarations the routing decision rests on.
        # Instruction-shaped content *inside* a card is handled one layer down, by `_minimise_statement`,
        # which quarantines the offending field and keeps the rest of the card.
        if fmt == "free_text" and contains_injection_marker(raw[:2000]):
            return self._degraded("INJECTION_REJECTED", enriched, state, drop_input=True)

        violation = minimised_card_violation(card)
        if violation is not None:
            # Fail closed on our own product. Steps 3–7 are written against the schema; handing them
            # something else would silently move the invariant's enforcement point to nowhere.
            emit_trace_event("request_card_ingest.schema_violation", {"violation": violation}, state)
            return self._degraded("MINIMISED_SCHEMA_VIOLATION", enriched, state, drop_input=True)

        body = json.dumps(card, ensure_ascii=False)
        emit_trace_event(
            "request_card_ingest.validated",
            {
                "input_format": fmt,
                "declared_value_count": len(card["declared_values"]),
                "statement_count": len(card["statements"]),
                "reference_count": len(card["references"]),
                # Field IDs only — never what was found in them.
                "body_content_fields": card["body_content_fields"],
                "quarantined_fields": [
                    s["field_id"] for s in card["statements"] if s.get("statement") == QUARANTINE_SENTINEL
                ],
            },
            state,
        )
        return {
            "validated_input": body,
            "input_format": fmt,
            "enriched_context": enriched,
            "body_content_detected": bool(card["body_content_fields"]),
            "status": AgentStatus.SUCCESS.value,
        }

    def _degraded(
        self,
        reason: str,
        enriched: dict[str, Any],
        state: dict[str, Any],
        *,
        fmt: str = "rejected",
        drop_input: bool = False,
    ) -> dict[str, Any]:
        emit_trace_event("request_card_ingest.rejected", {"reason": reason}, state)
        out = {
            "validated_input": json.dumps(_empty_card(), ensure_ascii=False),
            "input_format": fmt,
            "enriched_context": enriched,
            "body_content_detected": False,
            "error_code": reason,
            "status": AgentStatus.SUCCESS.value,
        }
        if drop_input:
            out["user_input"] = ""
        return out

    # ── S-2: build the minimised card ────────────────────────────────────────

    @classmethod
    def _parse(cls, text: str) -> tuple[dict[str, Any], str]:
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return _empty_card(), "free_text"  # a natural-language question → out of scope
        if not isinstance(obj, dict):
            return _empty_card(), "free_text"
        return cls._minimise_card(obj), "json"

    @classmethod
    def _minimise_card(cls, obj: dict[str, Any]) -> dict[str, Any]:
        contaminated: set[str] = set()

        card: dict[str, Any] = {
            # ★ Every caller identifier is tokenised unconditionally. A syntactic allowlist is not an
            # option here: a personal name is a valid string in this field, so "does it look like an
            # ID" cannot gate it.
            "request_card_id": (
                opaque_id(obj["request_card_id"], ID_TOKENISE_FIELDS["request_card_id"])
                if obj.get("request_card_id")
                else None
            ),
            "card_version_ref": cls._metadata(obj.get("card_version_ref"), "card_version_ref", contaminated),
            "routing_template_version": cls._metadata(
                obj.get("routing_template_version"), "routing_template_version", contaminated
            ),
            "declared_values": {},
            "statements": [],
            "references": [],
        }

        # ★ Requester / owner personal data never reaches the card because the copy above and the
        # loop below are an **allow-set copy**: an unlisted top-level key is simply not read. The
        # `PII_DROP_FIELDS` guard is defence in depth for the day someone widens the allow set —
        # it keeps "must never survive" enforced at the copy, not only in a constant.
        for field in DECLARED_VALUE_FIELDS:
            if field in PII_DROP_FIELDS or obj.get(field) in (None, ""):
                continue
            value = cls._metadata(
                obj.get(field), field if field != "accountable_owner_role" else "accountable_owner", contaminated
            )
            if value:
                card["declared_values"][field] = value

        statements = obj.get("statements") or {}
        if isinstance(statements, dict):
            for field in STATEMENT_FIELDS:
                item = cls._minimise_statement(field, statements.get(field), contaminated)
                if item is not None:
                    card["statements"].append(item)

        for reference in obj.get("declared_references") or []:
            item = cls._minimise_reference(reference, contaminated)
            if item is not None:
                card["references"].append(item)

        card["body_content_fields"] = sorted(contaminated)
        return card

    @staticmethod
    def _metadata(value: Any, field_id: str, contaminated: set[str]) -> str | None:
        """A declared value or an allowed reference-metadata value: short, redacted, body-free."""
        text = redact(nfkc(value)).strip()
        if not text:
            return None
        if document_body_signal(text) is not None:
            contaminated.add(field_id)
            return None
        return text

    @staticmethod
    def _minimise_statement(field_id: str, value: Any, contaminated: set[str]) -> dict[str, Any] | None:
        """One free-text declaration, reduced to the closed statement schema.

        ★ Contamination **drops the text**. The returned object then has no ``statement`` key at all —
        not an empty one, not a masked one — so there is nothing downstream for a step to quote,
        summarise, or derive an evidence span from. That is the property the negative test asserts.
        """
        text = redact(nfkc(value)).strip()
        if not text:
            return None
        reason = document_body_signal(text)
        if reason is not None:
            contaminated.add(field_id)
            return {"field_id": field_id, "body_content_detected": True, "detection_reason": reason}
        if contains_injection_marker(text):
            # pre-LLM containment: quote, never obey. The *fact* is retained; the content is not.
            return {"field_id": field_id, "statement": QUARANTINE_SENTINEL}
        return {"field_id": field_id, "statement": text}

    @classmethod
    def _minimise_reference(cls, reference: Any, contaminated: set[str]) -> dict[str, Any] | None:
        """One declared reference, reduced to the allowed metadata keys plus a resolved anchor.

        Anything outside ``ALLOWED_REFERENCE_METADATA`` is not copied — the allow set is applied by
        construction here, and re-checked by ``minimised_card_violation`` afterwards.
        """
        if not isinstance(reference, dict):
            return None
        field_id = str(reference.get("field_id") or "")
        if field_id not in CARD_FIELD_IDS:
            # A reference that does not sit in a known card field cannot be anchored, and an
            # unanchored citation is exactly what S-3 exists to refuse. Drop it here rather than
            # carrying a value the gate would have to reject later.
            return None
        item: dict[str, Any] = {"field_id": field_id}
        for key in ("reference_label", "reference_version"):
            value = cls._metadata(reference.get(key), field_id, contaminated)
            if value:
                item[key] = value
        anchor = None
        for key in _PROVENANCE_KEYS:
            if reference.get(key):
                # Single resolution point. Unauthorised / bare / forged-surrogate → None → S-3 blocks.
                anchor = resolve_provenance(reference[key])
                if anchor:
                    break
        if anchor:
            item["cited_source_ref"] = anchor
        return item if len(item) > 1 else None
