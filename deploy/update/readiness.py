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
from http.client import HTTPException
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Set, TextIO, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from .install import Installation

# Restarts that make a crash loop (bin/install uses the same number).
CRASH_LOOP_RESTARTS = 2


class NotReady(RuntimeError):
    """The stack did not become ready; the message says what it waited for."""


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
