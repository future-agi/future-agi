"""
Code Executor HTTP API Server.

Provides a simple HTTP endpoint for executing untrusted code in nsjail sandboxes.

POST /execute
{
    "code": "def evaluate(...):\n    ...",
    "input_data": {"key": "value"},
    "language": "python",   # or "javascript"
    "timeout": 30
}

Requires Authorization: Bearer <INTERNAL_API_SECRET> on all requests.
Execution is rejected (503) when nsjail is not available.
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

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB

INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")


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
            # TODO(TH-1961): network access needed for MCP tools/agent
            # playground. Restrict to an egress allowlist if data exfiltration
            # becomes a concern.
            "-N",
            "-R",
            "/usr",  # Python interpreter and standard library
            "-R",
            "/lib",  # System dynamic linker and libraries
            "-R",
            "/lib64",  # 64-bit system libraries
            "-R",
            "/sandbox/scripts",  # User scripts directory (read-only)
            # DNS + system CAs, read-only, so -N networking actually works
            # (/etc/passwd and host secrets stay unmounted).
            "-R",
            "/etc/resolv.conf",
            "-R",
            "/etc/hosts",
            "-R",
            "/etc/nsswitch.conf",
            "-R",
            "/etc/ssl/certs",
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
    """Execute JavaScript code in nsjail sandbox."""
    if not NODE_PATH:
        return {"status": "error", "data": "Node.js not available"}

    script = _build_js_script(code, input_data)

    os.makedirs("/sandbox/scripts", exist_ok=True)
    script_path = f"/sandbox/scripts/eval_{os.getpid()}_{id(code)}.js"
    with open(script_path, "w") as f:
        f.write(script)

    try:
        if not NSJAIL_AVAILABLE:
            return {"status": "error", "data": "Javascript sandbox not available"}

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
            # TODO(TH-1961): network access needed for sandboxed JS tools;
            # restrict to an egress allowlist if data exfiltration becomes a
            # concern.
            "-N",
            "-R",
            "/usr",
            "-R",
            "/lib",
            "-R",
            "/lib64",
            "-R",
            "/sandbox/scripts",
            # DNS + system CAs for -N networking, read-only.
            "-R",
            "/etc/resolv.conf",
            "-R",
            "/etc/hosts",
            "-R",
            "/etc/nsswitch.conf",
            "-R",
            "/etc/ssl/certs",
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
        if not INTERNAL_API_SECRET:
            resp.status = falcon.HTTP_401
            resp.media = {"status": "error", "data": "Authentication not configured"}
            return

        auth_header = req.get_header("Authorization", default="")
        if not auth_header.startswith("Bearer "):
            resp.status = falcon.HTTP_401
            resp.media = {"status": "error", "data": "Missing Bearer token"}
            return

        if not hmac.compare_digest(auth_header[7:], INTERNAL_API_SECRET):
            resp.status = falcon.HTTP_401
            resp.media = {"status": "error", "data": "Invalid token"}
            return

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
            if not NSJAIL_AVAILABLE:
                resp.status = falcon.HTTP_503
                resp.media = {
                    "status": "error",
                    "data": "Javascript sandbox not available",
                }
                return
            result = _execute_javascript(code, input_data, timeout)
        elif NSJAIL_AVAILABLE:
            result = _execute_python_nsjail(code, input_data, timeout)
        else:
            resp.status = falcon.HTTP_503
            resp.media = {"status": "error", "data": "Python sandbox not available"}
            return

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
