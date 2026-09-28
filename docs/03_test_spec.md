# GOV-C2-128 — Test Specification

## Strategy

Three layers, all deterministic in CI (the LLM seam is exercised with a stub client — no network):

- **unit** — `tests/unit/test_service.py` (provenance vs privacy tokenisation, the closed taxonomy,
  the four SoT §2-4 readings, route precedence, the S-3 re-derivations),
  `tests/unit/test_no_document_intake.py` (**the invariant**),
  and the three machine checks: `test_audit_coverage.py`, `test_state_contract.py`,
  `test_docs_match_implementation.py`.
- **integration** — `tests/integration/test_end_to_end.py`: the real outer `Graph().invoke()`,
  invoked exactly the way `src/api/server.py` does.
- **node boundary** — `tests/integration/test_s3_node_boundary.py`: `PostProcessNode().execute(...)`
  driven directly, so S-3 is proved at the node rather than through a helper or an upstream check.

> **Calling the graph in a test**: `invoke(user_input: str, session_id: str = "", ctx=None, ...)`.
> The first argument is a **string** and `ctx` **must be keyword-passed**. Passing it positionally
> lands it in the `session_id` slot, the caller stays ANONYMOUS, the S-1 trust gate refuses every
> node, and the run returns a bare `status=error` that reads like a template bug.

## Local result (local SDK stub)

- Core suites (`test_service.py`, `test_no_document_intake.py`, `tests/integration/`): **162 passed**.
- Full `tests/` run: **175 passed, 2 skipped, 3 known env-diff failures**
  (`test_pb_invoke_order`, `test_framework_compliance_tc06_tc07::tc06/tc07`). Those three assert
  framework-level `@final` enforcement and lifecycle-event ordering that the local SDK stub shim does not
  implement; **they pass under the real SDK in CI**, which is authoritative for them. The 2 skips are
  PB-7, conditional on `hitl.enabled` (the CoE conditional PB-7 stub) — this template is not HITL, so the
  boundary assertion passes and the active test is skipped.
- **Coverage 91 %** (`--cov=src`, floor 80 %). `ruff check src tests` clean.

## Framework compliance (mandatory)

| TC-ID | Test | Expected | Result |
|---|---|---|---|
| TC-01 | State contract: flat TypedDict | No Pydantic/dataclass; complex fields are JSON strings | ✅ `test_state_contract` + `test_state_safety` |
| TC-02 | Rejected input degrades, never raises | `SUCCESS` + an error code, body discarded | ✅ `test_empty_input_degrades_rather_than_erroring` |
| TC-03 | No credential in State | `gate-credential-scan`: 0 violations | ✅ CI |
| TC-04 | `InvocationContext` via configurable only | Not stored in State | ✅ `test_state_safety` |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | Domain events only; the framework owns the node_start / node_complete pair | ✅ 0 duplicates |
| TC-06 | S-2 default input gate not overridden | `TypeError` at class definition | ✅ CI (real SDK) |
| TC-07 | S-3 default output gate not overridden | `TypeError` at class definition | ✅ CI (real SDK) |
| TC-08 | `required_trust_level` on every node | Insufficient trust → node refused | ✅ integration ctx + `gate-trust-level-check` |
| TC-09 | S-2 `_extra_security_gate_input()` returns state, never raises | Returns dict | ✅ unit |
| TC-10 | S-3 `_extra_security_gate_output()` non-trivial | Disclaimer / citation-basis preservation | ✅ `test_s3_node_boundary` |
| TC-11 | S-4: ≥1 domain event on **every** `execute()` path | Static scan + behavioural check | ✅ `test_audit_coverage` |
| TC-12 | Identifiers named in the docs exist in `src/` | Taxonomy keys, trace events, registries | ✅ `test_docs_match_implementation` |
| TC-13 | The agent's own fixed text is inert against its own gates | No disclaimer, reviewer question or taxonomy description trips the document-content, injection or fabricated-anchor check | ✅ `test_service.py::TestTheAgentsOwnTextIsInertAgainstItsOwnGates` |

## Proof-of-Boundary

| PB-ID | Boundary | Result |
|---|---|---|
| PB-1 | BaseNode → EventEmitter | ✅ |
| PB-2 | State serialization (primitives only after invoke) | ✅ `test_state_safety` |
| PB-4 | Import isolation (no `agenticstar` import from `src/`) | ✅ `test_import_isolation` |
| PB-6 | Node `__call__` order | ✅ CI — **the `GraphNode` lives in `src/graph/graph.py`, never `src/nodes/`**, because it needs `session_id` from a real `InvocationContext` |
| PB-7 | HITL interrupt propagation | ✅ **Auto-waived — non-HITL**; `hitl.enabled` is false here |

## Domain test cases

