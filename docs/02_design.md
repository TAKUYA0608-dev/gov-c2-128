# GOV-C2-128 — Design (Stage ②)

Source: the original template proposal.
**L1 Base: `AgentBaseGraph`, inherited directly** (the `ChatAgent` name in the SoT §1 table is a
pattern reference only; Level 2 base agent types are deprecated).

## Scope

Routes a **completed administrative-document analysis request card** — purpose, intended corpus
classification, source agency, sensitivity classification, output type, retention request,
accountable owner, plus free-text authority-basis and remarks — to one of **five outcomes**, citing
the card field each conclusion rests on, and hands the named agency data / AI governance owner a
needs-review routing record with reviewer questions.

**The defining constraint is that the administrative-document corpus itself is never taken in.**
This template is the gate that stands *before* ingest; the only evidence it has is what the card
declares. Reading the document would defeat the gate's own purpose (SoT §5 "独立テンプレートとして
存在する意義").

**Out of scope, by design** — ingesting or analysing administrative documents; deciding whether
information is exempt from disclosure (不開示情報 該当性), whether a use is outside the stated purpose,
or whether a record is an 行政文書; approving AI use; granting data access; retaining records;
contacting the requester; publishing results. Every one of those is a human decision this template
only prepares.

## Architecture — Cat 2 GraphNode-in-main

```
START → initialize → pre_process → main(GraphNode) → post_process → finalize → END
                          │              │                  │
                     S-1 + S-2 +    inner BaseGraph      S-3 + S-4
                     containment    (linear, 4 steps)
```

| Slot | Node | SoT §4 step | Responsibility |
|---|---|---|---|
| `pre_process` | `PreProcessNode` | Step 1 + Step 2 | S-1 structural validation (required fields, NFKC, size cap) · **S-2 minimisation** (requester / owner PII dropped, caller identifiers tokenised, **administrative-document body dropped**) · **pre-LLM containment** (free text is quoted data) · provenance resolved exactly once |
| `main` | `AdminDocumentRequestBoundaryRoutingWorkflowGraphNode` | — | Wraps the inner workflow. Defined in `src/graph/graph.py`, **never under `src/nodes/`** |
| — inner 1 | `DeclaredFieldReconcileNode` | Step 3 | Deterministic: required-field presence, enum validity, authority-model lookup, card field anchoring. **The Tool-equivalent core** (SoT §2-1) |
| — inner 2 | `RequestStatementInterpretNode` | Step 4 | **The Agent value.** Bounded interpretation of the card's declarations into the approved taxonomy — conditional phasing, provisional designation, indirect reference, label-unchanged scope widening, ambiguity |
| — inner 3 | `CitationAnchorRetrieveNode` | Step 5 | Deterministic anchoring to card field IDs and declared reference metadata. Publishes the **closed citation index** this run minted |
| — inner 4 | `RoutingRecordComposeNode` | Step 6 | Composes the five-valued routing record. **Decides nothing, executes nothing** |
| `post_process` | `PostProcessNode` | Step 7 | S-3 output gate (three fail-closed checks, re-derived) + S-4 no-persist audit |

**Why the inner graph is linear with per-node skip guards.** `add_conditional_edges` does not branch
when a graph is driven through a `GraphNode`; every shipped Cat 2 template in this portfolio uses a
linear chain in which each node returns `{}` early if an upstream step degraded. Reproducing that
keeps behaviour identical whether the inner graph is invoked directly or through the outer agent.

**Why the GraphNode is not under `src/nodes/`.** PB-6 (`test_pb_invoke_order`) instantiates every
class under `src/nodes/` and calls it with a bare state; a `GraphNode` resolves an
`InvocationContext` from that state, so one placed there fails with `KeyError: 'session_id'`.

**Subgraph cache.** `_subgraph` is a `ClassVar` **keyed on the identity of the injected LLM client**,
with a capacity bound. A plain ClassVar cache is wrong on its own once a client can be injected: the
first construction wins, so an agent configured *with* a client could be handed a deterministic
subgraph cached earlier. Here that would be a correctness bug, not just a performance one — the
interpretation step behaves differently in the two modes and discloses which one ran.

## ★ The no-document-intake invariant, fixed as one schema

The review asked for this to be an *allow set* checkable at an interface, not prose and
not a list of prohibitions. It is:

