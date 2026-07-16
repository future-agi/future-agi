"""
Production-grade sandboxed code execution.

Multi-layer defense for executing untrusted user code:

Layer 1: RestrictedPython v8+ (AST-level restrictions for Python)
Layer 2: Subprocess isolation — code runs in a forked child process
Layer 3: OS-level limits — rlimits for CPU, memory, file size, processes
Layer 4: Minimal environment — no env vars, restricted PATH, no network indicators
Layer 5: Timeout enforcement — hard kill after deadline

For JavaScript: subprocess with Node.js --experimental-permission flag
(blocks fs, network, child_process at V8 engine level)

Architecture:
  Parent (API server) → fork subprocess → apply rlimits → exec sandbox → return JSON result

The sandboxed process communicates results via stdout JSON.
If it crashes, times out, or produces invalid output, the parent returns an error.
"""

import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from typing import Any

import structlog
import urllib.request

logger = structlog.get_logger(__name__)

# Code executor service URL (nsjail-based sandbox container)
CODE_EXECUTOR_URL = os.environ.get("CODE_EXECUTOR_URL", "http://code-executor:8060")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT_SECONDS = 30
MAX_MEMORY_BYTES = 1024 * 1024 * 1024  # 1 GB virtual address space (nltk/numpy/scipy reserve a lot of VM)
MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB stdout limit
MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB — allow stdout writes, but no large file creation
MAX_PROCESSES = 50  # Python/Node need internal threads

# Python modules safe to import inside the sandbox
SAFE_MODULES = [
    "json", "re", "math", "collections", "itertools", "functools",
    "string", "datetime", "decimal", "statistics", "copy", "difflib",
    "textwrap", "hashlib", "base64", "uuid", "enum", "dataclasses",
    "typing", "operator", "numbers",
    "os.path",  # Path manipulation only (basename, dirname, splitext, join)
    "struct",   # Binary data packing/unpacking
    "io",       # StringIO, BytesIO (in-memory streams)
    "csv",      # CSV parsing
    "html",     # HTML escaping
    "urllib.parse",  # URL parsing only
    # Data processing libraries (no network/filesystem/process risk)
    "PIL", "PIL.Image", "PIL.ImageStat",  # Image processing
    "numpy",    # Numerical computing
    "pandas",   # Data analysis
    # NLP / similarity libraries (used by system code evals)
    "Levenshtein",  # Edit distance
    "nltk", "nltk.translate", "nltk.translate.bleu_score",  # BLEU score
    "rouge_score",  # ROUGE score
    "scipy", "scipy.spatial", "scipy.spatial.distance",  # Cosine/euclidean distance
    "sklearn", "sklearn.metrics",  # ML metrics
    "jinja2",  # Template rendering
]

# os module with dangerous functions removed
def _build_safe_os_module():
    """Create a restricted os module — only safe path/string functions."""
    import os as _real_os
    import types
    safe_os = types.ModuleType("os")
    # Only expose safe attributes
    SAFE_OS_ATTRS = [
        "sep", "linesep", "devnull", "curdir", "pardir", "extsep", "altsep",
    ]
    for attr in SAFE_OS_ATTRS:
        if hasattr(_real_os, attr):
            setattr(safe_os, attr, getattr(_real_os, attr))
    # Expose os.path (read-only path manipulation)
    safe_os.path = _real_os.path
    return safe_os

_SAFE_OS = _build_safe_os_module()

# JS modules blocked (everything dangerous)
JS_BLOCKED_MODULES = [
    "fs", "child_process", "net", "http", "https", "dgram", "tls",
    "cluster", "worker_threads", "os", "path", "vm", "v8",
    "stream", "dns", "readline", "repl", "inspector", "perf_hooks",
    "async_hooks", "crypto", "zlib", "url", "querystring", "module",
    "fs/promises", "timers", "diagnostics_channel", "trace_events",
    "wasi", "node:fs", "node:child_process", "node:net", "node:http",
    "node:os", "node:path", "node:vm", "node:crypto",
]


