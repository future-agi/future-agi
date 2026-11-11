"""Measured identity and isolation facts of the native container runtime.

Nothing here is read from a control-plane request or a self-asserted constant. The
same module derives the application configuration for the host CLI
(`measure.py config`) and the live health facts (`health()`), so a changed image,
mount or network makes the Hub reject the guest.
"""

from __future__ import annotations

import errno
import hashlib
import importlib.metadata as metadata
import json
import os
import re
import socket
import sys
import uuid
from pathlib import Path

SOURCE = Path(os.environ.get("CTXHUB_SOURCE_MOUNT", "/fi_app"))
CONTEXT = Path(os.environ.get("CTXHUB_CONTEXT_MOUNT", "/fi_context"))
STATE = Path(os.environ.get("CTXHUB_STATE_MOUNT", "/fi_state"))
OUTPUT = Path(os.environ.get("CTXHUB_OUTPUT_MOUNT", "/fi_output"))
sys.path.insert(0, str(SOURCE / "src"))  # the mounted, read-only application source
RUNTIME_FILES = tuple(
    Path(__file__).resolve().with_name(name)
    for name in ("measure.py", "runtime.py", "app_cell.py", "retained_ci.py", "production_request.py", "ci_dataset.json")
)
PROTOCOL = "skills-native-v1"
ADAPTER = "fi.context.adapters.simulation_entrypoint"
PROVIDER_URL = os.environ.get("CTXHUB_PROVIDER_URL", "http://skills-fakes:8080/v1")
MODEL_ID = os.environ.get("CTXHUB_MODEL", "gpt-4o-mini")


def canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def tree_digest(root: Path) -> str:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".pyc"):
            rows.append({"path": path.relative_to(root).as_posix(), "sha256": sha256_bytes(path.read_bytes())})
    return digest(rows)


def mount_options(path: Path) -> set[str] | None:
    """Options of the mount whose mount point is exactly `path`, from the kernel."""
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        if len(fields) > 5 and fields[4] == str(path):
            return set(fields[5].split(","))
    return None


def write_denied(path: Path) -> bool:
    probe = path / f".probe-{uuid.uuid4().hex}"
    try:
        probe.write_text("x")
    except OSError as exc:
        return exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}
    probe.unlink()
    return False


def write_allowed(path: Path) -> bool:
    probe = path / f".probe-{uuid.uuid4().hex}"
    try:
        probe.write_text("x")
        probe.unlink()
    except OSError:
        return False
    return True


def read_only_mount(path: Path) -> bool:
    options = mount_options(path)
    return options is not None and "ro" in options and write_denied(path)


def egress_closed() -> bool:
    """True only when public addresses and public DNS are unreachable."""
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        return False
    except OSError:
        pass
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        return False
    except OSError:
        pass
    try:
        socket.getaddrinfo("example.com", 443)
        return False
    except OSError:
        return True


def packages() -> list[dict]:
    rows = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        record = dist.read_text("RECORD")
        if not name or record is None:
            continue
        key = re.sub(r"[-_.]+", "-", name).lower()
        rows[key] = {"name": name, "version": dist.version, "sha256": sha256_bytes(record.encode())}
    return sorted(rows.values(), key=lambda item: item["name"])


def image_digest() -> str:
    runtime = [{"path": p.name, "sha256": sha256_bytes(p.read_bytes())} for p in RUNTIME_FILES]
    return digest({"python": sys.version, "packages": packages(), "runtime": runtime})


def tools_digest() -> str:
    from ctxhub_support.application import TOOLS

    return digest(TOOLS)


def checkpoint_digest() -> str:
    import ctxhub_support.store as store

    return "sha256:" + sha256_bytes(Path(store.__file__).read_bytes())


def configuration() -> dict:
    """The exact pinned application configuration, derived from the live image."""
    commit = os.environ.get("CTXHUB_APP_COMMIT", "")
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise SystemExit("CTXHUB_APP_COMMIT must be a git object id")
    return {
        "code_commit": commit,
        "code_digest": tree_digest(SOURCE),
        "entrypoint": "ctxhub_support.entrypoint:main",
        "image_digest": image_digest(),
        "runtime_version": "python-" + ".".join(map(str, sys.version_info[:3])),
        "packages": packages(),
        "package_lock_digest": digest(packages()),
        "model": {"model_id": MODEL_ID, "temperature": 0, "max_output_tokens": 512},
        "connection_id": str(uuid.uuid5(uuid.NAMESPACE_URL, PROVIDER_URL)),
        "connection_revision": "1",
        "tools_digest": tools_digest(),
        "retrieval_fixture_digest": digest({"retrieval": "none"}),
        "state_schema": "support-order/v1",
        "checkpoint_digest": checkpoint_digest(),
        "side_effect_policy": "fixture_only",
        "reset_entrypoint": "ctxhub_support.entrypoint:reset",
        "context_mount": "/fi_context/",
        "observable_boundaries": [
            "catalog", "checkpoint", "child_selection", "file_delivery",
            "model_request", "root_delivery", "tool_result",
        ],
    }


def health(payload: dict) -> dict:
    live = configuration()
    return {
        "protocol": PROTOCOL,
        "adapter_module": ADAPTER,
        "configuration_digest": payload["configuration_digest"],
        "execution_fingerprint": payload["execution_fingerprint"],
        "code_digest": live["code_digest"],
        "image_digest": live["image_digest"],
        "packages": live["packages"],
        "checkpoint_digest": live["checkpoint_digest"],
        "read_only_source": read_only_mount(SOURCE),
        "read_only_context": read_only_mount(CONTEXT),
        "separate_writable_state": bool(
            write_allowed(STATE)
            and write_allowed(OUTPUT)
            and mount_options(STATE) is not None
            and "ro" not in (mount_options(STATE) or set())
            and os.stat(STATE).st_dev != os.stat(SOURCE).st_dev
            and os.stat(STATE).st_dev != os.stat(CONTEXT).st_dev
        ),
        "controlled_paid_egress": egress_closed(),
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["config"]:
        print(json.dumps(configuration(), sort_keys=True))
    else:
        raise SystemExit("usage: measure.py config")
