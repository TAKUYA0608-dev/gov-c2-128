"""GOV-C2-128 — post_process: SoT §4 Step 7 (OutputSanitise — S-3 output gate + S-4 no-persist).

**S-3 re-derives; it does not trust the composing step.** A routing record assembled by some other
path must not be able to publish, so every check here works from the envelope and the closed citation
index rather than from a flag set upstream.

Five things are enforced — the three fail-closed checks the SoT §4 Step 7 names, plus two that are
defence in depth:

1. **Citation completeness** — every gap, statement, contradiction and reviewer question carries a
   ``cited_card_field_ref`` that is in the closed index this run minted.
2. **Qualifier completeness** — every statement carries an approved qualifier, and
   ``scope_complete`` is **recomputed** from the four proofs, the gap list and the contradiction
   list. A composer that merely asserted completeness cannot publish it.
3. **No document content** — the rendered envelope is re-scanned with the same detector S-2 used.
   Deliberately redundant: this is the property the whole template rests on.
4. **No fabricated anchor** — every ``<kind>:<sha8>`` anywhere in the rendered envelope must be in the
   closed index, independent of the composing path.
5. **Redaction, injection neutralisation and the disclaimer.**

Failure is fail-closed and **degraded, not ERROR**: ``SUCCESS + error_code`` with the body withheld,
so the disclaimer and the terminal S-4 audit still run. ``routing_outcome`` is withheld as well —
publishing a route we have just decided we cannot ground would be the over-claiming this gate exists
to prevent.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.routing_record_compose_node import ROUTING_RECORD_KIND
from src.services.service import (
    CITATION_BASIS,
    CONTAINMENT_MARKERS,
    NO_DOCUMENT_INTAKE_BASIS,
    RULE_VERSION,
    citation_binding_failure,
    document_content_failure,
    qualifier_completeness_failure,
    redact,
    surrogates_in,
)
from src.utils.audit import emit_trace_event

# ── S-3: the authoritative injection / output-policy boundary ────────────────
#
# Upstream containment is required and is defence in depth, but it must not be *labelled* the
# injection security control and it must not *substitute* for the S-3 proof. The caller-visible
# boundary is here: whatever reaches generated output is neutralised at S-3, whichever path produced
# it — including a path that bypassed pre_process entirely. The pre_process marker check is an
# upstream abuse-screening short-circuit (availability / cost), not this boundary.
_NEUTRALISED = "[NEUTRALISED]"
_MARKER_RE = re.compile("|".join(re.escape(m) for m in CONTAINMENT_MARKERS), re.IGNORECASE)


def _neutralise_injection(text: str) -> tuple[str, int]:
    """Neutralise instruction-shaped artifacts in the rendered envelope. Returns (text, count)."""
    hits = len(_MARKER_RE.findall(text))
    return (_MARKER_RE.sub(_NEUTRALISED, text), hits) if hits else (text, 0)


_DISCLAIMER = (
    "本レコードは、提出された request card の宣言メタデータのみに基づく needs-review の"
    "経路判定ドラフトです。本エージェントは行政文書のコーパス本体を一切取り込まず、解析もしません。"
    "不開示情報の該当性、目的外利用の可否、行政文書該当性の判断は行わず、AI 利用の承認・"
    "データアクセスの付与・記録の保持・申請者への連絡・結果の公表も行いません。"
    "**S-3 が機械的に保証するのは、各記載が card のどの欄に基づくかの anchoring と、qualifier の"
    "完全性、および文書本体の不混入だけです。宣言された参照が実在するかは本エージェントでは"
    "検証できません**(citation_basis を参照)。最終判断は庁の named データ / AI ガバナンス owner が"
    "行ってください。"
)

_WITHHELD_MSG = (
    "経路判定の根拠に、card の欄に紐づかない記載、qualifier を欠く記載、"
    "または混入が疑われる記載が含まれていたため、経路判定を含む本体の提示を差し控えました。"
    "宣言メタデータのみで card を補正のうえ再実行してください。"
)
_NEEDS_REVIEW_NOTE = (
    "The routing record could not be grounded on the request card's own fields; it is withheld "
    "pending correction and accountable-owner review. Declared references are not verified by this "
    "agent (see citation_basis)."
)
_OUT_OF_SCOPE_MSG = (
    "request card として解釈できる入力が確認できませんでした。"
    "目的 / 想定コーパス区分 / 提供元庁 / 機微区分 / 出力種別 / 保持要求 / 説明責任者 を含む "
    "JSON を送信してください。行政文書そのものは送信しないでください。"
)

#: Keys withheld wholesale when a check fails. The route itself is in this list on purpose.
_WITHHELD_SECTIONS = (
    "routing_basis",
    "evidence_gaps",
    "contradictions",
    "reviewer_questions",
    "declared_purpose_statements",
    "accountable_owner_statements",
    "classification_statements",
    "citations",
    "scope_complete_proofs",
)


class PostProcessNode(FunctionNode):
    """S-3 output gate + S-4 no-persist audit."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3 preservation check. Receives the **delta from execute()**; MAY raise (SDK 1.0.0)."""
        out = result.get("formatted_output", "")
        if out and "needs-review" not in out and "citation_basis" not in out:
            raise ValueError("S-3: mandatory disclaimer / citation basis missing from output")
        return dict(result)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        record: dict[str, Any] = json.loads(state.get("routing_record", "{}") or "{}")
        grounded = record.get("status_kind") == ROUTING_RECORD_KIND

        if not grounded:
            # This is the path an upstream rejection (injection / oversize / empty / schema
            # violation) lands on. It previously set audit_logged=True while emitting nothing — the
            # one publication path with no S-4 event behind it.
            emit_trace_event(
                "output_sanitise.out_of_scope",
                {"reason": state.get("error_code") or "not_grounded", "rule_version": RULE_VERSION},
                state,
            )
            return self._envelope(
                {
                    "status_kind": "out_of_scope",
                    "routing_outcome": None,
                    "routing_basis": [],
                    "evidence_gaps": [],
                    "reviewer_questions": [],
                    "citation_basis": CITATION_BASIS,
                    "no_document_intake_attestation": {
                        "administrative_document_body_ingested": False,
                        "fields_with_detected_body_content": [],
                        "basis": NO_DOCUMENT_INTAKE_BASIS,
                    },
                    "message": _OUT_OF_SCOPE_MSG,
                },
                state,
                state.get("error_code"),
            )

        index = set(json.loads(state.get("citation_index", "[]") or "[]"))

        reason = citation_binding_failure(record, index)
        # S-3, the caller-visible boundary: redact, then neutralise instruction-shaped artifacts in
        # whatever reached generated output — including via a path that never passed pre_process.
        rendered, injected = _neutralise_injection(redact(json.dumps(record, ensure_ascii=False)))
        if reason is None:
            reason = qualifier_completeness_failure(record)
        if reason is None:
            reason = document_content_failure(rendered)
        if reason is None and not surrogates_in(rendered) <= index:
            reason = "UNVERIFIED_ANCHOR"

        if reason is not None:
            # The rejected value is deliberately NOT echoed: only the violation kind is reported.
            emit_trace_event(
                "output_sanitise.withheld",
                {
                    "reason": reason,
                    "gap_count": len(record.get("evidence_gaps", [])),
                    "injection_artifacts_neutralised": injected,
                    "rule_version": RULE_VERSION,
                },
                state,
            )
            withheld = {
                key: record.get(key)
                for key in (
                    "status_kind",
                    "request_card_id",
                    "card_version_ref",
                    "routing_template_version",
                    "no_document_intake_attestation",
                )
            }
            withheld.update(
                {
                    "status_kind": "needs_review",
                    "routing_outcome": None,
                    **{section: [] for section in _WITHHELD_SECTIONS},
                    "citation_basis": CITATION_BASIS,
                    "citation_complete": False,
                    "human_review": {"required": True, "status": "pending_owner_review", "note": _NEEDS_REVIEW_NOTE},
                    "message": _WITHHELD_MSG,
                }
            )
            return self._envelope(withheld, state, reason)

        body = json.loads(rendered)
        body["citation_complete"] = True
        emit_trace_event(
            "output_sanitise.complete",
            {
                "routing_outcome": body.get("routing_outcome"),
                "gap_count": len(body.get("evidence_gaps", [])),
                "reviewer_questions": len(body.get("reviewer_questions", [])),
                "contaminated_field_count": len(
                    body.get("no_document_intake_attestation", {}).get("fields_with_detected_body_content", [])
                ),
                "interpretation_mode": body.get("interpretation_mode"),
                "injection_artifacts_neutralised": injected,
                "rule_version": RULE_VERSION,
            },
            state,
        )
        return self._envelope(body, state, state.get("error_code"))

    @staticmethod
    def _envelope(body: dict[str, Any], state: dict[str, Any], error_code: str | None) -> dict[str, Any]:
        body["disclaimer"] = _DISCLAIMER
        out = {
            "formatted_output": json.dumps(body, ensure_ascii=False),
            "disclaimer": _DISCLAIMER,
            "audit_logged": True,
            "citation_complete": bool(body.get("citation_complete")),
            "status": AgentStatus.SUCCESS.value,
        }
        if error_code:
            out["error_code"] = error_code
        return out
