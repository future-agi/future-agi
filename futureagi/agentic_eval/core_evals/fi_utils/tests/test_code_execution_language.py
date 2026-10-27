"""
Regression tests for CodeExecution language detection.

A prior implementation substring-matched JS-flavoured keywords
(``"function"``, ``"const"``, ``"let"``, ``"var "``) anywhere in the
lowercased source. Python evals that happened to mention those tokens as
data (e.g. ``code_bleu`` ships a keyword set containing the strings
``"function"`` / ``"const"`` / ``"let"`` / ``"var"``) were routed to the
Node.js executor and failed with ``SyntaxError: Unexpected identifier``.

These tests pin the entry-point-shaped detection contract:
  - ``def evaluate(...)``  → python
  - ``function evaluate(...)`` → javascript
  - explicit ``language=`` always wins over auto-detect
"""

import pytest

from agentic_eval.core_evals.fi_utils.fi_code_execution import CodeExecution


class TestDetectLanguageEntryPoint:
    def test_python_def_evaluate(self):
        code = "def evaluate(output, expected, **kwargs):\n    return output == expected\n"
        assert CodeExecution(code=code).detect_language(code) == "python"

    def test_js_function_evaluate(self):
        code = "function evaluate(output, expected) { return output === expected; }\n"
        assert CodeExecution(code=code).detect_language(code) == "javascript"

    def test_python_with_function_keyword_in_data(self):
        # Mirrors code_bleu.yaml: the word "function" appears inside a Python
        # keyword-set literal. Must NOT be routed to JS.
        code = """
def evaluate(input, output, expected, context, **kwargs):
    kws = {"def", "class", "function", "const", "let", "var"}
    return {"score": 1.0, "reason": str(kws)}
"""
        assert CodeExecution(code=code).detect_language(code) == "python"

    def test_python_with_arrow_substring_in_string(self):
        # Arrow ``=>`` inside a Python string literal should not flip routing.
        code = """
def evaluate(**kwargs):
    arrow = "a => b"
    return {"score": 1.0, "reason": arrow}
"""
        assert CodeExecution(code=code).detect_language(code) == "python"


class TestExplicitLanguageWins:
    def test_explicit_python_not_overridden(self):
        # Even when the body looks JS-shaped, explicit language="python" must hold.
        code = "function evaluate() { return true; }"
        step = CodeExecution(code=code, language="python")
        # Don't call execute (no sandbox here) — just assert no auto-rewrite.
        assert step.language == "python"

    def test_explicit_javascript_not_overridden(self):
        code = "def evaluate(**kwargs):\n    return True\n"
        step = CodeExecution(code=code, language="javascript")
        assert step.language == "javascript"

    def test_no_language_defaults_to_none(self):
        # Default must be None — a literal "python" default would be
        # indistinguishable from "explicitly python" and re-trigger the old
        # always-detect bug.
        step = CodeExecution(code="def evaluate(): pass")
        assert step.language is None
