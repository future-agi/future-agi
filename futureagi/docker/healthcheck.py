#!/usr/bin/env python3
"""Docker HEALTHCHECK of the futureagi/future-agi image (docs/images.md).

One image runs every backend role (entrypoint.sh reads SERVICE_TYPE). Only
the roles that listen are probed:

  backend, HTTP on    GET http://127.0.0.1:80/health/ must answer 2xx. It
                      answers 503 while an embedded Temporal worker is down
                      (tfc/asgi.py).
  backend, HTTP off   TCP connect to the gRPC port, 50051
  grpc                TCP connect to 50051
  flower              TCP connect to 5555

Every other container is healthy while it runs: Temporal and Celery workers
and beat listen on nothing, one-shot jobs (SERVICE_TYPE=bootstrap) exit, and
a container started with a command of its own (entrypoint: python -m ...)
does not run entrypoint.sh at all.

Arguments, or HEALTHCHECK_URL (comma-separated), replace the role logic:
every URL must answer 2xx. Requests carry the first ALLOWED_HOSTS entry that
is not a wildcard as their Host header, so a tightened ALLOWED_HOSTS does
not turn /health/ into a 400, and never go through HTTP(S)_PROXY.

Standard library only: the image runs it as `python -I -S`, which skips
site-packages and keeps the probe cheap.
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.request

TIMEOUT_SECONDS = 5
HTTP_PORT = 80  # entrypoint.sh: granian --port 80
GRPC_PORT = 50051
FLOWER_PORT = 5555


def _pid1_runs_entrypoint() -> bool:
    """True when PID 1 is entrypoint.sh (directly or under docker-init/tini)."""
    try:
        with open("/proc/1/cmdline", "rb") as cmdline:
            args = cmdline.read().split(b"\0")
    except OSError:
        return True  # cannot tell; probe rather than report a blind success
    return any(arg.endswith(b"entrypoint.sh") for arg in args)


def _host_header() -> str | None:
    for entry in os.environ.get("ALLOWED_HOSTS", "").split(","):
        entry = entry.strip().lstrip(".")
        if entry and entry != "*":
            return entry
    return None


def _http_ok(url: str) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url, headers={"User-Agent": "futureagi-healthcheck"}
    )
    host = _host_header()
    if host:
        request.add_header("Host", host)
    # urllib raises HTTPError for 4xx/5xx; 3xx is followed.
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if not 200 <= response.status < 300:
            raise OSError(f"{url} answered HTTP {response.status}")


def _tcp_ok(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT_SECONDS):
        pass


def _role_probe():
    """The check for this container's role, or None when nothing listens."""
    if not _pid1_runs_entrypoint():
        return None
    role = os.environ.get("SERVICE_TYPE") or "backend"
    # entrypoint.sh compares these with "true" exactly.
    http_on = os.environ.get("ENABLE_HTTP", "true") == "true"
    grpc_on = os.environ.get("ENABLE_GRPC", "true") == "true"
    if role == "backend" and http_on:
        return lambda: _http_ok(f"http://127.0.0.1:{HTTP_PORT}/health/")
    if (role == "backend" and grpc_on) or role == "grpc":
        return lambda: _tcp_ok(GRPC_PORT)
    if role == "flower":
        return lambda: _tcp_ok(FLOWER_PORT)
    return None


def main(argv: list[str]) -> int:
    urls = argv or [
        u.strip() for u in os.environ.get("HEALTHCHECK_URL", "").split(",") if u.strip()
    ]
    try:
        if urls:
            for url in urls:
                _http_ok(url)
        else:
            probe = _role_probe()
            if probe is not None:
                probe()
    except Exception as exc:  # any failure is "unhealthy"; say why in `docker inspect`
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
