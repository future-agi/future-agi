"""
Code Executor HTTP API Server.

Provides a simple HTTP endpoint for executing untrusted code in nsjail sandboxes.
Falls back to subprocess isolation when nsjail is not available: each run is a
child in its own session and private temp dir, with an empty environment and
CPU, address-space, process, file-size and open-file limits; the session is
killed when the run ends or times out. The fallback does not isolate the
network or the filesystem beyond what the server's own (unprivileged) user can
reach. Concurrent runs share that user, and a process that starts a session of
its own outlives its run (RLIMIT_NPROC caps how many can).

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
import signal
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
MAX_TIMEOUT = 60
MB = 1024 * 1024
MAX_OUTPUT_BYTES = 1 * MB

# Fallback (no nsjail) limits, the same budget the nsjail flags below set.
# RLIMIT_NPROC counts every process and thread of this uid (the server's own
# included), so it bounds what a run can fork rather than being per run.
FALLBACK_MAX_PROCESSES = int(os.environ.get("CODE_EXECUTOR_MAX_PROCESSES", "64"))
FALLBACK_MAX_FILE_BYTES = 1 * MB
FALLBACK_MAX_OPEN_FILES = 64
FALLBACK_PYTHON_AS_MB = 1024  # nltk/numpy/scipy reserve lots of VM
FALLBACK_NODE_AS_MB = 512
# The child's whole environment. Numeric libraries otherwise start one thread
# per CPU and run into RLIMIT_NPROC.
FALLBACK_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "MALLOC_ARENA_MAX": "2",
}
# Sets the limits (never above an inherited hard limit), then execs the run.
# A launcher process instead of preexec_fn, which is unsafe in this threaded
# server. Only Linux must honour every limit (macOS rejects RLIMIT_AS, for
# local runs of this file).
_LIMITS_LAUNCHER = """\
import os, resource, sys
names = ("RLIMIT_CPU", "RLIMIT_AS", "RLIMIT_NPROC", "RLIMIT_FSIZE", "RLIMIT_NOFILE", "RLIMIT_CORE")
for name, value in zip(names, sys.argv[1:7]):
    limit = getattr(resource, name)
    hard = resource.getrlimit(limit)[1]
    value = int(value) if hard == resource.RLIM_INFINITY else min(int(value), hard)
    try:
        resource.setrlimit(limit, (value, value))
    except (ValueError, OSError):
        if sys.platform.startswith("linux"):
            raise
os.execv(sys.argv[7], sys.argv[7:])
"""


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


def _kill_session(pid: int) -> None:
    """Kill everything the run left in its session (start_new_session)."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _read_capped(stream, limit: int) -> str:
    stream.seek(0)
    return stream.read(limit).decode("utf-8", errors="replace")


def _run_contained(
    argv: list, script: str, suffix: str, timeout: int, address_space_mb: int
) -> dict:
    """Run one eval without nsjail and return its parsed JSON result.

    The script, the run's cwd, HOME and TMPDIR live in a private temp dir that
    is removed afterwards. Output goes to unlinked temp files, so RLIMIT_FSIZE
    caps it, the server never buffers more than MAX_OUTPUT_BYTES, and the run
    cannot swap them for something else.
    """
    run_dir = tempfile.mkdtemp(prefix="eval-")
    try:
        script_path = os.path.join(run_dir, f"eval{suffix}")
        with open(script_path, "w") as f:
            f.write(script)
        limits = [
            timeout,  # RLIMIT_CPU, seconds
            address_space_mb * MB,  # RLIMIT_AS
            FALLBACK_MAX_PROCESSES,  # RLIMIT_NPROC
            FALLBACK_MAX_FILE_BYTES,  # RLIMIT_FSIZE
            FALLBACK_MAX_OPEN_FILES,  # RLIMIT_NOFILE
            0,  # RLIMIT_CORE
        ]
        with (
            tempfile.TemporaryFile(dir=run_dir) as out,
            tempfile.TemporaryFile(dir=run_dir) as err,
        ):
            proc = subprocess.Popen(
                [PYTHON_PATH, "-I", "-c", _LIMITS_LAUNCHER]
                + [str(limit) for limit in limits]
                + argv
                + [script_path],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=run_dir,
                env={**FALLBACK_ENV, "HOME": run_dir, "TMPDIR": run_dir},
                close_fds=True,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return {"status": "error", "data": f"Timed out ({timeout}s)"}
            finally:
                # Also on success: nothing the run started may outlive it.
                _kill_session(proc.pid)
                proc.wait()
            stdout = _read_capped(out, MAX_OUTPUT_BYTES + 1).strip()
            stderr = _read_capped(err, 4096).strip()[:500]

        if not stdout:
            return {
                "status": "error",
                "data": f"No output. Exit: {proc.returncode}. {stderr}",
            }
        if len(stdout) > MAX_OUTPUT_BYTES:
            return {"status": "error", "data": "Output too large"}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"status": "error", "data": f"Invalid JSON: {stdout[:200]}"}
    except Exception as e:
        return {"status": "error", "data": f"Error: {e}"}
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _execute_python_fallback(code: str, input_data: dict, timeout: int) -> dict:
    """Fallback: execute Python code in a contained subprocess without nsjail."""
    return _run_contained(
        [PYTHON_PATH, "-I"],
        _build_python_script(code, input_data),
        ".py",
        timeout,
        FALLBACK_PYTHON_AS_MB,
    )


def _execute_javascript(code: str, input_data: dict, timeout: int) -> dict:
    """Execute JavaScript code in nsjail (or fallback subprocess)."""
    if not NODE_PATH:
        return {"status": "error", "data": "Node.js not available"}

    script = _build_js_script(code, input_data)
    if not NSJAIL_AVAILABLE:
        return _run_contained(
            [NODE_PATH, "--max-old-space-size=64"],
            script,
            ".js",
            timeout,
            FALLBACK_NODE_AS_MB,
        )

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
    escaped = (
        code.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )

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
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except (json.JSONDecodeError, Exception) as e:
            resp.media = {"status": "error", "data": f"Invalid request: {e}"}
            return

        code = data.get("code") or ""
        input_data = data.get("input_data") or {}
        language = data.get("language", "python")
        try:
            timeout = int(data.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        timeout = max(1, min(timeout, MAX_TIMEOUT))

        if not isinstance(code, str) or not code.strip():
            resp.media = {"status": "error", "data": "No code provided"}
            return

        start = time.time()

        if language == "javascript":
            result = _execute_javascript(code, input_data, timeout)
        elif NSJAIL_AVAILABLE:
            result = _execute_python_nsjail(code, input_data, timeout)
        else:
            result = _execute_python_fallback(code, input_data, timeout)
        if not isinstance(result, dict):
            # The run's stdout parsed as JSON, but not as a result object.
            result = {"status": "error", "data": "Invalid result"}

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
