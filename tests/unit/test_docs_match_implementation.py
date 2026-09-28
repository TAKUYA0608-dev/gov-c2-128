"""Identifiers named in the docs must exist in the implementation.

Review (Medium, sibling templates): docs named a taxonomy key the implementation did not
have — a substitution artefact from porting a template from a sibling — and `docs/07` described the
sibling's domain, listing an event and provenance namespaces that agent never emits or accepts.

Both are the same failure: prose drifting from the code with nothing checking. Reviewing docs by eye
finds it once; this finds it every time. It deliberately checks *identifiers* — taxonomy keys, trace
events, provenance registries — because those are the parts an operator copies verbatim into a
payload, a query, or an alert, and a wrong one silently does not match.

Every check derives its expectation **from the source**, never from a hand-maintained list here: a
list would go stale in exactly the way this file exists to prevent.
"""

import ast
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
_DOCS = sorted((_ROOT / "docs").glob("*.md"))

#: Backticked tokens that are not agent identifiers — filenames, config keys, JSON paths.
_NOT_IDENTIFIERS = re.compile(r"\.(py|md|json|yaml|yml|toml|sh|txt)$")


def _source_text() -> str:
    return "\n".join(p.read_text() for p in _SRC.rglob("*.py"))


def _emitted_events() -> set[str]:
    out = set()
    for path in _SRC.rglob("*.py"):
        for call in ast.walk(ast.parse(path.read_text())):
            if (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "emit_trace_event"
                    and call.args and isinstance(call.args[0], ast.Constant)):
                out.add(call.args[0].value)
    return out


def _backticked(pattern: re.Pattern) -> dict[str, list[str]]:
    """Matching backticked tokens per doc file."""
    found: dict[str, list[str]] = {}
    for doc in _DOCS:
        hits = [t for t in re.findall(r"`([^`\s]+)`", doc.read_text())
                if pattern.fullmatch(t) and not _NOT_IDENTIFIERS.search(t)]
        if hits:
            found[doc.name] = sorted(set(hits))
    return found


class TestTaxonomyKeys:
    """A gap kind, routing outcome or qualifier the docs name must be one the agent can produce."""

    @staticmethod
    def _closed_vocabulary() -> set[str]:
        """Every value in this template's closed sets — read from the module, not listed here."""
        from src.services import service
        out: set[str] = set()
        for name, value in vars(service).items():
            if not name.isupper():
                continue
            if isinstance(value, dict):
                out |= {k for k in value if isinstance(k, str)}
            elif isinstance(value, (frozenset, set, tuple, list)):
                out |= {v for v in value if isinstance(v, str)}
        return out

    def test_every_agent_identifier_named_in_the_docs_exists_in_src(self):
        """A backticked snake_case token counts as an agent identifier when its suffix is one this
        agent's own closed sets use. Deriving the suffixes from the code, rather than hardcoding
        them, keeps the check pointed at the identifiers as they are now: adding a taxonomy key
        ending `_withdrawn` extends the check automatically instead of falling outside it.

        The rule is deliberately blunt — **if the docs backtick it, `src/` must contain it.** An
        operator copies a backticked token verbatim into a payload or a log query, and a wrong one
        fails silently. Framework-owned lifecycle names (node_start / node_complete) belong to the
        platform, not to this template, so the docs mention them unbackticked.
        """
        vocabulary = self._closed_vocabulary()
        assert len(vocabulary) > 30, "closed-set scan found almost nothing — the check is vacuous"
        suffixes = {token.rsplit("_", 1)[-1] for token in vocabulary if "_" in token}
        assert len(suffixes) > 5, "no suffixes derivable — the check would be vacuous"

        src = _source_text()
        named = _backticked(re.compile(r"[a-z][a-z0-9_]{6,}"))
        missing = {doc: [t for t in tokens
                         if "_" in t and t.rsplit("_", 1)[-1] in suffixes and t not in src]
                   for doc, tokens in named.items()}
        missing = {d: t for d, t in missing.items() if t}
        assert not missing, f"identifiers in docs but not in src/: {missing}"


class TestTraceEvents:
    def test_every_event_named_in_the_docs_is_emitted(self):
        """A token counts as an event when its suffix is one this agent's own events use.

        Matching on shape alone was too broad — `hitl.enabled` and `agent.required_trust_level` are
        config keys, not events.
        """
        emitted = _emitted_events()
        assert emitted, "no emit_trace_event literals found — the check would be vacuous"
        verbs = {e.split(".", 1)[1] for e in emitted if "." in e}
        assert verbs, "no event suffixes derivable — the check would be vacuous"
        named = _backticked(re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*"))
        missing = {doc: [t for t in tokens if t.split(".", 1)[1] in verbs and t not in emitted]
                   for doc, tokens in named.items()}
        missing = {d: t for d, t in missing.items() if t}
        assert not missing, f"trace events in docs but never emitted: {missing}"


class TestProvenanceRegistries:
    """`docs/07` lists the reference registries an operator may cite. A wrong one is rejected silently.

    ★ The first version of this on a sibling template skipped on both repos because its constant
    lookup had a precedence bug — a silent skip, which is the same vacuous pass this file exists to
    prevent. It now decides on the doc: if the guide offers no registries the check does not apply;
    if it offers any, failing to find the authorised set is a **failure**, not a skip, because we
    would be publishing values we cannot check.
    """

    @staticmethod
    def _authorised() -> set[str] | None:
        from src.services import service
        for name, value in vars(service).items():
            if (name.isupper() and isinstance(value, (frozenset, set, tuple, list))
                    and ("AUTHORIS" in name or "AUTHORIZ" in name)
                    and all(isinstance(x, str) for x in value)):
                return set(value)
        return None

    @pytest.mark.parametrize("doc", [d for d in _DOCS if d.name.startswith("07")])
    def test_every_registry_offered_to_operators_is_authorised(self, doc):
        offered = set(re.findall(r"`([a-z][a-z0-9_]*):`", doc.read_text()))
        authorised = self._authorised()
        if not offered:
            pytest.skip(f"{doc.name} offers no `registry:` values — check does not apply")
        assert authorised is not None, (
            f"{doc.name} offers {sorted(offered)} but no authorised-registry constant was found — "
            "cannot verify what operators are told to send")
        unknown = sorted(offered - authorised)
        assert not unknown, f"{doc.name} offers registries the agent rejects: {unknown}"
