"""Tests for ``nest_dotted_value`` (TH-5443 regression coverage).

The bug: ``required_keys`` like ``spans.0.gen_ai.span.kind`` were nested into
a dict with the string key ``"0"``, but Jinja parses ``.0`` as list index 0
and raised ``UndefinedError: dict object has no element 0``. The fix builds
a list at the parent level whenever the next path component is numeric.

These tests are pure (no Django, no IO) so they run fast and don't break
when the surrounding eval pipeline changes.
"""

import pytest
from jinja2 import Environment

from agentic_eval.core.utils.jinja_utils import nest_dotted_value


# ── Backward compatibility (the TH-4715 word-only path) ─────────────────


def test_pure_dict_path_two_levels():
    out = {}
    nest_dotted_value(out, ["json_col", "field"], "X")
    assert out == {"json_col": {"field": "X"}}


def test_pure_dict_path_deep():
    out = {}
    nest_dotted_value(out, ["a", "b", "c", "d"], 42)
    assert out == {"a": {"b": {"c": {"d": 42}}}}


def test_pure_dict_preserves_existing_siblings():
    out = {"json_col": {"existing": 1}}
    nest_dotted_value(out, ["json_col", "field"], "X")
    assert out == {"json_col": {"existing": 1, "field": "X"}}


# ── The bug — numeric components become list indices ────────────────────


def test_single_numeric_creates_list():
    """The exact failure case from the bug report."""
    out = {}
    nest_dotted_value(out, ["spans", "0", "gen_ai", "span", "kind"], "AGENT")
    assert out == {"spans": [{"gen_ai": {"span": {"kind": "AGENT"}}}]}


def test_numeric_at_leaf_creates_list():
    out = {}
    nest_dotted_value(out, ["items", "0"], "first")
    nest_dotted_value(out, ["items", "1"], "second")
    assert out == {"items": ["first", "second"]}


def test_out_of_order_indices_auto_grow():
    out = {}
    nest_dotted_value(out, ["spans", "2", "k"], "C")
    nest_dotted_value(out, ["spans", "0", "k"], "A")
    nest_dotted_value(out, ["spans", "1", "k"], "B")
    assert out == {"spans": [{"k": "A"}, {"k": "B"}, {"k": "C"}]}


def test_sparse_index_pads_with_none():
    out = {}
    nest_dotted_value(out, ["items", "3"], "fourth")
    assert out == {"items": [None, None, None, "fourth"]}


# ── Mixed dict + list nesting ───────────────────────────────────────────


def test_dict_containing_list():
    out = {}
    nest_dotted_value(out, ["a", "b", "0", "c"], "X")
    assert out == {"a": {"b": [{"c": "X"}]}}


def test_list_containing_list():
    out = {}
    nest_dotted_value(out, ["matrix", "0", "1"], "X")
    assert out == {"matrix": [[None, "X"]]}


def test_multiple_keys_populate_same_list():
    """Two required_keys with shared parent paths share the same list."""
    out = {}
    nest_dotted_value(out, ["spans", "0", "name"], "first_span")
    nest_dotted_value(out, ["spans", "0", "kind"], "AGENT")
    nest_dotted_value(out, ["spans", "1", "name"], "second_span")
    assert out == {
        "spans": [
            {"name": "first_span", "kind": "AGENT"},
            {"name": "second_span"},
        ]
    }


# ── Wrong-typed existing children get replaced ──────────────────────────


def test_wrong_typed_child_dict_replaced_with_list():
    """Defensive: if an earlier call left a dict where a list now belongs, we replace it."""
    out = {"spans": {}}
    nest_dotted_value(out, ["spans", "0", "k"], "X")
    assert out == {"spans": [{"k": "X"}]}


def test_wrong_typed_child_list_replaced_with_dict():
    out = {"obj": []}
    nest_dotted_value(out, ["obj", "field"], "X")
    assert out == {"obj": {"field": "X"}}


# ── End-to-end: the renderer no longer raises ───────────────────────────


def _render(template_str: str, **context) -> str:
    """Render with Jinja the same way CustomPromptEvaluator/AgentEvaluator do."""
    return Environment().from_string(template_str).render(**context)


def test_jinja_renders_numeric_indexed_path():
    """The actual user-visible bug fix: this used to raise UndefinedError."""
    out = {}
    nest_dotted_value(out, ["spans", "0", "gen_ai", "span", "kind"], "AGENT")
    rendered = _render("kind: {{ spans.0.gen_ai.span.kind }}", **out)
    assert rendered == "kind: AGENT"


def test_jinja_renders_word_only_path():
    """Regression: TH-4715's original use case still works."""
    out = {}
    nest_dotted_value(out, ["json_col", "field"], "value")
    rendered = _render("{{ json_col.field }}", **out)
    assert rendered == "value"


def test_jinja_for_loop_over_populated_list():
    """Multi-element list populated via the helper is iterable in Jinja."""
    out = {}
    nest_dotted_value(out, ["spans", "0", "kind"], "A")
    nest_dotted_value(out, ["spans", "1", "kind"], "B")
    nest_dotted_value(out, ["spans", "2", "kind"], "C")
    rendered = _render(
        "{% for s in spans %}[{{ s.kind }}]{% endfor %}", **out
    )
    assert rendered == "[A][B][C]"


def test_jinja_bracket_access_also_works():
    """spans[0] syntax (in addition to spans.0) resolves correctly."""
    out = {}
    nest_dotted_value(out, ["spans", "0", "kind"], "X")
    rendered = _render("{{ spans[0].kind }}", **out)
    assert rendered == "X"
