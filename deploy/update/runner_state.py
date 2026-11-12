"""Private durable scheduler metadata, never an application/public projection.

Lock order: checkout runner.lock -> engine update.lock -> short metadata locks.
Control commands acquire only control.lock. Every lock is nonblocking and is
never unlinked; busy is typed. No caller may do network/subprocess work inside a
metadata transaction. Native env-commit qualification remains engine-owned.
"""
from __future__ import annotations

import dataclasses
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional, Tuple, Union, get_args, get_origin, get_type_hints

from .contracts import AutomationPolicy, InstallationBinding, OperationEvent, ReasonCode
from .manifest import parse_version
from .schedule import retry_deadline, successful_check_deadline, utc

MAX_COUNTER = 2**63 - 1
MAX_DOCUMENT_BYTES = 65536
MAX_EVENTS = 1000
_HASH = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,128}")


class StoreError(RuntimeError):
    def __init__(self, reason_code: ReasonCode = ReasonCode.CONTROL_INVALID) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.value)


class StoreBusy(StoreError):
    """A cooperating caller holds the lock; do not replace its active outcome."""


class StaleGeneration(StoreError):
    """CAS failed. Reload, do not blindly repeat an old control/state update."""


def _require(ok: bool) -> None:
    if not ok:
        raise StoreError()


def _counter(value: int) -> None:
    _require(type(value) is int and 0 <= value <= MAX_COUNTER)


def _increment(value: int) -> int:
    _counter(value)
    _require(value < MAX_COUNTER)
    return value + 1


def _hash(value: str) -> None:
    _require(type(value) is str and _HASH.fullmatch(value) is not None)


def _token(value: str) -> None:
    _require(type(value) is str and _TOKEN.fullmatch(value) is not None and value not in (".", ".."))


def _text(value: str) -> None:
    _require(type(value) is str and 0 < len(value) <= 4096 and all(ord(c) >= 32 and ord(c) != 127 for c in value))


def selection_hash(checkout: Path, env_target: Path, production: bool, topology: str) -> str:
    _require(type(production) is bool and topology in ("standalone", "distributed"))
    _require(not production or topology == "distributed")
    body = [str(checkout.resolve(strict=True)), str(env_target.resolve(strict=True)), production, topology]
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Control:
    schema_version: int = 1
    generation: int = 0
    enabled_override: Optional[bool] = None
    pause_until: Optional[datetime] = None
    pause_indefinite: bool = False
    changed_at: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def __post_init__(self) -> None:
        _require(type(self.schema_version) is int and self.schema_version == 1)
        _counter(self.generation)
        _require(self.enabled_override is None or type(self.enabled_override) is bool)
        _require(type(self.pause_indefinite) is bool)
        _require(not (self.pause_until is not None and self.pause_indefinite))
        utc(self.changed_at)
        if self.pause_until is not None:
            utc(self.pause_until)

    def effective(self, policy: AutomationPolicy, now: datetime) -> str:
        enabled = policy.enabled if self.enabled_override is None else self.enabled_override
        if not enabled:
            return "disabled"
        if self.pause_indefinite or (self.pause_until is not None and utc(now) < utc(self.pause_until)):
            return "paused"
        return "manual" if policy.mode == "manual" else "enabled"


@dataclass(frozen=True)
class TargetIdentity:
    version: str
    manifest_hash: str

    def __post_init__(self) -> None:
        _text(self.version)
        _require(self.version == self.version.strip())
        parse_version(self.version)
        _hash(self.manifest_hash)

    @property
    def key(self) -> str:
        return hashlib.sha256((self.version + "\0" + self.manifest_hash).encode()).hexdigest()


@dataclass(frozen=True)
class Observation:
    kind: str
    observed_at: datetime
    identity: str

    def __post_init__(self) -> None:
        _require(self.kind in ("versions", "discovery", "download"))
        utc(self.observed_at)
        _hash(self.identity)