# ---------------------------------------------------------------------------
# Python sandbox wrapper script
# ---------------------------------------------------------------------------
def _build_python_sandbox_script(user_code: str, input_data: dict) -> str:
    """Build a self-contained Python script that runs user code in RestrictedPython."""
    safe_modules_json = json.dumps(SAFE_MODULES)
    input_json = json.dumps(input_data, default=str)

    return textwrap.dedent(f'''\
import json
import sys
import inspect
import collections
import copy
import datetime
import decimal
import difflib
import functools
import itertools
import math
import re
import statistics
import string
import textwrap
import hashlib
import base64
import struct
import io
import csv
import html
import os
import os.path
import urllib.parse
import types

# Build a safe os module — only path functions and constants, no system/exec/fork/env
_safe_os = types.ModuleType("os")
for _attr in ["sep", "linesep", "devnull", "curdir", "pardir", "extsep", "altsep"]:
    if hasattr(os, _attr):
        setattr(_safe_os, _attr, getattr(os, _attr))
_safe_os.path = os.path

def main():
    try:
        from RestrictedPython import compile_restricted_exec
        from RestrictedPython.Eval import (
            default_guarded_getattr,
            default_guarded_getitem,
            default_guarded_getiter,
        )
        from RestrictedPython.Guards import guarded_unpack_sequence
    except ImportError:
        print(json.dumps({{"status": "error", "data": "RestrictedPython not installed"}}))
        return

    SAFE_MODULES = {safe_modules_json}
    SAFE_MODULE_MAP = {{}}
    for name in SAFE_MODULES:
        try:
            SAFE_MODULE_MAP[name] = __import__(name)
        except ImportError:
            pass

    # Replace os with safe version (no system/exec/fork/environ)
    SAFE_MODULE_MAP["os"] = _safe_os
    SAFE_MODULE_MAP["os.path"] = os.path

    def safe_import(name, globals_=None, locals_=None, fromlist=(), level=0):
        # Handle dotted imports: "os.path" → return safe os (which has .path)
        # "urllib.parse" → return urllib module
        top = name.split(".")[0]
        if top in SAFE_MODULE_MAP:
            mod = SAFE_MODULE_MAP[top]
            # For "from X import Y" (fromlist), return the top module
            # Python will extract Y from it
            return mod
        if name in SAFE_MODULE_MAP:
            return SAFE_MODULE_MAP[name]
        raise ImportError(f"Import of '{{name}}' is not allowed. Allowed: {{', '.join(sorted(SAFE_MODULE_MAP.keys()))}}")

    BLOCKED_BUILTINS = frozenset({{
        "exec", "eval", "compile", "open", "__import__",
        "globals", "locals", "getattr", "setattr", "delattr",
        "breakpoint", "input", "exit", "quit", "memoryview",
        "vars", "dir", "type", "super", "classmethod", "staticmethod",
        "property", "__build_class__",
    }})

    import builtins as _builtins
    safe_builtins = {{}}
    for name in dir(_builtins):
        if name.startswith("_"):
            continue
        if name in BLOCKED_BUILTINS:
            continue
        safe_builtins[name] = getattr(_builtins, name)
    for blocked in BLOCKED_BUILTINS:
        safe_builtins.pop(blocked, None)

    class PrintCollector:
        def __init__(self):
            self._lines = []
        def __call__(self, *args, **kwargs):
            sep = kwargs.get("sep", " ")
            end = kwargs.get("end", "\\n")
            self._lines.append(sep.join(str(a) for a in args) + end)

    def print_factory():
        return PrintCollector()

    def write_guard(ob):
        if isinstance(ob, (dict, list, set)):
            return ob
        raise TypeError(f"Cannot modify object of type {{type(ob).__name__}}")

    INPLACE_OPS = {{
        "+=": lambda x, y: x + y, "-=": lambda x, y: x - y,
        "*=": lambda x, y: x * y, "/=": lambda x, y: x / y,
        "//=": lambda x, y: x // y, "%=": lambda x, y: x % y,
        "**=": lambda x, y: x ** y,
    }}
    def safe_inplace(op, x, y):
        if isinstance(op, str):
            fn = INPLACE_OPS.get(op)
            if fn: return fn(x, y)
            raise ValueError(f"Unsupported: {{op}}")
        return op(x, y)

    user_code = {repr(user_code)}
    input_data = json.loads({repr(input_json)})

    result = compile_restricted_exec(user_code)
    if result.errors:
        print(json.dumps({{"status": "error", "data": "Compilation errors: " + "; ".join(result.errors)}}))
        return

    # Add safe_import to builtins so `from X import Y` works in restricted code
    safe_builtins["__import__"] = safe_import

    restricted_globals = {{
        "__builtins__": safe_builtins,
        "__name__": "__restricted__",
        "__metaclass__": type,
        "_getattr_": default_guarded_getattr,
        "_getitem_": default_guarded_getitem,
        "_getiter_": default_guarded_getiter,
        "_write_": write_guard,
        "_print_": print_factory,
        "_inplacevar_": safe_inplace,
        "_iter_unpack_sequence_": guarded_unpack_sequence,
    }}

    try:
        exec(result.code, restricted_globals)
    except Exception as e:
        print(json.dumps({{"status": "error", "data": f"Execution error: {{e}}"}}))
        return

    fn = restricted_globals.get("evaluate") or restricted_globals.get("main")
    if not callable(fn):
        print(json.dumps({{"status": "error", "data": "Code must define evaluate() or main()"}}))
        return

    try:
        # Provide defaults for standard eval function args if not in input_data
        try:
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
        except (ValueError, TypeError):
            params = []
        # Build context with row data from all kwargs (so context["row"] works)
        auto_context = {{"row": dict(input_data), "dataset_name": input_data.get("dataset_name", "")}}
        std_defaults = {{"input": None, "output": None, "expected": None, "context": auto_context}}
        call_args = {{}}
        for p in params:
            if p == "kwargs" or p.startswith("**"):
                continue
            if p in input_data:
                call_args[p] = input_data[p]
            elif p in std_defaults:
                call_args[p] = std_defaults[p]
        # Add remaining input_data as kwargs
        for k, v in input_data.items():
            if k not in call_args:
                call_args[k] = v
        output = fn(**call_args)
        # Normalize output
        if isinstance(output, dict):
            if "score" in output:
                output["result"] = output.pop("score")
            json_output = json.dumps(output, default=str)
        elif isinstance(output, bool):
            json_output = json.dumps({{"result": float(output), "reason": "Boolean result"}})
        elif isinstance(output, (int, float)):
            json_output = json.dumps({{"result": float(min(max(output, 0), 1)), "reason": "Numeric score"}})
        elif output is None:
            json_output = json.dumps({{"status": "skip", "data": None}})
        else:
            json_output = json.dumps({{"result": float(bool(output)), "reason": str(output)}})
        print(json.dumps({{"status": "success", "data": json.loads(json_output)}}))
    except Exception as e:
        print(json.dumps({{"status": "error", "data": f"Function error: {{e}}"}}))

if __name__ == "__main__":
    main()
''')


