"""GOV-C2-128 — unit tests for the deterministic service layer."""

import pytest

import src.services.service as svc


class TestProvenance:
    @pytest.mark.parametrize("value,ok", [
        ("file_management_ledger:annex-2", True), ("decision_record:2026-0731-01", True),
        ("annex:2", True),
        ("random_blog:r-1", False),           # unauthorised reference registry
        ("file_management_ledger", False),    # a registry, but no record → not a citation
        ("ref:1a2b3c4d", False),              # shaped like an internal anchor → no passthrough
        ("card:deadbeef", False), ("山田 太郎", False), ("unknown", False), ("", False),
    ])
    def test_resolve_provenance(self, value, ok):
        assert (svc.resolve_provenance(value) is not None) == ok

    def test_declared_provenance_is_not_verification(self):
        """★ The documented limit, asserted rather than hidden.

        A fabricated reference under an authorised registry DOES produce a citation. The reference
        and its label arrive in the same caller body and no verification surface is exposed to the
        template, so the citation asserts a caller *declaration* — which the envelope states via
        `citation_basis`. Closing this needs a server-side lookup or gateway-signed references (SoT
        §12-B). If a future change makes provenance verifiable, this test fails and the claim in
        CITATION_BASIS and the docs must be updated with it.
        """
        assert svc.resolve_provenance("file_management_ledger:invented-annex").startswith("ref:")

    def test_registry_case_folds_but_reference_does_not(self):
        assert svc.resolve_provenance("FILE_MANAGEMENT_LEDGER:a-1") == \
            svc.resolve_provenance("file_management_ledger:a-1")
        assert svc.resolve_provenance("file_management_ledger:A-1") != \
            svc.resolve_provenance("file_management_ledger:a-1")

    def test_identifiers_are_always_tokenised(self):
        """No syntactic passthrough: a value already shaped like a surrogate is re-hashed."""
        assert svc.opaque_id("card:aaaa1111", "card") != "card:aaaa1111"
        assert svc.opaque_id("山田 太郎", "own").startswith("own:")


class TestTheNoDocumentIntakeSchemaIsOneAllowSet:
    """★ The invariant is a schema, not prose and not a prohibition list."""

    def _card(self, **over):
        card = {"request_card_id": "card:11111111", "card_version_ref": "v2",
                "routing_template_version": "gov-routing-v1",
                "declared_values": {"corpus_classification": "public_disclosed"},
                "statements": [{"field_id": "purpose", "statement": "効率化。"}],
                "references": [{"field_id": "purpose", "reference_label": "一覧"}],
                "body_content_fields": []}
        card.update(over)
        return card

    def test_a_conforming_card_passes(self):
        assert svc.minimised_card_violation(self._card()) is None

    @pytest.mark.parametrize("over,fragment", [
        ({"raw_card": {"purpose": "…"}}, "card_keys_outside_schema"),
        ({"declared_values": {"requester_name": "山田 太郎"}}, "declared_value_keys_outside_schema"),
        ({"statements": [{"field_id": "purpose", "statement": "x", "document_excerpt": "…"}]},
         "statement_keys_outside_schema"),
        ({"statements": [{"field_id": "not_a_card_field", "statement": "x"}]},
         "statement_field_id_outside_closed_set"),
        ({"references": [{"field_id": "purpose", "document_number": "第123号"}]},
         "reference_keys_outside_schema"),
        ({"references": [{"field_id": "invented", "reference_label": "x"}]},
         "reference_field_id_outside_closed_set"),
    ])
    def test_anything_outside_the_allow_set_is_a_violation(self, over, fragment):
        assert fragment in svc.minimised_card_violation(self._card(**over))

    def test_a_contaminated_statement_may_not_keep_its_text(self):
        """The point of the invariant: contamination DROPS the text, it does not mask or keep it."""
        violation = svc.minimised_card_violation(self._card(statements=[
            {"field_id": "purpose", "body_content_detected": True, "statement": "決裁伺の本文"}]))
        assert violation == "contaminated_statement_retained_text:purpose"

    def test_the_checker_reports_keys_not_values(self):
        """A violation report must not become the leak it is reporting."""
        secret = "決裁を仰ぎたく、起案理由は下記のとおり"
        violation = svc.minimised_card_violation(self._card(document_body={"purpose": secret}))
        assert secret not in violation

    def test_no_personal_data_field_is_reachable_through_the_allow_set(self):
        """PII never crosses because the allow-set copy simply does not read those keys."""
        assert not set(svc.DECLARED_VALUE_FIELDS) & svc.PII_DROP_FIELDS
        assert not svc.MINIMISED_CARD_KEYS & svc.PII_DROP_FIELDS
        assert not set(svc.CARD_FIELD_IDS) & svc.PII_DROP_FIELDS