| Constant (`src/services/service.py`) | Meaning |
|---|---|
| `CARD_FIELD_IDS` | The closed set of card field IDs. A citation anchor can only be one of these — the agent mints them, a caller cannot introduce one |
| `ALLOWED_REFERENCE_METADATA` = `{reference_label, reference_version, field_id}` | **The only** caller-declared reference metadata that may cross S-2. Anything else on a reference object is a schema violation and the object is dropped |
| `MINIMISED_STATEMENT_KEYS` = `{field_id, statement, body_content_detected}` | The only keys a free-text statement may carry downstream |
| `MINIMISED_CARD_KEYS` | The only top-level keys the minimised card may carry |

`minimised_card_violation()` is the single checker. `pre_process` runs it on its own output and fails
closed if it does not conform, and `tests/unit/test_no_document_intake.py` runs it on the output of
adversarial cards. **Steps 3–7 only ever see an object that has passed it.**

Detection of administrative-document body content in free text is deterministic and **fails closed on
ambiguity**, which is what the SoT asks for ("検出が曖昧なら fail-closed で混入扱い"):

1. body-specific markers (`決裁伺` / `起案理由` / `供覧` / `原議` / `文書番号` / `収受番号` …);
2. document-number shapes (`第 123 号`);
3. a long bracketed verbatim span (`「…」` ≥ 60 chars) — a declaration cites, it does not transcribe;
4. **any free-text field longer than `DECLARATION_MAX_CHARS` (600)** — at that length a declaration
   and a pasted body are not distinguishable, so the field is treated as contaminated.

