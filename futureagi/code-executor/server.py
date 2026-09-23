"""
Code Executor HTTP API Server.

Provides a simple HTTP endpoint for executing untrusted code in nsjail sandboxes.
Falls back to subprocess isolation when nsjail is not available.

POST /execute
{
    "code": "def evaluate(...):\n    ...",
    "input_data": {"key": "value"},
    "language": "python",   # or "javascript"
    "timeout": 30
}

Returns:
{
    "status": "success" | "error",
    "data": <result> | <error message>
}
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import falcon

# Check if nsjail is available
NSJAIL_PATH = shutil.which("nsjail")
NSJAIL_AVAILABLE = NSJAIL_PATH is not None
PYTHON_PATH = sys.executable
NODE_PATH = shutil.which("node")

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB


def _execute_python_nsjail(code: str, input_data: dict, timeout: int) -> dict:
    """Execute Python code inside nsjail sandbox."""
    script = _build_python_script(code, input_data)

    # Write script to /sandbox/scripts (NOT /tmp, which gets overlaid by tmpfs inside nsjail)
    os.makedirs("/sandbox/scripts", exist_ok=True)
    script_path = f"/sandbox/scripts/eval_{os.getpid()}_{id(code)}.py"
    with open(script_path, "w") as f:
        f.write(script)

    try:
        cmd = [
            NSJAIL_PATH,
            "-Mo",  # Standalone once mode
            "-Q",  # Really quiet (only fatal logs)
            "--rlimit_as",
            "1024",  # 1 GB virtual address space (nltk/numpy/scipy reserve lots of VM)
            "--rlimit_cpu",
            str(timeout),  # CPU time limit
            "--rlimit_fsize",
            "1",  # 1 MB file writes
            "--rlimit_nofile",
            "64",  # Max open files (needs more for network)
            "--time_limit",
            str(timeout),  # Wall clock limit
            "-N",  # Allow network access — code can fetch URLs
            "-R",
            "/",  # Bind-mount root read-only (includes /sandbox/scripts)
            "-T",
            "/tmp:size=16777216",  # Writable tmpfs at /tmp (16MB)
            "--",
            PYTHON_PATH,
            "-I",
            script_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 5,  # nsjail has its own timeout
        )

        stdout = result.stdout.strip()
        if not stdout:
            stderr = result.stderr.strip()[:500]
            return {
                "status": "error",
                "data": f"No output. Exit code: {result.returncode}. {stderr}",
            }

        if len(stdout) > MAX_OUTPUT_BYTES:
            return {
                "status": "error",
                "data": f"Output too large ({len(stdout)} bytes)",
            }

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"status": "error", "data": f"Invalid JSON: {stdout[:200]}"}

    except subprocess.TimeoutExpired:
        return {"status": "error", "data": f"Execution timed out ({timeout}s)"}
    except Exception as e:
        return {"status": "error", "data": f"Sandbox error: {e}"}
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass


def _execute_python_fallback(code: str, input_data: dict, timeout: int) -> dict:
    """Fallback: execute Python code in a subprocess without nsjail."""
    script = _build_python_script(code, input_data)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, dir="/tmp", prefix="eval_"
    ) as f:
        f.write(script)
        script_path = f.name

    try:
        result = subprocess.run(
            [PYTHON_PATH, "-I", script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            cwd="/tmp",
        )

        stdout = result.stdout.strip()
        if not stdout:
            stderr = result.stderr.strip()[:500]
            return {
                "status": "error",
                "data": f"No output. Exit: {result.returncode}. {stderr}",
            }

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"status": "error", "data": f"Invalid JSON: {stdout[:200]}"}

    except subprocess.TimeoutExpired:
        return {"status": "error", "data": f"Timed out ({timeout}s)"}
    except Exception as e:
        return {"status": "error", "data": f"Error: {e}"}
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass


def _execute_javascript(code: str, input_data: dict, timeout: int) -> dict:
    """Execute JavaScript code in nsjail (or fallback subprocess)."""
    if not NODE_PATH:
        return {"status": "error", "data": "Node.js not available"}

    script = _build_js_script(code, input_data)

    os.makedirs("/sandbox/scripts", exist_ok=True)
    script_path = f"/sandbox/scripts/eval_{os.getpid()}_{id(code)}.js"
    with open(script_path, "w") as f:
        f.write(script)

    try:
        if NSJAIL_AVAILABLE:
            cmd = [
                NSJAIL_PATH,
                "-Mo",
                "-Q",
                "--rlimit_as",
                "512",
                "--rlimit_cpu",
                str(timeout),
                "--rlimit_nofile",
                "64",
                "--time_limit",
                str(timeout),
                "-N",  # Allow network
                "-R",
                "/",
                "-T",
                "/tmp:size=16777216",
                "--",
                NODE_PATH,
                "--max-old-space-size=64",
                script_path,
            ]
        else:
            cmd = [NODE_PATH, "--max-old-space-size=64", script_path]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            cwd="/tmp",
        )

        stdout = result.stdout.strip()
        if not stdout:
            stderr = result.stderr.strip()[:500]
            return {
                "status": "error",
                "data": f"No output. Exit: {result.returncode}. {stderr}",
            }

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"status": "error", "data": f"Invalid JSON: {stdout[:200]}"}

    except subprocess.TimeoutExpired:
        return {"status": "error", "data": f"Timed out ({timeout}s)"}
    except Exception as e:
        return {"status": "error", "data": f"Error: {e}"}
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass


def _build_python_script(code: str, input_data: dict) -> str:
    """Build self-contained Python eval script."""
    input_json = json.dumps(input_data, default=str)
    return f"""