class TestDocumentBodyDetection:
    @pytest.mark.parametrize("text,reason", [
        ("上記について決裁伺のとおり処理する。", "body_marker"),
        ("起案理由: 事務効率化のため。", "body_marker"),
        ("収受番号 A-1 の文書に関する件", "body_marker"),
        ("○○発第123号により通知された事項", "document_number"),
        ("第 4567 号 に基づく処理", "document_number"),
        ("「" + "本件については別添のとおり処理してよろしいか。" * 4 + "」", "verbatim_excerpt"),
        ("あ" * (svc.DECLARATION_MAX_CHARS + 1), "undistinguishable_length"),
    ])
    def test_contamination_is_detected_with_a_reason(self, text, reason):
        assert svc.document_body_signal(text) == reason

    @pytest.mark.parametrize("text", [
        # ★ Every one of these is a legitimate declaration taken from the SoT's own worked examples
        # or from the statutory citation an authority-basis field is expected to carry. A detector
        # that fires on them makes the gate unusable, which is a worse failure than the one it guards.
        "行政機関の保有する情報の公開に関する法律 第5条第1号に基づく所掌事務",
        "個人情報の保護に関する法律 第2項第3号",
        "○○課 課長補佐 (当面代行)。正式な説明責任者は次回課内決裁をもって指名する。",
        "対象は、令和7年度○○課決裁『行政文書ファイル管理簿 別表2』に掲げるファイルのとおり。",
        "区分は昨年度申請と同一 (公開済文書)。ただし対象に、部分的に不開示情報を含む決裁文書の写しを追加する。",
        "令和7年度の審査事務の効率化を目的とし、当面は要約のみを行う。",
        "別表第2号および様式第1号に定める区分による。",
    ])
    def test_legitimate_declarations_are_not_contamination(self, text):
        assert svc.document_body_signal(text) is None

    def test_ambiguity_fails_closed_on_length(self):
        """A declaration is short. Past the ceiling we cannot tell it from a pasted body, so we do
        not guess — the field is dropped and the request is escalated."""
        assert svc.document_body_signal("あ" * svc.DECLARATION_MAX_CHARS) is None
        assert svc.document_body_signal("あ" * (svc.DECLARATION_MAX_CHARS + 1)) is not None


