"""
Code Executor HTTP API Server.

Provides a simple HTTP endpoint for executing untrusted code in nsjail sandboxes.
Falls back to subprocess isolation when nsjail is not available: each run is a
child in its own session and private temp dir, with an empty environment and
CPU, address-space, process, file-size and open-file limits; the session is
killed when the run ends or times out. Both modes require the kernel egress
policy: public destinations on CODE_EXECUTOR_EGRESS_PORTS (80 and 443), with
internal destinations refused. A policy setup failure refuses execution.
Fallback evals use a dedicated unprivileged uid; the fallback does not isolate
the filesystem beyond what that uid can reach.
Concurrent runs share that user, and a process that starts a session of its
own outlives its run (RLIMIT_NPROC caps how many can).

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

GET /ready
200 {"status": "ok"} once the egress policy is in place and this container's
latest self-test, a trivial Python eval run the way customer evals run, has
passed; 503 {"status": "error", "data": <why>} otherwise. /health checks only
the egress policy.
"""

import contextlib
import ctypes
import fcntl
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

import falcon
from egress_guard import EVAL_UID
from egress_guard import verify_egress as prepare_egress

EGRESS_GUARD = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "egress_guard.py"
)

# Check if nsjail is available
NSJAIL_PATH = shutil.which("nsjail")
NSJAIL_AVAILABLE = NSJAIL_PATH is not None
PYTHON_PATH = sys.executable
NODE_PATH = shutil.which("node")

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")

# An executor without Node.js answers a JavaScript eval with this; the
# code-executor image has Node.js, so this comes from an executor
# CODE_EXECUTOR_URL points elsewhere.
NO_NODE_MESSAGE = (
    "JavaScript evals need Node.js, which this sandbox does not have. The "
    "code-executor service runs JavaScript as well as Python: point "
    "CODE_EXECUTOR_URL at it (in Standalone, remove CODE_EXECUTOR_URL from "
    ".env so the app uses code-executor, then run docker compose up -d)."
)

# nsjail reports that it could not build a run's jail with these lines.
NSJAIL_LAUNCH_FAILURES = ("Launching child process failed", "Couldn't launch the child process")
SANDBOX_UNAVAILABLE_MESSAGE = (
    "The code sandbox could not start a run on this host: nsjail could not "
    "create the run's isolated environment (its Linux namespaces). See "
    "INSTALLATION.md, Code evals and the sandbox."
)


# What the executor can see of the host settings that stop a jail from starting.
UID_MAP_PATH = "/proc/self/uid_map"
USERNS_RESTRICT_PATH = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"
APPARMOR_LABEL_PATH = "/proc/self/attr/current"
REMAP_CAUSE = (
    "Docker on this host runs with userns-remap, which puts every container inside "
    "a remapped user namespace, and code evaluations do not run there."
)
RESTRICT_CAUSE = (
    "This host restricts unprivileged user namespaces "
    "(kernel.apparmor_restrict_unprivileged_userns=1, the default on Ubuntu 23.10 "
    "and later): load the fa-code-executor AppArmor profile on the Docker host and "
    "set CODE_EXECUTOR_APPARMOR_PROFILE=fa-code-executor."
)


