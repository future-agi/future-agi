"""A code eval runs in the language its code is written in.

Code that parses as Python and binds evaluate or main at module level (a def,
an async def or an assignment) runs as Python. Otherwise code with a
JavaScript sign (``function evaluate(`` or ``function main(`` anywhere, or a
line that starts with function, const, let or var) runs as JavaScript, and so
does code saved with JavaScript chosen. Anything else runs as Python. The
saved language never overrides the code; it only decides code that shows
neither language.

Before, the language came from patterns over the raw text, comments and
strings included, so a JavaScript eval with ``def evaluate(`` in a comment ran
as Python, and a Python eval with ``function main(`` in a string ran as
JavaScript. Both failed.

Only code of at most 100,000 characters (the length the create route allows
for code) is parsed. Longer code is detected by those patterns as before. The
parse can never fail a run: any error while parsing counts as "not Python".
"""

import ast
import re
from pathlib import Path

import pytest
import yaml

from agentic_eval.core_evals.fi_utils import sandbox
from agentic_eval.core_evals.fi_utils.fi_code_execution import CodeExecution

# The longest code that is parsed.
PARSE_LIMIT = 100_000

SAVED = [None, "python", "javascript"]


def detect(code, saved=None):
    """The language ``code`` runs in when the eval was saved with ``saved``."""
    return CodeExecution(code=code, stored_language=saved).detect_language(code)


def each_saved(code):
    return {saved: detect(code, saved) for saved in SAVED}


# The patterns every code eval was detected by before, in order.
_JS_ENTRY_BEFORE = re.compile(r"\bfunction\s+(evaluate|main)\s*\(")
_PY_ENTRY_BEFORE = re.compile(r"\bdef\s+(evaluate|main)\s*\(")
_JS_LINE_BEFORE = re.compile(r"^\s*(function\s|const\s|let\s|var\s)", re.MULTILINE)


def detected_before(code):
    """The language ``code`` ran in before (the saved language was never read)."""
    if _JS_ENTRY_BEFORE.search(code):
        return "javascript"
    if _PY_ENTRY_BEFORE.search(code):
        return "python"
    if _JS_LINE_BEFORE.search(code):
        return "javascript"
    return "python"


# ─────────────────────────────────────────────────────────────────────
# Every system code eval keeps its runtime
# ─────────────────────────────────────────────────────────────────────

SYSTEM_EVALS = Path(__file__).resolve().parents[4] / "model_hub" / "system_evals"


def _system_code_evals():
    """Every seed under model_hub/system_evals that is a code eval: the
    *.yaml seeds and the earlier *.yaml.txt copies kept under tests/."""
    seeds = []
    for path in sorted(SYSTEM_EVALS.rglob("*")):
        if not path.name.endswith((".yaml", ".yaml.txt")):
            continue
        data = yaml.safe_load(path.read_text()) or {}
        config = data.get("config") or {}
        code = config.get("code") or data.get("code")
        if config.get("eval_type_id") != "CustomCodeEval" and "code" not in data:
            continue
        if isinstance(code, str) and code.strip():
            seeds.append(pytest.param(code, id=str(path.relative_to(SYSTEM_EVALS))))
    return seeds


SYSTEM_CODE_EVALS = _system_code_evals()


def test_every_system_code_eval_is_read():
    assert len(SYSTEM_CODE_EVALS) >= 80
    ids = {seed.id for seed in SYSTEM_CODE_EVALS}
    assert "function/syntax_validation.yaml" in ids


@pytest.mark.parametrize("code", SYSTEM_CODE_EVALS)
def test_every_system_code_eval_keeps_its_runtime(code):
    # Whatever language a copy of it was saved with.
    assert each_saved(code) == dict.fromkeys(SAVED, detected_before(code))


# ─────────────────────────────────────────────────────────────────────
# Code that failed before runs in its own language
# ─────────────────────────────────────────────────────────────────────

