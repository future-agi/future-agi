"""Standing an engine up in a container, which is the part no engine does differently.

Pulling an image, giving it a free port, waiting for it to actually answer, tearing it down
and not leaking it when a run is killed: none of that is about Postgres. It is the same work
for MySQL, ClickHouse, Mongo or anything else the harness is ever asked to run, so it is
written once here.

What an engine contributes is only what genuinely differs -- how to reach it, how to read what
it holds, and how to put that back. That is a small surface deliberately, because the cost of
teaching the harness a new engine is the thing that decides whether "whatever the agent uses"
is real or just an aspiration.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import time

from . import Held, StoreError

# How long to wait for a fresh container to start answering. The first run on a machine pulls
# the image, which dominates; afterwards this is a second or two.
READY_TIMEOUT_SECONDS = 180.0

# Marks every container this module starts, so strays from a killed run can be found and
# removed without guessing at names.
LABEL = "alk.harness.store"

# The network to join, when the harness is itself in a container. Publishing a port to the
# host's loopback is enough when the harness runs on the host, but from inside a container
# 127.0.0.1 is its own loopback and the engine is not there. Sharing a network instead lets
# the engine be reached by container name, on the port it actually listens on.
NETWORK = "ALK_DOCKER_NETWORK"


def docker(*args: str, check: bool = True) -> str:
    """Run a docker command, and turn its failure into something worth reading."""
    try:
        done = subprocess.run(  # nosec B603: list args, never shell=True
            ("docker", *args), capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on the machine
        raise StoreError(
            "docker is not on PATH, so no store can be stood up. Install Docker, or start "
            "Colima, and try again."
        ) from exc
    if check and done.returncode != 0:
        raise StoreError(
            f"docker {' '.join(args)} failed ({done.returncode}): "
            f"{(done.stderr or done.stdout).strip()}"
        )
    return done.stdout.strip()


class ContainerStore(Held):
    """An engine the harness runs in a container for the agent to be pointed at.

    Started once for a suite and reset between scenarios: standing an engine up costs seconds
    and putting its data back costs milliseconds, so the container stays and only its contents
    move.

    Subclasses supply ``image``, ``container_port``, the environment the image needs, and how
    to read and restore what it holds. Everything else is here.
    """

    engine: str = ""
    image: str = ""
    container_port: int = 0
    # Environment the image needs to come up with a known user, password and database. Values
    # are formatted with ``user``, ``password`` and ``database``.
    boot_env: dict[str, str] = {}

    def __init__(
        self,
        version: str | None = None,
        image: str | None = None,
        database: str = "alk",
        user: str = "alk",
        password: str | None = None,
    ) -> None:
        default = type(self).image
        if image:
            self.image = image
        elif version:
            self.image = f"{default.split(':')[0]}:{version}"
        else:
            self.image = default
        self.database = database
        self.user = user
        self.password = password or secrets.token_hex(16)
        self.container = f"alk-store-{secrets.token_hex(6)}"
        self.network = os.environ.get(NETWORK, "").strip()
        self.host = "127.0.0.1"
        self.port: int | None = None
        self._started = False
        # Every script `apply` has run, in order. Saved beside the rows so a restore into a
        # fresh container can stand the schema up before putting the rows back.
        self.applied: list[str] = []

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        """Stand the container up and block until it answers. Idempotent."""
        if self._started:
            return
        environment: list[str] = []
        for name, template in self.boot_env.items():
            environment += [
                "--env",
                f"{name}={template.format(user=self.user, password=self.password, database=self.database)}",
            ]
        docker(
            "run",
            "--detach",
            "--name",
            self.container,
            "--label",
            f"{LABEL}=1",
            *environment,
            *(("--network", self.network) if self.network else ()),
            # Bound to loopback and given whatever port is free, so parallel runs on one
            # machine never collide. Kept even on a shared network, where it is what lets
            # someone on the host open a client against a running scenario.
            "--publish",
            f"127.0.0.1::{self.container_port}",
            self.image,
        )
        self._started = True
        if self.network:
            self.host, self.port = self.container, self.container_port
        else:
            self.port = self._published_port()
        self._await_ready()

    def stop(self) -> None:
        """Remove the container. Safe when it never started, so teardown needs no guard."""
        if not self._started:
            return
        docker("rm", "--force", "--volumes", self.container, check=False)
        self._started = False
        self.port = None

    def _published_port(self) -> int:
        mapping = docker("port", self.container, f"{self.container_port}/tcp")
        if not mapping:
            raise StoreError(
                f"{self.container} published no port for {self.container_port}/tcp"
            )
        # "127.0.0.1:32768", or several lines when both stacks are bound.
        return int(mapping.splitlines()[0].rsplit(":", 1)[1])

    def _await_ready(self) -> None:
        """Poll until the engine answers, and say what went wrong if it never does.

        A container that is running is not an engine that is ready: most database images start,
        run their own initialisation, restart once, and only then listen. Connecting is the
        only honest test, which is why this asks the subclass to really connect rather than
        checking that the process exists.
        """
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.probe()
                return
            except Exception as exc:  # noqa: BLE001 - any failure means not ready yet
                last = exc
                time.sleep(0.25)
        logs = docker("logs", "--tail", "20", self.container, check=False)
        raise StoreError(
            f"{self.container} did not answer within {READY_TIMEOUT_SECONDS:.0f}s: {last}\n"
            f"last lines of its log:\n{logs}"
        )

    def probe(self) -> None:
        """Really talk to the engine. Anything raised means "not ready yet"."""
        raise NotImplementedError

    # -- what the agent is pointed at ------------------------------------------------

    def dsn(self) -> str:
        """The connection string to hand the agent, in place of its own."""
        raise NotImplementedError

    def env(self, variable: str) -> dict[str, str]:
        """The DSN under the name this agent reads it from.

        Redirecting an agent is usually one environment variable, and which one is a fact about
        the agent rather than about us -- so it is named by the caller, never assumed here.
        """
        return {variable: self.dsn()}

    def address(self) -> tuple[str, int]:
        # Started on first demand: nothing else owns the moment a store becomes
        # needed, and a world whose store never comes up refuses every schema,
        # seed and handler with an address error nothing in the stage explains.
        if not self._started:
            self.start()
        if not self._started or self.port is None:
            raise StoreError("the store has not been started, so it has no address yet")
        return self.host, self.port


def strays() -> list[str]:
    """Containers the harness started that are still running.

    A killed run leaves its container behind, and the next one has no way to know it is not the
    owner. Naming them is enough; removing them is the caller's decision.
    """
    listed = docker(
        "ps", "--filter", f"label={LABEL}=1", "--format", "{{.Names}}", check=False
    )
    return [name for name in listed.splitlines() if name.strip()]
