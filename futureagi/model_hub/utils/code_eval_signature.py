"""Parse the user-authored ``evaluate(...)`` signature of a custom code eval.

Splits declared parameters into two groups:

* **Mapping variables** (dataset row fields) — bound at run time from the
  row / span / trace context. Correspond to the built-in kwargs the
  sandbox passes to ``evaluate`` (``input``, ``output``, ``expected``,
  ``context``).
* **Config params** — constant values the user supplies once at eval
  creation time. Flow into ``function_params_schema`` on the template
  and reappear as text inputs on every binding surface.

The split matches how system YAML evals declare params (e.g.
``length_less_than.yaml`` has ``required_keys: [text]`` and
``config.max_length``). Populating the same fields for user-authored
code evals gives them the same UX with no other code changes.
"""

from __future__ import annotations

import ast
import re

# Names the sandbox binds from the dataset row: any param with one of these
# names is a mapping variable, everything else is a configurable constant.
# ``context`` is deliberately not here because the sandbox synthesises it
# from the row so the user never needs to map it. Keep in sync with
# ``frontend/src/utils/codeEvalParams.js``.
STANDARD_MAPPING_VARS: frozenset[str] = frozenset(
    {"input", "output", "expected"}
)

# Reserved names the sandbox owns end-to-end; they never enter either
# ``required_keys`` or ``function_params_schema``.
_RESERVED_PARAMS: frozenset[str] = frozenset({"context", "self", "cls"})

_JS_EVALUATE_RE = re.compile(
    r"function\s+evaluate\s*\(\s*\{([\s\S]*?)\}\s*\)"
)
_JS_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*")


def parse_evaluate_params(
    code: str,
    language: str | None = "python",
) -> tuple[list[str], list[str]]:
    """Return ``(mapping_vars, config_params)`` extracted from ``code``.

    Both lists preserve source order and never contain duplicates. Unknown
    languages, missing / malformed ``evaluate`` signatures, and any parse
    failure all yield ``([], [])`` so callers can fall through to their
    existing default behaviour.
    """
    if not code:
        return [], []
    lang = (language or "python").lower()
    if lang == "python":
        names = _python_params(code)
    elif lang == "javascript":
        names = _javascript_params(code)
    else:
        return [], []
    mapping_vars: list[str] = []
    config_params: list[str] = []
    for name in names:
        if name in _RESERVED_PARAMS:
            continue
        if name in STANDARD_MAPPING_VARS:
            mapping_vars.append(name)
        else:
            config_params.append(name)
    return mapping_vars, config_params


def _python_params(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate":
            names: list[str] = []
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if arg.arg not in names:
                    names.append(arg.arg)
            return names
    return []


def _javascript_params(code: str) -> list[str]:
    match = _JS_EVALUATE_RE.search(code)
    if not match:
        return []
    raw = _JS_COMMENT_RE.sub("", match.group(1))
    names: list[str] = []
    depth = 0
    buf = ""
    for ch in raw:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            _push_js_name(buf, names)
            buf = ""
        else:
            buf += ch
    _push_js_name(buf, names)
    return names


def _push_js_name(part: str, out: list[str]) -> None:
    token = part.strip()
    if not token or token.startswith("..."):
        return
    # `foo: bar` in a destructure renames; the bound name is on the right.
    if ":" in token:
        token = token.split(":", 1)[1]
    # Default value: `foo = 1`.
    token = token.split("=", 1)[0].strip()
    if not token:
        return
    if token not in out:
        out.append(token)


def build_function_params_schema(config_params: list[str]) -> dict[str, dict]:
    """Materialise a ``function_params_schema`` for a list of config params.

    Every param becomes an optional, nullable string with no default so the
    binding UI treats it as a free-form input. Callers can post-process to
    tighten types once the user marks a param as numeric on the FE.
    """
    return {
        name: {
            "type": "string",
            "default": None,
            "nullable": True,
            "required": False,
        }
        for name in config_params
    }
