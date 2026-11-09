"""NET_ADMIN-only initialization; no eval or HTTP process exists until it succeeds."""

import json
import logging
import os
import shutil
import subprocess
import sys
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
        result = subprocess.run(
            [nft, "-j", "-e", "-n", "-f", "-"],
            input=script,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            close_fds=True,
        )
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
        monitor.detach()
        os.execv(
            "/usr/local/bin/gunicorn",
            [
                "gunicorn",
                "--bind",
                "0.0.0.0:8060",
                "--workers",
                "4",
                "--timeout",
                "60",
                "server:app",
            ],
        )
    finally:
        monitor.close()


if __name__ == "__main__":
    try:
        initialize()
    except Exception:
        logging.error("EGRESS_POLICY_UNAVAILABLE: executor init refused")
        sys.exit(1)
