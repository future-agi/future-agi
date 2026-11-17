"""The inputs a code eval's own code reads.

A code eval template stores no required_keys (the create path saves []): its
inputs are the parameters of its entry function. The code executor calls
evaluate(), or main() when the code defines no evaluate(), with every input as
a keyword argument (code-executor/server.py), and the app's variable-mapping
form reads the same signature (frontend/src/utils/codeEvalParams.js).
"""

import ast
from dataclasses import dataclass

from agentic_eval.core_evals.fi_utils.fi_code_execution import CodeExecution

ENTRY_FUNCTIONS = ("evaluate", "main")
# Parameters the app's mapping form never offers as inputs (codeEvalParams.js).
NOT_INPUTS = ("context", "self", "cls")


@dataclass(frozen=True)
class CodeEvalEntry:
    """A Python entry function that takes only the named keyword inputs.

    ``parameters`` leaves out the names in NOT_INPUTS. The code runner passes
    its own code and language apart from the inputs, so a parameter named
    code or language is an input like any other.
    """

    name: str
    parameters: tuple[str, ...]


def _bindings(tree, name):
    """Every node in ``tree`` that binds ``name``."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound = node.name
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound = node.id
        elif isinstance(node, ast.alias):
            bound = node.asname or node.name.split(".")[0]
        else:
            continue
        if bound == name:
            found.append(node)
    return found


def code_eval_entry(code):
    """The entry function whose parameters are the only inputs ``code`` takes.

    None when there is no code, or when the code takes any input key: its
    entry function has **kwargs, the code is JavaScript (the executor passes
    evaluate() one object holding every input), or the entry function is not
    one plain, undecorated top-level def that can be read here, or it has
    positional-only parameters (the executor passes every input by name). The
    executor then reports any problem when it runs.
    """
    if not isinstance(code, str) or not code.strip():
        return None
    try:
        return _read_entry(code)
    except Exception:
        # Code too deep or too large to read here (RecursionError,
        # MemoryError, ...) is the executor's to report, never the route's.
        return None


def _read_entry(code):
    # The run detects the language from the code. Code with a top-level def
    # read below runs as Python whatever language is stored, so the stored
    # one is not needed here.
    if CodeExecution(code=code).detect_language(code) != "python":
        return None
    tree = ast.parse(code)

    for name in ENTRY_FUNCTIONS:
        bindings = _bindings(tree, name)
        if not bindings:
            continue
        entry = bindings[0]
        if (
            len(bindings) != 1
            or not isinstance(entry, ast.FunctionDef)
            or entry not in tree.body
            # A decorator can change the signature the executor calls.
            or entry.decorator_list
            or entry.args.kwarg is not None
            or entry.args.posonlyargs
        ):
            return None
        names = [arg.arg for arg in entry.args.args + entry.args.kwonlyargs]
        return CodeEvalEntry(name, tuple(n for n in names if n not in NOT_INPUTS))
    return None