@dataclass(frozen=True)
class RunnerState:
    schema_version: int = 1
    generation: int = 0
    installation_fingerprint: Optional[str] = None
    last_tick_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None
    last_successful_check_at: Optional[datetime] = None
    next_check_at: Optional[datetime] = None
    retry_count: int = 0
    retry_not_before: Optional[datetime] = None
    cached_candidate_identity: Optional[TargetIdentity] = None
    download_identity: Optional[TargetIdentity] = None
    active_attempt_id: Optional[str] = None
    active_operation_token: Optional[str] = None
    failed_target_latch: Optional[TargetIdentity] = None
    last_window_occurrence: Optional[str] = None
    next_event_sequence: int = 0
    last_good_observations: Tuple[Observation, ...] = ()
    baseline_head: Optional[str] = None  # Private immutable generation, not registration identity.

    def __post_init__(self) -> None:
        _require(type(self.schema_version) is int and self.schema_version == 1)
        for value in (self.generation, self.retry_count, self.next_event_sequence):
            _counter(value)
        if self.installation_fingerprint is not None:
            _hash(self.installation_fingerprint)
        if self.baseline_head is not None:
            _hash(self.baseline_head)
        for name in ("last_tick_at", "last_attempt_at", "last_successful_check_at", "next_check_at", "retry_not_before"):
            if getattr(self, name) is not None:
                utc(getattr(self, name))
        for value in (self.active_attempt_id, self.active_operation_token):
            if value is not None:
                _token(value)
        _require((self.active_attempt_id is None) == (self.active_operation_token is None))
        if self.last_window_occurrence is not None:
            _text(self.last_window_occurrence)
            _require(len(self.last_window_occurrence) <= 256)
        _require(type(self.last_good_observations) is tuple and len(self.last_good_observations) <= 3)
        _require(all(isinstance(v, Observation) for v in self.last_good_observations))
        _require(len({v.kind for v in self.last_good_observations}) == len(self.last_good_observations))
        for value in (self.cached_candidate_identity, self.download_identity, self.failed_target_latch):
            _require(value is None or isinstance(value, TargetIdentity))


@dataclass(frozen=True)
class LauncherConfig:
    python: Path
    home: Path
    path: str
    user: str

    def __post_init__(self) -> None:
        for value in (self.python, self.home):
            _require(isinstance(value, Path) and value.is_absolute())
            _text(str(value))
        _text(self.path)
        _require(all(part and Path(part).is_absolute() for part in self.path.split(":")))
        _require(type(self.user) is str and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", self.user) is not None)


@dataclass(frozen=True)
class Registration:
    schema_version: int
    binding: InstallationBinding
    launcher: LauncherConfig
    registered_at: datetime

    def __post_init__(self) -> None:
        _require(type(self.schema_version) is int and self.schema_version == 1)
        _require(isinstance(self.binding, InstallationBinding) and isinstance(self.launcher, LauncherConfig))
        utc(self.registered_at)
        for value in (self.binding.selection_hash, self.binding.installation_fingerprint):
            _hash(value)
        for value in (self.binding.checkout, self.binding.env_alias, self.binding.env_target, *self.binding.compose_files):
            _require(value.is_absolute())
            _text(str(value))
        for value in (self.binding.compose_project, self.binding.docker_context, self.binding.daemon_identity,
                      *self.binding.profiles):
            _text(value)
        _require(bool(self.binding.compose_files))


@dataclass(frozen=True)
class AttemptRecord:
    """Private correlation only; proof/receipt bytes live in their owning stores.

    An incomplete forward attempt is never reapplied. Restore-backed attempts
    resume the same ledger's safety recovery; legacy ambiguity remains blocked.
    Terminal records survive explicit retry, including failed restoration.
    """
    schema_version: int
    attempt_id: str
    operation_token: str
    binding: InstallationBinding
    target: TargetIdentity
    window_occurrence: str
    reserved_at: datetime
    outcome: str = "reserved"
    finished_at: Optional[datetime] = None
    backup_proof_identity: Optional[str] = None
    receipt_identity: Optional[str] = None

    def __post_init__(self) -> None:
        _require(type(self.schema_version) is int and self.schema_version == 1)
        _token(self.attempt_id)
        _token(self.operation_token)
        _require(isinstance(self.binding, InstallationBinding) and isinstance(self.target, TargetIdentity))
        _text(self.window_occurrence)
        utc(self.reserved_at)
        _require(self.outcome in ("reserved", "succeeded", "failed", "interrupted", "rolled_back", "restored", "failed_available"))
        _require((self.outcome == "reserved") == (self.finished_at is None))
        if self.finished_at is not None:
            _require(utc(self.finished_at) >= utc(self.reserved_at))
        for value in (self.backup_proof_identity, self.receipt_identity):
            if value is not None:
                _hash(value)


@dataclass(frozen=True)
class EventRecord:
    schema_version: int
    sequence: int
    event: OperationEvent
    target: Optional[TargetIdentity]

    def __post_init__(self) -> None:
        _require(type(self.schema_version) is int and self.schema_version == 1)
        _counter(self.sequence)
        _require(isinstance(self.event, OperationEvent))
        _token(self.event.operation_token)
        _require(self.target is None or isinstance(self.target, TargetIdentity))


def _wire(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: _wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, (Path, Enum)):
        return value.value if isinstance(value, Enum) else str(value)
    if isinstance(value, Mapping):
        _require(all(type(key) is str for key in value))
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    _require(value is None or type(value) in (int, str, bool))
    return value


@lru_cache(maxsize=32)
def _hints(kind):
    return get_type_hints(kind)


def _typed(kind: Any, value: Any) -> Any:
    origin, args = get_origin(kind), get_args(kind)
    if origin is Union:
        if value is None and type(None) in args:
            return None
        return _typed(next(arg for arg in args if arg is not type(None)), value)
    if origin is tuple:
        _require(type(value) is list and len(value) <= 64)
        return tuple(_typed(args[0], item) for item in value)
    if str(origin) == "typing.Literal":
        _require(value in args and type(value) is type(args[0]))
        return value
    if dataclasses.is_dataclass(kind):
        _require(type(value) is dict and set(value) == {field.name for field in fields(kind)})
        hints = _hints(kind)
        return kind(**{key: _typed(hints[key], item) for key, item in value.items()})
    if kind is datetime:
        _require(type(value) is str and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{6})?Z", value) is not None)
        return datetime.fromisoformat(value[:-1] + "+00:00")
    if kind is Path:
        _require(type(value) is str)
        return Path(value)
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    _require(type(value) is kind)
    return value


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def _decode(kind, raw):
    try:
        _require(len(raw) <= MAX_DOCUMENT_BYTES)
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_require(False)))
        # Exact legacy v1 state remains readable. No other missing field is defaulted.
        if kind is RunnerState and type(value) is dict and "baseline_head" not in value:
            value["baseline_head"] = None
        return _typed(kind, value)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise StoreError() from None