import json, sys, inspect

def main():
    input_data = json.loads({repr(input_json)})

    # User code
    # Pre-import common modules so user code can reference them
    import typing, math, re, collections, datetime, itertools, functools
    import builtins as _builtins

    # Security: build restricted builtins — block dangerous operations
    _BLOCKED = frozenset({{
        'exec', 'eval', 'compile',          # code execution
        'open',                              # filesystem access
        '__import__',                        # unrestricted imports
        'globals', 'locals',                 # namespace inspection
        'getattr', 'setattr', 'delattr',     # attribute manipulation
        'breakpoint', 'input',               # interactive
        'exit', 'quit',                      # process control
        'memoryview',                        # low-level memory
        'vars', 'dir',                       # introspection
    }})
    _safe_builtins = {{k: getattr(_builtins, k) for k in dir(_builtins)
                      if not k.startswith('_') and k not in _BLOCKED}}

    # Safe import: only allow whitelisted modules
    _SAFE_MODULES = {{
        'json': json, 're': re, 'math': math, 'collections': collections,
        'itertools': itertools, 'functools': functools, 'datetime': datetime,
        'typing': typing, 'string': __import__('string'),
        'decimal': __import__('decimal'), 'statistics': __import__('statistics'),
        'copy': __import__('copy'), 'difflib': __import__('difflib'),
        'textwrap': __import__('textwrap'), 'hashlib': __import__('hashlib'),
        'base64': __import__('base64'), 'uuid': __import__('uuid'),
        'enum': __import__('enum'), 'dataclasses': __import__('dataclasses'),
        'operator': __import__('operator'), 'numbers': __import__('numbers'),
    }}
    def _safe_import(name, *args, **kwargs):
        if name in _SAFE_MODULES:
            return _SAFE_MODULES[name]
        raise ImportError(f"Import of '{{name}}' is blocked. Allowed: {{', '.join(sorted(_SAFE_MODULES))}}")

    _safe_builtins['__import__'] = _safe_import

    exec_globals = {{
        "__builtins__": _safe_builtins,
        **vars(typing),
        "math": math,
        "re": re,
        "collections": collections,
        "datetime": datetime,
        "itertools": itertools,
        "functools": functools,
    }}
    user_code = {repr(code)}

    try:
        exec(user_code, exec_globals)
    except Exception as e:
        print(json.dumps({{"status": "error", "data": f"Compilation error: {{e}}"}}))
        return

    fn = exec_globals.get("evaluate") or exec_globals.get("main")
    if not callable(fn):
        print(json.dumps({{"status": "error", "data": "Must define evaluate() or main()"}}))
        return

    try:
        # Auto-provide standard eval args
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        std = {{"input": None, "output": None, "expected": None, "context": {{"row": dict(input_data)}}}}
        call_args = {{}}
        for p in params:
            if p == "kwargs" or p.startswith("**"):
                continue
            call_args[p] = input_data.get(p, std.get(p))
        for k, v in input_data.items():
            if k not in call_args:
                call_args[k] = v

        result = fn(**call_args)

        if isinstance(result, dict):
            if "score" in result:
                result["result"] = result.pop("score")
            print(json.dumps({{"status": "success", "data": result}}, default=str))
        elif isinstance(result, bool):
            print(json.dumps({{"status": "success", "data": {{"result": float(result), "reason": "bool"}}}}))
        elif isinstance(result, (int, float)):
            print(json.dumps({{"status": "success", "data": {{"result": float(min(max(result, 0), 1)), "reason": "numeric"}}}}))
        elif result is None:
            print(json.dumps({{"status": "skip", "data": None}}))
        else:
            print(json.dumps({{"status": "success", "data": {{"result": float(bool(result)), "reason": str(result)[:200]}}}}))
    except Exception as e:
        print(json.dumps({{"status": "error", "data": f"Runtime error: {{e}}"}}))

