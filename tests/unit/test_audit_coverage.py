"""S-4 coverage is machine-checked: every execute() path leaves a domain trace event.

Review (Medium, sibling template): a node returned ``{}`` on the upstream-degraded path without
emitting, so a degraded run had steps with no audit record at all — while the envelope still reported
``audit_logged: True``. Scanning by hand found one; scanning by machine found four, including the
publication path that upstream rejections actually land on.

The static half stops the gap from reappearing anywhere; the behavioural half proves the static check
is describing the real runtime. Neither is written against a hand-maintained list of event names —
the expectation is derived from the source, so adding a node cannot quietly fall outside it.
"""

import ast
import importlib
import pathlib
import pkgutil

import pytest

from src.graph.graph import Graph

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
_TRANSITIVE = {"AdminDocumentRequestBoundaryRoutingWorkflowGraphNode"}


def _emit_lines(fn):
    return {n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "emit_trace_event"}


def _own_returns(fn):
    """Returns belonging to fn itself — not to a helper defined inside it."""
    nested = {id(n) for d in ast.walk(fn)
              if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d is not fn
              for n in ast.walk(d)}
    return [n for n in ast.walk(fn) if isinstance(n, ast.Return) and id(n) not in nested]


def _uncovered() -> list[str]:
    gaps = []
    for path in sorted(_SRC.rglob("*.py")):
        for cls in [n for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.ClassDef)]:
            methods = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}
            emitting = {n for n, m in methods.items() if _emit_lines(m)}
            fn = methods.get("execute")
            if not fn or cls.name in _TRANSITIVE:
                continue
            emits = _emit_lines(fn)
            for ret in _own_returns(fn):
                if any(e < ret.lineno for e in emits):
                    continue
                # covered when it returns a same-class helper that emits one frame down
                call = ret.value
                if isinstance(call, ast.Call):
                    name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
                    if name in emitting:
                        continue
                gaps.append(f"{path.name}:{ret.lineno} {cls.name}.execute")
    return gaps


def _events_by_module() -> dict[str, set[str]]:
    """Event names each node module can emit — read from its own ``emit_trace_event`` literals.

    Deriving this beats matching on the module name: a step's events are not obliged to be named
    after its file (``post_process_node.py`` emits ``pack.withheld`` in one sibling template), and a
    filename-based expectation quietly stopped covering that node.
    """
    out: dict[str, set[str]] = {}
    for path in sorted((_SRC / "nodes").glob("*_node.py")):
        names = set()
        for call in [n for n in ast.walk(ast.parse(path.read_text()))
                     if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "emit_trace_event"]:
            if call.args and isinstance(call.args[0], ast.Constant):
                names.add(call.args[0].value)
            elif call.args and isinstance(call.args[0], ast.Name):
                names.add("<dynamic>")      # a module-level constant — accept anything from it
        if names:
            out[path.stem] = names
    return out


class TestStaticCoverage:
    def test_every_execute_path_emits(self):
        gaps = _uncovered()
        assert not gaps, ("execute() paths returning with no S-4 event:\n  " + "\n  ".join(gaps))

    def test_the_scan_is_actually_looking_at_something(self):
        """A scan that silently matched nothing would pass the check above."""
        seen = [c.name for p in _SRC.rglob("*.py")
                for c in ast.walk(ast.parse(p.read_text())) if isinstance(c, ast.ClassDef)
                and any(isinstance(m, ast.FunctionDef) and m.name == "execute" for m in c.body)]
        assert len(seen) >= 5, f"only {len(seen)} execute() classes found — scan is not covering"


@pytest.fixture
def captured(monkeypatch):
    """Capture what each node module actually emits (the name is bound at import time)."""
    events: list[str] = []

    def _spy(event_type, payload, state=None):
        events.append(event_type)

    mods = [importlib.import_module(f"src.nodes.{m.name}")
            for m in pkgutil.iter_modules([str(_SRC / "nodes")])]
    mods.append(importlib.import_module("src.graph.graph"))
    for mod in mods:
        if hasattr(mod, "emit_trace_event"):
            monkeypatch.setattr(mod, "emit_trace_event", _spy)
    return events


class TestSkipPathBehaviour:
    def test_every_step_accounts_for_itself_on_a_degraded_run(self, captured):
        """Upstream rejection: no step may return silently, and publication must be audited.

        Trigger note (2026-08-18): this used a high-confidence injection marker, which AgentCore
        1.0.1 now refuses at ``InitializeNode`` — before any template node runs — so no S-4 event
        can exist on that path. The injection was only the *trigger* for a
        template-level upstream rejection, so the trigger is swapped to the oversize rejection the
        template itself performs; the property under test (degraded runs skip loudly and publish
        auditably) is unchanged. The framework refusal itself is pinned in the integration suite.
        """
        Graph().invoke("あ" * 400_001, ctx=_ctx(), session_id="s")
        assert captured, "degraded run produced no S-4 events at all"
        expected = _events_by_module()
        assert len(expected) >= 3, f"expectation covers only {list(expected)} — not meaningful"
        silent = {mod for mod, names in expected.items()
                  if "<dynamic>" not in names and not (names & set(captured))}
        assert not silent, f"steps that returned silently: {sorted(silent)} (emitted: {captured})"
        assert any(e.endswith(".skipped") for e in captured), (
            f"no step reported skipping on a degraded run: {captured}")


def _ctx():
    from framework.schemas.invocation_context import InvocationContext
    from framework.schemas.trust_level import TrustLevel
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, caller_id="t")