class TestReconciliation:
    def _card(self, values=None, statements=None, **over):
        card = {"request_card_id": "card:11111111", "card_version_ref": "v2",
                "routing_template_version": "gov-routing-v1",
                "declared_values": {"source_agency": "○○省", "corpus_classification": "public_disclosed",
                                    "sensitivity_classification": "none", "output_type": "summary",
                                    "retention_request": "no_retention",
                                    "accountable_owner_role": "division_director"},
                "statements": [{"field_id": "authority_basis", "statement": "情報公開法に基づく"},
                               {"field_id": "purpose", "statement": "効率化。"}],
                "references": [], "body_content_fields": []}
        card["declared_values"].update(values or {})
        card["statements"] += statements or []
        card.update(over)
        return card

    def test_a_complete_card_raises_no_gap_and_proves_all_three_deterministic_claims(self):
        result = svc.reconcile_declared_fields(self._card())
        assert result["gaps"] == []
        assert result["required_fields_present"] and result["declared_values_within_enum"]
        assert result["accountable_owner_authorised"]

    def test_an_unauthorised_owner_role_is_named_not_guessed(self):
        result = svc.reconcile_declared_fields(self._card(
            {"corpus_classification": "non_disclosable",
             "sensitivity_classification": "non_disclosure_information",
             "accountable_owner_role": "section_chief"}))
        assert "accountable_owner_not_authorised_for_classification" in {g["gap_kind"] for g in result["gaps"]}
        assert result["accountable_owner_authorised"] is False

    def test_an_unknown_owner_role_is_ambiguous_not_unauthorised(self):
        """"Not authorised" would over-claim: the seeded model simply cannot resolve the role."""
        kinds = {g["gap_kind"] for g in
                 svc.reconcile_declared_fields(self._card({"accountable_owner_role": "外部委員"}))["gaps"]}
        assert "statement_ambiguous" in kinds
        assert "accountable_owner_not_authorised_for_classification" not in kinds

    def test_a_sensitivity_declaration_that_contradicts_the_classification_is_surfaced(self):
        result = svc.reconcile_declared_fields(self._card(
            {"corpus_classification": "non_disclosable", "sensitivity_classification": "none",
             "accountable_owner_role": "director_general"}))
        assert "sensitivity_declaration_inconsistent" in {g["gap_kind"] for g in result["gaps"]}
        assert result["contradictions"][0]["cited_card_field_refs"] == [
            "corpus_classification", "sensitivity_classification"]

    def test_retention_beyond_the_classification_ceiling_is_surfaced(self):
        result = svc.reconcile_declared_fields(self._card(
            {"corpus_classification": "non_disclosable",
             "sensitivity_classification": "non_disclosure_information",
             "accountable_owner_role": "director_general",
             "retention_request": "retain_beyond_review"}))
        assert "retention_request_exceeds_declared_basis" in {g["gap_kind"] for g in result["gaps"]}

    def test_every_gap_is_anchored_on_a_card_field(self):
        result = svc.reconcile_declared_fields({"declared_values": {}, "statements": [],
                                                "references": [], "body_content_fields": []})
        assert result["gaps"]
        for item in result["gaps"]:
            assert item["cited_card_field_ref"] in svc.CARD_FIELD_IDS

    def test_contamination_is_a_gap_in_its_own_right(self):
        result = svc.reconcile_declared_fields(self._card(body_content_fields=["remarks"]))
        assert ("document_body_content_detected", "remarks") in {
            (g["gap_kind"], g["cited_card_field_ref"]) for g in result["gaps"]}


class TestInterpretationOfTheSoTWorkedCases:
    """★ SoT §2-4 cases 1–4 — the readings a deterministic field check gets wrong."""

    def _card(self, **statements):
        return {"statements": [{"field_id": k, "statement": v} for k, v in statements.items()],
                "body_content_fields": []}

    def test_case_1_a_phased_purpose_is_not_reported_as_settled(self):
        [item] = svc.interpret_statements(self._card(purpose=(
            "令和7年度の審査事務の効率化を目的とし、当面は要約のみを行う。"
            "統計的な傾向分析については、個人情報保護担当課の確認を得た上で別途実施する。")))
        assert item["gap_kind"] == "purpose_stated_conditional_phase"
        assert item["qualifier"] == "conditional_phase"
        assert item["cited_card_field_ref"] == "purpose"
        assert item["evidence_span"]

    def test_case_2_an_acting_designation_is_neither_named_nor_unnamed(self):
        [item] = svc.interpret_statements(self._card(accountable_owner=(
            "○○課 課長補佐 (当面代行)。正式な説明責任者は次回課内決裁をもって指名する。")))
        assert item["gap_kind"] == "accountable_owner_delegated_provisional"
        assert item["qualifier"] == "delegated_provisional"

    def test_a_designation_settled_by_an_acting_decision_is_ambiguous_not_provisional(self):
        """★ The SoT's own counter-example to widening the lexicon: "代行決裁により正式に指名済".

        Both signals are present, so neither reading may be asserted. Guessing either way is the
        failure the evaluation described — in one direction a false clearance, in the other worklist
        noise.
        """
        [item] = svc.interpret_statements(self._card(accountable_owner="代行決裁により正式に指名済。"))
        assert item["gap_kind"] == "statement_ambiguous"
        assert item["qualifier"] == "ambiguous"

    def test_case_3_a_delegated_scope_is_distinguished_from_a_blank_field(self):
        [item] = svc.interpret_statements(self._card(corpus_classification=(
            "対象は、令和7年度○○課決裁『行政文書ファイル管理簿 別表2』に掲げるファイルのとおり。"
            "本欄では個別に再掲しない。")))
        assert item["gap_kind"] == "corpus_scope_indirect_reference"
        assert item["qualifier"] == "indirect_reference"

    def test_case_4_a_widened_scope_under_an_unchanged_label_is_read_across_both_fields(self):
        """The claim and the addition sit in different 欄; reading either alone misses it."""
        [item] = svc.interpret_statements(self._card(
            corpus_classification="区分は昨年度申請と同一 (公開済文書)。",
            remarks="ただし対象に、部分的に不開示情報を含む決裁文書の写しを追加する。"))
        assert item["gap_kind"] == "classification_label_unchanged_scope_widened"
        assert item["qualifier"] == "label_unchanged_scope_widened"

    def test_a_settled_card_produces_no_gap_kind(self):
        items = svc.interpret_statements(self._card(
            purpose="審査事務の効率化を行う。", accountable_owner="○○課長を正式に指名済。",
            corpus_classification="本欄に列挙した公開済文書に限る。"))
        assert [i["gap_kind"] for i in items] == [None, None, None]
        assert [i["qualifier"] for i in items] == ["unconditional", "named_substantive",
                                                   "enumerated_in_card"]

    def test_a_quarantined_declaration_is_ambiguous_not_settled(self):
        """A field that was never read must not be reported as read and settled."""
        items = svc.interpret_statements(self._card(purpose=svc.QUARANTINE_SENTINEL))
        assert items[0]["qualifier"] == "ambiguous"