def _read_first_line(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.readline().strip()
    except OSError:
        return ""


def _host_blocker() -> str:
    """The host setting that stops this container's jails, when it is visible from inside:
    a uid map that is not the host's identity map means Docker's userns-remap; the
    user-namespace restriction counts only when the container does not run under the
    fa-code-executor profile, which lifts it. An empty string when neither shows."""
    uid_map = _read_first_line(UID_MAP_PATH).split()
    if uid_map and uid_map != ["0", "0", "4294967295"]:
        return REMAP_CAUSE
    restricted = _read_first_line(USERNS_RESTRICT_PATH) == "1"
    if restricted and "fa-code-executor" not in _read_first_line(APPARMOR_LABEL_PATH):
        return RESTRICT_CAUSE
    return ""


def _sandbox_unavailable() -> str:
    cause = _host_blocker()
    if not cause:
        return SANDBOX_UNAVAILABLE_MESSAGE
    return (
        "The code sandbox could not start a run on this host: nsjail could not "
        f"create the run's isolated environment (its Linux namespaces). {cause} "
        "See INSTALLATION.md, Code evals and the sandbox."
    )


def _no_output_error(label: str, returncode: int, stderr: str) -> dict:
    if any(marker in stderr for marker in NSJAIL_LAUNCH_FAILURES):
        return {"status": "error", "data": _sandbox_unavailable()}
    return {"status": "error", "data": f"No output. {label}: {returncode}. {stderr}"}


DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 60
MB = 1024 * 1024
MAX_OUTPUT_BYTES = 1 * MB


def _output_limit_mb() -> int:
    """MAX_OUTPUT_BYTES in the whole MB nsjail's --rlimit_fsize takes, rounded up:
    0 would stop every write a run makes, its result included."""
    return (MAX_OUTPUT_BYTES + MB - 1) // MB


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


def _egress_ports_setting() -> tuple:
    value = os.environ.get("CODE_EXECUTOR_EGRESS_PORTS") or "80,443"
    try:
        ports = tuple(int(port) for port in value.split(","))
    except ValueError:
        ports = ()
    if not ports or not all(0 < port < 65536 for port in ports):
        raise ValueError(
            f"CODE_EXECUTOR_EGRESS_PORTS must list TCP ports, comma-separated: {value!r}"
        )
    return ports


def _landlock_abi() -> int:
    """This kernel's Landlock ABI, 0 without Landlock."""
    if not sys.platform.startswith("linux"):
        return 0
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    # landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)
    abi = libc.syscall(ctypes.c_long(444), None, ctypes.c_long(0), ctypes.c_long(1))
    return max(abi, 0)


# One port policy for BOTH modes. Landlock remains defense in depth; it is
# not the address boundary and its absence never permits unrestricted egress.
FALLBACK_EGRESS_PORTS = _egress_ports_setting()
LANDLOCK_TCP_RULES = _landlock_abi() >= 4


def _egress_ports() -> list:
    return list(FALLBACK_EGRESS_PORTS)


def _prepare_network() -> dict | None:
    try:
        prepare_egress(FALLBACK_EGRESS_PORTS)
    except Exception:
        # Deliberately omit exception text, config values and child output.
        logging.getLogger(__name__).error("EGRESS_POLICY_UNAVAILABLE")
        return {
            "status": "error",
            "error_code": "EGRESS_POLICY_UNAVAILABLE",
            "data": "Cannot apply eval network policy",
        }
    return None


def _network_command(argv: list) -> list:
    return [PYTHON_PATH, "-I", EGRESS_GUARD] + argv


# Sets the limits (never above an inherited hard limit) and, unless its port
# list is "-", allows TCP connections to those ports only and no TCP listening
# (Landlock), then execs the run. A launcher process instead of preexec_fn,
# which is unsafe in this threaded server. Only Linux must honour every limit
# (macOS rejects RLIMIT_AS, for local runs of this file). Syscall numbers are
# those of x86_64 and arm64.
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
if sys.argv[7] != "-":
    import ctypes, struct
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    def check(result, name):
        if result < 0:
            raise OSError(ctypes.get_errno(), name)
        return result
    def syscall(number, *args):
        args = [a if isinstance(a, bytes) else ctypes.c_long(a) for a in args]
        return check(libc.syscall(ctypes.c_long(number), *args), f"syscall {number}")
    BIND_TCP, CONNECT_TCP, RULE_NET_PORT = 1, 2, 2
    # landlock_create_ruleset: handled_access_fs 0, handled_access_net
    attr = struct.pack("QQ", 0, BIND_TCP | CONNECT_TCP)
    ruleset = syscall(444, attr, len(attr), 0)
    for port in sys.argv[7].split(","):
        # landlock_add_rule
        syscall(445, ruleset, RULE_NET_PORT, struct.pack("QQ", CONNECT_TCP, int(port)), 0)
    no_new_privs = [ctypes.c_ulong(value) for value in (1, 0, 0, 0)]
    check(libc.prctl(ctypes.c_int(38), *no_new_privs), "prctl")  # PR_SET_NO_NEW_PRIVS
    syscall(446, ruleset, 0)  # landlock_restrict_self
    os.close(ruleset)
os.execv(sys.argv[8], sys.argv[8:])
"""


JAIL_SCRIPTS_DIR = "/sandbox/scripts"


def _write_run_script(script: str, suffix: str) -> str:
    """Write one run's script to a new file of its own with an unguessable name.
    _jail_mounts binds only that file into the run's jail, so no other run sees it.
    It is private to the server's uid when evals share that uid, as they do here."""
    os.makedirs(JAIL_SCRIPTS_DIR, exist_ok=True)
    path = os.path.join(JAIL_SCRIPTS_DIR, f"eval_{secrets.token_hex(16)}{suffix}")
    mode = 0o600 if os.geteuid() == EVAL_UID else 0o644

    def opener(name, flags):
        # "x" already asks for O_CREAT | O_EXCL; never follow a planted link.
        return os.open(name, flags | os.O_NOFOLLOW, mode)

    with open(path, "x", opener=opener) as handle:
        handle.write(script)
    return path


def _jail_mounts(script_path: str, jail_script: str) -> list:
    """nsjail flags for one run's file system. The root is the container's, read only.
    The run gets its own empty /tmp and /dev/shm, a scripts directory that holds only
    its own script (bound read only), and an empty /proc: the kernel refuses a fresh
    procfs over the container's masked /proc, and the container's /proc, which the
    root bind would bring in, shows the server and the other runs, which share a uid."""
    return [
        "-R",
        "/",
        "-m",
        "none:/tmp:tmpfs:size=16777216",  # -T takes a mount point only
        "-m",
        "none:/dev/shm:tmpfs:size=16777216",
        "-m",
        f"none:{JAIL_SCRIPTS_DIR}:tmpfs:size=65536",
        "-R",
        f"{script_path}:{jail_script}",
        "--disable_proc",
        "-m",
        "none:/proc:tmpfs:size=4096",
    ]


def _execute_python_nsjail(code: str, input_data: dict, timeout: int) -> dict:
    """Execute Python code inside nsjail sandbox."""
    refusal = _prepare_network()
    if refusal is not None:
        return refusal
    script = _build_python_script(code, input_data)

    script_path = _write_run_script(script, ".py")
    jail_script = f"{JAIL_SCRIPTS_DIR}/run.py"

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
            str(_output_limit_mb()),  # File writes: the limit, rounded up to whole MB
            "--rlimit_nofile",
            "64",  # Max open files (needs more for network)
            "--time_limit",
            str(timeout),  # Wall clock limit
            "-N",  # Share ONLY the network protected by prepare_egress.
            "--user",
            str(EVAL_UID),
            "--group",
            str(EVAL_UID),
            *_jail_mounts(script_path, jail_script),
            "--",
        ] + _network_command([PYTHON_PATH, "-I", jail_script])

        return _run_jail(cmd, timeout, "Exit code")

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


