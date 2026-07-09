"""Split an ``evaluate(...)`` signature into mapping vars and config params."""

from __future__ import annotations

import ast
import logging
import re

logger = logging.getLogger(__name__)

# Keep in sync with ``frontend/src/utils/codeEvalParams.js``. ``context`` is
# NOT a mapping var because the sandbox synthesises it from the row.
STANDARD_MAPPING_VARS: frozenset[str] = frozenset({"input", "output", "expected"})

# The sandbox owns ``context`` end-to-end, so it never enters either list.
_RESERVED_PARAMS: frozenset[str] = frozenset({"context"})

_JS_EVALUATE_RE = re.compile(
    r"function\s+evaluate\s*\(\s*\{([\s\S]*?)\}\s*\)"
)
_JS_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*")


def parse_evaluate_params(
    code: str | None,
    language: str | None = "python",
) -> tuple[list[str], list[str]]:
    """Return ``(mapping_vars, config_params)`` extracted from ``code``.

    Fails open to ``([], [])`` on missing input or an unparseable signature
    so save/update paths degrade to the pre-existing "no params declared"
    behaviour rather than raising.
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
        logger.debug("code_eval_signature_python_parse_failed", exc_info=True)
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
    """Wrap each param in the optional-nullable-string entry the runtime consumes."""
    return {
        name: {
            "type": "string",
            "default": None,
            "nullable": True,
            "required": False,
        }
        for name in config_params
    }