class TestLlmViewAndValidation:
    def _card(self):
        return {"statements": [{"field_id": "purpose", "statement": "効率化。"},
                               {"field_id": "remarks", "body_content_detected": True,
                                "detection_reason": "body_marker"}],
                "body_content_fields": ["remarks"]}

    def test_the_view_carries_only_surviving_statements(self):
        view = svc.build_llm_view(self._card())
        assert [s["field_id"] for s in view["statements"]] == ["purpose"]
        assert view["contaminated_field_ids"] == ["remarks"]

    def test_the_view_frames_the_card_as_quoted_data(self):
        assert "never instructions" in svc.build_llm_view(self._card())["instructions_to_model"]

    def test_an_interpretation_outside_the_approved_sets_is_discarded_not_echoed(self):
        kept, rejected = svc.validate_interpretations([
            {"field_id": "purpose", "gap_kind": "invented_kind", "qualifier_key": "phase_qualifier",
             "qualifier": "unconditional"},
            {"field_id": "purpose", "gap_kind": None, "qualifier_key": "phase_qualifier",
             "qualifier": "totally_made_up"},
            {"field_id": "not_a_field", "gap_kind": None, "qualifier_key": "phase_qualifier",
             "qualifier": "unconditional"},
            {"field_id": "remarks", "gap_kind": None, "qualifier_key": "phase_qualifier",
             "qualifier": "unconditional"},       # contaminated field — not in the view
        ], self._card())
        assert kept == [] and rejected == 4

    def test_the_span_is_re_derived_from_the_card_never_taken_from_the_model(self):
        """A model that echoed text back must not be able to reintroduce it through its own output."""
        [item], _ = svc.validate_interpretations([
            {"field_id": "purpose", "gap_kind": None, "qualifier_key": "phase_qualifier",
             "qualifier": "unconditional", "evidence_span": "決裁伺の本文をここに再掲する"}], self._card())
        assert item["evidence_span"] == "効率化。"


