"""Supervise the fi-collector child process from Django app boot.

Why this exists: fi-collector ships as a standalone Go binary
(see future-agi/fi-collector/cmd/fi-collector). In production K8s we run it
as a sidecar container — see fi-collector/EMBEDDED.md mode #2. In single-host
deployments and local dev, the simplest path is to have Django launch and
supervise the collector as a child process — this module is that supervisor.

The complex supervision (auto-restart, SIGTERM propagation, log piping) is
all the systemd-style behaviour the operator expects without adding systemd
or systemctl into the picture.

For prod K8s: use the sidecar pattern instead. See EMBEDDED.md. The
supervisor here is "best-effort" — Python's atexit/SIGTERM semantics are
not robust enough for prod-K8s graceful drain on pod preemption (codex
review High-1). Treat this module as suitable for single-host / docker-
compose / local dev only, and use the sidecar pattern in K8s.
"""
from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

# Module-level state — there's only one collector per Django process.
_proc: Optional[subprocess.Popen] = None
_stop = threading.Event()
_starting = False                                                # codex H-2: guard against concurrent start() races
_lock = threading.Lock()
_log_thread: Optional[threading.Thread] = None


def start(
    *,
    binary_path: str,
    ch_url: str,
    grpc_addr: str = ":4317",
    admin_addr: str = ":9464",
    dead_letter_dir: str = "/var/lib/fi-collector",
    restart_backoff_initial_sec: float = 0.5,
    restart_backoff_max_sec: float = 30.0,
    install_signal_handlers: bool = True,
) -> None:
    """Launch fi-collector as a child process; restart on crash with
    capped exponential backoff. Idempotent — safe to call from multiple
    Django app-startup hooks (we lock around the start).

    Concurrency / SIGTERM notes (codex review High-1, High-2):
      - The whole start critical section is inside `_lock` AND uses the
        `_starting` sentinel, so two concurrent callers cannot both pass
        the alive-check and spawn duplicate supervisors.
      - `_stop` is cleared on every start() so a previous stop() doesn't
        wedge subsequent restarts.
      - If `install_signal_handlers=True` (default), SIGTERM/SIGINT are
        wired to stop(). This propagates k8s pod-preempt SIGTERM to the
        collector. Disable when Django (or its ASGI server) installs its
        own handlers and will call stop() itself in a shutdown hook.
    """
    global _proc, _starting
    with _lock:
        if _starting:
            log.debug("fi-collector supervisor: another caller is starting")
            return
        if _proc is not None and _proc.poll() is None:
            log.debug("fi-collector supervisor: already running", extra={"pid": _proc.pid})
            return
        _starting = True
        _stop.clear()                                            # H-2: allow restart after a prior stop()

    try:
        env = os.environ.copy()
        env["FI_CH_URL"] = ch_url
        env["FI_GRPC_ADDR"] = grpc_addr
        env["FI_ADMIN_ADDR"] = admin_addr
        env["FI_DEAD_LETTER_FILE"] = os.path.join(dead_letter_dir, "dead_letter.jsonl")
        os.makedirs(dead_letter_dir, exist_ok=True)

        threading.Thread(
            target=_supervise,
            args=(binary_path, env, restart_backoff_initial_sec, restart_backoff_max_sec),
            daemon=True,
            name="fi-collector-supervisor",
        ).start()

        # Signal forwarding: atexit alone is not reliable on SIGTERM /
        # OOM-kill / hard crash (codex review High-1). Installing
        # explicit handlers means PID 1 (container init) → us → child.
        if install_signal_handlers:
            try:
                signal.signal(signal.SIGTERM, _signal_stop)
                signal.signal(signal.SIGINT, _signal_stop)
            except ValueError:
                # signal.signal must be called from main thread; in
                # multi-threaded test harnesses this raises. Skip silently
                # in that case — atexit is the fallback.
                pass
        atexit.register(stop)
        log.info("fi-collector supervisor: started",
                 extra={"binary": binary_path, "grpc_addr": grpc_addr})
    finally:
        with _lock:
            _starting = False


def _signal_stop(_signum, _frame) -> None:
    """SIGTERM / SIGINT handler — forwards graceful stop to the child."""
    log.info("fi-collector supervisor: signal received, draining child")
    stop()


def stop() -> None:
    """Send SIGTERM to the collector, wait up to 10 s, then SIGKILL.
    Safe to call multiple times.
    """
    _stop.set()
    with _lock:
        proc = _proc
    if proc and proc.poll() is None:
        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            log.warning("fi-collector did not exit on SIGTERM; SIGKILL")
            try:
                proc.kill()
            except ProcessLookupError:
                pass


def is_running() -> bool:
    """For /healthz endpoints that want to surface whether the embedded
    collector is alive.
    """
    with _lock:
        return _proc is not None and _proc.poll() is None


def _supervise(binary: str, env: dict, backoff_init: float, backoff_max: float) -> None:
    """Inner restart loop. Runs in its own daemon thread."""
    global _proc, _log_thread
    backoff = backoff_init
    while not _stop.is_set():
        log.info("fi-collector: launching", extra={"binary": binary})
        try:
            # Do NOT pass start_new_session=True (codex H-1): we want the
            # child to live in our process group so a SIGTERM to PID 1
            # propagates naturally. We also still install our own handlers
            # in start() to forward SIGTERM explicitly via stop().
            proc = subprocess.Popen(
                [binary],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except FileNotFoundError:
            log.error("fi-collector: binary not found", extra={"path": binary})
            return  # don't restart — this is a config error
        with _lock:
            _proc = proc

        # Pipe child output into Django's logger so log aggregation
        # captures it without a separate sidecar log shipper.
        log_thread = threading.Thread(
            target=_stream_logs, args=(proc,), daemon=True,
            name="fi-collector-logs",
        )
        log_thread.start()
        with _lock:
            _log_thread = log_thread

        rc = proc.wait()
        # Codex M-3: close the pipe and join the log thread bounded so
        # FDs don't accumulate across restarts.
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:                                        # noqa: BLE001
            pass
        log_thread.join(timeout=2)

        if _stop.is_set():
            log.info("fi-collector: stopped on supervisor shutdown")
            return

        log.warning(
            "fi-collector: exited unexpectedly",
            extra={"rc": rc, "next_restart_sec": backoff},
        )
        # Honor backoff but wake early if a shutdown is requested.
        _stop.wait(timeout=backoff)
        backoff = min(backoff * 2, backoff_max)


def _stream_logs(proc: subprocess.Popen) -> None:
    """Pump child stdout/stderr into Django's logger line-by-line.
    The child emits JSON lines (see cmd/fi-collector/main.go, slog
    JSONHandler), so we forward them verbatim — log aggregators that
    expect JSON pick them up unchanged.

    Exits when proc.stdout closes (either child exits or parent calls
    .close() on it from _supervise). No bare `except Exception` —
    we want a real traceback if logging breaks.
    """
    if not proc.stdout:
        return
    try:
        for line in iter(proc.stdout.readline, b""):
            if not line:
                break
            log.info("fi-collector", extra={"line": line.decode(errors="replace").rstrip()})
    except ValueError:
        # Raised on read from a closed pipe — expected during shutdown.
        return
