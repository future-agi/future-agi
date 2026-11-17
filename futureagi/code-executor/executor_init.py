"""NET_ADMIN-only initialization; no eval or HTTP process exists until it succeeds."""

import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

# Isolated Python intentionally omits the script directory. This is the fixed,
# root-owned image directory, not a working directory or a user-supplied path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from egress_guard import EVAL_UID, nft_script, policy_rules, read_resolvers
from egress_runtime import (
    NET_ADMIN,
    assert_monitor_quiet,
    drain_install_events,
    drop_capabilities,
    open_monitor,
    process_status,
    seal_witness,
    verify_policy,
)


def _objects(output, echoed=False):
    rows = json.loads(output)["nftables"]
    objects = []
    for row in rows:
        if "metainfo" in row:
            continue
        if echoed:
            if "flush" in row:
                continue
            row = row["add"]
        if len(row) != 1 or next(iter(row)) not in ("table", "chain", "rule"):
            raise RuntimeError("Unexpected policy readback")
        kind = next(iter(row))
        item = {
            key: value
            for key, value in row[kind].items()
            if key not in ("handle", "index", "position")
        }
        objects.append({kind: item})
    return objects


def verify_readback(echo, snapshot, rule_count):
    expected = _objects(echo, echoed=True)
    actual = _objects(snapshot)
    kinds = [next(iter(item)) for item in expected]
    if kinds != ["table", "chain"] + ["rule"] * rule_count or actual != expected:
        raise RuntimeError("Installed policy differs from acknowledged transaction")


# Debian 12's nft (1.0.6) with -j first tries its JSON parser on -f input. Reading stdin that way
# consumes the script; the native-syntax fallback then reads nothing and nft exits 0 with nothing
# installed. A private file on the /tmp tmpfs can be read twice.
POLICY_DIR = "/tmp"


def install_policy(nft, script):
    fd, path = tempfile.mkstemp(prefix="fa-egress-", suffix=".nft", dir=POLICY_DIR)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(script)
        return subprocess.run(
            [nft, "-j", "-e", "-n", "-f", path],
            stdin=subprocess.DEVNULL,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            close_fds=True,
        )
    finally:
        os.unlink(path)


GUNICORN = "/usr/local/bin/gunicorn"
GUNICORN_ARGV = [
    "gunicorn",
    "--bind",
    "0.0.0.0:8060",
    "--workers",
    "4",
    "--timeout",
    "60",
    "server:app",
]
# gunicorn's control signals. Init passes each one on to gunicorn.
SERVER_SIGNALS = (
    signal.SIGTERM,
    signal.SIGINT,
    signal.SIGHUP,
    signal.SIGQUIT,
    signal.SIGUSR1,
    signal.SIGUSR2,
    signal.SIGTTIN,
    signal.SIGTTOU,
    signal.SIGWINCH,
)


def start_server():
    """Fork. The child execs gunicorn and keeps every inheritable descriptor, the
    policy witness and the monitor included. Returns the child's pid."""
    pid = os.fork()
    if pid == 0:
        os.execv(GUNICORN, GUNICORN_ARGV)
    return pid


def supervise(pid):
    """Init stays the container's first process while gunicorn runs as its child.
    It passes gunicorn's control signals on to it, reaps every other process that
    ends as its child, and returns gunicorn's exit code, or 128 plus the number of
    the signal that ended it."""

    def forward(signum, _frame):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signum)

    for signum in SERVER_SIGNALS:
        signal.signal(signum, forward)
    while True:
        try:
            child, status = os.waitpid(-1, 0)
        except InterruptedError:
            continue
        if child == pid:
            break
    # Reap the children that have already exited. The kernel ends the rest when
    # init exits.
    with contextlib.suppress(ChildProcessError):
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    code = os.waitstatus_to_exitcode(status)
    return 128 - code if code < 0 else code


def initialize():
    status = process_status()
    if (
        os.getuid() != EVAL_UID
        or os.geteuid() != EVAL_UID
        or os.getgid() != EVAL_UID
        or any(group != EVAL_UID for group in os.getgroups())
        or any(
            int(status[key], 16) != NET_ADMIN
            for key in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd")
        )
    ):
        raise RuntimeError("Init requires the eval identity and only NET_ADMIN")
    ports = tuple(
        int(p)
        for p in os.environ.get("CODE_EXECUTOR_EGRESS_PORTS", "80,443").split(",")
    )
    raw = os.environ.get("CODE_EXECUTOR_INTERNAL_CIDRS", "")
    internal = tuple(part.strip() for part in raw.split(",")) if raw else ()
    script = nft_script(policy_rules(ports, internal, resolvers=read_resolvers()))
    nft = shutil.which("nft")
    if nft is None:
        raise RuntimeError("Missing nftables")
    monitor = open_monitor()
    try:
        result = install_policy(nft, script)
        drain_install_events(monitor)
        snapshot = subprocess.run(
            [nft, "-j", "-n", "list", "table", "inet", "fa_code_eval"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            close_fds=True,
        )
        verify_readback(result.stdout, snapshot.stdout, script.count("add rule "))
        assert_monitor_quiet(monitor.fileno())
        fd = seal_witness(script, monitor.fileno())
        os.environ["CODE_EXECUTOR_POLICY_FD"] = str(fd)
        drop_capabilities()
        verify_policy(script)
        # The monitor must survive exec. Runtime readiness never consumes it.
        monitor_fd = monitor.detach()
        pid = start_server()
        # gunicorn holds the witness and the monitor now. Init does not use them.
        os.close(monitor_fd)
        os.close(fd)
    finally:
        monitor.close()
    return pid


if __name__ == "__main__":
    try:
        server = initialize()
    except Exception:
        logging.error("EGRESS_POLICY_UNAVAILABLE: executor init refused")
        sys.exit(1)
    sys.exit(supervise(server))