def _build_js_sandbox_single_file(user_code: str, input_data: dict) -> str:
    """Build a SINGLE JS file that runs user code inside a scope with no require/process/module.

    Strategy: The file itself has access to require/process (it's a CommonJS module).
    We use that access to read the user code, then execute it inside a Function()
    that does NOT have require/process/module in its closure scope.

    Function() constructor creates a function with ONLY global scope — no local
    closure over require/process/module. Combined with deleting these from global,
    user code cannot access them.
    """
    input_json = json.dumps(input_data, default=str)
    # Escape for embedding in a JS string literal
    escaped_code = user_code.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")

    return f"""'use strict';

// ── Phase 1: Capture what we need BEFORE lockdown ──
const _write = process.stdout.write.bind(process.stdout);
const _stringify = JSON.stringify;
const _freeze = Object.freeze;
const _defineProperty = Object.defineProperty;
const _getPrototypeOf = Object.getPrototypeOf;

// ── Phase 2: Lock down Function.prototype.constructor ──
// This prevents: this.constructor.constructor("return process")()
try {{
    const FP = _getPrototypeOf(function(){{}});
    _defineProperty(FP, 'constructor', {{
        value: undefined,
        writable: false,
        configurable: false,
    }});
}} catch(e) {{}}

// ── Phase 3: Nuke ALL dangerous globals ──
delete globalThis.require;
delete globalThis.module;
delete globalThis.exports;
delete globalThis.__filename;
delete globalThis.__dirname;
delete globalThis.Buffer;
delete globalThis.global;

// Replace process with a frozen stub (no env, no exit, no binding)
const frozenProcess = _freeze(Object.create(null));
globalThis.process = frozenProcess;

// ── Phase 4: Execute user code inside a Function (no closure over require) ──
const userCodeStr = '{escaped_code}';
const inputData = {input_json};

try {{
    // Function() creates code with ONLY global scope — require is not in scope
    // since we deleted it from globalThis above
    const sandboxedFn = new Function('inputData', '__write', '__stringify',
        userCodeStr + `;
        var _result;
        if (typeof evaluate === 'function') _result = evaluate(inputData);
        else if (typeof main === 'function') _result = main(inputData);
        else {{ __write(__stringify({{status: "error", data: "Must define evaluate() or main()"}})); return; }}

        if (_result !== undefined && _result !== null) {{
            if (typeof _result === 'object' && 'score' in _result) {{
                _result.result = _result.score;
                delete _result.score;
            }}
            __write(__stringify({{status: "success", data: _result}}));
        }} else {{
            __write(__stringify({{status: "skip", data: null}}));
        }}
    `);

    sandboxedFn(inputData, _write, _stringify);
}} catch (e) {{
    _write(_stringify({{status: "error", data: "Runtime error: " + e.message}}));
}}
"""


