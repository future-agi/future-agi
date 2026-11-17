"""Keep the reference backend network guard active for the entire proof process."""
import json
import os
from pathlib import Path
import socket
import sys

DIRECTORY = Path(os.environ["EVAL_GUARD_DIR"])
DIRECTORY.mkdir(parents=True, exist_ok=True)
SERVICE = os.environ.get("EVAL_GUARD_SERVICE", "host")
# Containers mount the committed backend tests; host controls use the same tree.
reference = os.environ.get("EVAL_GUARD_REFERENCE_ROOT")
sys.path.insert(0, reference or str(Path(__file__).resolve().parents[4] / "futureagi"))
if os.environ.get("EVAL_GUARD_PYTHON_DEPS"):
    sys.path.insert(0, os.environ["EVAL_GUARD_PYTHON_DEPS"])

from importlib.util import find_spec  # noqa: E402

if find_spec("model_hub.apps") is None:
    raise ImportError("Reference guard must preserve the application package")
from model_hub.tests import network_isolation  # noqa: E402


def record(kind, layer, hostname, port):
    if kind == "refused" and os.environ.get("EVAL_GUARD_CONTROL") == "1":
        kind = "refusal-control"
    port = port if isinstance(port, (str, int, type(None))) else None
    row = {"kind": kind, "layer": layer, "hostname": str(hostname), "port": port,
           "service": SERVICE, "pid": os.getpid()}
    fd = os.open(DIRECTORY / f"python-{SERVICE}-{os.getpid()}.jsonl",
                 os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        data = (json.dumps(row) + "\n").encode()
        if os.write(fd, data) != len(data):
            raise OSError("Incomplete network record")
    finally:
        os.close(fd)


# The harness supplies exact compose service names, not arbitrary domain suffixes.
# The reference refresh resolves them once and validates EVERY answer as a fixture
# address before any name or address becomes usable by the socket adapters.
services = frozenset(filter(None, os.environ.get("EVAL_GUARD_SERVICES", "").split(",")))
if any(not name.replace("-", "").replace("_", "").isalnum() for name in services):
    raise ValueError("Invalid compose service name")
network_isolation.configured_service_hosts = lambda: services
network_isolation.write_event = record
GUARD = network_isolation.GUARD
WATCH = GUARD.watch()
ATTEMPTS = WATCH.__enter__()
record("installed", "python.socket", socket.gethostname(), 0)
for host in sorted(GUARD._own_names | GUARD._own_addresses):
    record("own-host-snapshot", "python.socket", host, 0)