JS_WITH_A_PYTHON_COMMENT = (
    "// Ported from our Python check: def evaluate(output, expected)\n"
    "const evaluate = ({ output, expected }) => ({\n"
    "  score: 1,\n"
    '  reason: "ran as JavaScript",\n'
    "});\n"
)
JS_WITH_A_PYTHON_STRING = (
    "const evaluate = ({ output }) =>\n"
    '  output.includes("def main(") ? 1 : 0;\n'
)
PY_WITH_A_JS_STRING = (
    "def evaluate(output, **kwargs):\n"
    '    return "function main(" in output\n'
)
PY_WITH_A_JS_DOCSTRING = (
    "def evaluate(output, **kwargs):\n"
    '    """Ported from the function evaluate(output) we ran in Node."""\n'
    "    return bool(output)\n"
)
PY_ASSIGNED_WITH_A_JS_LINE = (
    '"""Scores a reply.\n'
    "\n"
    "let the reply be empty and it fails.\n"
    '"""\n'
    "evaluate = lambda output, **kwargs: bool(output)\n"
)


@pytest.mark.parametrize(
    "code, language",
    [
        pytest.param(
            JS_WITH_A_PYTHON_COMMENT,
            "javascript",
            id="javascript-with-def-evaluate-in-a-comment",
        ),
        pytest.param(
            JS_WITH_A_PYTHON_STRING,
            "javascript",
            id="javascript-with-def-main-in-a-string",
        ),
        pytest.param(
            PY_WITH_A_JS_STRING, "python", id="python-with-function-main-in-a-string"
        ),
        pytest.param(
            PY_WITH_A_JS_DOCSTRING,
            "python",
            id="python-with-function-evaluate-in-a-docstring",
        ),
        pytest.param(
            PY_ASSIGNED_WITH_A_JS_LINE,
            "python",
            id="python-assigned-evaluate-with-a-let-line",
        ),
    ],
)
def test_code_that_ran_in_the_other_language_runs_in_its_own(code, language):
    assert detected_before(code) != language
    assert each_saved(code) == dict.fromkeys(SAVED, language)


@pytest.mark.parametrize(
    "code",
    [
        pytest.param(
            'def evaluate(output, **kwargs):\n    return "function main(" in output\n',
            id="def",
        ),
        pytest.param(
            "async def evaluate(output, **kwargs):\n"
            '    return "function main(" in output\n',
            id="async-def",
        ),
        pytest.param(
            'def main(output, **kwargs):\n    return "function evaluate(" in output\n',
            id="def-main",
        ),
        pytest.param(
            'evaluate = lambda output, **kwargs: "function main(" in output\n',
            id="assignment",
        ),
        pytest.param(
            "from typing import Callable\n"
            "\n"
            "evaluate: Callable = (\n"
            '    lambda output, **kwargs: "function main(" in output\n'
            ")\n",
            id="annotated-assignment",
        ),
        pytest.param(
            "if True:\n"
            "    def evaluate(output, **kwargs):\n"
            '        return "function main(" in output\n',
            id="def-inside-a-module-level-if",
        ),
        pytest.param(
            "try:\n"
            "    def evaluate(output, **kwargs):\n"
            '        return "function main(" in output\n'
            "except Exception:\n"
            "    raise\n",
            id="def-inside-a-module-level-try",
        ),
    ],
)
def test_python_that_binds_its_entry_at_module_level_runs_as_python(code):
    # Each mentions a JavaScript entry in a string, which ran it as JavaScript.
    assert detected_before(code) == "javascript"
    assert each_saved(code) == dict.fromkeys(SAVED, "python")


# ─────────────────────────────────────────────────────────────────────
# The saved language decides only code that shows neither language
# ─────────────────────────────────────────────────────────────────────

PY_PLAIN = "def evaluate(output, expected, **kwargs):\n    return output == expected\n"
JS_DECLARED = (
    "function evaluate({ output, expected }) {\n  return output === expected;\n}\n"
)
JS_ARROW = "const evaluate = ({ output, expected }) => output === expected;\n"
# JavaScript with no sign the code shows: its only declaration follows a comment
# on the same line.
JS_AFTER_A_COMMENT = (
    "/* exact match */ const evaluate = ({ output, expected }) =>\n"
    "  output === expected;\n"
)


@pytest.mark.parametrize(
    "code, language",
    [
        pytest.param(PY_PLAIN, "python", id="python"),
        pytest.param(JS_DECLARED, "javascript", id="javascript-function"),
        pytest.param(JS_ARROW, "javascript", id="javascript-arrow"),
    ],
)
def test_the_saved_language_never_overrides_the_code(code, language):
    # A Python eval saved with JavaScript chosen stays Python, and the reverse.
    assert each_saved(code) == dict.fromkeys(SAVED, language)


