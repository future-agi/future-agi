"""
Sandboxed code execution for user-written eval functions.

Uses RestrictedPython to compile user code with AST-level restrictions,
then executes in a controlled environment with:
  - Whitelisted imports only (json, re, math, collections, etc.)
  - No filesystem, network, subprocess, or OS access
  - No code generation (exec, eval, compile blocked)
  - Timeout enforcement via threading
  - Attribute access mediated through guards

Function signature:
  def evaluate(output, expected, **kwargs) -> bool | float | dict
  OR
  def main(**kwargs) -> bool | float | dict

Return format:
  - bool: True = pass, False = fail
  - float/int: Score 0-1
  - dict: {"result": bool|float, "reason": "explanation"}
"""

import collections
import copy
import ctypes
import datetime
import decimal
import difflib
import functools
import itertools
import json
import math
import re
import statistics
import string
import textwrap
import threading
import traceback
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Safe modules: Only these can be imported by user code
# ---------------------------------------------------------------------------
SAFE_MODULES = {
    "json": json,
    "re": re,
    "math": math,
    "collections": collections,
    "itertools": itertools,
    "functools": functools,
    "string": string,
    "datetime": datetime,
    "decimal": decimal,
    "statistics": statistics,
    "copy": copy,
    "difflib": difflib,
    "textwrap": textwrap,
}

# ---------------------------------------------------------------------------
# Safe builtins: Dangerous operations are explicitly excluded
# ---------------------------------------------------------------------------
_BLOCKED_BUILTINS = frozenset(
    {
        "exec",
        "eval",
        "compile",  # Code execution
        "open",  # File I/O
        "__import__",  # Raw imports (replaced with safe version)
        "globals",
        "locals",  # Namespace inspection
        "getattr",
        "setattr",
        "delattr",  # Attribute manipulation
        "breakpoint",
        "input",  # Interactive
        "exit",
        "quit",  # Process control
        "memoryview",  # Low-level memory
        "vars",
        "dir",  # Introspection
    }
)


def _build_safe_builtins():
    """Build a dict of safe builtins, excluding dangerous ones."""
    import builtins as _builtins

    safe = {}
    for name in dir(_builtins):
        if name.startswith("_"):
            continue
        if name in _BLOCKED_BUILTINS:
            continue
        safe[name] = getattr(_builtins, name)

    # Double-check removals
    for blocked in _BLOCKED_BUILTINS:
        safe.pop(blocked, None)

    return safe


SAFE_BUILTINS = _build_safe_builtins()


# ---------------------------------------------------------------------------
# Safe import: Only allows whitelisted modules
# ---------------------------------------------------------------------------
def _safe_import(name, globals_=None, locals_=None, fromlist=(), level=0):
    """Import guard that only allows whitelisted modules."""
    if name in SAFE_MODULES:
        return SAFE_MODULES[name]
    raise ImportError(
        f"Import of '{name}' is not allowed. "
        f"Allowed modules: {', '.join(sorted(SAFE_MODULES.keys()))}"
    )


