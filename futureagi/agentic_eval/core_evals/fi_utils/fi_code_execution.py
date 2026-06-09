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
_PY_ENTRY = re.compile(r"\bdef\s+(evaluate|main)\s*\(")
_JS_ENTRY = re.compile(r"\bfunction\s+(evaluate|main)\s*\(")
# Line-anchored fallback — avoids matching arbitrary substrings inside Python
# string literals / sets of keyword tokens (see code_bleu, tool_call_accuracy).
_JS_FALLBACK = re.compile(r"^\s*(function\s|const\s|let\s|var\s)", re.MULTILINE)


class CodeExecution(Step):
    """
    Step that executes user-provided code in a sandboxed environment.

    Python: Uses RestrictedPython with process-level isolation.
    JavaScript: Uses Node.js subprocess with timeout.

    Attributes:
        code: The code to execute.
        language: 'python' or 'javascript'. None triggers auto-detect.
    """

    code: str
    language: str | None = None
    name: str | None = None

    def detect_language(self, code: str) -> str:
        """Detect language by entry-point shape (`def evaluate` vs `function evaluate`).

        Substring matching on ``"function"`` / ``"const"`` / ``"let"`` would
        mis-route Python evals that mention those words as data (e.g. inside
        a keyword set) to the JS executor.
        """
        if _JS_ENTRY.search(code):
            return "javascript"
        if _PY_ENTRY.search(code):
            return "python"
        if _JS_FALLBACK.search(code):
            return "javascript"
        return "python"

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