Bare `決裁` is deliberately **not** a marker. The SoT's own worked examples 2 and 3 use it in
legitimate declarations ("次回課内決裁をもって指名する", "令和7年度○○課決裁『行政文書ファイル管理簿
別表2』"); a marker that fires on those would make the gate unusable.

When a field is contaminated, the text is **dropped, not masked**. Downstream receives
`{"field_id": …, "body_content_detected": true}` and no `statement` key at all, so there is nothing
for a later step to quote, summarise, or derive a citation from.

## LLM usage and the LLM view

The Agent value the SoT claims (§2-3, §2-4) is bounded interpretation of the card's declarations, so
an LLM seam exists and is wired: `RequestStatementInterpretNode` receives the client the graph was
configured with (`config["llm"]`, forwarded through the GraphNode per
`sdk/how-to/compose-agents-graphnode.md`) and calls `complete(prompt)` — the `BaseLLM` interface
documented in `sdk/how-to/use-shared-services.md`.

- **What the model is shown** is built by `build_llm_view()` from the **minimised card only**:
  `field_id` + `statement` for fields that survived S-2, the approved taxonomy, and the approved
  qualifier sets. Nothing else. A contaminated field contributes its `field_id` and nothing more.
- **What the model may return** is constrained to `{field_id ∈ card fields, taxonomy_key ∈ approved
  taxonomy, qualifier ∈ approved qualifier set}`. Anything outside is discarded and raises
  `statement_ambiguous` with `human_review_flag` — it is never echoed.
- **When no client is configured** the node falls back to the seeded lexicon (SoT §12-A) and the
  envelope discloses `interpretation_mode: "deterministic_fallback"`. Nothing degrades silently.

The card's free text is framed as **quoted data — evidence only, never instructions**. This
containment is a layer of its own, tested independently of S-2 and of S-3 (SoT §4 備考).

## Routing decision (SoT §4 Step 6)

Five outcomes, closed set, evaluated in a fixed precedence so the function is total:

| # | Condition | Outcome |
|---|---|---|
| 1 | `classification_label_unchanged_scope_widened` or `sensitivity_declaration_inconsistent` | `privacy_security_owner_review` |
| 2 | `document_body_content_detected` | `restricted_escalate` |
| 3 | `output_type_unsupported` | `unsupported_request` |
| 4 | any other gap, or any unresolved qualifier | `clarification_needed` |
| 5 | all four proofs hold | `scope_complete` |
| — | otherwise / undecidable | `clarification_needed` (fail-safe) |

The SoT names both `restricted_escalate` and `privacy_security_owner_review` as acceptable for rows
1 and 2. The precedence above resolves that into one deterministic rule: a signal that a *statutory*
control point is implicated (non-disclosure information, retained personal information) names the
privacy / security owner; a breach of this template's own intake premise is restricted escalation.

**`scope_complete` requires four proofs, and S-3 re-derives all four** (SoT §2-4 出力規律):

| Proof | Meaning |
|---|---|
| `required_fields_present` | every required card field is declared |
| `declared_values_within_enum` | every enum-valued field holds an approved value |
| `accountable_owner_authorised` | the declared owner role covers the declared corpus classification in the seeded authority model |
| `no_unresolved_qualifier` | no conditional phasing, provisional designation, indirect reference, scope widening, or detected document body |

## Security layers

Four layers, deliberately distinct — the SoT is explicit that they are not the same thing:

| Layer | Where | What |
|---|---|---|
| **S-1** | `pre_process` | Required fields, NFKC, 400 000-char cap. Injection detection is *not* attributed here |
| **S-2** | `pre_process` | Requester / owner PII (`requester_name`, `email`, `phone`, `employee_number`, 個人番号 …) **dropped, not masked**; caller identifiers tokenised; **administrative-document body dropped** (the invariant above). Minimisation, not containment |
| **pre-LLM containment** | `pre_process` + `build_llm_view` | Free text is quoted data. An instruction-shaped *statement* is quarantined; an instruction-shaped *request body* is rejected outright. A layer of its own, tested separately |
| **S-3** | `post_process` | Three fail-closed checks + fabricated-surrogate check + redaction + injection neutralisation + disclaimer |
| **S-4** | all nodes | Counts, rule version, error codes and **field IDs** only. Never a statement, an identifier, or a rejected value |

### S-3 re-derives; it does not trust the composing step

A routing record assembled by any other path must not be able to publish, so each check works from
the envelope and the closed index rather than from a flag set upstream:

1. **Citation completeness.** Every `routing_basis`, `evidence_gaps` and `*_statements` entry must
   carry a `cited_card_field_ref` that is in the closed citation set this run minted.
2. **Qualifier completeness.** Every statement must carry a qualifier drawn from its approved set,
   and `routing_outcome == "scope_complete"` is re-derived from the four proofs, the gap list and the
   contradiction list — a composer that simply asserted it cannot publish it.
3. **No document content.** The rendered envelope is re-scanned with the same detector S-2 used. If
   any document-body content is present, the body is withheld. This is deliberately redundant with
   S-2: the invariant is enforced at intake (Step 2), at citation (Step 5 never cites a document),
   and at output (here).

Failure is **degraded, not `ERROR`**: `SUCCESS + error_code` with the body withheld, so the
disclaimer and the terminal S-4 audit still run. `routing_outcome` is withheld too — publishing a
route we have just decided we cannot ground would be exactly the over-claiming the SoT warns about.

> `status_kind` carries three machine values —
> `administrative_document_request_routing` (grounded), `needs_review` (S-3 withheld), `out_of_scope`
> (no interpretable card). The SoT §4 output list writes `status_kind=needs-review` to mean "this
> record is for human review": every grounded envelope encodes that as
> `human_review.required = true` with `status: pending_owner_review` plus the needs-review
> disclaimer. A single literal value could not also mark the withheld state, which S-3 must be able
> to distinguish.

## ★ Deviation from the SoT: the ingress-attestation acceptance condition is NOT implemented

SoT §4 Step 1 makes caller-declared values (`card_version_ref`, source agency, "approved" /
"sanitised" markers) conditional on **"trusted ingress attestation (platform / gateway が付与し caller
が request body から設定できない `input_context` 等) との照合"**, tracked as a platform dependency under
§12-B. **This template does not implement that condition, deliberately.**

**Why.** `input_context` is **caller metadata**, not a platform attestation:
the SDK state-schemas reference lists it as `input_context | dict | invoke() | Caller metadata`, and
the scaffold's own `src/api/server.py` calls `agent.invoke(req.input, ctx=ctx)` — it never passes
`input_context` at all. Two sibling templates gated their output on it. Because nothing populates it
on the deployed path, **every valid payload degraded to `needs_review` with the body withheld**: the
agents produced no usable output at all, while their tests stayed green because the tests passed a
kwarg the deployment never passes. Forwarding a caller-supplied attestation instead would validate
the caller's own `source` label against the caller's own metadata.

