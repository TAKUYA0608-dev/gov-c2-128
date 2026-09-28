"""★ The no-document-intake invariant, asserted end to end.

This template's entire value rests on one property: it decides whether an agency may ingest an
administrative document **without ever reading one**. So the invariant is not a policy statement, it
is a testable claim, and this file is where it is tested.

The claim has three parts, and asserting only the first would be vacuous:

1. an excerpt pasted into a card field never reaches the **LLM view** — captured from a fake client,
   because "the model was not shown it" is otherwise unobservable;
2. it never reaches the **final output**;
3. **nothing derived from it** reaches either — no evidence span, no quotation, no summary. This is
   the part a mask or a truncation would fail, which is why contamination drops the field entirely.

Every test here first asserts the card was **processed**. Wholesale rejection satisfies "the token is
absent" just as well as containment does, and a sibling template shipped exactly that vacuous test for
weeks before measurement showed the path it was named for had never run.
"""

import json

import pytest
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

import src.services.service as svc
from src.graph.graph import Graph
from src.nodes.pre_process_node import PreProcessNode

#: A 決裁伺 body. Each fragment is a distinct thing the invariant must stop: a decision-request
#: sentence, a drafting rationale, a document number, and a verbatim transcription.
_EXCERPT = ("上記について決裁を仰ぎたく、起案理由は下記のとおり。標記の件、○○発第123号により"
            "通知された事項に関し、別添のとおり処理してよろしいか。")
_EXCERPT_TOKENS = ("決裁を仰", "起案理由", "第123号", "標記の件", "別添のとおり処理")


class _RecordingLLM:
    """Captures exactly what the model was shown. The prompt is the LLM view, verbatim."""

    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        # Answer off-schema on purpose: the deterministic fallback then runs, so the rest of the
        # pipeline is exercised even though this client is a stub.
        return "[]"


def _card(**statements):
    card = {
        "request_card_id": "RC-2026-0801-014", "card_version_ref": "v2",
        "routing_template_version": "gov-routing-v1", "source_agency": "○○省○○局",
        "corpus_classification": "public_disclosed", "sensitivity_classification": "none",
        "output_type": "summary", "retention_request": "no_retention",
        "accountable_owner_role": "division_director",
        "statements": {"authority_basis": "情報公開法 第5条第1号に基づく所掌事務",
                       "purpose": "令和7年度の審査事務の効率化。",
                       "corpus_classification": "本欄に列挙した公開済文書に限る。",
                       "accountable_owner": "○○課長を正式に指名済。",
                       "remarks": "特記事項なし。"},
    }
    card["statements"].update(statements)
    return card


def _invoke(card, llm=None):
    """Exactly how the shipped `src/api/server.py` calls the agent: `invoke(input, ctx=ctx)`."""
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    body = card if isinstance(card, str) else json.dumps(card, ensure_ascii=False)
    return Graph(config={"llm": llm} if llm else None).invoke(body, ctx=ctx)


