"""Explicit host scheduler activation; never enrollment, policy enablement or sudo.

Call outside runner/update locks: mutations take runner.lock, with short
state.lock ledger transactions and NO subprocesses inside metadata locks. Setup
must keep durable controls disabled until enrollment/backup/capacity prerequisites
and activation are verified. Removal does not disable policy or erase evidence.

Cron's read/compare/replace is NOT atomic CAS. The runner lock serializes our
callers, not crontab editors. We rebase on observed edits and verify afterwards;
an external edit in crontab(1)'s final replacement gap cannot be protected by its
API. Systemd uses create-only files/links, never `enable --force` or broad disable.
Native Windows is unsupported. User systemd requires ALREADY enabled linger;
we never change host login policy. A running non-systemd cron is usable while
that host/WSL distribution runs; this does not promise to boot a stopped WSL VM.

Private _Host is a finite test seam, not a CLI/release-controlled path/command
extension point. Real defaults use fixed unit directories and registered PATH.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Tuple

from .contracts import AutomationPolicy, ReasonCode
from .runner_state import RunnerStateStore, StoreBusy, StoreError, _wire
from .scheduler_assets import render_scheduler

LEDGER = "scheduler-activation.json"
MAX_OUTPUT = 1024 * 1024
TIMEOUT = 15
CRON_SERVICES = ("cron.service", "crond.service", "cron", "crond", "com.vix.cron")
INSTALL = {"cron": ("crontab",), "systemd": ("service", "timer", "wants", "enable", "reload", "start")}
REMOVE = {"cron": ("remove-crontab",), "systemd": ("stop", "unlink-enable", "unlink-timer", "unlink-service", "reload-remove")}


class ActivationReason(str, Enum):
    UNSUPPORTED = "scheduler_unsupported"
    REGISTRATION = "scheduler_registration_required"
    AUTHORITY = "scheduler_authority_required"
    DEPENDENCY = "scheduler_dependency_missing"
    UNSAFE = "scheduler_unsafe_path"
    FOREIGN = "scheduler_foreign_state"
    MANAGER = "scheduler_manager_unavailable"
    CRON = "scheduler_cron_unavailable"
    LINGER = "scheduler_user_persistence_required"
    ABSENT = "scheduler_not_installed"
    INACTIVE = "scheduler_installed_inactive"
    CHANGED = "scheduler_configuration_changed"
    CONCURRENT = "scheduler_concurrent_change"
    COMMAND = "scheduler_command_failed"
    UNCERTAIN = "scheduler_command_uncertain"
    METADATA = "scheduler_metadata_invalid"
    BUSY = "scheduler_busy"
    REMOVED = "scheduler_removed"
    RECOVERY = "automatic_recovery_pending"


_ACTIONS = {
    ActivationReason.UNSUPPORTED: "Select cron on macOS/Linux/WSL, or systemd on Linux; native Windows is unsupported.",
    ActivationReason.REGISTRATION: "Register the original installation user and verified host launcher first.",
    ActivationReason.AUTHORITY: "Provide normal noninteractive host authority for the selected scheduler; no elevation is attempted.",
    ActivationReason.DEPENDENCY: "Provision the fixed scheduler utilities in the registered trusted host PATH.",
    ActivationReason.UNSAFE: "Provide real trusted scheduler directories/files without symlinks or foreign write access.",
    ActivationReason.FOREIGN: "Inspect conflicting or edited owned-name assets; nothing foreign is overwritten or adopted.",
    ActivationReason.MANAGER: "Make the selected service manager available under the original host identity.",
    ActivationReason.CRON: "Provide a genuinely running cron daemon; rendering an entry does not start cron.",
    ActivationReason.LINGER: "Arrange persistent user-manager operation explicitly, or select a system timer/running cron; linger is not changed.",
    ActivationReason.ABSENT: "Run explicit scheduler setup after enrollment and backup prerequisites.",
    ActivationReason.INACTIVE: "Inspect the installed scheduler and restore its availability explicitly; completed starts are not replayed.",
    ActivationReason.CHANGED: "Use the original registered launcher/backend and exact settings; inspect drift before changing ownership.",
    ActivationReason.CONCURRENT: "Retry after the concurrent scheduler edit; unrelated current entries are retained.",
    ActivationReason.COMMAND: "Inspect scheduler authority/availability privately, then retry; raw command output is suppressed.",
    ActivationReason.UNCERTAIN: "Inspect the interrupted manager action; its outcome is not known and it will not be replayed blindly.",
    ActivationReason.METADATA: "Inspect private activation evidence; malformed records are never reset automatically.",
    ActivationReason.BUSY: "Retry after the other runner/setup transaction finishes.",
    ActivationReason.REMOVED: "Activation was explicitly removed; retained evidence is not automatically reset or reinstalled.",
    ActivationReason.RECOVERY: "Forward starts are disabled. Keep the safety scheduler until automatic recovery is terminal; then repeat deactivation.",
}


@dataclass(frozen=True)
class SchedulerActivation:
    state: str  # active | incomplete | removed; scheduler only, NOT policy eligibility
    backend: str
    name: str
    reason: Optional[ActivationReason] = None
    action: str = ""

    @property
    def active(self) -> bool:
        return self.state == "active"


class _Incomplete(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason.value)


def _need(ok, reason=ActivationReason.FOREIGN):
    if not ok:
        raise _Incomplete(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _registration_hash(reg):
    return _sha(json.dumps(_wire(reg), sort_keys=True, separators=(",", ":")).encode())


@dataclass(frozen=True)
class _Asset:
    name: str
    content: str

    def __post_init__(self):
        _need(type(self.name) is str and len(self.name) <= 128
              and type(self.content) is str and 0 < len(self.content.encode()) < 24000,
              ActivationReason.METADATA)


@dataclass(frozen=True)
class _Ledger:
    schema_version: int
    selection_hash: str
    registration_hash: str
    backend: str
    scope: str
    cron_service: str
    directory: str
    assets: Tuple[_Asset, ...]
    phase: str
    completed: Tuple[str, ...] = ()
    intended: Tuple[str, ...] = ()  # Retained even when removal interrupts installation.
    removed: Tuple[str, ...] = ()
    pending: Optional[str] = None
    cron_before_sha256: str = ""
    cron_before_size: int = 0
    cron_separator: bool = False

    def __post_init__(self):
        reason = ActivationReason.METADATA
        _need(type(self.schema_version) is int and self.schema_version == 1, reason)
        for value in (self.selection_hash, self.registration_hash):
            _need(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, reason)
        _need(self.backend in INSTALL and self.phase in ("installing", "active", "removing", "removed"), reason)
        _need(self.scope in (("current-user",) if self.backend == "cron" else ("system", "user")), reason)
        _need(self.cron_service in CRON_SERVICES if self.backend == "cron" else self.cron_service == "", reason)
        _need(self.directory == "" if self.backend == "cron" else
              Path(self.directory).is_absolute() and len(self.directory) < 4096, reason)
        suffixes = ("cron",) if self.backend == "cron" else ("service", "timer")
        _need(tuple(a.name for a in self.assets) == tuple(self.name + "." + s for s in suffixes), reason)
        _need(self.completed == INSTALL[self.backend][:len(self.completed)]
              and self.removed == REMOVE[self.backend][:len(self.removed)], reason)
        _need(len(self.completed) <= len(INSTALL[self.backend]) and len(self.removed) <= len(REMOVE[self.backend]), reason)
        _need(self.intended == INSTALL[self.backend][:len(self.intended)]
              and len(self.completed) <= len(self.intended) <= min(len(self.completed) + 1, len(INSTALL[self.backend])), reason)
        remaining = (REMOVE[self.backend][len(self.removed):] if self.phase in ("removing", "removed")
                     else INSTALL[self.backend][len(self.completed):])
        _need(self.pending is None or bool(remaining) and self.pending == remaining[0], reason)
        if self.phase in ("installing", "active"):
            _need(not self.removed and len(self.intended) == len(self.completed) + (self.pending is not None), reason)
        if self.phase == "active":
            _need(self.completed == INSTALL[self.backend] and self.pending is None, reason)
        if self.phase == "removed":
            _need(self.removed == REMOVE[self.backend] and self.pending is None, reason)
        if self.backend == "cron":
            _need(re.fullmatch(r"[0-9a-f]{64}", self.cron_before_sha256) is not None, reason)
        else:
            _need(self.cron_before_sha256 == "" and self.cron_before_size == 0 and not self.cron_separator, reason)
        _need(type(self.cron_before_size) is int and 0 <= self.cron_before_size <= MAX_OUTPUT, reason)
        _need(type(self.cron_separator) is bool, reason)

    @property
    def name(self):
        return "futureagi-compose-update-" + self.selection_hash


@dataclass(frozen=True)
class _Host:
    platform: str = sys.platform
    unit_directory: Optional[Path] = None
    runtime_directory: Optional[Path] = None
    run: Callable = subprocess.run


def _read(store):
    with store._directory() as directory:
        try:
            return store._read(directory, LEDGER, _Ledger)
        except FileNotFoundError:
            return None


def _save(store, old, new):
    # The only ledger writer. Keep commands and filesystem installation outside.
    with store._lock("state.lock") as directory:
        _need(_read(store) == old, ActivationReason.CONCURRENT)
        _need(_registration_hash(store.read_registration()) == new.registration_hash, ActivationReason.CHANGED)
        store._write(directory, LEDGER, new)
    return new


def _trusted(path, *, directory=False):
    """Check real ancestry. Executable symlinks are resolved BEFORE calling this."""
    _need(path.is_absolute(), ActivationReason.UNSAFE)
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        _need(not stat.S_ISLNK(info.st_mode) and info.st_uid in (0, os.geteuid())
              and not info.st_mode & 0o022, ActivationReason.UNSAFE)
        _need(stat.S_ISDIR(info.st_mode) if part != path or directory else stat.S_ISREG(info.st_mode),
              ActivationReason.UNSAFE)
    return info


@contextmanager
def _directory(path):
    before = _trusted(path, directory=True)
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        after = os.fstat(fd)
        _need((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino), ActivationReason.CONCURRENT)
        yield fd
    finally:
        os.close(fd)


class _Manager:
    def __init__(self, registration, host, scope):
        self.reg, self.host, self.scope = registration, host, scope
        launch = registration.launcher
        self.env = {"HOME": str(launch.home), "PATH": launch.path, "USER": launch.user,
                    "LOGNAME": launch.user, "LC_ALL": "C", "LANG": "C"}
        for part in launch.path.split(":"):
            _trusted(Path(part).resolve(strict=True), directory=True)
        if scope == "user":
            runtime = host.runtime_directory or Path("/run/user") / str(registration.binding.host_uid)
            info = _trusted(runtime, directory=True)
            _need(info.st_uid == registration.binding.host_uid and stat.S_IMODE(info.st_mode) == 0o700,
                  ActivationReason.UNSAFE)
            self.env.update(XDG_RUNTIME_DIR=str(runtime), DBUS_SESSION_BUS_ADDRESS="unix:path=" + str(runtime / "bus"))

    def call(self, tool, *args, data=None):
        executable = shutil.which(tool, path=self.reg.launcher.path)
        _need(executable is not None, ActivationReason.DEPENDENCY)
        executable = Path(executable).resolve(strict=True)
        _trusted(executable)
        try:
            stdin = {"stdin": subprocess.DEVNULL} if data is None else {"input": data}
            result = self.host.run([str(executable), *args], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=self.env, cwd=str(self.reg.launcher.home),
                                   timeout=TIMEOUT, check=False, **stdin)
        except (OSError, subprocess.SubprocessError):
            raise _Incomplete(ActivationReason.COMMAND) from None
        _need(type(result.returncode) is int and type(result.stdout) is bytes and type(result.stderr) is bytes
              and len(result.stdout) <= MAX_OUTPUT and len(result.stderr) <= MAX_OUTPUT, ActivationReason.COMMAND)
        return result

    def systemctl(self, *args):
        return self.call("systemctl", "--" + self.scope, "--no-pager", "--no-ask-password", *args)

    def available(self, *, persistent=True):
        result = self.systemctl("show", "--property=Version", "--value")
        _need(result.returncode == 0 and re.fullmatch(rb"[0-9][^\r\n]{0,128}\n?", result.stdout) is not None,
              ActivationReason.MANAGER)
        if self.scope == "user" and persistent:
            result = self.call("loginctl", "--no-pager", "show-user", str(self.reg.binding.host_uid),
                               "--property=Linger", "--value")
            _need(result.returncode == 0 and result.stdout.strip() == b"yes", ActivationReason.LINGER)

    def cron_available(self, service):
        if service == "com.vix.cron":
            _need(self.host.platform == "darwin", ActivationReason.UNSUPPORTED)
            result = self.call("launchctl", "print", "system/com.vix.cron")
            _need(result.returncode == 0 and re.search(rb"(?m)^\s*state = running\s*$", result.stdout)
                  and re.search(rb"(?m)^\s*pid = [1-9][0-9]*\s*$", result.stdout), ActivationReason.CRON)
            disabled = self.call("launchctl", "print-disabled", "system")
            _need(disabled.returncode == 0 and not re.search(rb'"com\.vix\.cron"\s*=>\s*true', disabled.stdout),
                  ActivationReason.CRON)
        elif service in ("cron", "crond"):
            _need(self.host.platform.startswith("linux"), ActivationReason.UNSUPPORTED)
            result = self.call("pgrep", "-x", "-u", "0", service)
            _need(result.returncode == 0 and re.fullmatch(rb"(?:[1-9][0-9]*\n)+", result.stdout) is not None,
                  ActivationReason.CRON)
        else:
            _need(self.host.platform.startswith("linux"), ActivationReason.UNSUPPORTED)
            for verb, expected in (("is-active", b"active"), ("is-enabled", b"enabled")):
                result = self.systemctl(verb, service)
                _need(result.returncode == 0 and result.stdout.strip() == expected, ActivationReason.CRON)

    def crontab(self):
        result = self.call("crontab", "-l")  # NEVER -u, even when an ambient sudo variable exists.
        if result.returncode == 0:
            _need(b"\0" not in result.stdout, ActivationReason.FOREIGN)
            return result.stdout
        missing = ("no crontab for " + self.reg.launcher.user).encode()
        _need(result.returncode == 1 and not result.stdout
              and result.stderr.strip() in (missing, b"crontab: " + missing), ActivationReason.COMMAND)
        return b""

    def unit(self, name):
        keys = ("LoadState", "ActiveState", "SubState", "UnitFileState", "FragmentPath", "DropInPaths", "NeedDaemonReload", "Job")
        result = self.systemctl("show", name, "--property=" + ",".join(keys))
        _need(result.returncode in (0, 4), ActivationReason.MANAGER)
        try:
            pairs = [line.split("=", 1) for line in result.stdout.decode("utf-8").splitlines()]
            value = dict(pairs)
        except (ValueError, UnicodeError):
            raise _Incomplete(ActivationReason.MANAGER) from None
        _need(len(pairs) == len(keys) and set(value) == set(keys), ActivationReason.MANAGER)
        return value


def _cron_shell(raw):
    # A table-level SHELL can defeat an otherwise correct entry (e.g. /bin/false)
    # before env -i runs. Do not rewrite unrelated environment directives. Only
    # the standard cron /bin/sh grammar is supported, and it cannot source an
    # ambient BASH_ENV noninteractively as an invoked bash shell could.
    for line in raw.splitlines():
        match = re.fullmatch(rb"\s*SHELL\s*=\s*(.*?)\s*", line)
        if match:
            shell = match[1]
            if len(shell) >= 2 and shell[:1] == shell[-1:] and shell[:1] in (b"'", b'"'):
                shell = shell[1:-1]
            _need(shell == b"/bin/sh", ActivationReason.CRON)


def _cron_span(raw, ledger):
    name = ledger.name.encode()
    lines = raw.splitlines(keepends=True)
    hits, offset = [], 0
    for line in lines:
        if name in line and (b"BEGIN" in line or b"END" in line):
            _need(line in (b"# BEGIN " + name + b"\n", b"# END " + name + b"\n"))
            hits.append((offset, offset + len(line), line))
        offset += len(line)
    if not hits:
        return None
    _need(len(hits) == 2 and hits[0][2].startswith(b"# BEGIN ") and hits[1][2].startswith(b"# END "))
    start, end = hits[0][0], hits[1][1]
    _need(raw[start:end] == ledger.assets[0].content.encode())
    return start, end


def _cron_change(store, ledger, manager, *, remove=False):
    step = "remove-crontab" if remove else "crontab"
    for _ in range(3):
        raw = manager.crontab()
        span = _cron_span(raw, ledger)
        if not remove:
            _cron_shell(raw if span is None else raw[:span[0]])
        if (span is None) == remove:
            # Exact postimage observed after an interrupted call: don't repeat it.
            _need(ledger.pending == step, ActivationReason.FOREIGN)
            break
        if remove:
            start, end = span
            prefix = raw[:start]
            if (ledger.cron_separator and prefix.endswith(b"\n")
                    and len(prefix) - 1 == ledger.cron_before_size
                    and _sha(prefix[:-1]) == ledger.cron_before_sha256):
                prefix = prefix[:-1]
            candidate = prefix + raw[end:]
        else:
            _need(span is None)
            separator = bool(raw and not raw.endswith(b"\n"))
            new = replace(ledger, cron_before_sha256=_sha(raw), cron_before_size=len(raw), cron_separator=separator)
            if new != ledger:
                ledger = _save(store, ledger, new)
            candidate = raw + (b"\n" if separator else b"") + ledger.assets[0].content.encode()
        _need(len(candidate) <= MAX_OUTPUT, ActivationReason.FOREIGN)
        if manager.crontab() != raw:
            continue
        result = manager.call("crontab", "-", data=candidate)
        _need(result.returncode == 0, ActivationReason.COMMAND)
        actual = manager.crontab()
        found = _cron_span(actual, ledger)
        _need((found is None) == remove, ActivationReason.CONCURRENT)
        break
    else:
        raise _Incomplete(ActivationReason.CONCURRENT)
    return ledger


def _file(fd, name):
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(handle)
        _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
              and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink in (1, 2) and info.st_size < 24000,
              ActivationReason.UNSAFE)
        with os.fdopen(os.dup(handle), "rb") as stream:
            raw = stream.read(24000)
        after = os.fstat(handle)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        _need(all(getattr(after, key) == getattr(info, key) for key in stable), ActivationReason.CONCURRENT)
        return raw, info.st_dev, info.st_ino, info.st_nlink
    finally:
        os.close(handle)


def _asset(fd, asset, *, staged=False):
    value = _file(fd, asset.name)
    temp = _file(fd, "." + asset.name + ".setup")
    if value is not None:
        _need(value[0] == asset.content.encode())
        _need(value[3] == 1 or staged and temp is not None and value[1:3] == temp[1:3], ActivationReason.UNSAFE)
    if temp is not None:
        _need(staged and temp[0] == asset.content.encode() and (value is None or value[1:3] == temp[1:3]))
    return value, temp


def _install_file(fd, asset):
    value, temp = _asset(fd, asset, staged=True)
    temporary = "." + asset.name + ".setup"
    if value is None:
        if temp is None:
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
            with os.fdopen(handle, "wb") as stream:
                stream.write(asset.content.encode())
                stream.flush()
                from .envcommit import sync_file
                sync_file(stream.fileno())
            os.fsync(fd)
        # Atomic create-only publication, including on separate /home filesystems.
        os.link(temporary, asset.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    if _file(fd, temporary) is not None:
        _asset(fd, asset, staged=True)
        os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)
    _asset(fd, asset)


def _link(ledger):
    directory = Path(ledger.directory) / "timers.target.wants"
    if not directory.exists() and not directory.is_symlink():
        return False
    with _directory(directory) as fd:
        try:
            info = os.stat(ledger.name + ".timer", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        _need(stat.S_ISLNK(info.st_mode) and info.st_uid == os.geteuid()
              and os.readlink(ledger.name + ".timer", dir_fd=fd) == str(Path(ledger.directory) / (ledger.name + ".timer")))
        return True


def _units(manager, ledger, *, missing=False, reloaded=False):
    states = []
    for asset in ledger.assets:
        value = manager.unit(asset.name)
        _need(value["Job"] == "0", ActivationReason.UNCERTAIN)
        if value["LoadState"] == "not-found" and missing:
            _need(value["FragmentPath"] == "" and value["ActiveState"] == "inactive"
                  and value["UnitFileState"] in ("", "disabled") and not value["DropInPaths"])
        else:
            _need(value["LoadState"] == "loaded" and value["FragmentPath"] == str(Path(ledger.directory) / asset.name)
                  and not value["DropInPaths"])
        if reloaded:
            _need(value["NeedDaemonReload"] == "no", ActivationReason.INACTIVE)
        states.append(value)
    return states


def _systemd_files(ledger, *, partial=False):
    with _directory(Path(ledger.directory)) as fd:
        for suffix, asset in zip(("service", "timer"), ledger.assets):
            value, temp = _asset(fd, asset, staged=partial and ledger.pending == suffix)
            if not partial:
                _need(value is not None and temp is None, ActivationReason.INACTIVE)
            elif value is not None or temp is not None:
                _need(suffix in ledger.intended)
        linked = _link(ledger)
        if not partial:
            _need(linked, ActivationReason.INACTIVE)
        elif linked:
            _need("enable" in ledger.intended)


def _systemd_step(ledger, manager, step, fresh):
    root = Path(ledger.directory)
    if step in ("service", "timer"):
        with _directory(root) as fd:
            _install_file(fd, ledger.assets[0 if step == "service" else 1])
    elif step == "wants":
        with _directory(root) as fd:
            try:
                os.mkdir("timers.target.wants", 0o755, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
        _trusted(root / "timers.target.wants", directory=True)
    elif step == "enable":
        if not _link(ledger):
            with _directory(root / "timers.target.wants") as fd:
                os.symlink(str(root / (ledger.name + ".timer")), ledger.name + ".timer", dir_fd=fd)
                os.fsync(fd)
        _need(_link(ledger))
    elif step == "reload":
        states = _units(manager, ledger, missing=True)
        if not all(s["LoadState"] == "loaded" and s["NeedDaemonReload"] == "no" for s in states):
            _need(manager.systemctl("daemon-reload").returncode == 0, ActivationReason.COMMAND)
        _units(manager, ledger, reloaded=True)
    elif step == "start":
        _systemd_files(ledger)
        timer = _units(manager, ledger, reloaded=True)[1]
        _need(timer["UnitFileState"] == "enabled", ActivationReason.INACTIVE)
        if timer["ActiveState"] != "active":
            _need(timer["ActiveState"] == "inactive" and fresh, ActivationReason.UNCERTAIN)
            _need(manager.systemctl("start", ledger.name + ".timer").returncode == 0, ActivationReason.COMMAND)
        _verify_systemd(ledger, manager)


def _verify_systemd(ledger, manager):
    _systemd_files(ledger)
    timer = _units(manager, ledger, reloaded=True)[1]
    _need(timer["UnitFileState"] == "enabled" and timer["ActiveState"] == "active"
          and timer["SubState"] in ("waiting", "running"), ActivationReason.INACTIVE)


def _remove_systemd(ledger, manager, step, fresh):
    root = Path(ledger.directory)
    if step == "stop":
        timer = _units(manager, ledger, missing=True)[1]
        if timer["ActiveState"] != "inactive":
            _need(timer["ActiveState"] == "active" and fresh, ActivationReason.UNCERTAIN)
            _need(manager.systemctl("stop", ledger.name + ".timer").returncode == 0, ActivationReason.COMMAND)
            _need(_units(manager, ledger, missing=True)[1]["ActiveState"] == "inactive", ActivationReason.INACTIVE)
    elif step == "unlink-enable":
        if _link(ledger):
            with _directory(root / "timers.target.wants") as fd:
                os.unlink(ledger.name + ".timer", dir_fd=fd)
                os.fsync(fd)
    elif step in ("unlink-timer", "unlink-service"):
        asset = ledger.assets[1 if step == "unlink-timer" else 0]
        with _directory(root) as fd:
            value, temp = _asset(fd, asset, staged=True)
            if temp is not None:
                os.unlink("." + asset.name + ".setup", dir_fd=fd)
            if value is not None:
                os.unlink(asset.name, dir_fd=fd)
            os.fsync(fd)
    elif step == "reload-remove":
        states = _units(manager, ledger, missing=True)
        if not all(s["LoadState"] == "not-found" and s["NeedDaemonReload"] == "no" for s in states):
            _need(manager.systemctl("daemon-reload").returncode == 0, ActivationReason.COMMAND)
        timer = _units(manager, ledger, missing=True)[1]
        _need(timer["ActiveState"] == "inactive" and timer["UnitFileState"] != "enabled", ActivationReason.INACTIVE)


def _location(reg, host, backend, scope, cron_service):
    _need(host.platform == "darwin" or host.platform.startswith("linux"), ActivationReason.UNSUPPORTED)
    if backend == "cron":
        service = cron_service or ("com.vix.cron" if host.platform == "darwin" else "cron.service")
        _need(service in CRON_SERVICES, ActivationReason.UNSUPPORTED)
        return "current-user", service, ""
    _need(backend == "systemd" and host.platform.startswith("linux") and scope in ("system", "user")
          and cron_service is None, ActivationReason.UNSUPPORTED)
    path = host.unit_directory or (Path("/etc/systemd/system") if scope == "system" else reg.launcher.home / ".config/systemd/user")
    _trusted(path, directory=True)
    return scope, "", str(path)


def _prepare(store, policy, backend, scope, cron_service, host, operation):
    _need(backend in INSTALL, ActivationReason.UNSUPPORTED)
    reg = store.read_registration()
    _need(os.getuid() == os.geteuid() == reg.binding.host_uid, ActivationReason.AUTHORITY)
    scope, service, directory = _location(reg, host, backend, scope, cron_service)
    manager = _Manager(reg, host, "system" if backend == "cron" else scope)
    old = _read(store)
    if old is not None:
        _need((old.selection_hash, old.registration_hash, old.backend, old.scope, old.cron_service, old.directory)
              == (store.selection_hash, _registration_hash(reg), backend, scope, service, directory), ActivationReason.CHANGED)
    if operation != "remove":
        assets = render_scheduler(store, policy, backend=backend)
        rendered = tuple(_Asset(k, v) for k, v in assets.files.items())
        if old is not None:
            _need(old.assets == rendered, ActivationReason.CHANGED)
    if operation != "remove" or backend == "systemd":
        manager.cron_available(service) if backend == "cron" else manager.available(persistent=operation != "remove")
    if old is None and operation == "install":
        before = manager.crontab() if backend == "cron" else b""
        old = _Ledger(1, store.selection_hash, _registration_hash(reg), backend, scope, service, directory,
                      rendered, "installing", cron_before_sha256=_sha(before) if backend == "cron" else "",
                      cron_before_size=len(before), cron_separator=bool(before and not before.endswith(b"\n")))
        if backend == "cron":
            _need(_cron_span(before, old) is None)
            _cron_shell(before)
        else:
            with _directory(Path(directory)) as fd:
                for asset in old.assets:
                    _need(_file(fd, asset.name) is None and _file(fd, "." + asset.name + ".setup") is None)
            _need(not _link(old))
            _need(all(v["LoadState"] == "not-found" for v in _units(manager, old, missing=True)))
            _need(os.access(directory, os.W_OK), ActivationReason.AUTHORITY)
        old = _save(store, None, old)  # Intent precedes EVERY external mutation.
    _need(old is not None, ActivationReason.ABSENT)
    return old, manager


def _execute(store, policy, backend, scope, cron_service, host, operation):
    ledger, manager = _prepare(store, policy, backend, scope, cron_service, host, operation)
    if ledger.phase == "removed":
        _need(operation != "install", ActivationReason.REMOVED)
        if backend == "cron":
            _need(_cron_span(manager.crontab(), ledger) is None)
        else:
            with _directory(Path(ledger.directory)) as fd:
                for asset in ledger.assets:
                    _need(_file(fd, asset.name) is None and _file(fd, "." + asset.name + ".setup") is None)
            _need(not _link(ledger))
            timer = _units(manager, ledger, missing=True)[1]
            _need(timer["ActiveState"] == "inactive" and timer["UnitFileState"] != "enabled", ActivationReason.INACTIVE)
        return SchedulerActivation("removed", backend, ledger.name)
    if operation == "inspect":
        _need(ledger.phase == "active", ActivationReason.INACTIVE)
    elif operation == "install":
        _need(ledger.phase in ("installing", "active"), ActivationReason.INACTIVE)
        if backend == "systemd":
            _systemd_files(ledger, partial=ledger.phase == "installing")
        for step in INSTALL[backend][len(ledger.completed):]:
            fresh = ledger.pending is None
            if fresh:
                ledger = _save(store, ledger, replace(ledger, pending=step, intended=ledger.intended + (step,)))
            if backend == "cron":
                ledger = _cron_change(store, ledger, manager)
            else:
                _systemd_step(ledger, manager, step, fresh)
            ledger = _save(store, ledger, replace(ledger, completed=ledger.completed + (step,), pending=None))
    else:
        # Validate ALL still-present owned bytes BEFORE stopping a timer. Partial
        # removal permits already absent files, never edited/foreign replacements.
        if backend == "systemd":
            with _directory(Path(ledger.directory)) as fd:
                for suffix, asset in zip(("service", "timer"), ledger.assets):
                    value, temp = _asset(fd, asset, staged=True)
                    if value is not None or temp is not None:
                        _need(suffix in ledger.intended)
            linked = _link(ledger)
            _need(not linked or "enable" in ledger.intended)
            _units(manager, ledger, missing=True)
        else:
            span = _cron_span(manager.crontab(), ledger)
            _need(span is None or "crontab" in ledger.intended)
        if ledger.phase != "removing":
            ledger = _save(store, ledger, replace(ledger, phase="removing", pending=None))
        for step in REMOVE[backend][len(ledger.removed):]:
            fresh = ledger.pending is None
            if fresh:
                ledger = _save(store, ledger, replace(ledger, pending=step))
            if backend == "cron":
                ledger = _cron_change(store, ledger, manager, remove=True)
            else:
                _remove_systemd(ledger, manager, step, fresh)
            ledger = _save(store, ledger, replace(ledger, removed=ledger.removed + (step,), pending=None))
        ledger = _save(store, ledger, replace(ledger, phase="removed"))
        return SchedulerActivation("removed", backend, ledger.name)
    if backend == "cron":
        manager.cron_available(ledger.cron_service)
        raw = manager.crontab()
        span = _cron_span(raw, ledger)
        _need(span is not None, ActivationReason.INACTIVE)
        _cron_shell(raw[:span[0]])
    else:
        _verify_systemd(ledger, manager)
    if operation == "install" and ledger.phase != "active":
        ledger = _save(store, ledger, replace(ledger, phase="active"))
    return SchedulerActivation("active", backend, ledger.name)


def _operation(store, policy, *, backend, scope, cron_service, _host, operation):
    name = "futureagi-compose-update-" + store.selection_hash
    try:
        if operation == "inspect":
            return _execute(store, policy, backend, scope, cron_service, _host or _Host(), operation)
        with store.runner_lock():
            if operation == 'remove':
                from .receipt import StateDir
                # runner.lock prevents a new reservation between this check and
                # removal. Read failure also retains the sole safety launcher.
                try:
                    pending = (store.read_state().active_attempt_id is not None
                               or StateDir(store.checkout).read_journal() is not None)
                except Exception:
                    raise _Incomplete(ActivationReason.RECOVERY) from None
                _need(not pending, ActivationReason.RECOVERY)
            return _execute(store, policy, backend, scope, cron_service, _host or _Host(), operation)
    except _Incomplete as exc:
        reason = exc.reason
    except StoreBusy:
        reason = ActivationReason.BUSY
    except StoreError as exc:
        reason = (ActivationReason.REGISTRATION if exc.reason_code == ReasonCode.SCHEDULER_UNREGISTERED
                  else ActivationReason.METADATA)
    except PermissionError:
        reason = ActivationReason.AUTHORITY
    except FileNotFoundError:
        reason = ActivationReason.UNSAFE
    except (OSError, ValueError, TypeError):
        reason = ActivationReason.METADATA
    return SchedulerActivation("incomplete", backend if backend in INSTALL else "unsupported", name, reason, _ACTIONS[reason])


def install_scheduler(store: RunnerStateStore, policy: AutomationPolicy, *, backend: str,
                      scope: str = "system", cron_service: Optional[str] = None,
                      _host: Optional[_Host] = None) -> SchedulerActivation:
    """Explicit create-only activation. No policy/baseline changes; see module contract."""
    return _operation(store, policy, backend=backend, scope=scope, cron_service=cron_service,
                      _host=_host, operation="install")


def inspect_activation(store: RunnerStateStore, policy: AutomationPolicy, *, backend: str,
                       scope: str = "system", cron_service: Optional[str] = None,
                       _host: Optional[_Host] = None) -> SchedulerActivation:
    """Read-only live verification, not the saved 'active' flag. No recovery writes."""
    return _operation(store, policy, backend=backend, scope=scope, cron_service=cron_service,
                      _host=_host, operation="inspect")


def remove_scheduler(store: RunnerStateStore, policy: AutomationPolicy, *, backend: str,
                     scope: str = "system", cron_service: Optional[str] = None,
                     _host: Optional[_Host] = None) -> SchedulerActivation:
    """Disable automation separately first. Remove exact assets; retain the ledger."""
    return _operation(store, policy, backend=backend, scope=scope, cron_service=cron_service,
                      _host=_host, operation="remove")