**What is implemented instead** (the "if we cannot verify it, we do not say we verified it" rule,
moderator-approved 2026-08-04):

- `resolve_provenance()` accepts a declared reference **only** when its namespace names an authorised
  reference registry **and** a reference part is present. There is no format passthrough — a caller
  value shaped like an internal surrogate (`ref:<hex>`) has namespace `ref`, which is not authorised,
  so it resolves to `None`. A **bare namespace is also rejected**: it names a registry but no record.
- **Every envelope**, grounded or withheld, carries
  `citation_basis: "caller_declared_authorized_source"`.
- `service.CITATION_BASIS` documents what the citation does and does not assert, and why forwarding a
  caller-supplied attestation is not a fix.
- `tests/unit/test_service.py::test_declared_provenance_is_not_verification` asserts the **remaining
  hole**: a fabricated reference under an authorised namespace *does* produce a citation. If a future
  change makes provenance verifiable, that test fails and `CITATION_BASIS` and these docs must be
  updated with it.
- `tests/integration/test_end_to_end.py::TestDeployedPath` invokes exactly the way `server.py` does
  and asserts a valid card **returns a body**.

Real verification needs a server-side lookup or gateway-signed references delivered outside the
caller's body — **the platform dependency in SoT §12-B, not something to simulate here.**

## Evidential anchoring is passed in, never inferred

Each gap and each statement carries its `cited_card_field_ref` explicitly. "Did we produce a
reference?" and "does this claim rest on a card field?" are different questions; inferring the second
from the first is how an ungrounded record publishes. A gap that could not be anchored carries
`None` and S-3 fails closed.

## State

Flat TypedDict (ADR-005). Every non-primitive payload is a **JSON string**, never a nested structure:
LangGraph checkpoints use msgpack, and a bare `list[dict]` in state has been raised in review on
sibling templates. `enriched_context` is the platform's own field and is not re-declared. No
credential, no requester identifier and no document body is placed in state.

| Field | Type | Purpose |
|---|---|---|
| `validated_input` | `str` (JSON) | the minimised card — the single S-2 output schema |
| `reconciled_fields` | `str` (JSON) | Step 3 presence / enum / authority result |
| `interpreted_statements` | `str` (JSON) | Step 4 taxonomy + qualifier + cited field ref |
| `citations` / `citation_index` | `str` (JSON) | Step 5 anchors and the closed index |
| `routing_record` | `str` (JSON) | Step 6 record, before S-3 |
| `body_content_detected` | `bool` | a field carried document-body content — flag only |
| `interpretation_mode` | `str` | `llm` \| `deterministic_fallback`, disclosed in the envelope |
| `human_review_flag` | `bool` | an interpretation fell outside the approved taxonomy |
| counters | `int` | declared fields, evidence gaps, contradictions |

## Import isolation

No `agenticstar` import from `src/` (PB-4). Imports are `framework.*` and `src.*` only; the LLM
client is injected, so `shared.services.llm` is not imported here either.

## Design decision record

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| L1 Base type | `AgentBaseGraph` | `AutonomousBaseGraph` | **A** | Fixed 7-step pipeline; no autonomous loop (SoT §4) |
| Composition | FunctionNode-in-main (Cat 1) | GraphNode-in-main (Cat 2) | **B** | Cat 2; domain complexity is encapsulated in the inner workflow |
| Inner topology | conditional edges | linear + skip guards | **B** | `add_conditional_edges` does not branch through a `GraphNode` |
| Ingress attestation | implement SoT §4 Step 1 as written | declare the limit, verify what is verifiable | **B** | See the deviation section — implementing it as written produces no output on the deployed path |
| Body-content ambiguity | keep the field, flag it | drop the field | **B** | SoT §4 Step 2: fail closed on ambiguity |
| Interpretation | deterministic only | injectable LLM + seeded fallback | **B** | The SoT's Agent value *is* the bounded interpretation (§2-3); the mode is disclosed |

## Open items (Stage ③)

None outstanding — the implementation ships in the same MR series as this design. The external
dependencies are the three in SoT §12-B (PM/CoE routing-rule acceptance, the agency owner's approved
taxonomy and authority model, and the no-document-intake sample set with a manual baseline). Until
they land, the taxonomy, the authority model and the seeded lexicon run on the §12-A seeded defaults
and the envelope says which interpretation mode produced the record.
