"""GOV-C2-128 — agent state (ADR-005: flat TypedDict, msgpack-serializable only).

**Every field this template declares carries its payload as a primitive or a JSON string**, never as
a nested structure: LangGraph checkpoints use msgpack serialization, and a bare ``list[dict]`` in
state has been raised in review on sibling templates.

The one nested field in the effective state is ``enriched_context``, and this template does not
declare it: it is **inherited from** ``AgentState`` and **platform-defined as** ``dict``
(SDK state-schema docs: *"``enriched_context`` | ``dict`` | ``pre_process`` node"*).
Re-declaring it here would read as if this template had chosen a nested type against its own rule,
and narrowing an inherited platform field to a JSON string is *not* the fix — that would put the
template out of contract with the framework that owns the field.

``tests/unit/test_state_contract.py`` enforces both halves of this: declared fields must be
primitive / JSON-string, and the inherited nested field must be exactly the platform-defined one — so
deleting a declaration is not a way to escape the rule.

★ **no-document-intake invariant.** No administrative-document body ever reaches state.
``validated_input`` holds the *minimised card* — the single S-2 output schema defined in
``src/services/service.py`` — whose free-text statements are dropped outright where document-body
content was detected, or where the text was long enough that "declaration" and "pasted body" cannot
be told apart (fail-closed). What survives contamination is the ``body_content_detected`` flag plus
the **field ID**, never the content. No requester PII and no credential reaches state either; the
S-4 audit carries counts and rule references only.
"""

from typing import NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Administrative-document analysis request boundary routing state.

    Shared fields (``user_input``, ``status``, ``session_id``, ``node_history``, ``error_log``,
    ``input_context``, …) are inherited from ``AgentState``.
    """

    # ── S-1 / S-2 (pre_process) ──────────────────────────────────────────────
    validated_input: NotRequired[str]  # type: ignore[valid-type] # JSON: the minimised request card (single S-2 schema)
    input_format: NotRequired[str]  # type: ignore[valid-type] # "json" | "empty" | "rejected"
    # `enriched_context` is inherited from AgentState (platform-defined `dict`), not declared here.
    # pre_process writes {source, channel} into it — never the card body.

    # ── inner workflow (SoT §4 Steps 3–6) ────────────────────────────────────
    reconciled_fields: NotRequired[str]  # type: ignore[valid-type] # JSON: Step 3 presence / enum / authority lookup
    interpreted_statements: NotRequired[str]  # type: ignore[valid-type] # JSON: Step 4 taxonomy + qualifier + cited field ref
    citations: NotRequired[str]  # type: ignore[valid-type] # JSON: Step 5 [{cited_card_field_ref, ...}]
    citation_index: NotRequired[str]  # type: ignore[valid-type] # JSON: the closed citation set this run minted
    routing_record: NotRequired[str]  # type: ignore[valid-type] # JSON: Step 6 routing record (pre-S-3)

    # ── counters / flags (primitives only) ───────────────────────────────────
    declared_field_count: NotRequired[int]  # type: ignore[valid-type]
    evidence_gap_count: NotRequired[int]  # type: ignore[valid-type]
    contradiction_count: NotRequired[int]  # type: ignore[valid-type]
    body_content_detected: NotRequired[bool]  # type: ignore[valid-type] # a card field carried document-body content (flag only)
    human_review_required: NotRequired[bool]  # type: ignore[valid-type]
    human_review_flag: NotRequired[bool]  # type: ignore[valid-type] # an interpretation fell outside the approved taxonomy
    interpretation_mode: NotRequired[str]  # type: ignore[valid-type] # "llm" | "deterministic_fallback"
    citation_complete: NotRequired[bool]  # type: ignore[valid-type]
    error_code: NotRequired[str]  # type: ignore[valid-type]

    # ── output (post_process) ────────────────────────────────────────────────
    formatted_output: NotRequired[str]  # type: ignore[valid-type]
    disclaimer: NotRequired[str]  # type: ignore[valid-type]
    audit_logged: NotRequired[bool]  # type: ignore[valid-type]
