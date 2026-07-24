"""
Code Executor HTTP API Server.

Provides a simple HTTP endpoint for executing untrusted code in nsjail sandboxes.
nsjail is the only execution path — /execute refuses (and /health reports
unhealthy) when nsjail is unavailable, rather than degrading to a weaker sandbox.

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

import hmac
import json
import os
import shutil
import subprocess
import sys
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
            "64",  # Max open files
            "--time_limit",
            str(timeout),  # Wall clock limit
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


def _execute_javascript(code: str, input_data: dict, timeout: int) -> dict:
    """Execute JavaScript code in the nsjail sandbox. Callers must gate on
    NSJAIL_AVAILABLE (on_post refuses without it) — nsjail is the only path."""
    if not NODE_PATH:
        return {"status": "error", "data": "Node.js not available"}

    script = _build_js_script(code, input_data)

    os.makedirs("/sandbox/scripts", exist_ok=True)
    script_path = f"/sandbox/scripts/eval_{os.getpid()}_{id(code)}.js"
    with open(script_path, "w") as f:
        f.write(script)

    try:
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
            "-R",
            "/",
            "-T",
            "/tmp:size=16777216",
            "--",
            NODE_PATH,
            "--max-old-space-size=64",
            script_path,
        ]

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
    exec_globals = {{
        "__builtins__": __builtins__,
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
    """Build JS eval script."""
    input_json = json.dumps(input_data, default=str)
    return f"""'use strict';
const inputData = {input_json};

{code}

try {{
    let result;
    if (typeof evaluate === 'function') result = evaluate(inputData);
    else if (typeof main === 'function') result = main(inputData);
    else {{ console.log(JSON.stringify({{status: "error", data: "Must define evaluate() or main()"}})); process.exit(0); }}

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

        # Fail closed: the sandbox boundary is nsjail. Without it, refuse rather
        # than silently degrade to running untrusted code unsandboxed on the host.
        if not NSJAIL_AVAILABLE:
            resp.media = {"status": "error", "data": "code executor misconfigured: nsjail unavailable, refusing to run untrusted code"}
            return

        start = time.time()

        if language == "javascript":
            result = _execute_javascript(code, input_data, timeout)
        elif language == "python":
            result = _execute_python_nsjail(code, input_data, timeout)
        else:
            resp.media = {"status": "error", "data": f"Unsupported language: {language!r}"}
            return

        elapsed = time.time() - start
        result["execution_time"] = round(elapsed, 3)

        resp.media = result


class HealthResource:
    def on_get(self, req, resp):
        resp.media = {
            # Unhealthy without nsjail — /execute refuses to run, so the service
            # is not doing its job and orchestrators should not route to it.
            "status": "ok" if NSJAIL_AVAILABLE else "unhealthy",
            "nsjail": NSJAIL_AVAILABLE,
            "python": PYTHON_PATH,
            "node": NODE_PATH,
        }


class AuthMiddleware:
    """Reject /execute calls that don't present the internal API key. Fail closed:
    an unset key rejects everything, so a misconfigured executor never runs open."""

    _EXEMPT = ("/health",)

    def __init__(self, key=None):
        self._key = key if key is not None else os.environ.get("CODE_EXECUTOR_INTERNAL_API_KEY", "")

    def process_request(self, req, resp):
        if req.path in self._EXEMPT:
            return
        presented = req.get_header("X-Internal-Api-Key") or ""
        if not self._key or not hmac.compare_digest(presented, self._key):
            raise falcon.HTTPUnauthorized(title="Unauthorized")


app = falcon.App(middleware=[AuthMiddleware()])
app.add_route("/execute", ExecuteResource())
app.add_route("/health", HealthResource())