if __name__ == "__main__":
    main()
"""


def _build_js_script(code: str, input_data: dict) -> str:
    """Build JS eval script.

    Security: User code is loaded via new Function() to isolate it
    from the top-level scope and prevent early process.exit() or
    require() calls from bypassing the try/catch error handling.
    """
    input_json = json.dumps(input_data, default=str)
    # Escape user code for safe embedding in a JS string literal
    escaped = (
        code.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("`", "\\`")
        .replace("$", "\\$")
    )

    return f"""'use strict';
const inputData = {input_json};

try {{
    // Load user code via new Function() to prevent top-level execution
    // before the try/catch block. The code is embedded as an escaped string.
    const _userCode = '{escaped}';
    const _fn = new Function('module', 'exports', 'require', _userCode);
    const _module = {{ exports: {{}} }};
    // Block dangerous require() calls
    const _safeRequire = (name) => {{
        const blocked = ['child_process', 'fs', 'net', 'http', 'https',
            'dgram', 'tls', 'cluster', 'worker_threads', 'os', 'vm',
            'v8', 'dns', 'readline', 'repl', 'inspector', 'crypto',
            'module', 'fs/promises', 'path'];
        if (blocked.includes(name)) throw new Error(`require('${{name}}') is blocked for security`);
        throw new Error(`require('${{name}}') is not available in sandbox`);
    }};
    _fn(_module, _module.exports, _safeRequire);

    const evaluate = _module.exports.evaluate || (typeof globalThis.evaluate === 'function' ? globalThis.evaluate : undefined);
    const main = _module.exports.main || (typeof globalThis.main === 'function' ? globalThis.main : undefined);
    const userFn = evaluate || main;

    // Also check if functions were defined in the Function scope
    let result;
    if (typeof userFn === 'function') {{
        result = userFn(inputData);
    }} else {{
        // Fallback: try evaluating as a script that defines evaluate/main globally
        const vm = require('vm');
        const ctx = {{ inputData, console, JSON, Math, String, Number, Array, Object, Date, RegExp, Boolean, Set, Map, Error, parseInt, parseFloat, isNaN, isFinite, undefined, null: null }};
        vm.createContext(ctx);
        vm.runInContext(_userCode, ctx);
        const fn = ctx.evaluate || ctx.main;
        if (typeof fn !== 'function') {{
            console.log(JSON.stringify({{status: "error", data: "Must define evaluate() or main()"}}));
            process.exit(0);
        }}
        result = fn(inputData);
    }}

    if (result !== undefined && result !== null) {{
        if (typeof result === 'object' && 'score' in result) {{ result.result = result.score; delete result.score; }}
        console.log(JSON.stringify({{status: "success", data: result}}));
    }} else {{
        console.log(JSON.stringify({{status: "skip", data: null}}));
    }}
}} catch (e) {{
    console.log(JSON.stringify({{status: "error", data: "Runtime error: " + e.message}}));
}}
"""


# ── Falcon HTTP API ──


class ExecuteResource:
    def on_post(self, req, resp):
        try:
            body = req.bounded_stream.read()
            data = json.loads(body)
        except (json.JSONDecodeError, Exception) as e:
            resp.media = {"status": "error", "data": f"Invalid request: {e}"}
            return

        code = data.get("code", "")
        input_data = data.get("input_data", {})
        language = data.get("language", "python")
        timeout = min(data.get("timeout", DEFAULT_TIMEOUT), 60)

        if not code.strip():
            resp.media = {"status": "error", "data": "No code provided"}
            return

        start = time.time()

        if language == "javascript":
            result = _execute_javascript(code, input_data, timeout)
        elif NSJAIL_AVAILABLE:
            result = _execute_python_nsjail(code, input_data, timeout)
        else:
            result = _execute_python_fallback(code, input_data, timeout)

        elapsed = time.time() - start
        result["execution_time"] = round(elapsed, 3)

        resp.media = result


class HealthResource:
    def on_get(self, req, resp):
        resp.media = {
            "status": "ok",
            "nsjail": NSJAIL_AVAILABLE,
            "python": PYTHON_PATH,
            "node": NODE_PATH,
        }


app = falcon.App()
app.add_route("/execute", ExecuteResource())
app.add_route("/health", HealthResource())
