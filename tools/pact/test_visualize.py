"""
Tests for tools/pact/visualize.py

Verifies Mermaid output structure, color class assignment, reduction
sequence frame count, and PR comment formatting.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from .encoder import Violation
from .extractor import ArgConstraint, CallSite, FunctionManifest
from .refactor import RefactorSuggestion
from .visualize import (
    format_pr_comment,
    render_mermaid,
    render_reduction_sequence,
    _score,
    _style_class,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _func(name: str, file: str = "x.py", line: int = 1) -> FunctionManifest:
    return FunctionManifest(name=name, file=file, line=line, module_path="", args=[])


def _site(caller: str, callee: str, file: str = "x.py", line: int = 10) -> CallSite:
    return CallSite(
        callee_name=callee, file=file, line=line,
        provided_kwargs=set(), positional_count=0,
        caller_name=caller,
    )


def _viol(call: str, file: str = "x.py", line: int = 5, ctx: str = "bare_except") -> Violation:
    return Violation(file=file, line=line, call=call, missing=["msg"], context=ctx)


def _suggestion(name: str, n: int = 3) -> RefactorSuggestion:
    return RefactorSuggestion(
        func_name=name, file="x.py", line=1,
        violation_count=n, caller_count=1,
        modes=["bare_except"], violations=[], z3_safe=True,
    )


# ---------------------------------------------------------------------------
# Unit: _style_class
# ---------------------------------------------------------------------------

class TestStyleClass:
    def test_zero_is_clean(self):
        assert _style_class(0) == "clean"

    def test_one_is_warn(self):
        assert _style_class(1) == "warn"

    def test_three_is_hot(self):
        assert _style_class(3) == "hot"

    def test_four_plus_is_fire(self):
        assert _style_class(4) == "fire"
        assert _style_class(10) == "fire"


# ---------------------------------------------------------------------------
# Unit: render_mermaid
# ---------------------------------------------------------------------------

class TestRenderMermaid:
    def _setup(self):
        funcs = [_func("handler"), _func("helper")]
        sites = [_site("handler", "helper")]
        viols = [_viol("handler")] * 4  # fire
        return viols, funcs, sites

    def test_starts_with_flowchart(self):
        viols, funcs, sites = self._setup()
        out = render_mermaid(viols, funcs, sites)
        assert out.startswith("flowchart TD")

    def test_contains_node_ids(self):
        viols, funcs, sites = self._setup()
        out = render_mermaid(viols, funcs, sites)
        assert "handler" in out
        assert "helper" in out

    def test_contains_edge(self):
        viols, funcs, sites = self._setup()
        out = render_mermaid(viols, funcs, sites)
        assert "-->" in out

    def test_fire_class_applied(self):
        viols, funcs, sites = self._setup()
        out = render_mermaid(viols, funcs, sites)
        assert ":::fire" in out

    def test_clean_node_has_clean_class(self):
        funcs = [_func("handler"), _func("helper")]
        sites = [_site("handler", "helper")]
        viols = [_viol("handler")]  # only handler has violations
        out = render_mermaid(viols, funcs, sites)
        assert ":::clean" in out  # helper is clean

    def test_highlight_uses_stadium_shape(self):
        funcs = [_func("handler"), _func("helper")]
        sites = [_site("handler", "helper")]
        viols = [_viol("handler")]
        out = render_mermaid(viols, funcs, sites, highlight={"handler"})
        assert "([" in out  # stadium shape for candidates

    def test_resolved_uses_resolved_class(self):
        funcs = [_func("handler")]
        sites = []
        viols = [_viol("handler")]
        out = render_mermaid(viols, funcs, sites, resolved={"handler"})
        assert ":::resolved" in out

    def test_title_appears_in_comment(self):
        funcs = [_func("f")]
        out = render_mermaid([], funcs, [], title="my title")
        assert "my title" in out

    def test_style_classes_appended(self):
        out = render_mermaid([], [_func("f")], [])
        assert "classDef clean" in out
        assert "classDef fire" in out

    def test_no_duplicate_edges(self):
        funcs = [_func("a"), _func("b")]
        # Two call sites with same caller→callee
        sites = [_site("a", "b"), _site("a", "b", line=20)]
        out = render_mermaid([], funcs, sites)
        assert out.count("-->") == 1

    def test_empty_graph_still_renders(self):
        out = render_mermaid([], [], [])
        assert "flowchart" in out


# ---------------------------------------------------------------------------
# Unit: render_reduction_sequence
# ---------------------------------------------------------------------------

class TestRenderReductionSequence:
    def test_empty_suggestions_returns_empty(self):
        assert render_reduction_sequence([], [], [], []) == []

    def test_frame_count_is_suggestions_plus_one(self):
        funcs = [_func("a"), _func("b"), _func("c")]
        sites = [_site("a", "b"), _site("b", "c")]
        viols = [_viol("a")] * 3 + [_viol("b")]
        suggestions = [_suggestion("a"), _suggestion("b")]
        frames = render_reduction_sequence(suggestions, viols, funcs, sites)
        assert len(frames) == 3  # baseline + 2

    def test_first_frame_is_baseline(self):
        funcs = [_func("a")]
        viols = [_viol("a")]
        suggestions = [_suggestion("a")]
        frames = render_reduction_sequence(suggestions, viols, funcs, [])
        label, _ = frames[0]
        assert "baseline" in label.lower()

    def test_subsequent_frames_mention_function(self):
        funcs = [_func("a")]
        viols = [_viol("a")]
        suggestions = [_suggestion("a", n=2)]
        frames = render_reduction_sequence(suggestions, viols, funcs, [])
        label, _ = frames[1]
        assert "a" in label

    def test_resolved_nodes_shown_green_in_later_frames(self):
        funcs = [_func("a"), _func("b")]
        sites = [_site("a", "b")]
        viols = [_viol("a")] * 3
        suggestions = [_suggestion("a")]
        frames = render_reduction_sequence(suggestions, viols, funcs, sites)
        _, diagram = frames[1]  # after resolving "a"
        assert ":::resolved" in diagram


# ---------------------------------------------------------------------------
# Integration: format_pr_comment
# ---------------------------------------------------------------------------

class TestFormatPrComment:
    def test_no_violations_returns_checkmark(self):
        result = format_pr_comment([], [], [], [])
        assert "✓" in result

    def test_has_violation_count(self):
        funcs = [_func("handler")]
        viols = [_viol("handler")] * 3
        result = format_pr_comment([], viols, funcs, [])
        assert "3 violation" in result

    def test_has_suggestion_count(self):
        funcs = [_func("handler")]
        viols = [_viol("handler")] * 3
        suggestions = [_suggestion("handler")]
        result = format_pr_comment(suggestions, viols, funcs, [])
        assert "refactor target" in result

    def test_contains_mermaid_block(self):
        funcs = [_func("a"), _func("b")]
        sites = [_site("a", "b")]
        viols = [_viol("a")]
        result = format_pr_comment([], viols, funcs, sites)
        assert "```mermaid" in result

    def test_reduction_frames_are_collapsible(self):
        funcs = [_func("a"), _func("b")]
        sites = [_site("a", "b")]
        viols = [_viol("a")] * 3
        suggestions = [_suggestion("a")]
        result = format_pr_comment(suggestions, viols, funcs, sites)
        assert "<details>" in result
        assert "<summary>" in result