# ---------------------------------------------------------------------------
# Print capture
# ---------------------------------------------------------------------------
class _PrintCollector:
    """
    Collects print() output from sandboxed code.

    RestrictedPython transforms print(x) into _print_()(x),
    so _print_ must be a factory that returns the callable.
    """

    def __init__(self):
        self._lines = []

    def __call__(self, *args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        self._lines.append(sep.join(str(a) for a in args) + end)

    @property
    def output(self):
        return "".join(self._lines)


def _print_factory():
    """Factory that returns a PrintCollector. RestrictedPython calls _print_() to get the printer."""
    collector = _PrintCollector()
    return collector


# ---------------------------------------------------------------------------
# Write guard for RestrictedPython
# ---------------------------------------------------------------------------
def _full_write_guard(ob):
    """Guard for attribute writes. Allows writes to dicts, lists, sets."""
    if isinstance(ob, (dict, list, set)):
        return ob
    raise TypeError(
        f"Cannot modify object of type {type(ob).__name__}. "
        "Only dict, list, and set modifications are allowed."
    )


_INPLACE_OPS = {
    "+=": lambda x, y: x + y,
    "-=": lambda x, y: x - y,
    "*=": lambda x, y: x * y,
    "/=": lambda x, y: x / y,
    "//=": lambda x, y: x // y,
    "%=": lambda x, y: x % y,
    "**=": lambda x, y: x**y,
    "&=": lambda x, y: x & y,
    "|=": lambda x, y: x | y,
    "^=": lambda x, y: x ^ y,
    ">>=": lambda x, y: x >> y,
    "<<=": lambda x, y: x << y,
}


def _safe_inplace_var(op, x, y):
    """Guard for augmented assignment (+=, -=, etc.)."""
    if isinstance(op, str):
        fn = _INPLACE_OPS.get(op)
        if fn:
            return fn(x, y)
        raise ValueError(f"Unsupported inplace operation: {op}")
    return op(x, y)


# ---------------------------------------------------------------------------
# Timeout via threading
# ---------------------------------------------------------------------------
class _ExecutionTimeout(Exception):
    pass


def _raise_in_thread(thread_id, exception_type):
    """Raise an exception in the target thread (CPython only)."""
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread_id),
        ctypes.py_object(exception_type),
    )
    if res == 0:
        pass  # Thread already finished
    elif res > 1:
        # Reset if more than one thread was affected
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None)


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------
def _run_sandboxed(code: str, input_data: dict) -> dict[str, Any]:
    """
    Execute user code in a RestrictedPython sandbox (in-process).
    """
    from RestrictedPython import compile_restricted_exec
    from RestrictedPython.Eval import (
        default_guarded_getattr,
        default_guarded_getitem,
        default_guarded_getiter,
    )
    from RestrictedPython.Guards import guarded_unpack_sequence

    # Step 1: Compile with RestrictedPython
    compile_result = compile_restricted_exec(code, filename="<user_eval>")

    if compile_result.errors:
        return {
            "status": "error",
            "data": f"Code compilation blocked: {'; '.join(compile_result.errors)}",
        }

    compiled_code = compile_result.code

    # Step 2: Build restricted globals
    from RestrictedPython import PrintCollector

    restricted_globals = {
        "__builtins__": {
            **SAFE_BUILTINS,
            "__import__": _safe_import,
        },
        "__name__": "__main__",
        "_getattr_": default_guarded_getattr,
        "_getitem_": default_guarded_getitem,
        "_getiter_": default_guarded_getiter,
        "_unpack_sequence_": guarded_unpack_sequence,
        "_write_": _full_write_guard,
        "_inplacevar_": _safe_inplace_var,
        "_print_": PrintCollector,
    }

    # Pre-import safe modules
    for mod_name, mod in SAFE_MODULES.items():
        restricted_globals[mod_name] = mod

    local_namespace = {}

    # Step 3: Execute the code definition
    exec(compiled_code, restricted_globals, local_namespace)  # noqa: S102

    # Step 4: Find the user function
    func = local_namespace.get("evaluate") or local_namespace.get("main")
    if func is None:
        return {
            "status": "error",
            "data": (
                "Code must define an 'evaluate' or 'main' function.\n"
                "Example:\n"
                "  def evaluate(output, expected, **kwargs):\n"
                "      return output == expected"
            ),
        }

    # Step 5: Call the function
    result = func(**input_data)

    # Capture any print output from the PrintCollector
    print_output = None
    _print_inst = local_namespace.get("_print")
    if _print_inst and hasattr(_print_inst, "txt"):
        print_output = _print_inst.txt or None

    return {
        "status": "success",
        "data": result,
        "print_output": print_output,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def execute_restricted_python(
    code: str,
    input_data: dict[str, Any],
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """
    Execute user-written Python code in a sandboxed environment.

    Security guarantees (via RestrictedPython AST-level restrictions):
      - No filesystem access (open, os, pathlib blocked)
      - No network access (socket, http, urllib blocked)
      - No subprocess spawning (subprocess, os.system blocked)
      - No env var access (os.environ blocked)
      - No code generation (exec, eval, compile blocked)
      - No attribute introspection (__class__, __mro__ blocked)
      - Timeout enforced via thread interruption

    Args:
        code: User's Python code. Must define `evaluate()` or `main()`.
        input_data: Dict of variables passed as kwargs to the function.
        timeout_seconds: Max execution time.

    Returns:
        dict with "status" ("success" or "error") and "data".
    """
    if not code or not code.strip():
        return {"status": "error", "data": "No code provided"}

    # Run in a thread with timeout enforcement
    result_holder = [None]
    error_holder = [None]

    def _target():
        try:
            result_holder[0] = _run_sandboxed(code, input_data)
        except _ExecutionTimeout:
            error_holder[0] = (
                f"Code execution timed out after {timeout_seconds} seconds"
            )
        except MemoryError:
            error_holder[0] = "Code execution exceeded memory limit"
        except ImportError as e:
            error_holder[0] = str(e)
        except Exception as e:
            error_holder[0] = f"{type(e).__name__}: {e}"

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        # Thread is stuck — try to interrupt it
        try:
            _raise_in_thread(thread.ident, _ExecutionTimeout)
        except Exception:
            pass
        thread.join(timeout=2)
        return {
            "status": "error",
            "data": f"Code execution timed out after {timeout_seconds} seconds",
        }

    if error_holder[0]:
        return {"status": "error", "data": error_holder[0]}

    if result_holder[0] is None:
        return {
            "status": "error",
            "data": "Code execution failed with no output",
        }

    return result_holder[0]
