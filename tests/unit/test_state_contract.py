"""GOV-C2-128 — the state contract is machine-checked, not asserted field by field.

Review (Medium, sibling template): the state tests checked credentials and Pydantic
shapes only, so a nested annotation could be added without any test noticing — which is how
``enriched_context: NotRequired[dict]`` came to contradict this template's own documented rule.

Two halves, both needed. Checking only declared fields would let the rule be escaped by deleting a
declaration; checking only the effective state would flag the platform's own field forever.
"""

import json
import typing

from framework.schemas.agent_state import AgentState

from src.schemas.state import State

_PRIMITIVES = {str, int, float, bool}


def _resolved(cls) -> dict:
    """Annotations with forward references resolved.

    ``__annotations__`` alone is not enough: a module using string annotations yields
    ``ForwardRef('Optional[str]')``, which no type check can see through — a sibling template's
    perfectly flat state read as nested until this was fixed.
    """
    try:
        return typing.get_type_hints(cls, include_extras=True)
    except Exception:                      # unresolvable name — fall back to the raw annotations
        return dict(getattr(cls, "__annotations__", {}))


def _declared() -> dict:
    """Annotations this template declares, excluding everything inherited from AgentState."""
    return {k: v for k, v in _resolved(State).items()
            if k not in getattr(AgentState, "__annotations__", {})}


def _unwrap(ann):
    """Strip NotRequired[...] / Optional[...] down to the concrete types."""
    if typing.get_origin(ann) is not None:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return [t for a in args for t in _unwrap(a)]
    return [ann]


def _is_nested(ann) -> bool:
    return any(t not in _PRIMITIVES for t in _unwrap(ann))


def _platform_nested() -> set[str]:
    """Fields the **platform** declares as non-primitive — computed, not hardcoded.

    ``AgentState`` legitimately carries several (``input_context``, ``execution_time``,
    ``node_history``, ``enriched_context``, the ``hitl_*`` family…). Those are the framework's
    contract, not this template's choice. Hardcoding one name here would have made this check wrong
    the moment the platform added a field.
    """
    return {n for n, ann in _resolved(AgentState).items() if _is_nested(ann)}


class TestEveryDeclaredFieldIsFlat:
    def test_no_declared_field_is_nested(self):
        offenders = {name: ann for name, ann in _declared().items() if _is_nested(ann)}
        assert not offenders, (
            f"nested annotations declared by this template: {offenders}. "
            "Carry the payload as a JSON string — msgpack checkpoint serialization.")

    def test_the_check_actually_covers_the_fields(self):
        """Guards against a refactor that empties `_declared()` and passes vacuously."""
        declared = _declared()
        assert len(declared) >= 8, f"only {len(declared)} declared fields — check is not covering"
        carriers = [n for n, ann in declared.items() if _unwrap(ann) == [str]]
        assert len(carriers) >= 3, f"expected JSON-carrying str fields, found {carriers}"


class TestNestedFieldsAreOnlyThePlatformsOwn:
    def test_deleting_a_declaration_does_not_escape_the_rule(self):
        """The effective state — inherited included — may nest only where the platform defines it."""
        effective = _resolved(AgentState) | _resolved(State)
        nested = {n for n, ann in effective.items() if _is_nested(ann)}
        extra = nested - _platform_nested()
        assert not extra, f"nested fields this template introduced: {extra}"

    def test_enriched_context_is_the_platforms_field_not_ours(self):
        """Backs the claim the module docstring makes, so the docs cannot drift from the code."""
        assert "enriched_context" in _platform_nested(), (
            "if the platform stops declaring it, this template must carry it as a JSON string")
        assert "enriched_context" not in _declared(), (
            "re-declaring the platform's field reads as this template choosing a nested type")


class TestJsonStringFieldsRoundTrip:
    def test_every_json_carrying_field_is_written_as_a_string(self):
        from src.nodes.pre_process_node import PreProcessNode
        out = PreProcessNode().execute({"user_input": json.dumps({
            "request_card_id": "RC-1", "card_version_ref": "v2",
            "routing_template_version": "gov-routing-v1",
            "corpus_classification": "public_disclosed",
            "statements": {"purpose": "審査事務の効率化。"}}, ensure_ascii=False),
            "session_id": "s"})
        assert isinstance(out["validated_input"], str)
        json.loads(out["validated_input"])          # parses — a JSON string, not a repr