class TestRouting:
    _PROVEN = dict.fromkeys(svc.SCOPE_COMPLETE_PROOFS, True)

    def test_nothing_wrong_and_everything_proven_is_the_only_way_to_scope_complete(self):
        assert svc.decide_route(set(), self._PROVEN) == ("scope_complete", [])

    @pytest.mark.parametrize("kind,outcome", [
        ("classification_label_unchanged_scope_widened", "privacy_security_owner_review"),
        ("sensitivity_declaration_inconsistent", "privacy_security_owner_review"),
        ("document_body_content_detected", "restricted_escalate"),
        ("output_type_unsupported", "unsupported_request"),
        ("purpose_stated_conditional_phase", "clarification_needed"),
        ("accountable_owner_delegated_provisional", "clarification_needed"),
        ("corpus_scope_indirect_reference", "clarification_needed"),
    ])
    def test_each_signal_reaches_its_route(self, kind, outcome):
        assert svc.decide_route({kind}, self._PROVEN)[0] == outcome

    def test_a_statutory_control_point_outranks_the_intake_breach(self):
        """Both are escalations; the precedence has to be one rule, not a coin toss."""
        assert svc.decide_route(
            {"document_body_content_detected", "sensitivity_declaration_inconsistent"},
            self._PROVEN)[0] == "privacy_security_owner_review"

    def test_an_unproven_claim_with_no_gap_still_never_reaches_scope_complete(self):
        outcome, driving = svc.decide_route(set(), {**self._PROVEN, "accountable_owner_authorised": False})
        assert outcome == "clarification_needed" and driving == ["accountable_owner_authorised"]

    def test_every_route_is_in_the_closed_set(self):
        for kind in svc.GAP_TAXONOMY:
            assert svc.decide_route({kind}, self._PROVEN)[0] in svc.ROUTING_OUTCOMES


class TestS3ReDerivations:
    def _record(self, **over):
        record = {"routing_outcome": "clarification_needed",
                  "routing_basis": [{"gap_kind": "purpose_stated_conditional_phase",
                                     "cited_card_field_ref": "purpose"}],
                  "evidence_gaps": [{"gap_kind": "purpose_stated_conditional_phase",
                                     "cited_card_field_ref": "purpose"}],
                  "reviewer_questions": [{"gap_kind": "purpose_stated_conditional_phase",
                                          "question": "q", "cited_card_field_ref": "purpose"}],
                  "contradictions": [],
                  "declared_purpose_statements": [{"purpose_ref": "purpose#1",
                                                   "phase_qualifier": "conditional_phase",
                                                   "cited_card_field_ref": "purpose"}],
                  "accountable_owner_statements": [], "classification_statements": [],
                  "scope_complete_proofs": dict.fromkeys(svc.SCOPE_COMPLETE_PROOFS, True)}
        record.update(over)
        return record

    def test_a_grounded_record_binds(self):
        assert svc.citation_binding_failure(self._record(), {"purpose"}) is None

    def test_an_unanchored_entry_is_blocked(self):
        assert svc.citation_binding_failure(
            self._record(evidence_gaps=[{"gap_kind": "out_of_scope", "cited_card_field_ref": None}]),
            {"purpose"}) == "CITATION_MISSING"

    def test_an_anchor_outside_the_closed_index_is_blocked(self):
        assert svc.citation_binding_failure(
            self._record(evidence_gaps=[{"gap_kind": "out_of_scope",
                                         "cited_card_field_ref": "remarks"}]),
            {"purpose"}) == "CITATION_INCOMPLETE"

    def test_a_statement_without_an_approved_qualifier_is_blocked(self):
        assert svc.qualifier_completeness_failure(self._record(
            declared_purpose_statements=[{"purpose_ref": "purpose#1",
                                          "cited_card_field_ref": "purpose"}])) == "QUALIFIER_MISSING"

    def test_scope_complete_is_recomputed_not_believed(self):
        """★ A composer that merely wrote the outcome must not be able to publish it."""
        assert svc.qualifier_completeness_failure(
            self._record(routing_outcome="scope_complete")) == "SCOPE_COMPLETE_UNPROVEN"

    def test_scope_complete_needs_all_four_proofs(self):
        clean = self._record(routing_outcome="scope_complete", routing_basis=[], evidence_gaps=[],
                             reviewer_questions=[], declared_purpose_statements=[])
        assert svc.qualifier_completeness_failure(clean) is None
        for proof in svc.SCOPE_COMPLETE_PROOFS:
            broken = dict(clean, scope_complete_proofs={
                **clean["scope_complete_proofs"], proof: False})
            assert svc.qualifier_completeness_failure(broken) == "SCOPE_COMPLETE_UNPROVEN", proof

    def test_scope_complete_may_not_carry_an_unresolved_qualifier(self):
        assert svc.qualifier_completeness_failure(self._record(
            routing_outcome="scope_complete", routing_basis=[], evidence_gaps=[],
            reviewer_questions=[])) == "SCOPE_COMPLETE_UNPROVEN"

    @pytest.mark.parametrize("rendered,blocked", [
        ('{"evidence_span": "当面は要約のみ"}', False),
        ('{"evidence_span": "決裁伺の本文"}', True),
        ('{"evidence_span": "○○発第123号"}', True),
    ])
    def test_document_content_is_re_scanned_at_the_output(self, rendered, blocked):
        assert (svc.document_content_failure(rendered) is not None) == blocked