def _check_file(fd: int) -> None:
    info = os.fstat(fd)
    _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)


class RunnerStateStore:
    def __init__(self, checkout: Path, env_target: Path, production: bool = False,
                 topology: str = "standalone") -> None:
        self.checkout = checkout.resolve(strict=True)
        self.env_target = env_target.resolve(strict=True)
        self.production, self.topology = production, topology
        self.selection_hash = selection_hash(self.checkout, self.env_target, production, topology)
        self.path = self.checkout / ".futureagi-updates" / "automation" / self.selection_hash

    @contextmanager
    def _directory(self, *, create: bool = False, global_lock: bool = False,
                   child: Optional[str] = None) -> Iterator[int]:
        fds = []
        try:
            fd = os.open(str(self.checkout), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            fds.append(fd)
            checkout_info = os.fstat(fd)
            _require(checkout_info.st_uid == os.geteuid() and not (checkout_info.st_mode & 0o022))
            parts = [".futureagi-updates", "automation"]
            if not global_lock:
                parts.append(self.selection_hash)
            if child is not None:
                _require(child in ("events", "attempts", "evidence", "baselines", "transitions"))
                parts.append(child)
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                fds.append(fd)
                info = os.fstat(fd)
                _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
            yield fd
        except FileNotFoundError:
            raise
        except OSError:
            raise StoreError() from None
        finally:
            for fd in reversed(fds):
                os.close(fd)

    @contextmanager
    def _lock(self, name: str, *, global_lock: bool = False) -> Iterator[int]:
        with self._directory(create=True, global_lock=global_lock) as directory:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         0o600, dir_fd=directory)
            try:
                _check_file(fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EACCES):
                        raise StoreBusy() from None
                    raise
                # Lock names/inodes are stable for the lifetime of this store.
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                _require((info.st_dev, info.st_ino) == (os.fstat(fd).st_dev, os.fstat(fd).st_ino))
                yield directory
            finally:
                os.close(fd)

    def runner_lock(self):
        return self._lock("runner.lock", global_lock=True)

    @staticmethod
    def _read(directory: int, name: str, kind):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        try:
            _check_file(fd)
            _require(os.fstat(fd).st_size <= MAX_DOCUMENT_BYTES)
            with os.fdopen(os.dup(fd), "rb") as handle:
                return _decode(kind, handle.read(MAX_DOCUMENT_BYTES + 1))
        finally:
            os.close(fd)

    @staticmethod
    def _write(directory: int, name: str, value: Any) -> None:
        raw = (json.dumps(_wire(value), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        _decode(type(value), raw)  # Validate writes too, including nested S01 objects.
        RunnerStateStore._write_raw(directory, name, raw)

    @staticmethod
    def _read_raw(directory: int, name: str, limit: int) -> bytes:
        _token(name)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        try:
            _check_file(fd)
            before = os.fstat(fd)
            _require(before.st_size <= limit)
            with os.fdopen(os.dup(fd), "rb") as handle:
                raw = handle.read(limit + 1)
            after = os.fstat(fd)
            stable = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_uid', 'st_nlink', 'st_mtime_ns', 'st_ctime_ns')
            _require(len(raw) <= limit and all(getattr(before, key) == getattr(after, key) for key in stable))
            return raw
        finally:
            os.close(fd)

    @staticmethod
    def _write_raw(directory: int, name: str, raw: bytes) -> None:
        """Bounded private atomic writer. Caller serializes with state.lock."""
        _token(name)
        _require(type(raw) is bytes and len(raw) <= 4 * 1024 * 1024)
        try:
            existing = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            pass
        else:
            try:
                _check_file(existing)
            finally:
                os.close(existing)
        temp = "." + name + "." + uuid.uuid4().hex + ".tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                from .envcommit import sync_file
                sync_file(handle.fileno())
            os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temp, dir_fd=directory)
            except FileNotFoundError:
                pass

    def read_control(self) -> Control:
        try:
            with self._directory() as directory:
                try:
                    return self._read(directory, "control.json", Control)
                except FileNotFoundError:
                    _require(not any(name in os.listdir(directory) for name in ("registration.json", "state.json")))
                    return Control()
        except FileNotFoundError:
            return Control()

    def change_control(self, action: str, now: datetime, *, until: Optional[datetime] = None,
                       expected_generation: Optional[int] = None) -> Control:
        now = utc(now)
        if expected_generation is not None:
            _counter(expected_generation)
        _require(action in ("enable", "disable", "pause", "resume"))
        _require(until is None or action == "pause")
        if until is not None:
            _require(utc(until) > now)
        with self._lock("control.lock") as directory:
            old = self.read_control()
            if expected_generation is not None and old.generation != expected_generation:
                raise StaleGeneration()
            changes = {"generation": _increment(old.generation), "changed_at": now}
            if action in ("enable", "disable"):
                changes["enabled_override"] = action == "enable"
            else:
                changes.update(pause_until=utc(until) if until else None,
                               pause_indefinite=action == "pause" and until is None)
            new = replace(old, **changes)
            self._write(directory, "control.json", new)
            return new

    def register(self, registration: Registration) -> None:
        """Explicit init only; identical init is idempotent, drift is never migrated."""
        self._binding(registration.binding)
        with self._lock("control.lock") as directory:
            control = self.read_control()
            self._write(directory, "control.json", control)
        with self._lock("state.lock") as directory:
            try:
                old = self._read(directory, "registration.json", Registration)
            except FileNotFoundError:
                old = None
            if old is not None:
                _require(old.binding == registration.binding and old.launcher == registration.launcher)
                self._read(directory, "state.json", RunnerState)
                return
            try:
                state = self._read(directory, "state.json", RunnerState)
                _require(state.installation_fingerprint == registration.binding.installation_fingerprint)
            except FileNotFoundError:
                state = RunnerState(installation_fingerprint=registration.binding.installation_fingerprint)
                self._write(directory, "state.json", state)
            self._write(directory, "registration.json", registration)

    def _binding(self, binding: InstallationBinding) -> None:
        _require(binding.checkout == self.checkout and binding.env_target == self.env_target
                 and binding.production == self.production and binding.topology == self.topology
                 and binding.selection_hash == self.selection_hash and binding.host_uid == os.geteuid())
        try:
            _require(binding.env_alias.resolve(strict=True) == self.env_target)
        except OSError:
            raise StoreError(ReasonCode.CONFIGURATION_CHANGED) from None

    def validate_binding(self, binding: InstallationBinding) -> Registration:
        """Compare fresh caller-owned Docker/config observations, without Docker I/O."""
        registration = self.read_registration()
        if registration.binding != binding:
            raise StoreError(ReasonCode.CONFIGURATION_CHANGED)
        return registration

    def read_registration(self) -> Registration:
        try:
            with self._directory() as directory:
                result = self._read(directory, "registration.json", Registration)
                self._binding(result.binding)
                return result
        except FileNotFoundError:
            raise StoreError(ReasonCode.SCHEDULER_UNREGISTERED) from None

    def read_state(self) -> RunnerState:
        try:
            with self._directory() as directory:
                result = self._read(directory, "state.json", RunnerState)
                _require(result.installation_fingerprint == self.read_registration().binding.installation_fingerprint)
                return result
        except FileNotFoundError:
            raise StoreError() from None

    def save_state(self, state: RunnerState, *, expected_generation: int) -> RunnerState:
        """CAS snapshot; observations/deadlines supplied by runner, no side effects."""
        _counter(expected_generation)
        _require(isinstance(state, RunnerState))
        with self._lock("state.lock") as directory:
            old = self.read_state()
            if old.generation != expected_generation or state.generation != expected_generation:
                raise StaleGeneration()
            _require(state.installation_fingerprint == old.installation_fingerprint
                     and state.baseline_head == old.baseline_head
                     and state.next_event_sequence >= old.next_event_sequence)
            new = replace(state, generation=_increment(old.generation))
            self._write(directory, "state.json", new)
            return new

    def record_check_attempt(self, policy: AutomationPolicy, now: datetime) -> RunnerState:
        """Reserve a conservative retry deadline before starting network I/O.

        A process crash cannot make every five-minute wake a fresh first try.
        Verified success/failure below replaces this provisional deadline.
        """
        with self._lock("state.lock") as directory:
            old = self.read_state()
            deadline = retry_deadline(policy, now, self.selection_hash, old.retry_count)
            new = replace(old, generation=_increment(old.generation), last_attempt_at=utc(now),
                          retry_count=deadline.retry_count, retry_not_before=deadline.at)
            self._write(directory, "state.json", new)
            return new

    def record_check_success(self, policy: AutomationPolicy, now: datetime, target: TargetIdentity) -> RunnerState:
        """Only a caller-verified check advances freshness; never clear failure latches."""
        with self._lock("state.lock") as directory:
            old = self.read_state()
            _require(old.last_attempt_at is not None and old.last_attempt_at <= utc(now))
            new = replace(old, generation=_increment(old.generation), last_successful_check_at=utc(now),
                          next_check_at=successful_check_deadline(policy, now, self.selection_hash),
                          retry_count=0, retry_not_before=None, cached_candidate_identity=target)
            self._write(directory, "state.json", new)
            return new

    def record_check_failure(self, policy: AutomationPolicy, now: datetime,
                             retry_after: Optional[datetime] = None) -> RunnerState:
        """Failure preserves last verified freshness/candidate and failed-target latch."""
        with self._lock("state.lock") as directory:
            old = self.read_state()
            _require(old.last_attempt_at is not None and old.last_attempt_at <= utc(now))
            _require(old.retry_count > 0)
            deadline = retry_deadline(policy, now, self.selection_hash, old.retry_count - 1, retry_after)
            new = replace(old, generation=_increment(old.generation), retry_count=deadline.retry_count,
                          retry_not_before=deadline.at)
            self._write(directory, "state.json", new)
            return new

    def reserve_attempt(self, target: TargetIdentity, window: str, now: datetime) -> AttemptRecord:
        """Under engine update.lock, after guards, BEFORE backup or any stop.

        State is committed before its record. A crash in between leaves a live
        intent which blocks all further work rather than allowing a duplicate.
        """
        with self._lock("state.lock") as directory:
            old = self.read_state()
            if old.active_attempt_id is not None:
                raise StoreError(ReasonCode.RECOVERY_REQUIRED)
            if old.failed_target_latch == target:
                raise StoreError(ReasonCode.FAILED_TARGET_LATCHED)
            key = "always:" + target.key if window == "always" else window
            if old.last_window_occurrence == key:
                raise StoreError(ReasonCode.OUTSIDE_WINDOW)
            attempt = AttemptRecord(1, uuid.uuid4().hex, uuid.uuid4().hex,
                                    self.read_registration().binding, target, key, utc(now))
            new = replace(old, generation=_increment(old.generation), active_attempt_id=attempt.attempt_id,
                          active_operation_token=attempt.operation_token, last_window_occurrence=key)
            self._write(directory, "state.json", new)
            with self._directory(create=True, child="attempts") as attempts:
                self._write(attempts, attempt.attempt_id + ".json", attempt)
            return attempt

    def read_attempt(self, attempt_id: str) -> AttemptRecord:
        _token(attempt_id)
        try:
            with self._directory(child="attempts") as directory:
                attempt = self._read(directory, attempt_id + ".json", AttemptRecord)
                _require(attempt.attempt_id == attempt_id)
                self._binding(attempt.binding)
                self.validate_binding(attempt.binding)
                return attempt
        except FileNotFoundError:
            raise StoreError(ReasonCode.RECOVERY_REQUIRED) from None

    def finish_attempt(self, terminal: AttemptRecord) -> RunnerState:
        _require(terminal.outcome != "reserved")
        with self._lock("state.lock") as directory:
            old = self.read_state()
            # Enrolled automatic success MUST use the successor-head transaction.
            _require(old.baseline_head is None or terminal.outcome not in ("succeeded", "restored"))
            original = self.read_attempt(terminal.attempt_id)
            if original.outcome != "reserved":
                # A crash may have published the terminal record but not state.
                # Only byte-equivalent typed replay (including token/evidence)
                # can finish that same transaction. Never re-latch after retry.
                _require(original == terminal)
                if old.active_attempt_id is None:
                    return old
            else:
                _require(replace(terminal, outcome=original.outcome, finished_at=original.finished_at,
                                 backup_proof_identity=original.backup_proof_identity,
                                 receipt_identity=original.receipt_identity) == original)
            _require((old.active_attempt_id, old.active_operation_token) == (terminal.attempt_id, terminal.operation_token))
            # Evidence first, clear active intent and publish the latch together.
            # An exact terminal replay does not rewrite immutable evidence.
            if original != terminal:
                with self._directory(child="attempts") as attempts:
                    self._write(attempts, terminal.attempt_id + ".json", terminal)
            new = replace(old, generation=_increment(old.generation), active_attempt_id=None, active_operation_token=None,
                          failed_target_latch=terminal.target if terminal.outcome != "succeeded" else old.failed_target_latch)
            self._write(directory, "state.json", new)
            return new

    def retry_target(self, target: TargetIdentity) -> RunnerState:
        with self._lock("state.lock") as directory:
            old = self.read_state()
            if old.active_attempt_id is not None:
                raise StoreError(ReasonCode.RECOVERY_REQUIRED)
            _require(old.failed_target_latch == target)
            # Interval occurrence remains spent. Always permits an explicit
            # retry, but original attempt/failure evidence is never deleted.
            window = None if old.last_window_occurrence == "always:" + target.key else old.last_window_occurrence
            new = replace(old, generation=_increment(old.generation), failed_target_latch=None, last_window_occurrence=window)
            self._write(directory, "state.json", new)
            return new

    @staticmethod
    def _event_names(events: int):
        names = sorted(name for name in os.listdir(events) if re.fullmatch(r"[0-9]{20}\.json", name))
        if len(names) > MAX_EVENTS:
            raise StoreError(ReasonCode.DISK_SPACE_INSUFFICIENT)
        return names

    def read_events(self) -> Tuple[EventRecord, ...]:
        with self._lock("state.lock"):
            state = self.read_state()
            try:
                with self._directory(child="events") as events:
                    result = []
                    for name in self._event_names(events):
                        event = self._read(events, name, EventRecord)
                        _require(name == f"{event.sequence:020d}.json" and event.sequence < state.next_event_sequence)
                        result.append(event)
                    return tuple(result)
            except FileNotFoundError:
                return ()

    def append_event(self, event: OperationEvent, target: Optional[TargetIdentity] = None) -> EventRecord:
        with self._lock("state.lock") as directory:
            state = self.read_state()
            record = EventRecord(1, state.next_event_sequence, event, target)
            # Validate counters and the record BEFORE pruning any old metadata.
            new = replace(state, generation=_increment(state.generation),
                          next_event_sequence=_increment(state.next_event_sequence))
            with self._directory(create=True, child="events") as events:
                names = self._event_names(events)
                # Crash after sequence reservation leaves a gap, never reuse it.
                _require(all(int(name[:-5]) < state.next_event_sequence for name in names))
                if len(names) == MAX_EVENTS:
                    # Inspect only as far as the first unpinned old record. Never
                    # deserialize all 1000 records for every normal append.
                    for name in names:
                        old = self._read(events, name, EventRecord)
                        _require(name == f"{old.sequence:020d}.json")
                        if (old.event.operation_token != state.active_operation_token
                                and not (old.target is not None and old.target == state.failed_target_latch)):
                            os.unlink(name, dir_fd=events)
                            os.fsync(events)
                            break
                    else:
                        raise StoreError(ReasonCode.DISK_SPACE_INSUFFICIENT)
                self._write(directory, "state.json", new)
                self._write(events, f"{record.sequence:020d}.json", record)
                return record
