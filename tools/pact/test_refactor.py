"""
Tests for tools/pact/refactor.py

Verifies that suggest_refactors() correctly ranks functions by violation
density / coupling, attributes violations to their enclosing functions,
and that Z3 safety verification correctly identifies safe vs unsafe extractions.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from .checker import check_codebase
from .extractor import (
    ArgConstraint, CallSite, FunctionManifest, extract_from_codebase,
)
from .encoder import Violation
from .refactor import RefactorSuggestion, _verify_extraction_safe, suggest_refactors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_src(tmp_path: Path, name: str, src: str) -> Path:
    f = tmp_path / name
    f.write_text(textwrap.dedent(src))
    return f


def _func(name: str, file: str, line: int = 1, args=None) -> FunctionManifest:
    return FunctionManifest(
        name=name, file=file, line=line,
        module_path="", args=args or [],
    )


def _site(callee: str, file: str, line: int, caller: str = None,
          positional: int = 0, kwargs: set = None) -> CallSite:
    return CallSite(
        callee_name=callee, file=file, line=line,
        provided_kwargs=kwargs or set(),
        positional_count=positional,
        caller_name=caller,
    )


def _viol(file: str, line: int, context: str = "bare_except") -> Violation:
    return Violation(file=file, line=line, call="x", missing=["msg"], context=context)


# ---------------------------------------------------------------------------
# Unit: RefactorSuggestion.score
# ---------------------------------------------------------------------------

class TestRefactorSuggestionScore:
    def test_score_favors_many_violations_few_callers(self):
        s = RefactorSuggestion(
            func_name="f", file="a.py", line=1,
            violation_count=6, caller_count=2,
            modes=[], violations=[], z3_safe=True,
        )
        assert s.score == 3.0

    def test_score_zero_callers_uses_1(self):
        s = RefactorSuggestion(
            func_name="f", file="a.py", line=1,
            violation_count=4, caller_count=0,
            modes=[], violations=[], z3_safe=None,
        )
        assert s.score == 4.0  # 4 / max(1, 0) = 4

    def test_summary_includes_z3_safe(self):
        s = RefactorSuggestion(
            func_name="process", file="svc.py", line=10,
            violation_count=3, caller_count=1,
            modes=["bare_except"], violations=[], z3_safe=True,
        )
        text = s.summary()
        assert "Z3-safe" in text
        assert "process" in text
        assert "bare_except" in text

    def test_summary_includes_z3_unsafe(self):
        s = RefactorSuggestion(
            func_name="risky", file="x.py", line=5,
            violation_count=2, caller_count=1,
            modes=["optional_dereference"], violations=[], z3_safe=False,
            z3_detail="1 caller(s) missing required args",
        )
        text = s.summary()
        assert "Z3-unsafe" in text
        assert "missing required args" in text


# ---------------------------------------------------------------------------
# Unit: _verify_extraction_safe
# ---------------------------------------------------------------------------

class TestVerifyExtractionSafe:
    def _required_arg(self, name: str) -> ArgConstraint:
        return ArgConstraint(name=name, required=True, has_default=False)

    def _optional_arg(self, name: str) -> ArgConstraint:
        return ArgConstraint(name=name, required=False, has_default=True)

    def _kwonly_arg(self, name: str) -> ArgConstraint:
        return ArgConstraint(name=name, required=True, has_default=False, kwonly=True)

    def test_no_args_is_safe(self):
        func = _func("f", "a.py", args=[])
        safe, detail = _verify_extraction_safe(func, [])
        assert safe is True

    def test_no_callers_is_safe(self):
        func = _func("f", "a.py", args=[self._required_arg("x")])
        safe, detail = _verify_extraction_safe(func, [])
        assert safe is True

    def test_caller_provides_enough_positional(self):
        func = _func("f", "a.py", args=[self._required_arg("x"), self._required_arg("y")])
        caller = _site("f", "b.py", 10, positional=2)
        safe, _ = _verify_extraction_safe(func, [caller])
        assert safe is True

    def test_caller_missing_positional_is_unsafe(self):
        func = _func("f", "a.py", args=[self._required_arg("x"), self._required_arg("y")])
        caller = _site("f", "b.py", 10, positional=1)  # needs 2, provides 1
        safe, detail = _verify_extraction_safe(func, [caller])
        assert safe is False
        assert "caller" in detail

    def test_caller_provides_required_kwonly(self):
        func = _func("f", "a.py", args=[self._kwonly_arg("mode")])
        caller = _site("f", "b.py", 10, kwargs={"mode"})
        safe, _ = _verify_extraction_safe(func, [caller])
        assert safe is True

    def test_caller_missing_kwonly_is_unsafe(self):
        func = _func("f", "a.py", args=[self._kwonly_arg("mode")])
        caller = _site("f", "b.py", 10, kwargs=set())
        safe, detail = _verify_extraction_safe(func, [caller])
        assert safe is False

    def test_only_optional_args_is_safe(self):
        func = _func("f", "a.py", args=[self._optional_arg("x")])
        caller = _site("f", "b.py", 10, positional=0)
        safe, _ = _verify_extraction_safe(func, [caller])
        assert safe is True

    def test_mixed_required_optional_caller_satisfies(self):
        func = _func("f", "a.py", args=[
            self._required_arg("x"), self._optional_arg("y"),
        ])
        caller = _site("f", "b.py", 10, positional=1)
        safe, _ = _verify_extraction_safe(func, [caller])
        assert safe is True


# ---------------------------------------------------------------------------
# Integration: suggest_refactors with real violations
# ---------------------------------------------------------------------------

class TestSuggestRefactors:
    def test_function_with_violations_is_suggested(self, tmp_path):
        _write_src(tmp_path, "service.py", """
            def process_response(resp):
                try:
                    return resp.data
                except:
                    return None

            def clean():
                pass
        """)
        models, functions, call_sites = extract_from_codebase(tmp_path)
        violations = check_codebase(tmp_path, _extracted=(models, functions, call_sites))
        suggestions = suggest_refactors(violations, functions, call_sites)
        func_names = [s.func_name for s in suggestions]
        assert any("process_response" in n for n in func_names)

    def test_min_violations_filters_low_density(self, tmp_path):
        _write_src(tmp_path, "svc.py", """
            def noisy():
                try:
                    pass
                except:
                    pass

            def clean():
                pass
        """)
        models, functions, call_sites = extract_from_codebase(tmp_path)
        violations = check_codebase(tmp_path, _extracted=(models, functions, call_sites))
        suggestions = suggest_refactors(
            violations, functions, call_sites, min_violations=99
        )
        assert suggestions == []

    def test_higher_score_ranked_first(self):
        file = "x.py"
        functions = [
            _func("high_density", file, line=1),
            _func("low_density", file, line=20),
        ]
        violations = (
            [_viol(file, 2)] * 5  # 5 violations near high_density
            + [_viol(file, 21)]   # 1 violation near low_density
        )
        call_sites = [
            _site("high_density", file, 2, caller="caller_a"),
            _site("low_density", file, 21, caller="caller_b"),
            _site("low_density", file, 21, caller="caller_c"),
        ]
        suggestions = suggest_refactors(violations, functions, call_sites, verify=False)
        assert len(suggestions) >= 1
        # high_density: 5 violations, 1 caller → score 5.0
        # low_density: 1 violation, 2 callers → score 0.5
        assert suggestions[0].func_name == "high_density"

    def test_max_suggestions_cap(self):
        file = "x.py"
        functions = [_func(f"f{i}", file, line=i * 10 + 1) for i in range(20)]
        violations = [_viol(file, i * 10 + 2) for i in range(20)]
        call_sites = []
        suggestions = suggest_refactors(
            violations, functions, call_sites,
            max_suggestions=5, verify=False,
        )
        assert len(suggestions) <= 5

    def test_modes_list_populated(self, tmp_path):
        _write_src(tmp_path, "mod.py", """
            def handler():
                try:
                    pass
                except:
                    pass
        """)
        models, functions, call_sites = extract_from_codebase(tmp_path)
        violations = check_codebase(tmp_path, _extracted=(models, functions, call_sites))
        suggestions = suggest_refactors(violations, functions, call_sites)
        if suggestions:
            assert all(len(s.modes) > 0 for s in suggestions)

    def test_no_violations_returns_empty(self, tmp_path):
        _write_src(tmp_path, "clean.py", """
            def add(x, y):
                return x + y
        """)
        models, functions, call_sites = extract_from_codebase(tmp_path)
        violations = check_codebase(tmp_path, _extracted=(models, functions, call_sites))
        suggestions = suggest_refactors(violations, functions, call_sites)
        assert suggestions == []