@pytest.mark.parametrize(
    "saved, language",
    [
        pytest.param("javascript", "javascript", id="saved-javascript"),
        pytest.param("python", "python", id="saved-python"),
        pytest.param(None, "python", id="nothing-saved"),
    ],
)
def test_the_saved_language_decides_code_that_shows_neither(saved, language):
    assert detected_before(JS_AFTER_A_COMMENT) == "python"
    assert detect(JS_AFTER_A_COMMENT, saved) == language


# ─────────────────────────────────────────────────────────────────────
# The parse can never fail a run
# ─────────────────────────────────────────────────────────────────────

# Python with an entry and a line that looks like JavaScript: Python when it
# parses, JavaScript when it does not.
PY_WITH_A_VAR_LINE = (
    "def evaluate(output, **kwargs):\n    return bool(output)\n\n\nvar = 1\n"
)


@pytest.fixture
def ran(monkeypatch):
    """The runtime each run is sent to (the sandbox is replaced)."""
    sent = []

    def runtime(language):
        def run(code, input_data, timeout):
            sent.append(language)
            return {"status": "success", "data": True}

        return run

    monkeypatch.setattr(sandbox, "execute_sandboxed_python", runtime("python"))
    monkeypatch.setattr(
        sandbox, "execute_sandboxed_javascript", runtime("javascript")
    )
    return sent


def test_code_that_parses_is_read_as_python(ran):
    assert detected_before(PY_WITH_A_VAR_LINE) == "python"

    result = CodeExecution(code=PY_WITH_A_VAR_LINE).execute({"output": "Paris"})

    assert (ran, result["status"]) == (["python"], "success")


@pytest.mark.parametrize(
    "error",
    [
        SyntaxError("invalid syntax"),
        RecursionError("maximum recursion depth exceeded"),
        MemoryError(),
        ValueError("source code string cannot contain null bytes"),
        RuntimeError("anything else"),
    ],
    ids=["syntax-error", "recursion-error", "memory-error", "value-error", "other"],
)
def test_a_parse_error_counts_as_not_python(error, ran, monkeypatch):
    real_parse = ast.parse

    # Only the eval's code fails to parse; any other source parses as usual.
    def cannot_parse(source, *args, **kwargs):
        if source == PY_WITH_A_VAR_LINE:
            raise error
        return real_parse(source, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ast, "parse", cannot_parse)
        # The run goes on: the next rule finds the JavaScript-looking line.
        result = CodeExecution(code=PY_WITH_A_VAR_LINE).execute({"output": "Paris"})

    assert (ran, result["status"]) == (["javascript"], "success")


def test_code_with_a_null_byte_counts_as_not_python(ran):
    # The parser refuses a null byte (ValueError on Python 3.11, SyntaxError
    # on later versions).
    code = PY_WITH_A_VAR_LINE.replace("bool(output)", "bool(output)  # \x00")

    result = CodeExecution(code=code).execute({"output": "Paris"})

    assert (ran, result["status"]) == (["javascript"], "success")


def test_code_that_does_not_parse_counts_as_not_python(ran):
    code = PY_WITH_A_VAR_LINE.replace("bool(output)", "bool(output")

    result = CodeExecution(code=code).execute({"output": "Paris"})

    assert (ran, result["status"]) == (["javascript"], "success")


def _padded(code, length):
    filler = "// " + "-" * (length - len(code) - 4) + "\n"
    padded = code + filler
    assert len(padded) == length
    return padded


def test_code_up_to_the_limit_is_parsed():
    code = _padded(JS_WITH_A_PYTHON_COMMENT, PARSE_LIMIT)

    assert detected_before(code) == "python"
    assert each_saved(code) == dict.fromkeys(SAVED, "javascript")


@pytest.mark.parametrize(
    "code",
    [
        pytest.param(JS_WITH_A_PYTHON_COMMENT, id="javascript-with-def-evaluate"),
        pytest.param(PY_WITH_A_JS_STRING, id="python-with-function-main"),
        pytest.param(JS_AFTER_A_COMMENT, id="shows-neither"),
    ],
)
def test_longer_code_is_not_parsed_and_is_detected_as_before(code, monkeypatch):
    code = _padded(code, PARSE_LIMIT + 1)
    parsed = []
    real_parse = ast.parse

    def parse(source, *args, **kwargs):
        if source == code:
            parsed.append(len(source))
        return real_parse(source, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ast, "parse", parse)
        languages = each_saved(code)

    assert languages == dict.fromkeys(SAVED, detected_before(code))
    assert parsed == []
