"""Render only: no crontab/systemctl installation, enabling or policy changes.

Raw command/hook output is discarded, not mailed by cron or sent to a public
journal. The runner's private, bounded event store is the scheduler log (1000
closed-schema records); operator diagnostic capture is a separate explicit job.
"""
from __future__ import annotations

import os
import pwd
import shlex
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType
from typing import Mapping

from .contracts import AutomationPolicy, ReasonCode
from .runner_state import Registration, RunnerStateStore, StoreError


@dataclass(frozen=True)
class RenderedAssets:
    name: str
    files: Mapping[str, str]
    removal_instructions: str


def _shell_word(value: str) -> str:
    # cron treats % specially EVEN inside shell quotes. Avoid it altogether:
    # interpolate a literal percent via printf's octal escape at shell runtime.
    # All surrounding chunks use shell quoting; no user input becomes code.
    return '"$(printf \'\\045\')"'.join(shlex.quote(part) for part in value.split("%"))


def _systemd_word(value: str, *, command: bool = False) -> str:
    # systemd is NOT a shell. %% escapes specifiers, $$ escapes ExecStart's
    # environment substitution; Environment/WorkingDirectory do not expand $.
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def _template(relative: str, **values: str) -> str:
    return Template((Path(__file__).parent / relative).read_text(encoding="utf-8")).substitute(values)


def _validate(registration: Registration) -> None:
    # Re-run strict private validation even for direct in-memory callers.
    registration.__post_init__()
    registration.launcher.__post_init__()
    binding, launch = registration.binding, registration.launcher
    if binding.host_uid != os.geteuid():
        raise StoreError(ReasonCode.OWNERSHIP_UNCONFIRMED)
    try:
        if pwd.getpwnam(launch.user).pw_uid != binding.host_uid:
            raise StoreError(ReasonCode.OWNERSHIP_UNCONFIRMED)
        if not launch.home.is_dir() or launch.home.stat().st_uid != binding.host_uid:
            raise StoreError(ReasonCode.OWNERSHIP_UNCONFIRMED)
        for executable in (launch.python, binding.checkout / "bin" / "update", Path("/bin/bash"), Path("/usr/bin/env")):
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise StoreError(ReasonCode.SCHEDULER_UNREGISTERED)
    except (OSError, KeyError):
        raise StoreError(ReasonCode.SCHEDULER_UNREGISTERED) from None


def render_scheduler(store: RunnerStateStore, policy: AutomationPolicy, *, backend: str) -> RenderedAssets:
    """Requires persisted registration, but never enables policy or creates files.

    Absolute interpreter and PATH were captured by explicit registration. No
    ambient PATH, PYTHON, Docker host, Compose flags or shell startup files leak
    into the source-checkout launch. Always dispatch via absolute /bin/bash,
    bypassing bin/update's env-based shebang lookup.
    """
    if backend not in ("cron", "systemd"):
        raise StoreError()
    registration = store.read_registration()
    _validate(registration)
    binding, launch = registration.binding, registration.launcher
    name = "futureagi-compose-update-" + binding.selection_hash
    argv = ["/usr/bin/env", "-i", "HOME=" + str(launch.home), "PATH=" + launch.path,
            "PYTHON=" + str(launch.python), "DOCKER_CONTEXT=" + binding.docker_context,
            "/bin/bash", "--", str(binding.checkout / "bin" / "update"), "run", "--env-file", str(binding.env_alias)]
    if binding.production:
        argv.append("--production")
    if backend == "cron":
        command = "umask 077; " + " ".join(_shell_word(arg) for arg in argv) + " </dev/null >/dev/null 2>&1"
        # Classic cron implementations bound command lines at 1000 bytes.
        # Refuse rather than emit a truncated/injectable or unrunnable entry.
        if len(("*/5 * * * * " + command).encode("utf-8")) >= 1000:
            raise StoreError(ReasonCode.SCHEDULER_UNREGISTERED)
        content = _template("cron/futureagi-compose-update.cron.in", name=name, command=command)
        return RenderedAssets(name, MappingProxyType({name + ".cron": content}),
                              "Remove only the crontab entry between the BEGIN/END " + name
                              + " markers. Disable durable automation separately; do not delete private evidence.")
    service = _template("systemd/futureagi-compose-update.service.in", name=name, user=launch.user,
                        # WorkingDirectory is a raw scalar, NOT an argv word.
                        # /. keeps trailing path spaces/backslashes away from
                        # config-parser trimming and line continuation.
                        working_directory=(str(binding.checkout) + "/.").replace("%", "%%"),
                        environment=" ".join(_systemd_word(arg) for arg in argv[2:6]),
                        command=" ".join(_systemd_word(arg, command=True) for arg in argv),
                        timeout=str(policy.tick_timeout_seconds + 660))
    timer = _template("systemd/futureagi-compose-update.timer.in", name=name)
    return RenderedAssets(name, MappingProxyType({name + ".service": service, name + ".timer": timer}),
                          "Under normal OS authority, stop and disable only " + name + ".timer, remove only "
                          + name + ".timer and " + name + ".service, then reload the unit manager. "
                          "Disable durable automation separately; do not delete private evidence.")
