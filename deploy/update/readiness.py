"""Wait until an updated stack is really serving, the way bin/install does.

Standalone: `up -d` without --wait (the app's first boot after an image change
runs migrations under a 3600 s healthcheck start period), then bin/install's
readiness loop: the app container keeps running without a crash loop, the API
answers /health/ and the UI answers, steadily, for INSTALL_STABILITY_SECONDS.
The deadline (INSTALL_READY_TIMEOUT_SECONDS) extends while migrations are
still being applied, up to INSTALL_READY_MAX_SECONDS.

A crash loop fails fast: an app container that exited, two restarts during
the rollout (every restart of a container the rollout created counts), or a
container Docker is restarting again after two or more restarts.

Distributed and production: `up -d --wait` (as bin/install and deploy/setup.sh
do) and then /health/.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
import math
from http.client import HTTPException
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Set, TextIO, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from .install import Installation

# Restarts that make a crash loop (bin/install uses the same number).
CRASH_LOOP_RESTARTS = 2


class NotReady(RuntimeError):
    """The stack did not become ready; the message says what it waited for."""


class AutomaticNotReady(NotReady):
    """Closed automatic guard failure, never raw product diagnostics."""
    def __init__(self, reason):
        from .contracts import ReasonCode
        self.reason_code = ReasonCode(reason)
        super().__init__(self.reason_code.value)


@dataclass(frozen=True)
class RuntimeSample:
    phase: str
    current_migration: str
    postgres_progress: str
    free_bytes: int
    # full ID -> (running without restart/oom/pause, healthy, restart count, StartedAt)
    containers: Mapping[str, Tuple[bool, bool, int, str]]


class AutomaticReadiness:
    """Independent A6 deadlines and A1 continuous health/restart stability.

    estimate.migration_deadline_seconds ALREADY includes the signed safety
    factor. Do not multiply again or borrow the protected recovery budget.
    Callback data is bounded, read-only host evidence, not a release command.
    The legacy Readiness implementation below remains unchanged.
    """
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep, interval=5.0):
        self.clock, self.sleep, self.interval = clock, sleep, interval

    def wait(self, sample, *, policy, estimate, deadline, previous=False, expected_ids=(),
             startup_timeout_seconds=None, phase_started_at=None, ready_callback=None):
        from .contracts import AutomaticUpdateEstimate, ReasonCode
        def fail(reason):
            raise AutomaticNotReady(reason)
        if not previous and not isinstance(estimate, AutomaticUpdateEstimate):
            fail(ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        start = self.clock() if phase_started_at is None else phase_started_at
        if not math.isfinite(start) or start > self.clock() or not math.isfinite(deadline) or self.clock() >= deadline:
            fail(ReasonCode.READINESS_FAILED)
        migration_seconds = 0 if previous else min(estimate.migration_deadline_seconds,
                                                   policy.migration_timeout_seconds)
        migration_deadline = min(deadline, start + migration_seconds)
        startup_seconds = (policy.recovery_startup_timeout_seconds if previous else policy.wait_timeout_seconds)
        if startup_timeout_seconds is not None:
            if not math.isfinite(startup_timeout_seconds) or startup_timeout_seconds <= 0:
                fail(ReasonCode.READINESS_FAILED)
            startup_seconds = min(startup_seconds, startup_timeout_seconds)
        startup_deadline = min(deadline, start + migration_seconds + startup_seconds)
        migration_done = previous or migration_seconds == 0
        ready_since = None
        last_progress, moved_at = None, start
        stable_roster = None
        while True:
            before = self.clock()
            if before >= deadline:
                fail(ReasonCode.READINESS_FAILED)
            observed = sample()
            now = self.clock()  # Slow probes cannot authorize past a deadline.
            if not isinstance(observed, RuntimeSample):
                fail(ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            if type(observed.free_bytes) is not int or observed.free_bytes < 0:
                fail(ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            reserve = policy.disk_watermark_reserve_bytes if previous else estimate.protected_restore_reserve_bytes
            if observed.free_bytes < reserve:
                fail(ReasonCode.DISK_WATERMARK_ABORT)
            if now >= deadline:
                fail(ReasonCode.READINESS_FAILED)
            if set(observed.containers) != set(expected_ids) or not expected_ids:
                fail(ReasonCode.READINESS_FAILED)
            current = tuple(sorted((cid, row[2], row[3]) for cid, row in observed.containers.items()))
            if any(not row[0] or type(row[2]) is not int or row[2] < 0 for row in observed.containers.values()):
                fail(ReasonCode.READINESS_FAILED)
            if not previous and any(row[2] != 0 for row in observed.containers.values()):
                fail(ReasonCode.READINESS_FAILED)
            all_ready = all(row[1] for row in observed.containers.values())
            # Readiness cannot hide a restart or identity change during stability.
            if ready_since is not None and (not all_ready or current != stable_roster):
                fail(ReasonCode.READINESS_FAILED)
            migration_active = observed.phase not in ('api', 'ready') and not all_ready
            if not previous and not migration_done:
                # Completion is evidence observed NOW, not retroactive proof
                # that a migration finished before its hard deadline. In
                # particular a slow read returning healthy cannot erase expiry.
                if now >= migration_deadline:
                    fail(ReasonCode.MIGRATION_EXCEEDS_WINDOW)
                if not migration_active:
                    migration_done = True
                    startup_deadline = min(deadline, now + startup_seconds)
                else:
                    progress = (observed.phase, observed.current_migration, observed.postgres_progress)
                    if progress != last_progress:
                        last_progress, moved_at = progress, now
                    if now - moved_at >= min(policy.migration_stall_timeout_seconds,
                                             estimate.migration_stall_timeout_seconds):
                        fail(ReasonCode.MIGRATION_STALLED)
            # A zero plan still invokes Django's migrate check in bootstrap.
            # Its 'migrating' phase is bounded by ordinary startup, not treated
            # as evidence of an unqualified migration or given extra time.
            if now >= deadline or (ready_since is None and now >= startup_deadline):
                fail(ReasonCode.READINESS_FAILED)
            if all_ready and migration_done:
                if ready_since is None:
                    ready_since, stable_roster = now, current
                    if ready_callback is not None:
                        # A full observer performs its genuine stability period
                        # INSIDE this same interval, not after another wait.
                        # The callback guards the exact roster/capacity while
                        # observing. Always resample afterward: neither slow
                        # completion nor a changed roster can erase a deadline.
                        ready_callback()
                        continue
                if now - ready_since >= policy.stability_period_seconds:
                    return observed
            limits = [self.interval, max(0, deadline - self.clock())]
            if ready_since is not None:
                limits.append(max(0, policy.stability_period_seconds - (now - ready_since)))
            elif not migration_done:
                limits.extend((max(0, migration_deadline - now),
                    max(0, min(policy.migration_stall_timeout_seconds,
                        estimate.migration_stall_timeout_seconds) - (now - moved_at))))
            else:
                limits.append(max(0, startup_deadline - now))
            self.sleep(min(limits))


def bounded(env: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = env.get(key) or str(default)
    if not re.fullmatch(r"[0-9]+", raw) or not minimum <= int(raw) <= maximum:
        raise NotReady(f"{key} must be an integer in [{minimum},{maximum}] (got {raw!r})")
    return int(raw)


def standalone_settings(env: Mapping[str, str]) -> Tuple[int, int, int]:
    """bin/install's readiness settings from .env: (ready timeout, stability window, ready max) in seconds."""

    return (bounded(env, "INSTALL_READY_TIMEOUT_SECONDS", 600, 60, 1800),
            bounded(env, "INSTALL_STABILITY_SECONDS", 15, 5, 120),
            bounded(env, "INSTALL_READY_MAX_SECONDS", 3600, 300, 7200))


