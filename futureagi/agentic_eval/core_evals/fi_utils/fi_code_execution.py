import ast
import json
import os
import re
import subprocess
import tempfile
from typing import Any

import structlog

from agentic_eval.core_evals.fi_utils.fi_code_base import Step

logger = structlog.get_logger(__name__)


# Entry-point signatures: every code eval must define `evaluate(...)` or `main(...)`.
_ENTRY_NAMES = ("evaluate", "main")
_PY_ENTRY = re.compile(r"\bdef\s+(evaluate|main)\s*\(")
_JS_ENTRY = re.compile(r"\bfunction\s+(evaluate|main)\s*\(")
# Line-anchored fallback — avoids matching arbitrary substrings inside Python
# string literals / sets of keyword tokens (see code_bleu, tool_call_accuracy).
_JS_FALLBACK = re.compile(r"^\s*(function\s|const\s|let\s|var\s)", re.MULTILINE)

# Only code of at most this many characters is parsed: the length the create
# route allows for code (model_hub/types.py). Longer code is detected by the
# entry-point patterns alone, as before.
_MAX_PARSED_CODE_CHARS = 100_000

# Nodes whose bodies are their own scope: a name bound inside one is not
# bound at module level.
_OWN_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _binds_entry_at_module_level(tree: ast.Module) -> bool:
    """Whether module-level code binds evaluate or main: a def, an async def
    or an assignment, including one inside a module-level if, try, with or
    loop (the executor looks the entry function up in the module's names)."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name in _ENTRY_NAMES:
                return True
            continue
        if isinstance(node, _OWN_SCOPES):
            continue
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id in _ENTRY_NAMES
        ):
            return True
        pending.extend(ast.iter_child_nodes(node))
    return False


def _defines_python_entry(code: str) -> bool:
    """Whether ``code`` parses as Python and binds evaluate or main at module
    level.

    Any error while parsing (a syntax error, a null byte, code nested too
    deep or too large to parse, or anything else) means it does not: the
    parse never fails a run, and the next rule decides.
    """
    try:
        return _binds_entry_at_module_level(ast.parse(code))
    except Exception:
        return False


def _detect_by_entry_patterns(code: str) -> str:
    """The entry-point patterns over the raw text, comments and strings
    included. Only code too long to parse is detected by these alone."""
    if _JS_ENTRY.search(code):
        return "javascript"
    if _PY_ENTRY.search(code):
        return "python"
    if _JS_FALLBACK.search(code):
        return "javascript"
    return "python"


def detect_code_language(code: str, stored_language: str | None = None) -> str:
    """The language a code eval runs in: the one its code is written in.

    1. Code that parses as Python and binds evaluate or main at module level
       (a def, an async def or an assignment) is Python.
    2. Otherwise code with a JavaScript sign (``function evaluate(`` or
       ``function main(`` anywhere, or a line that starts with function,
       const, let or var) is JavaScript, and so is code whose eval was saved
       with ``stored_language`` "javascript".
    3. Anything else is Python.

    So the saved language never overrides the code; it decides only code
    that shows neither language. Code longer than _MAX_PARSED_CODE_CHARS is
    not parsed and is detected by the entry-point patterns, as before.
    """
    if len(code) > _MAX_PARSED_CODE_CHARS:
        return _detect_by_entry_patterns(code)
    if _defines_python_entry(code):
        return "python"
    if (
        _JS_ENTRY.search(code)
        or _JS_FALLBACK.search(code)
        or stored_language == "javascript"
    ):
        return "javascript"
    return "python"


class CodeExecution(Step):
    """
    Step that executes user-provided code in a sandboxed environment.

    Python: Uses RestrictedPython with process-level isolation.
    JavaScript: Uses Node.js subprocess with timeout.

    Attributes:
        code: The code to execute.
        language: 'python' or 'javascript', run as given. None detects the
            language from the code (detect_code_language).
        stored_language: The language the eval was saved with. Detection uses
            it only for code that shows neither language; it never overrides
            the code.
    """

    code: str
    language: str | None = None
    stored_language: str | None = None
    name: str | None = None

    def detect_language(self, code: str) -> str:
        """The language ``code`` is written in (detect_code_language), with
        this step's stored language for code that shows neither.

        Python is read by parsing, not by matching words: Python that mentions
        ``function main(`` or ``const`` as data stays Python, and JavaScript
        that mentions ``def evaluate(`` in a comment stays JavaScript.
        """
        return detect_code_language(code, self.stored_language)

    def execute_python(self, input_data: dict[str, Any]) -> dict[str, Any] | None:
        """Execute Python code in a production-grade sandbox.

        Uses multi-layer isolation:
        1. RestrictedPython v8 (AST-level)
        2. Subprocess isolation
        3. rlimits (memory, CPU, no files, no fork)
        4. Minimal environment
        """
        from agentic_eval.core_evals.fi_utils.sandbox import (
            execute_sandboxed_python,
        )

        try:
            return execute_sandboxed_python(
                code=self.code,
                input_data=input_data,
                timeout=30,
            )
        except Exception as e:
            logger.error("Sandboxed Python execution failed", error=str(e))
            return {
                "status": "error",
                "data": f"Failed to execute Python code: {e}",
            }

    def execute_javascript(self, input_data: dict[str, Any]) -> dict[str, Any] | None:
        """Execute JavaScript code in a production-grade sandbox.

        Uses multi-layer isolation:
        1. Module blocking (40+ dangerous modules)
        2. Process freezing (exit, kill, binding, env)
        3. rlimits (memory, CPU, no fork)
        4. 64MB heap, 1MB stack
        5. Timeout enforcement
        """
        from agentic_eval.core_evals.fi_utils.sandbox import (
            execute_sandboxed_javascript,
        )

        try:
            return execute_sandboxed_javascript(
                code=self.code,
                input_data=input_data,
                timeout=30,
            )
        except Exception as e:
            logger.error("Sandboxed JS execution failed", error=str(e))
            return {
                "status": "error",
                "data": f"Failed to execute JavaScript code: {e}",
            }

    def execute(self, input_data: Any) -> dict[str, Any] | None:
        """Execute the code with the input data."""
        if input_data is None:
            input_data = {}

        if not isinstance(input_data, dict):
            raise TypeError("Input data must be a dictionary.")

        # Only sniff when the caller didn't tell us — explicit "python"
        # / "javascript" must be honoured even if the body looks ambiguous.
        if not self.language:
            self.language = self.detect_language(self.code)

        if self.language == "javascript":
            return self.execute_javascript(input_data)
        else:
            return self.execute_python(input_data)