class TestTaxonomyDiscipline:
    def test_no_taxonomy_key_states_a_legal_outcome(self):
        """The agent reports what the card says, never whether it is lawful."""
        for key in svc.GAP_TAXONOMY:
            for banned in ("legitimate", "lawful", "compliant", "approved", "permitted",
                           "exempt", "_is_valid", "disclosable_confirmed"):
                assert banned not in key, f"taxonomy key {key!r} states an outcome"

    def test_every_gap_kind_has_a_reviewer_question(self):
        assert set(svc.GAP_TAXONOMY) == set(svc.REVIEWER_QUESTIONS)

    def test_an_unknown_kind_falls_back_to_the_closed_set(self):
        assert svc.gap("invented_kind", "purpose")["gap_kind"] == "out_of_scope"

    def test_an_unknown_anchor_is_dropped_rather_than_published(self):
        assert svc.gap("out_of_scope", "not_a_card_field")["cited_card_field_ref"] is None


class TestTheAgentsOwnTextIsInertAgainstItsOwnGates:
    """★ A self-inflicted failure mode, closed by measurement rather than by care.

    S-3 re-scans the **rendered envelope**, and the envelope contains this template's own fixed text:
    the disclaimer, the withheld message, every reviewer question, every taxonomy description. If any
    of those ever contained a document-body marker (`文書番号`), a document-number shape, or an
    injection marker, the agent would withhold — or mangle — every record it produced, including
    perfectly good ones, and the cause would be a doc edit rather than the caller's payload.

    Nothing here is a rule about wording. It is a check that the wording actually chosen is inert.
    """

    @staticmethod
    def _fixed_strings() -> dict[str, str]:
        from src.nodes import post_process_node as post
        strings = {"disclaimer": post._DISCLAIMER, "withheld": post._WITHHELD_MSG,
                   "out_of_scope": post._OUT_OF_SCOPE_MSG, "needs_review": post._NEEDS_REVIEW_NOTE,
                   "no_intake_basis": svc.NO_DOCUMENT_INTAKE_BASIS,
                   "citation_basis": svc.CITATION_BASIS, "quarantine": svc.QUARANTINE_SENTINEL}
        strings |= {f"question:{k}": v for k, v in svc.REVIEWER_QUESTIONS.items()}
        strings |= {f"taxonomy:{k}": v for k, v in svc.GAP_TAXONOMY.items()}
        return strings

    def test_the_scan_covers_every_fixed_string(self):
        assert len(self._fixed_strings()) > 35, "the fixed-string inventory is not covering"

    def test_no_fixed_string_trips_the_document_content_check(self):
        tripped = {name: svc.document_content_failure(text)
                   for name, text in self._fixed_strings().items()
                   if svc.document_content_failure(text)}
        assert not tripped, f"the agent's own text would withhold every record: {tripped}"

    def test_no_fixed_string_trips_the_injection_neutraliser(self):
        tripped = [name for name, text in self._fixed_strings().items()
                   if svc.contains_injection_marker(text)]
        assert not tripped, f"the agent's own text would be neutralised in its own output: {tripped}"

    def test_no_fixed_string_looks_like_a_minted_anchor(self):
        tripped = {name: svc.surrogates_in(text) for name, text in self._fixed_strings().items()
                   if svc.surrogates_in(text)}
        assert not tripped, f"fixed text would fail the fabricated-anchor check: {tripped}"


class TestRedaction:
    @pytest.mark.parametrize("text", ["taro@example.go.jp", "03-1234-5678", "1234 5678 9012",
                                      "sk-abcdefghijklmnop"])
    def test_contact_and_credential_shapes_are_redacted(self, text):
        assert svc.REDACTED in svc.redact(f"担当 {text} まで")

    def test_ordinary_declarations_are_untouched(self):
        text = "令和7年度の審査事務の効率化を目的とする。"
        assert svc.redact(text) == text