def http_ok(url: str, timeout: float = 3.0) -> bool:
    # No proxies: an HTTP_PROXY from the shell must not see loopback probes.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except HTTPError as exc:  # an answer, just not a good one: close it like a response
        try:
            exc.close()
        except Exception:
            pass
        return False
    except (URLError, HTTPException, OSError, ValueError):
        return False


def _number(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _restarts(count: int) -> str:
    return f"{count} restart{'' if count == 1 else 's'}"


class Readiness:
    def __init__(self, installation: Installation, out: TextIO, *, probe: Callable[[str], bool] = http_ok,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 interval: float = 5.0) -> None:
        self.installation = installation
        self.out = out
        self.probe = probe
        self.clock = clock
        self.sleep = sleep
        self.interval = interval

    def _ports(self) -> Dict[str, str]:
        env = self.installation.env
        return {"backend": env.get("BACKEND_PORT") or "8000", "frontend": env.get("FRONTEND_PORT") or "3000"}

    def _compose_hint(self) -> str:
        """The Compose command an operator types for this install (`docker compose`, or the standalone binary).

        It starts with `cd <checkout> && ` when bin/update was run from another
        directory, so the commands printed here work pasted where it ran.
        """

        return self.installation.compose_hint()

    def _migrations_applied(self) -> int:
        result = self.installation.compose_run("logs", "--no-color", "--tail", "2000", "app", check=False)
        return sum(1 for line in result.stdout.splitlines() if "Applying " in line)

    def _app_container(self, previous: Set[str]) -> Optional[Dict[str, Any]]:
        """The app container to watch: one this rollout created if there is one (docker lists the newest first)."""

        # A one-off container (`docker compose run app ...`) is not the service's container.
        app = [c for c in self.installation.containers() if c["service"] == "app" and not c.get("oneoff")]
        created = [c for c in app if c["id"] not in previous]
        return (created or app or [None])[0]

    def wait_standalone(self, previous_ids: Iterable[str] = ()) -> None:
        """Wait for a steadily serving app container.

        `previous_ids` are the app containers that existed before `up`. A
        container that is not one of them was created by this rollout, so all
        of its restarts count; one `up` kept counts from the first look.
        """

        ready_timeout, stability, ready_max = standalone_settings(self.installation.env)
        previous = set(previous_ids)
        ports = self._ports()
        start = self.clock()
        deadline, hard_deadline = start + ready_timeout, start + ready_max
        baselines: Dict[str, int] = {}
        last_signature, ready_since = "", 0.0
        migrations = self._migrations_applied()
        while True:
            now = self.clock()
            container = self._app_container(previous)
            all_ready, pending, restarts, exit_code = True, "", 0, None
            if container is None:
                all_ready, pending = False, "the app container to exist"
            else:
                count = _number(container.get("restart_count")) or 0
                baseline = baselines.setdefault(container["id"], count if container["id"] in previous else 0)
                if count < baseline:  # Docker resets the count when the container is started by hand
                    baselines[container["id"]] = baseline = count
                restarts = count - baseline
                exit_code = _number(container.get("exit_code"))
                status = container["status"]
                last_exit = f", last exit code {exit_code}" if exit_code else ""
                if status in ("exited", "dead"):
                    raise NotReady(
                        f"the app container stopped (status={status} exit_code={container.get('exit_code')})")
                if restarts >= CRASH_LOOP_RESTARTS:
                    raise NotReady(f"the app container keeps restarting ({restarts} restarts during this update{last_exit})")
                if status == "restarting" and count >= CRASH_LOOP_RESTARTS:
                    raise NotReady(f"the app container keeps restarting (Docker is restarting it again after "
                                   f"{_restarts(count)}{last_exit})")
                if status != "running":
                    all_ready, pending = False, "the app container to run"
            signature = f"{container['id']}:{container['restart_count']}:{container['started_at']}" if container else ""
            backend_ok = self.probe(f"http://localhost:{ports['backend']}/health/")
            if not backend_ok:
                all_ready = False
                pending = pending or f"the API (/health/ on port {ports['backend']})"
            if not self.probe(f"http://localhost:{ports['frontend']}/"):
                all_ready = False
                pending = pending or f"the UI on port {ports['frontend']}"
            if not backend_ok:
                applied = self._migrations_applied()
                if applied > migrations:
                    migrations = applied
                    self.out.write(f"  migrations in progress ({applied} applied), extending the readiness window\n")
                    deadline = min(now + ready_timeout, hard_deadline)
            if all_ready:
                if signature != last_signature:
                    last_signature, ready_since = signature, now
                elif now - ready_since >= stability:
                    return
            else:
                last_signature, ready_since = "", 0.0
            if now >= deadline:
                if container is not None and (restarts or exit_code):
                    details = ([f"exit code {exit_code}"] if exit_code else []) + [_restarts(restarts)]
                    raise NotReady(
                        f"the stack did not become ready in time: the app container keeps crashing "
                        f"({', '.join(details)}). Its log says why: `{self._compose_hint()} logs app`")
                raise NotReady(
                    f"the stack did not become ready in time, still waiting on {pending or 'a stable app container'}. "
                    f"It may still be starting: watch `{self._compose_hint()} logs -f app`, or raise "
                    "INSTALL_READY_MAX_SECONDS (up to 7200) in .env")
            self.sleep(self.interval)

    def wait_api(self, seconds: int = 180) -> None:
        port = self._ports()["backend"]
        deadline = self.clock() + seconds
        while not self.probe(f"http://localhost:{port}/health/"):
            if self.clock() >= deadline:
                raise NotReady(f"the API did not answer /health/ on port {port} within {seconds}s")
            self.sleep(self.interval)