| TC-ID | Case | Expected |
|---|---|---|
| TC-D01 | Complete card, everything declared | `scope_complete`, all four proofs true, citation complete |
| TC-D02 | **Deployed call path** (`invoke(input, ctx=ctx)`, nothing else) | Returns a real routing record — the agent must not be inert on the path `server.py` uses |
| TC-D03 | Every envelope: grounded / withheld / out-of-scope | Carries the citation basis and the no-document-intake attestation |
| TC-D04 | Free text, empty, oversized | Out-of-scope safe answer; no route invented |
| **TC-D05** | **SoT §2-4 case 1** — "当面は要約のみ … 傾向分析は別途" | Read as a phased purpose, route `clarification_needed` — **never `scope_complete`** |
| **TC-D06** | **SoT §2-4 case 2** — "課長補佐 (当面代行) … 次回課内決裁をもって指名" | Read as a provisional designation; asserted neither as named nor as unnamed |
| TC-D07 | **The lexicon's own counter-example** — "代行決裁により正式に指名済" | Ambiguous; the acting-designation reading is **not** asserted |
| **TC-D08** | **SoT §2-4 case 3** — "対象は … 別表2 … のとおり" | Read as a delegated scope, distinguished from a blank field |
| **TC-D09** | **SoT §2-4 case 4** — "昨年度と同一 … ただし不開示情報を含む写しを追加" | Read across both 欄 → `privacy_security_owner_review` |
| TC-D10 | Sensitivity "none" under a non-disclosable classification | Contradiction surfaced → `privacy_security_owner_review` |
| TC-D11 | Output type outside the approved set | → `unsupported_request` |
| TC-D12 | Owner role outranked by the classification | Named as an authority-model result, not guessed |
| TC-D13 | Retention beyond the classification ceiling | Gap + a contradiction citing both 欄 |
| TC-D14 | Requester personal data in the card | Never read (allow-set copy); absent from the output |
| TC-D15 | Caller identifiers | Tokenised to a non-reversible surrogate; raw values absent from the output |
| TC-D16 | Unauthorised / bare / surrogate-shaped declared source | No anchor; the value is absent from the output |
| TC-D17 | Injection markers on the **request body** | Degraded, body discarded, markers absent |
| TC-D18 | Injection-shaped text **inside a card declaration** | Card still processed; the field is quarantined and read as ambiguous, never echoed or obeyed |
| TC-D19 | Fabricated reference under an authorised registry | **Does anchor** — asserted deliberately (see below) |
| TC-D20 | LLM answers off-schema, or the client raises | Falls back to the seeded lexicon; the mode is disclosed; nothing invented is echoed |

> **TC-D19 records a known limit rather than hiding it.** The template cannot confirm a declared
> reference exists: the reference and its label arrive in the same caller body, and no verification
> surface is exposed. The envelope declares the basis instead of implying verification, and real
> verification is the SoT §12-B platform dependency. If a future change makes provenance verifiable,
> `test_declared_provenance_is_not_verification` fails and the claim in the code and in these docs
> must be updated with it.

## ★ The no-document-intake invariant (`tests/unit/test_no_document_intake.py`)

The claim has several parts, and asserting only the first would be vacuous:

| # | Claim | How it is tested |
|---|---|---|
| 1 | An excerpt pasted into a card field never reaches the **LLM view** | Captured from a recording stub client — otherwise "the model was not shown it" is unobservable |
| 2 | It never reaches the **output** | All four free-text fields, parametrised |
| 3 | **Nothing derived from it** reaches either — no span, no quotation, no summary | The part a mask or a truncation would fail |
| 4 | The residue is the flag plus the field ID, and the object has **no statement text at all** | Asserted at the minimised-card interface itself |
| 5 | An excerpt smuggled through reference metadata, or through an unlisted key, is dropped too | The allow set does the work by construction |
| 6 | Ambiguity fails closed on length | Over the ceiling → `restricted_escalate` |
| 7 | **Legitimate declarations survive** | The four SoT worked examples, plus statutory citations (第5条第1号 / 別表第2号 / 様式第1号) |

Every one of these first asserts the card was **processed**. Wholesale rejection satisfies "the token
is absent" just as well as containment does; a sibling template shipped exactly that vacuous test and
the path it was named for had never run.

## Boundary / non-goal tests

| BL-ID | The agent must NOT | Verified by |
|---|---|---|
| BL-01 | Ingest or analyse an administrative document | `test_no_document_intake` (all) |
| BL-02 | Decide disclosure exemption, purpose limitation, or 行政文書 status | Taxonomy test: no key states a legal outcome |
| BL-03 | Assert completeness without all four proofs | TC-D05/06/08/09 + `TestScopeCompleteIsReDerivedNotBelieved` |
| BL-04 | Invent a routing outcome or a gap kind | Closed sets; an unknown kind falls back to the out-of-scope key |
| BL-05 | Publish an unanchored conclusion | TC-D16, S-3 citation check |
| BL-06 | Carry requester identity downstream | TC-D14, TC-D15 |
| BL-07 | Echo instruction-shaped content | TC-D17 / TC-D18 / `test_s3_node_boundary` |
| BL-08 | Return an error status on a degraded path (which would skip S-3/S-4) | TC-D04 / TC-D17 |
| BL-09 | Generate a reviewer question | Fixed template map, one entry per gap kind |
| BL-10 | Publish a route it cannot ground | `test_the_route_itself_is_withheld_not_just_the_evidence` |

## Mutation checks

Each gate is shown to constrain behaviour, not merely to pass:

| Mutation | Tests that fail |
|---|---|
| Any one of the four completeness proofs is forced true | **4** (parametrised, one per proof) |
| S-3 trusts the composed outcome instead of recomputing it | **3** |
| A contaminated field keeps a masked or truncated statement | **3** |
| The citation index is restricted to fields the card carries | **1** — every missing-field gap becomes unpublishable. **Found by measurement, not by review**: the first implementation did exactly this, and the test for "a missing required field is named, not filled in" caught it |
| The whole-request injection screen is applied to structured cards too | **2** |
| A document-body marker is introduced into the disclaimer or a reviewer question | **1** — the agent would then withhold every record it produced, caused by a doc edit rather than a payload |

## Reproduce

```bash
source .venv/bin/activate
python -m pytest tests/ -q --cov=src --cov-report=term
ruff check src tests
python scripts/check_trust_level.py src/
python scripts/check_cat_consistency.py
python scripts/check_dep_pinning.py
python scripts/check_oss_license.py
```

## Test execution summary

- Execution date: 2026-08-05
- Core suites: **162 passed**
- Full `tests/`: **175 passed, 2 skipped, 3 env-diff failures** (real-SDK-only assertions; green in CI)
- Coverage: **91 %** (floor 80 %)