def _run_jail(
    cmd: list, timeout: int, exit_label: str, *, cwd: str | None = None
) -> dict:
    """Run one nsjail command and return the run's parsed JSON result.

    The run's output goes to unlinked temp files, so the server reads back at
    most MAX_OUTPUT_BYTES + 1 bytes of it however much the run prints.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        # nsjail ends the run at its own time limit; this one is the backstop.
        result = subprocess.run(
            cmd, stdout=out, stderr=err, timeout=timeout + 5, cwd=cwd
        )
        stdout = _read_capped(out, MAX_OUTPUT_BYTES + 1).strip()
        stderr = _read_capped(err, 4096).strip()[:500]
        printed_bytes = os.fstat(out.fileno()).st_size
    if not stdout:
        return _no_output_error(exit_label, result.returncode, stderr)
    # Both jails' --rlimit_fsize stops a run's output at MAX_OUTPUT_BYTES, rounded
    # up to whole MB, so output that reaches MAX_OUTPUT_BYTES is cut off or over it.
    if printed_bytes >= MAX_OUTPUT_BYTES:
        return {"status": "error", "data": "Output too large"}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"status": "error", "data": f"Invalid JSON: {stdout[:200]}"}


def _run_contained(
    argv: list, script: str, suffix: str, timeout: int, address_space_mb: int
) -> dict:
    """Run one eval without nsjail and return its parsed JSON result.

    The script, the run's cwd, HOME and TMPDIR live in a private temp dir that
    is removed afterwards. Output goes to unlinked temp files, so RLIMIT_FSIZE
    caps it, the server never buffers more than MAX_OUTPUT_BYTES, and the run
    cannot swap them for something else.
    """
    refusal = _prepare_network()
    if refusal is not None:
        return refusal
    run_dir = tempfile.mkdtemp(prefix="eval-")
    try:
        script_path = os.path.join(run_dir, f"eval{suffix}")
        with open(script_path, "w") as f:
            f.write(script)
        os.chmod(script_path, 0o444)
        os.chown(run_dir, EVAL_UID, EVAL_UID)
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
                + [
                    # DNS may fall back from UDP to TCP. nftables still limits
                    # port 53 to the exact resolv.conf resolver destinations.
                    ",".join(str(port) for port in sorted({53, *FALLBACK_EGRESS_PORTS}))
                    if LANDLOCK_TCP_RULES
                    else "-"
                ]
                + _network_command(argv + [script_path]),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=run_dir,
                env={**FALLBACK_ENV, "HOME": run_dir, "TMPDIR": run_dir},
                close_fds=True,
                user=EVAL_UID,
                group=EVAL_UID,
                # An already-unprivileged server cannot call setgroups, even
                # to clear it. Init has verified its inherited groups instead.
                **({} if os.geteuid() == EVAL_UID else {"extra_groups": []}),
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
            printed_bytes = os.fstat(out.fileno()).st_size

        if not stdout:
            return {
                "status": "error",
                "data": f"No output. Exit: {proc.returncode}. {stderr}",
            }
        # As in the jails, output whose size in bytes on disk reaches the limit
        # was cut off or is over it.
        if printed_bytes >= MAX_OUTPUT_BYTES:
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
        # The backend shows users this exact text, which it keeps a copy of
        # (sandbox.EXECUTOR_NO_NODE_MESSAGE, compared in the tests).
        return {"status": "error", "data": NO_NODE_MESSAGE, "setup_error": True}

    script = _build_js_script(code, input_data)
    if not NSJAIL_AVAILABLE:
        return _run_contained(
            [NODE_PATH, "--max-old-space-size=64"],
            script,
            ".js",
            timeout,
            FALLBACK_NODE_AS_MB,
        )

    refusal = _prepare_network()
    if refusal is not None:
        return refusal
    script_path = _write_run_script(script, ".js")
    jail_script = f"{JAIL_SCRIPTS_DIR}/run.js"

    try:
        cmd = [
            NSJAIL_PATH,
            "-Mo",
            "-Q",
            "--rlimit_as",
            "512",
            "--rlimit_cpu",
            str(timeout),
            "--rlimit_fsize",
            str(_output_limit_mb()),  # File writes: the limit, rounded up to whole MB
            "--rlimit_nofile",
            "64",
            "--time_limit",
            str(timeout),
            "-N",  # Share ONLY the network protected by prepare_egress.
            "--user",
            str(EVAL_UID),
            "--group",
            str(EVAL_UID),
            *_jail_mounts(script_path, jail_script),
            "--",
        ] + _network_command([NODE_PATH, "--max-old-space-size=64", jail_script])

        return _run_jail(cmd, timeout, "Exit", cwd="/tmp")

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
import json, sys, inspect, numbers

def plain_number(value):
    # The scorer reads Python's own bool, int and float. Other numbers (numpy's,
    # Decimal, Fraction) become a plain float, and numpy's bool a plain bool.
    if isinstance(value, (bool, int, float)):
        return value
    numpy = sys.modules.get("numpy")
    if numpy is not None and isinstance(value, numpy.bool_):
        return bool(value)
    decimal = sys.modules.get("decimal")
    if isinstance(value, numbers.Real) or (decimal is not None and isinstance(value, decimal.Decimal)):
        return float(value)
    return value

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

    name = "evaluate" if exec_globals.get("evaluate") else "main"
    fn = exec_globals.get(name)
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

        result = plain_number(fn(**call_args))

        if inspect.iscoroutine(result) or inspect.isasyncgen(result):
            # An async generator has no sync close, and one never started needs none.
            if inspect.iscoroutine(result):
                result.close()
            print(json.dumps({{"status": "error", "data": f"Code evals call {{name}}() without awaiting it. Define {{name}}() with def, not async def."}}))
        elif isinstance(result, dict):
            for key in ("result", "score"):
                if key in result:
                    result[key] = plain_number(result[key])
            if "score" in result:
                result["result"] = result.pop("score")
            print(json.dumps({{"status": "success", "data": result}}, default=str))
        elif isinstance(result, (bool, int, float)):
            # As it is: the scorer clamps a number and words the reason.
            print(json.dumps({{"status": "success", "data": result}}))
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


# GET /ready reads the result of a self-test: SELF_TEST_CODE run the way a Python
# eval runs, by a background thread, every SELF_TEST_INTERVAL seconds. The gunicorn
# workers share one self-test per interval through two files in the container's
# /tmp (a tmpfs in Compose, an emptyDir in Helm). A flock picks the worker that
# runs it; the kernel releases it if that worker dies mid-run, where an O_EXCL
# lock file would stay behind. Times are time.monotonic(), CLOCK_MONOTONIC on
# Linux: one clock for every process on the host, so the workers can compare them.
SELF_TEST_CODE = "def evaluate():\n    return True\n"
SELF_TEST_INTERVAL = 10  # seconds, about a readiness probe's period
SELF_TEST_DIR = "/tmp"
SELF_TEST_LOCK = "fa-executor-self-test.lock"
SELF_TEST_STATE = "fa-executor-self-test.json"
SELF_TEST_PENDING = "The code sandbox's self-test has not finished yet."
INIT_STAT_PATH = "/proc/1/stat"


def _container() -> str:
    """When this container's PID 1 started, in clock ticks since the host booted
    (field 22 of its stat; execve keeps it, and it never repeats within a boot). A
    restarted container can keep the old one's /tmp (a Kubernetes emptyDir does),
    and the old one's result must not count. Containers that share their pod's
    process namespace keep PID 1 across restarts, so there it cannot tell them apart."""
    try:
        with open(INIT_STAT_PATH, encoding="utf-8", errors="replace") as handle:
            return handle.read().rsplit(")", 1)[1].split()[19]
    except OSError:
        return ""


def _read_self_test() -> dict | None:
    path = os.path.join(SELF_TEST_DIR, SELF_TEST_STATE)
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("container") != _container():
        return None
    return state


def _write_self_test(state: dict) -> None:
    """Replace the shared result in one step, so a reader never sees half of it."""
    fd, path = tempfile.mkstemp(prefix=".fa-executor-self-test-", dir=SELF_TEST_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({**state, "container": _container()}, handle)
        os.replace(path, os.path.join(SELF_TEST_DIR, SELF_TEST_STATE))
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def _self_test() -> str | None:
    """None when SELF_TEST_CODE runs, otherwise the run's own sentence. Its limit is
    the customer run's default: a shorter one would end a slow run as a failure,
    which reads not ready at once, before that patience."""
    run = _execute_python_nsjail if NSJAIL_AVAILABLE else _execute_python_fallback
    result = run(SELF_TEST_CODE, {}, DEFAULT_TIMEOUT)
    if result.get("status") == "success":
        return None
    return str(result.get("data"))


def _run_self_test_when_due() -> float:
    """Run the self-test unless another worker is running it or the last one
    finished less than SELF_TEST_INTERVAL ago; return the seconds to wait."""
    lock = os.open(
        os.path.join(SELF_TEST_DIR, SELF_TEST_LOCK),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return SELF_TEST_INTERVAL
        state = _read_self_test() or {}
        finished_at = state.get("finished_at")
        if finished_at is not None and state.get("running_since") is None:
            wait = finished_at + SELF_TEST_INTERVAL - time.monotonic()
            if wait > 0:
                return wait
        _write_self_test({**state, "running_since": time.monotonic()})
        failure = _self_test()
        _write_self_test({"failure": failure, "finished_at": time.monotonic()})
        return SELF_TEST_INTERVAL
    finally:
        os.close(lock)


def _self_test_loop() -> None:
    while True:
        try:
            wait = _run_self_test_when_due()
        except Exception:
            # Logged, then tried again. /ready reads not ready once the result is
            # overdue, so a loop that keeps failing fails closed.
            logging.getLogger(__name__).exception("SELF_TEST_FAILED")
            wait = SELF_TEST_INTERVAL
        time.sleep(wait)


def _not_ready_reason(state: dict | None, now: float) -> str | None:
    """Why /ready answers 503, or None for 200. A failed self-test counts at once.
    Otherwise the last result stands until the next one is DEFAULT_TIMEOUT overdue,
    counted from the running self-test's start or, when none runs, from when it was
    due: a slow host reads not ready only after the patience a customer run gets."""
    if state is None or state.get("finished_at") is None:
        return SELF_TEST_PENDING
    if state.get("failure") is not None:
        return state["failure"]
    awaited_since = state.get("running_since")
    if awaited_since is None:
        awaited_since = state["finished_at"] + SELF_TEST_INTERVAL
    if now - awaited_since > DEFAULT_TIMEOUT:
        return SELF_TEST_PENDING
    return None


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
        refusal = _prepare_network()
        if refusal is not None:
            resp.status = falcon.HTTP_503
            resp.media = refusal
            return
        resp.media = {
            "status": "ok",
            "nsjail": NSJAIL_AVAILABLE,
            "python": PYTHON_PATH,
            "node": NODE_PATH,
            # Configured policy; execution refuses if it cannot be installed.
            "egress_ports": _egress_ports(),
        }


class ReadyResource:
    """Each worker starts the self-test loop on its first /ready, so an executor
    that nothing probes this way runs no self-tests."""

    def __init__(self):
        self._self_tests = None

    def on_get(self, req, resp):
        if self._self_tests is None:
            self._self_tests = threading.Thread(
                target=_self_test_loop, name="self-test", daemon=True
            )
            self._self_tests.start()
        refusal = _prepare_network()
        if refusal is not None:
            resp.status = falcon.HTTP_503
            resp.media = refusal
            return
        reason = _not_ready_reason(_read_self_test(), time.monotonic())
        if reason is not None:
            resp.status = falcon.HTTP_503
            resp.media = {"status": "error", "data": reason}
            return
        resp.media = {"status": "ok"}


app = falcon.App()
app.add_route("/execute", ExecuteResource())
app.add_route("/health", HealthResource())
app.add_route("/ready", ReadyResource())