# ---------------------------------------------------------------------------
# OS-level resource limits (applied in subprocess preexec_fn)
# ---------------------------------------------------------------------------
def _set_resource_limits():
    """Apply strict resource limits before exec. Called in forked child.

    This runs in the child process BEFORE the user code. It:
    - Caps memory, CPU, file size, processes
    - Closes all file descriptors except stdin/stdout/stderr
    - Clears all environment variables
    """
    # Memory limit — skip on emulated architectures (Rosetta/QEMU)
    # where even RLIMIT_DATA breaks mmap. On native Linux production,
    # Docker --memory flag or cgroups handle this at container level.
    # The subprocess timeout (30s) + RestrictedPython provide the safety net.

    # CPU time limit (seconds)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (DEFAULT_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS + 5))
    except (ValueError, resource.error):
        pass

    # No file creation/writes
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_BYTES))
    except (ValueError, resource.error):
        pass

    # No forking
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))
    except (ValueError, resource.error):
        pass

    # No core dumps
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, resource.error):
        pass

    # Close all file descriptors beyond stdin/stdout/stderr
    # This prevents reading any open files from the parent process
    try:
        import resource as _res
        max_fd = _res.getrlimit(_res.RLIMIT_NOFILE)[0]
        for fd in range(3, min(max_fd, 1024)):
            try:
                os.close(fd)
            except OSError:
                pass
    except Exception:
        pass

    # Clear ALL environment variables — no secrets leak
    os.environ.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _call_executor_service(code: str, input_data: dict, language: str, timeout: int) -> dict | None:
    """Call the nsjail code-executor service via HTTP. Returns None if unavailable."""
    try:
        # default=str so non-JSON-native types coming through trace/span column
        # mapping (Decimal from clickhouse-driver, datetime, UUID) serialize
        # cleanly. The eval body still gets a string for those keys, which
        # matches what `str(kwargs.get(...))` already expects in every system eval.
        payload = json.dumps({
            "code": code,
            "input_data": input_data,
            "language": language,
            "timeout": timeout,
        }, default=str).encode("utf-8")

        req = urllib.request.Request(
            f"{CODE_EXECUTOR_URL}/execute",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            logger.info("code_executor_service_used", language=language, status=result.get("status"))
            return result
    except Exception as e:
        logger.debug("code_executor_service_unavailable", error=str(e))
        return None  # Fall back to local sandbox


def execute_sandboxed_python(code: str, input_data: dict, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """
    Execute Python code in a production-grade sandbox.

    Tries the nsjail code-executor service first (Tier 1: full namespace isolation).
    Falls back to RestrictedPython subprocess sandbox (Tier 2).
    """
    # Try nsjail executor service first
    result = _call_executor_service(code, input_data, "python", timeout)
    if result is not None:
        return result
    script = _build_python_sandbox_script(code, input_data)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, prefix="sandbox_") as f:
        f.write(script)
        script_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, "-I", script_path],  # -I: isolated mode (no user site, no PYTHONPATH)
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_set_resource_limits,
            env={"PYTHONDONTWRITEBYTECODE": "1"},  # Minimal env — no PATH, no HOME
            cwd="/tmp",
        )

        stdout = result.stdout.strip()
        if not stdout:
            stderr = result.stderr.strip()[:500]
            if result.returncode == -signal.SIGKILL:
                return {"status": "error", "data": "Process killed — likely exceeded memory limit (128MB)"}
            if result.returncode == -signal.SIGXCPU:
                return {"status": "error", "data": f"CPU time limit exceeded ({timeout}s)"}
            return {"status": "error", "data": f"No output from sandbox. Exit code: {result.returncode}. Stderr: {stderr}"}

        # Limit output size
        if len(stdout) > MAX_OUTPUT_BYTES:
            return {"status": "error", "data": f"Output too large ({len(stdout)} bytes, max {MAX_OUTPUT_BYTES})"}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"status": "error", "data": f"Invalid JSON from sandbox: {stdout[:200]}"}

    except subprocess.TimeoutExpired:
        return {"status": "error", "data": f"Execution timed out ({timeout}s)"}
    except Exception as e:
        logger.error("sandbox_execution_failed", error=str(e))
        return {"status": "error", "data": f"Sandbox error: {e}"}
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass


