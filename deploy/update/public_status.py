"""Closed, disposable public reports; private CAS metadata is the only authority.

Mount the stable slot directory, never the status.json inode. The private copy
is presentation history only, never candidate, backup, recovery or apply proof.
No network, subprocess, or long-lived lock belongs in this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .contracts import ACTIVE_OPERATION_STATES, ReasonCode, validate_public_status
from .receipt import StateDir
from .runner_state import StaleGeneration, StoreError
from .schedule import utc

MAX_SEQUENCE = 2**53 - 1


def stamp(value):
    return None if value is None else utc(value).strftime('%Y-%m-%dT%H:%M:%SZ')


def installation_identity(binding):
    return {'slot_id': binding.slot_id, 'topology': binding.topology, 'production': binding.production}


def _pick(value, keys):
    return {key: copy.deepcopy(value[key]) for key in keys.split()}


def _reasons(values):
    return list(dict.fromkeys(ReasonCode(value).value for value in values))


def _versions(value):
    result = _pick(value, 'observed_at runtime_state selected_release running_release')
    result['components'] = [_pick(item, 'component selected_version observed_versions observation')
                            for item in value['components']]
    return result


def _last_run(value):
    return None if value is None else _pick(value, 'kind automatic outcome from_version target_version finished_at fallback_outcome')


def build_status(binding, policy, control, state, *, now, previous=None, observation=None,
                 discovery=None, download=None, operation=None, last_run=None,
                 reason_codes=(), next_action_at=None, safety=None):
    """Project only named fields, then validate; never dump a private dataclass.

    ``discovery`` is a DiscoveryResult from THIS tick. Failed checks retain the
    last verified candidate/as-of time. ``download`` is a freshly inspected
    DownloadReceipt, not merely a persisted receipt or an operation label.
    """
    identity = installation_identity(binding)
    if previous is not None:
        validate_public_status(previous, expected_installation=identity)
    versions = ({'observed_at': None, 'runtime_state': 'unknown', 'selected_release': None,
                 'running_release': None, 'components': []} if previous is None else _versions(previous['versions']))
    old_discovery = ({'state': 'not_checked', 'last_attempt_at': None, 'checked_at': None,
                      'candidate_version': None, 'verification': 'not_checked', 'reason_codes': []}
                     if previous is None else copy.deepcopy(previous['discovery']))
    cached = ({'state': 'not_downloaded', 'target_version': None, 'completed_at': None,
               'verified_components': [], 'reason_codes': []}
              if previous is None else copy.deepcopy(previous['download']))
    run = None if previous is None else _last_run(previous['last_run'])
    if observation is not None:
        # A failed inspection cannot renew the last good observation.
        if observation.versions['observed_at'] is not None:
            versions = _versions(observation.versions)
        if observation.last_run is not None:
            run = _last_run(observation.last_run)
    if discovery is not None:
        old_discovery['last_attempt_at'] = stamp(state.last_attempt_at)
        old_discovery['reason_codes'] = _reasons(discovery.reason_codes)
        if discovery.complete and discovery.candidate is not None:
            version = discovery.candidate.version
            old_discovery.update(state='current' if version == versions['running_release'] else 'available',
                                 candidate_version=version, checked_at=stamp(discovery.checked_at), verification='verified')
            # A different pair/trust/config is invalidated by the caller too.
            if cached['target_version'] != version:
                cached = {'state': 'not_downloaded', 'target_version': None, 'completed_at': None,
                          'verified_components': [], 'reason_codes': []}
            if download is None and cached['state'] == 'downloaded':
                # Same version is not proof of unchanged raw pair, trust/config
                # or cached images. A new check without reinspection invalidates
                # this claim; idle/failed checks keep its original as-of time.
                cached = {'state': 'unknown', 'target_version': None, 'completed_at': None,
                          'verified_components': [], 'reason_codes': []}
        else:
            old_discovery['state'] = 'failed'
    if download is not None:
        cached = {'state': 'downloaded', 'target_version': old_discovery['candidate_version'],
                  'completed_at': download.completed_at,
                  'verified_components': sorted({image.component for image in download.images}), 'reason_codes': []}
    reasons = _reasons(reason_codes)
    if ReasonCode.CONFIGURATION_CHANGED.value in reasons or ReasonCode.SIGNATURE_REJECTED.value in reasons:
        cached = {'state': 'unknown', 'target_version': None, 'completed_at': None,
                  'verified_components': [], 'reason_codes': reasons}
    effective = control.effective(policy, now)
    blocked = bool(reasons) and effective == 'enabled' and not all(
        reason in (ReasonCode.OUTSIDE_WINDOW.value, ReasonCode.INSUFFICIENT_WINDOW.value) for reason in reasons)
    effective = 'blocked' if blocked else effective
    enabled = policy.enabled if control.enabled_override is None else control.enabled_override
    window = policy.maintenance_window
    window_json = None
    if window is not None:
        window_json = {'kind': window.kind, 'timezone': window.timezone}
        if window.kind == 'interval':
            window_json.update(days_of_week=list(window.days_of_week), start=window.start, end=window.end)
    pause_until = control.pause_until
    if pause_until is not None and utc(now) < utc(pause_until) and pause_until.microsecond:
        # Wire timestamps have second precision: never publish an active
        # sub-second pause as already expired. This is display rounding only.
        pause_until = pause_until.replace(microsecond=0) + timedelta(seconds=1)
    next_check = state.retry_not_before or state.next_check_at
    if effective != 'enabled':
        next_check = next_action_at = pause_until if effective == 'paused' else None
    op = ({'state': 'idle', 'kind': None, 'stage': None, 'started_at': None, 'finished_at': None,
           'reported_at': None, 'reason_codes': []} if previous is None else copy.deepcopy(previous['operation']))
    if operation is not None:
        # A read-only check/download cannot turn a live or failed apply into a
        # green check result. This is presentation precedence, not authorization.
        preserve = (op['kind'] in ('apply', 'rollback')
                    and op['state'] in ACTIVE_OPERATION_STATES | {'failed', 'interrupted'}
                    and operation['kind'] in ('check', 'download'))
        if not preserve:
            op = _pick(operation, 'state kind stage started_at finished_at reported_at reason_codes')
    if last_run is not None:
        run = _last_run(last_run)
    recovery = ReasonCode.RECOVERY_REQUIRED.value in reasons or ReasonCode.OPERATION_INTERRUPTED.value in reasons
    snapshot = {
        'schema_version': 1, 'sequence': 0, 'generated_at': stamp(now), 'installation': identity,
        'versions': versions, 'policy': {'channel': policy.channel, 'version_policy': policy.version_policy,
                                       'check_interval_seconds': policy.check_interval_seconds},
        'automation': {'mode': policy.mode, 'enabled': enabled, 'state': effective,
                       'paused_until': stamp(pause_until), 'pause_indefinite': control.pause_indefinite,
                       'maintenance_window': window_json, 'last_tick_at': stamp(state.last_tick_at),
                       'next_check_at': stamp(next_check), 'next_action_at': stamp(next_action_at)},
        'discovery': old_discovery, 'download': cached, 'operation': op, 'last_run': run,
        'safety': {'backup_required': True, 'backup_state': 'not_evaluated', 'readiness': 'unknown',
                   'automatic_rollback': 'ineligible' if policy.automatic_rollback else 'disabled',
                   'operator_action': 'review_recovery' if recovery else 'initialize_then_apply'
                   if binding.production and policy.mode == 'apply' else 'inspect_host' if blocked else 'none'},
    }
    if safety is not None:
        snapshot['safety'] = _pick(safety, 'backup_required backup_state readiness automatic_rollback operator_action')
    elif previous is not None and not reasons and operation is None:
        snapshot['safety'] = copy.deepcopy(previous['safety'])
        snapshot['safety']['automatic_rollback'] = 'ineligible' if policy.automatic_rollback else 'disabled'
    validate_public_status(snapshot, expected_installation=identity, now=now, previous=previous)
    return snapshot


@dataclass(frozen=True)
class PublicationTicket:
    generation: int = 0
    operation_token: str = ''  # Private, never part of status.json.


class PublicationError(StoreError):
    def __init__(self):
        super().__init__(ReasonCode.STATUS_REPORTING_UNAVAILABLE)


def _require(ok):
    if not ok:
        raise PublicationError()


class PublicStatusPublisher:
    """Per-install generation AND operation-token CAS, serialized by public.lock.

    Private intent is committed before public replacement. A failed publication
    consumes its generation; reload a ticket instead of replaying an old write.
    The previous sanitized snapshot is private and can survive a missing mount.
    All callers, including manual/control adapters, must use this same publisher.
    """
    def __init__(self, store, public_parent: Path):
        self.store = store
        self.binding = store.read_registration().binding
        self.parent = Path(public_parent)
        _require(self.parent.is_absolute() and '..' not in self.parent.parts
                 and '.futureagi-updates' not in self.parent.parts)
        self.path = self.parent / self.binding.slot_id
        self.state = StateDir(store.checkout)
        self.key = hashlib.sha256(('public:' + self.binding.slot_id).encode()).hexdigest()

    def _read(self):
        value = self.state.read_private('automation', self.key)
        if value is None:
            return PublicationTicket(), None
        _require(type(value) is dict and set(value) == {'generation', 'operation_token', 'snapshot'})
        generation, token, snapshot = value['generation'], value['operation_token'], value['snapshot']
        _require(type(generation) is int and 1 <= generation <= MAX_SEQUENCE
                 and type(token) is str and len(token) <= 128)
        validate_public_status(snapshot, expected_installation=installation_identity(self.binding))
        _require(snapshot['sequence'] == generation)
        return PublicationTicket(generation, token), snapshot

    def capture(self):
        """Return (ticket, private presentation snapshot); never read public JSON."""
        with self.store._lock('public.lock'):
            return self._read()

    @contextmanager
    def _directory(self):
        fds = []
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            fds.append(fd)
            # Do not resolve: reject symlinks in EVERY ancestor, including parent.
            parts = self.path.parts[1:]
            for index, part in enumerate(parts):
                dedicated = index >= len(parts) - 2
                created = False
                if dedicated:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                        created = True
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                parent_fd = fd
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                fds.append(fd)
                info = os.fstat(fd)
                _require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022)
                if created:
                    # Scheduler launchers intentionally use umask 077. Set the
                    # public mode only on directories WE exclusively created;
                    # never repair/chown an existing operator-owned mount.
                    _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
                    os.fchmod(fd, 0o755)
                    os.fsync(fd)
                    os.fsync(parent_fd)
                    info = os.fstat(fd)
                if dedicated:
                    _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o755)
            yield fd
        finally:
            for fd in reversed(fds):
                os.close(fd)

    @staticmethod
    def _existing(directory):
        try:
            fd = os.open('status.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        except FileNotFoundError:
            return
        try:
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                     and stat.S_IMODE(info.st_mode) == 0o644 and info.st_nlink == 1)
        finally:
            os.close(fd)

    def _replace(self, snapshot):
        raw = (json.dumps(snapshot, separators=(',', ':'), sort_keys=True) + '\n').encode('utf-8')
        with self._directory() as directory:
            self._existing(directory)
            name = '.status-' + uuid.uuid4().hex + '.tmp'
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fchmod(handle.fileno(), 0o644)
                    from .envcommit import sync_file
                    sync_file(handle.fileno())
                self._existing(directory)
                os.replace(name, 'status.json', src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    pass

    def publish(self, snapshot, *, expected: PublicationTicket, operation_token: str = ''):
        """Return the new ticket; failures are warnings at the runner boundary."""
        _require(isinstance(expected, PublicationTicket) and type(expected.generation) is int
                 and 0 <= expected.generation <= MAX_SEQUENCE and type(expected.operation_token) is str
                 and len(expected.operation_token) <= 128 and type(operation_token) is str
                 and len(operation_token) <= 128)
        snapshot = copy.deepcopy(snapshot)
        validate_public_status(snapshot, expected_installation=installation_identity(self.binding))
        try:
            with self.store._lock('public.lock'):
                ticket, previous = self._read()
                if ticket != expected:
                    raise StaleGeneration()
                _require(ticket.generation < MAX_SEQUENCE)
                snapshot['sequence'] = ticket.generation + 1
                validate_public_status(snapshot, expected_installation=installation_identity(self.binding), previous=previous)
                # Persist sequence first, so failure/crash can never roll CAS back.
                self.state.write_private('automation', self.key, {'generation': snapshot['sequence'],
                                         'operation_token': operation_token, 'snapshot': snapshot})
                self._replace(snapshot)
                return PublicationTicket(snapshot['sequence'], operation_token)
        except StaleGeneration:
            raise
        except Exception:
            raise PublicationError() from None

    def heartbeat(self, *, expected, now: datetime, last_tick_at=None):
        """Only heartbeat fields change. In particular progress/as-of stay old."""
        ticket, snapshot = self.capture()
        if ticket != expected:
            raise StaleGeneration()
        _require(snapshot is not None)
        snapshot['generated_at'] = stamp(now)
        if last_tick_at is not None:
            snapshot['automation']['last_tick_at'] = stamp(last_tick_at)
        # An expiring pause changes effective controls; use build_status instead
        # of heartbeat for control changes. Validation refuses inconsistent state.
        return self.publish(snapshot, expected=ticket, operation_token=ticket.operation_token)