class TestAnExcerptReachesNeitherTheModelNorTheOutput:
    @pytest.mark.parametrize("field", ["purpose", "remarks", "authority_basis",
                                       "corpus_classification"])
    def test_the_card_is_processed_and_the_excerpt_is_nowhere(self, field):
        llm = _RecordingLLM()
        out = _invoke(_card(**{field: f"{_EXCERPT}"}), llm=llm)
        envelope = json.loads(out["output"])

        # (0) Processed — not discarded. Without this the assertions below are satisfied by
        # wholesale rejection, which would prove nothing about containment.
        assert out["status"] == AgentStatus.SUCCESS.value
        assert envelope["status_kind"] == "administrative_document_request_routing"
        assert envelope["routing_outcome"] == "restricted_escalate"

        # (1) The model was shown the card, and the excerpt was not in it.
        assert llm.prompts, "the LLM seam did not run — the view assertion would be vacuous"
        view = "\n".join(llm.prompts)
        for token in _EXCERPT_TOKENS:
            assert token not in view, f"{token!r} reached the LLM view via {field}"

        # (2) and (3) — neither the excerpt nor anything derived from it reaches the caller.
        rendered = out["output"]
        for token in _EXCERPT_TOKENS:
            assert token not in rendered, f"{token!r} reached the output via {field}"

        # What is left is the flag and the field ID — and nothing else about that field.
        attestation = envelope["no_document_intake_attestation"]
        assert attestation["administrative_document_body_ingested"] is False
        assert field in attestation["fields_with_detected_body_content"]
        assert ("document_body_content_detected", field) in {
            (g["gap_kind"], g["cited_card_field_ref"]) for g in envelope["evidence_gaps"]}

    def test_no_evidence_span_is_derived_from_a_contaminated_field(self):
        """★ Part 3. A masked or truncated field would still yield a span; a dropped one cannot."""
        out = _invoke(_card(purpose=_EXCERPT))
        envelope = json.loads(out["output"])
        spans = [entry["evidence_span"]
                 for section in ("declared_purpose_statements", "accountable_owner_statements",
                                 "classification_statements")
                 for entry in envelope[section]]
        assert all(entry["cited_card_field_ref"] != "purpose"
                   for entry in envelope["declared_purpose_statements"]), \
            "a contaminated field produced a statement — it should have produced only a flag"
        for span in spans:
            for token in _EXCERPT_TOKENS:
                assert token not in span

    def test_the_minimised_card_keeps_the_field_id_and_no_text(self):
        """At the interface itself: the object handed to Steps 3–6 has no `statement` key at all."""
        out = PreProcessNode().execute(
            {"user_input": json.dumps(_card(remarks=_EXCERPT), ensure_ascii=False), "session_id": "s"})
        minimised = json.loads(out["validated_input"])
        [entry] = [s for s in minimised["statements"] if s["field_id"] == "remarks"]
        assert entry == {"field_id": "remarks", "body_content_detected": True,
                         "detection_reason": "body_marker"}
        assert "statement" not in entry
        assert svc.minimised_card_violation(minimised) is None

    def test_an_excerpt_smuggled_through_reference_metadata_is_dropped_too(self):
        """The allow set permits a label and a version — it does not permit them to carry a body."""
        card = _card()
        card["declared_references"] = [{"field_id": "corpus_classification",
                                        "reference_label": _EXCERPT,
                                        "reference_version": "令和7年度"}]
        out = _invoke(card)
        envelope = json.loads(out["output"])
        assert envelope["routing_outcome"] == "restricted_escalate"
        for token in _EXCERPT_TOKENS:
            assert token not in out["output"]

    def test_an_unlisted_key_cannot_carry_a_body_through(self):
        """The allow-set copy simply does not read it, so no new rule is needed per leak shape."""
        card = _card()
        card["document_body"] = _EXCERPT
        card["attachment_text"] = _EXCERPT
        out = _invoke(card)
        assert json.loads(out["output"])["status_kind"] == "administrative_document_request_routing"
        for token in _EXCERPT_TOKENS:
            assert token not in out["output"]


class TestAmbiguityFailsClosed:
    def test_a_field_too_long_to_classify_is_treated_as_contaminated(self):
        """SoT §4 Step 2: 検出が曖昧なら fail-closed で混入扱い. Not a detection — the absence of one."""
        long_text = "本件の目的は審査事務の効率化である。" * 40
        assert svc.document_body_signal(long_text) == "undistinguishable_length"
        envelope = json.loads(_invoke(_card(remarks=long_text))["output"])
        assert envelope["routing_outcome"] == "restricted_escalate"
        assert "remarks" in envelope["no_document_intake_attestation"][
            "fields_with_detected_body_content"]
        assert "審査事務の効率化である" not in json.dumps(envelope, ensure_ascii=False)


class TestTheInvariantDoesNotBreakLegitimateCards:
    """A gate that fires on ordinary declarations is unusable, which is the worse failure."""

    @pytest.mark.parametrize("field,text", [
        ("authority_basis", "行政機関の保有する情報の公開に関する法律 第5条第1号に基づく所掌事務"),
        ("accountable_owner", "○○課 課長補佐 (当面代行)。正式な説明責任者は次回課内決裁をもって指名する。"),
        ("corpus_classification",
         "対象は、令和7年度○○課決裁『行政文書ファイル管理簿 別表2』に掲げるファイルのとおり。"),
        ("remarks", "区分は昨年度申請と同一。ただし対象に不開示情報を含む決裁文書の写しを追加する。"),
    ])
    def test_a_declaration_from_the_sot_worked_examples_survives(self, field, text):
        envelope = json.loads(_invoke(_card(**{field: text}))["output"])
        assert field not in envelope["no_document_intake_attestation"][
            "fields_with_detected_body_content"], f"legitimate declaration in {field} was dropped"
        assert envelope["routing_outcome"] != "restricted_escalate"