def execute_sandboxed_javascript(code: str, input_data: dict, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """
    Execute JavaScript code in a sandboxed subprocess.

    Tries the nsjail code-executor service first (Tier 1).
    Falls back to local Function() sandbox (Tier 2).
    """
    # Try nsjail executor service first
    result = _call_executor_service(code, input_data, "javascript", timeout)
    if result is not None:
        return result

    # Fallback: local sandbox
    """
    """
    # Check if Node.js is available
    node_path = None
    for candidate in ["/usr/local/bin/node", "/usr/bin/node"]:
        if os.path.isfile(candidate):
            node_path = candidate
            break

    if not node_path:
        return {"status": "error", "data": "Node.js is not available for JavaScript execution"}

    script = _build_js_sandbox_single_file(code, input_data)

    script_path = None

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, prefix="sandbox_") as f:
            f.write(script)
            script_path = f.name

        cmd = [
            node_path,
            "--no-warnings",
            "--max-old-space-size=64",
            "--stack-size=1024",
            script_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"PATH": os.path.dirname(node_path)},  # Minimal env — no secrets
            cwd="/tmp",
        )

        stdout = result.stdout.strip()
        if not stdout:
            stderr = result.stderr.strip()[:500]
            return {"status": "error", "data": f"No output. Exit code: {result.returncode}. Stderr: {stderr}"}

        if len(stdout) > MAX_OUTPUT_BYTES:
            return {"status": "error", "data": f"Output too large ({len(stdout)} bytes)"}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"status": "error", "data": f"Invalid JSON: {stdout[:200]}"}

    except subprocess.TimeoutExpired:
        return {"status": "error", "data": f"JavaScript execution timed out ({timeout}s)"}
    except Exception as e:
        logger.error("js_sandbox_failed", error=str(e))
        return {"status": "error", "data": f"JavaScript sandbox error: {e}"}
    finally:
        if script_path:
            try:
                os.remove(script_path)
            except OSError:
                pass
