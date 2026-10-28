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
import re
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .contracts import (ACTIVE_OPERATION_STATES, QUALIFICATION_POLICY, AutomaticUpdateDecision,
                        AutomaticUpdateEstimate, ContractError, OperationEvent, ReasonCode, RestorationResult, validate_public_status)
from .receipt import StateDir
from .runner_state import StaleGeneration, StoreBusy, StoreError
from .schedule import utc

MAX_SEQUENCE = 2**53 - 1


def retry_busy(action, *, monotonic=None, sleep=None):
    """Retry only an unacquired short metadata lock, never engine mutation.

    A deadline AND attempt bound cover slow I/O and even a frozen test clock.
    Keep the same opaque ticket; conflicts and disk errors are not retryable.
    """
    monotonic, sleep = monotonic or time.monotonic, sleep or time.sleep
    deadline = monotonic() + 0.2
    for attempt in range(5):
        try:
            return action()
        except StoreBusy:
            remaining = deadline - monotonic()
            if attempt == 4 or remaining <= 0:
                raise
            sleep(min(0.05, remaining))


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


ESTIMATE_FIELDS = (
    'backup_seconds', 'migration_seconds', 'restore_seconds', 'extra_disk_bytes',
    'backup_required_free_bytes', 'docker_required_free_bytes', 'protected_restore_reserve_bytes',
    'forward_seconds', 'recovery_seconds', 'total_seconds', 'migration_deadline_seconds',
    'migration_stall_timeout_seconds',
)
RESTORE_SAFETY_DEFAULTS = dict(
    fallback_strategy='restore_backup', restore_state='not_needed', snapshot_at=None,
    loss_window_start=None, loss_window_end=None, loss_window_qualification='not_applicable',
    guidance_id='none', estimates=None, failed_target_version=None,
)
SUCCESS_PHASES = ('success_decided', 'success_metadata_committed', 'succeeded')


def _recovery_report(operation):
    """Explicit recovery progress/completion, not a refused manual transaction.

    These are presentation markers from the runner, never recovery authority.
    A phase-less recovery error is meaningful too; ordinary apply/rollback or
    check results cannot establish that an automatic transaction finished.
    """
    return operation is not None and (
        operation['state'] in ('restoring', 'recovery_required', 'restored')
        or operation['kind'] in ('apply', 'restore')
        and operation.get('phase') in SUCCESS_PHASES + ('failed_available',))


def _last_run(value):
    if value is None:
        return None
    result = _pick(value, 'kind automatic outcome from_version target_version finished_at fallback_outcome')
    fields = 'forward_result restore_result effective_version failed_target_version'.split()
    if any(key in value for key in fields):
        result.update(forward_result='not_started', restore_result='not_needed',
                      effective_version=None, failed_target_version=None)
        result.update({key: copy.deepcopy(value[key]) for key in fields if key in value})
    return result


def operation_projection(event, *, started_at=None, finished_at=None):
    """Typed runner event -> public fields; tokens/contexts never cross the wire."""
    if not isinstance(event, OperationEvent):
        raise ValueError('typed operation event required')
    return dict(state=event.state, kind=event.kind, stage=event.stage, phase=event.phase,
                started_at=stamp(started_at), finished_at=stamp(finished_at),
                reported_at=stamp(event.reported_at), reason_codes=_reasons(event.reason_codes))


def project_estimate(decision):
    """Only measured scalar budgets, never table names, hashes or proof details."""
    if not isinstance(decision, AutomaticUpdateDecision):
        raise ValueError('typed automatic update decision required')
    estimate = decision.estimate
    if estimate is None:
        return None
    if not isinstance(estimate, AutomaticUpdateEstimate):
        raise ValueError('typed automatic update estimate required')
    result = {name: getattr(estimate, name) for name in ESTIMATE_FIELDS}
    if any(type(value) is not int or not 0 <= value <= MAX_SEQUENCE for value in result.values()):
        raise ValueError('automatic update estimate out of public bounds')
    return result


def restoration_projection(result, *, now, started_at=None, forward_result='failed'):
    """Runner-only typed completion projection; no private evidence is serialized.

    Supply these returned operation/last_run/safety kwargs to build_status, plus
    the independently collected live observation. This function does not publish,
    recover, finalize an attempt or turn status into admission evidence.
    """
    if not isinstance(result, RestorationResult) or forward_result not in ('failed', 'interrupted'):
        raise ValueError('typed restoration result required')
    verified = result.outcome == 'restored'
    restore_state = 'verified' if verified else 'not_needed' if result.outcome == 'failed_available' else 'recovery_required'
    observation = result.observation
    effective = (observation.release_version if observation is not None and observation.ready and observation.stable
                 and result.outcome != 'recovery_required' else None)
    snapshot_at = None if result.backup_seal is None else stamp(result.backup_seal.snapshot_at)
    return dict(
        operation=dict(state='restored' if verified else 'recovery_required' if restore_state == 'recovery_required' else 'failed',
                       kind='restore' if verified or restore_state == 'recovery_required' else 'apply',
                       stage='restore', phase=result.last_verified_phase, started_at=stamp(started_at),
                       finished_at=stamp(now) if started_at is not None else None,
                       reported_at=stamp(now), reason_codes=_reasons(result.reason_codes)),
        last_run=dict(kind='apply', automatic=True, outcome='failed_during_rollout' if result.backup_seal else 'failed_before_rollout',
                      from_version=result.source_version, target_version=result.target_version, finished_at=stamp(now),
                      fallback_outcome='none', forward_result=forward_result, restore_result=restore_state,
                      effective_version=effective, failed_target_version=result.target_version),
        safety=dict(backup_required=True, backup_state='sealed' if result.backup_seal else 'not_evaluated',
                    readiness='passed' if effective else 'unknown', automatic_rollback='ineligible',
                    operator_action='review_recovery' if restore_state == 'recovery_required' else 'none',
                    **{**RESTORE_SAFETY_DEFAULTS, 'restore_state': restore_state, 'snapshot_at': snapshot_at,
                       'loss_window_start': snapshot_at, 'loss_window_end': None,
                       'loss_window_qualification': 'conservative' if snapshot_at else 'unknown',
                       'guidance_id': result.guidance_id, 'failed_target_version': result.target_version}),
    )


def build_status(binding, policy, control, state, *, now, previous=None, observation=None,
                 discovery=None, download=None, operation=None, last_run=None,
                 reason_codes=(), next_action_at=None, safety=None, automatic_decision=None):
    """Project only named fields, then validate; never dump a private dataclass.

    ``discovery`` is a DiscoveryResult from THIS tick. Failed checks retain the
    last verified candidate/as-of time. ``download`` is a freshly inspected
    DownloadReceipt, not merely a persisted receipt or an operation label.
    """
    identity = installation_identity(binding)
    if previous is not None:
        validate_public_status(previous, expected_installation=identity)
    unresolved_recovery = (previous is not None
        and previous['safety'].get('fallback_strategy') == 'restore_backup'
        and (previous['operation']['state'] in ('restoring', 'recovery_required')
             or previous['safety'].get('restore_state') in ('running', 'failed', 'recovery_required')))
    recovery_report = _recovery_report(operation)
    preserve_recovery = unresolved_recovery and not recovery_report
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
            observed_run = _last_run(observation.last_run)
            # A later read of the original failed forward receipt must not erase
            # its richer, newer restore completion. A genuinely new manual or
            # automatic attempt has a different finish time/pair and can replace it.
            richer_restore = (run is not None and run.get('restore_result') == 'verified'
                and 'forward_result' not in observed_run
                and all(run[key] == observed_run[key] for key in ('kind', 'automatic', 'from_version', 'target_version'))
                and run['finished_at'] is not None and observed_run['finished_at'] is not None
                and observed_run['finished_at'] <= run['finished_at'])
            if not richer_restore:
                run = observed_run
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
    estimates = project_estimate(automatic_decision) if automatic_decision is not None else None
    reasons = _reasons(tuple(reason_codes) + (() if automatic_decision is None else automatic_decision.reason_codes))
    if automatic_decision is not None and not automatic_decision.allowed:
        old_discovery.update(state='blocked', reason_codes=_reasons(automatic_decision.reason_codes))
    elif (operation is not None and operation['state'] == 'blocked'
          and operation['kind'] == 'check' and operation.get('reason_codes')
          and old_discovery['state'] == 'available'):
        # Selection/authorization may refuse before size preflight exists.
        # Completed restoration remains history, not the current admission result.
        old_discovery.update(state='blocked', reason_codes=_reasons(operation['reason_codes']))
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
    same_success_decision = False
    if operation is not None:
        # A read-only check/download cannot turn a live or failed apply into a
        # green check result. This is presentation precedence, not authorization.
        preserve = (op['kind'] in ('apply', 'rollback', 'restore')
                    and op['state'] in ACTIVE_OPERATION_STATES | {'failed', 'interrupted', 'recovery_required'}
                    and operation['kind'] in ('check', 'download') and not recovery_report)
        # A metadata/status failure after the durable success boundary cannot
        # rewrite that SAME operation into a failed update or authorize restore.
        same_success_decision = (op.get('phase') in SUCCESS_PHASES
            and op['started_at'] is not None and op['started_at'] == operation['started_at']
            and operation['kind'] in ('apply', 'restore')
            and operation['state'] in ('failed', 'interrupted', 'restoring', 'recovery_required'))
        preserve = preserve or same_success_decision or preserve_recovery
        if not preserve:
            op = _pick(operation, 'state kind stage started_at finished_at reported_at reason_codes')
            if 'phase' in operation:
                op['phase'] = copy.deepcopy(operation['phase'])
            if (unresolved_recovery and op['state'] in ('restoring', 'recovery_required')
                    and op.get('phase') is None and previous['operation'].get('phase') is not None):
                op['phase'] = previous['operation']['phase']
    if last_run is not None:
        run = _last_run(last_run)
    if unresolved_recovery:
        # A container-label observation or unrelated receipt cannot complete
        # recovery. Preserve the whole unresolved report, not only its banner;
        # otherwise default safety fields would claim restoration was unneeded.
        if preserve_recovery or last_run is None:
            run = _last_run(previous['last_run'])
        if preserve_recovery or safety is None and op['state'] in ('restoring', 'recovery_required'):
            safety = copy.deepcopy(previous['safety'])
    if same_success_decision:
        run = _last_run(previous['last_run'])
        safety = copy.deepcopy(previous['safety'])
    if (safety is not None and safety.get('restore_state') == 'verified' and run is not None
            and run.get('restore_result') == 'verified' and run.get('effective_version') is not None):
        # An engine RestoreSeal is fresh source-runtime evidence. The legacy
        # status observer may have run BEFORE the recovery or not run at all.
        # Project only the signed release scalar, never its private inventory.
        version = run['effective_version']
        versions = {'observed_at': stamp(now), 'runtime_state': 'consistent', 'selected_release': version,
                    'running_release': version, 'components': [{'component': 'standalone',
                    'selected_version': version, 'observed_versions': [version], 'observation': 'known'}]}
    recovery = (any(reason in reasons for reason in ('recovery_required', 'operation_interrupted', 'automatic_recovery_pending'))
                or op['state'] == 'recovery_required')
    # A prior blocked recovery check is not fresh evidence of an unfinished
    # transaction. Keep failed apply history, but drop a resolved recovery block.
    if not recovery and operation is None and op['state'] == 'blocked' and any(
            value in op['reason_codes'] for value in ('recovery_required', 'operation_interrupted')):
        op = {'state': 'idle', 'kind': None, 'stage': None, 'started_at': None,
              'finished_at': None, 'reported_at': None, 'reason_codes': []}
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
        if 'fallback_strategy' in safety or policy.qualification_policy == QUALIFICATION_POLICY:
            snapshot['safety'].update(RESTORE_SAFETY_DEFAULTS)
            snapshot['safety'].update({key: copy.deepcopy(safety[key]) for key in RESTORE_SAFETY_DEFAULTS if key in safety})
    elif previous is not None and not reasons and operation is None:
        action = snapshot['safety']['operator_action']
        snapshot['safety'] = copy.deepcopy(previous['safety'])
        if snapshot['safety']['operator_action'] == 'review_recovery':
            snapshot['safety']['operator_action'] = action
        snapshot['safety']['automatic_rollback'] = 'ineligible' if policy.automatic_rollback else 'disabled'
    if policy.qualification_policy == QUALIFICATION_POLICY or automatic_decision is not None:
        for key, value in RESTORE_SAFETY_DEFAULTS.items():
            snapshot['safety'].setdefault(key, value)
        # Mandatory restoration is not controlled by the legacy optional flag.
        snapshot['safety']['fallback_strategy'] = 'restore_backup'
        snapshot['safety']['automatic_rollback'] = 'ineligible'  # No image-only fallback eligibility claim.
        latch = state.failed_target_latch
        snapshot['safety']['failed_target_version'] = None if latch is None else latch.version
        if automatic_decision is not None:
            snapshot['safety']['estimates'] = estimates
        if safety is None and type(op.get('phase')) is str:
            phase = op['phase']
            if phase == 'quiescing':
                snapshot['safety']['backup_state'] = 'quiescing'
            elif phase in ('quiesced', 'archiving'):
                snapshot['safety']['backup_state'] = 'pending'
            elif phase in ('backup_sealed', 'env_commit_pending', 'env_selected', 'target_create_pending',
                           'target_created', 'target_starting', 'target_verifying', 'success_decided',
                           'success_metadata_committed', 'succeeded') or phase.startswith(('restore_', 'restoring_', 'previous_')):
                snapshot['safety']['backup_state'] = 'sealed'
            if phase in ('restore_verified', 'restore_metadata_committed', 'restored'):
                snapshot['safety']['restore_state'] = 'verified'
        # Runtime uncertainty is meaningful even before a phase can be read.
        # Accepted success needs metadata/availability reconciliation, never a
        # restore, so retain that explicit exception to recovery-state inference.
        accepted_success = (op.get('phase') in SUCCESS_PHASES
            or op['state'] == 'recovery_required' and recovery_report and last_run is not None
            and last_run.get('automatic') is True and last_run.get('forward_result') == 'succeeded'
            and last_run.get('restore_result') == 'not_needed')
        if accepted_success:
            snapshot['safety']['restore_state'] = 'not_needed'
        elif op['state'] == 'recovery_required':
            snapshot['safety']['restore_state'] = 'recovery_required'
        elif op['state'] == 'restoring':
            snapshot['safety']['restore_state'] = 'running'
    if snapshot['safety'].get('restore_state') in ('running', 'recovery_required', 'failed') or op['state'] in ('restoring', 'recovery_required'):
        # Old healthy A (or failed B) is not a live version observation while
        # stores are cold/unverified. Do not advertise availability from history.
        snapshot['versions'] = {'observed_at': None, 'runtime_state': 'unknown', 'selected_release': None,
                                'running_release': None, 'components': []}
        if run is not None and 'effective_version' in run:
            run['effective_version'] = None
    if 'restore_state' in snapshot['safety']:
        if recovery:
            snapshot['safety'].update(guidance_id='review_recovery', operator_action='review_recovery')
        elif blocked and snapshot['safety']['guidance_id'] == 'none':
            snapshot['safety']['guidance_id'] = 'inspect_host'
    validate_public_status(snapshot, expected_installation=identity, now=now, previous=previous)
    restoration_semantics(snapshot)
    return snapshot


def restoration_semantics(snapshot):
    """Extra cross-field checks for the additive restore group, after shape validation."""
    safety = snapshot['safety']
    if 'restore_state' not in safety:
        return
    start, end = safety['loss_window_start'], safety['loss_window_end']
    if (end is not None and (start is None or end < start)
            or safety['loss_window_qualification'] == 'bounded' and (start is None or end is None)):
        raise ContractError()
    def parse(value):
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    generated = parse(snapshot['generated_at'])
    if any(value is not None and (parse(value) - generated).total_seconds() > 300
           for value in (safety['snapshot_at'], start, end)):
        raise ContractError()
    if (safety['restore_state'] in ('running', 'failed', 'recovery_required')
            or snapshot['operation']['state'] in ('restoring', 'recovery_required')):
        run = snapshot['last_run']
        if (snapshot['versions']['runtime_state'] != 'unknown' or snapshot['versions']['running_release'] is not None
                or run is not None and run.get('effective_version') is not None):
            raise ContractError()


@dataclass(frozen=True)
class PublicationTicket:
    generation: int = 0
    operation_token: str = ''  # Private, never part of status.json.
    operation_epoch: Optional[int] = None  # None supports the original two-field tickets.

    @property
    def epoch(self):
        return self.generation if self.operation_epoch is None else self.operation_epoch


class PublicationConflict(StaleGeneration):
    """A newer operation won; not a registration or host-control failure."""
    def __init__(self):
        super().__init__(ReasonCode.STATUS_REPORTING_UNAVAILABLE)


class PublicationError(StoreError):
    def __init__(self):
        super().__init__(ReasonCode.STATUS_REPORTING_UNAVAILABLE)


def _require(ok):
    if not ok:
        raise PublicationError()


class PublicStatusPublisher:
    """Monotonic sequence plus operation-epoch/token CAS under public.lock.

    Only operation-bearing publish() advances the epoch. Controls, heartbeats
    and observation-only exports never take ownership of an operation.
    An operation captures once before slow work and retains each returned ticket.
    Busy locks consume nothing; skip that report, not the owner's later reports.
    A conflict fences an older operation permanently: never recapture and replay.
    Private intent precedes public replacement, so an I/O failure can consume an
    epoch. The private sanitized snapshot survives a missing public mount.
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
        _require(type(value) is dict and set(value) in (
            {'generation', 'operation_token', 'snapshot'},
            {'generation', 'operation_epoch', 'operation_token', 'snapshot'}))
        generation, token, snapshot = value['generation'], value['operation_token'], value['snapshot']
        epoch = value.get('operation_epoch', generation)  # Original reports were all operation-bearing.
        _require(type(generation) is int and 1 <= generation <= MAX_SEQUENCE
                 and type(epoch) is int and 0 <= epoch <= generation
                 and type(token) is str and len(token) <= 128)
        validate_public_status(snapshot, expected_installation=installation_identity(self.binding))
        restoration_semantics(snapshot)
        _require(snapshot['sequence'] == generation)
        return PublicationTicket(generation, token, epoch), snapshot

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
                intent_key = hashlib.sha256(('public-directory:' + str(Path('/', *parts[:index + 1]))).encode()).hexdigest()
                parent_info = os.fstat(fd)
                parent_identity = [parent_info.st_dev, parent_info.st_ino]
                created_identity = None
                if dedicated:
                    try:
                        os.stat(part, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        try:
                            os.mkdir(part, 0o700, dir_fd=fd)
                        except FileExistsError:
                            # Another creator won. Never claim its directory.
                            raise PublicationError() from None
                        created = os.stat(part, dir_fd=fd, follow_symlinks=False)
                        created_identity = [created.st_dev, created.st_ino]
                        os.fsync(fd)
                parent_fd = fd
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                fds.append(fd)
                info = os.fstat(fd)
                _require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022)
                if dedicated and stat.S_IMODE(info.st_mode) == 0o700:
                    intent = self.state.read_private('automation', intent_key)
                    identity = [info.st_dev, info.st_ino]
                    _require(info.st_uid == os.geteuid() and (created_identity == identity
                             or type(intent) is dict and set(intent) == {'parent', 'directory'}
                             and intent['parent'] == parent_identity and intent['directory'] == identity))
                    # Bind durably before chmod. A crash before this binding is
                    # ambiguous, even if an old pre-mkdir intent exists and the
                    # directory is empty: refuse and require manual remediation.
                    self.state.write_private('automation', intent_key,
                                             {'parent': parent_identity, 'directory': identity})
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

    @staticmethod
    def _clean_temps(directory):
        for name in os.listdir(directory):
            if re.fullmatch(r'\.status-[0-9a-f]{32}\.tmp', name):
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid():
                    os.unlink(name, dir_fd=directory)  # Unlink only; never open a target.

    def _replace(self, snapshot):
        raw = (json.dumps(snapshot, separators=(',', ':'), sort_keys=True) + '\n').encode('utf-8')
        with self._directory() as directory:
            self._existing(directory)
            self._clean_temps(directory)
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

    def _commit(self, snapshot, ticket, previous, *, epoch, token):
        """Called only under public.lock, including cleanup and atomic replacement."""
        _require(ticket.generation < MAX_SEQUENCE)
        snapshot['sequence'] = ticket.generation + 1
        validate_public_status(snapshot, expected_installation=installation_identity(self.binding), previous=previous)
        self.state.write_private('automation', self.key, {'generation': snapshot['sequence'],
                                 'operation_epoch': epoch, 'operation_token': token, 'snapshot': snapshot})
        self._replace(snapshot)
        return PublicationTicket(snapshot['sequence'], token, epoch)

    def publish(self, snapshot, *, expected: PublicationTicket, operation_token: str = '', observation_only=False):
        """Fence operations by epoch, exports by generation; neither replays stale evidence.

        Observation-only exports never own an epoch or change operation/safety.
        They refuse ANY intervening report, including another export, rather
        than overwriting newer observations. The caller must observe again.
        """
        _require(isinstance(expected, PublicationTicket) and type(expected.generation) is int
                 and 0 <= expected.generation <= MAX_SEQUENCE and type(expected.operation_token) is str
                 and len(expected.operation_token) <= 128 and type(expected.epoch) is int
                 and 0 <= expected.epoch <= expected.generation and type(operation_token) is str
                 and len(operation_token) <= 128)
        snapshot = copy.deepcopy(snapshot)
        validate_public_status(snapshot, expected_installation=installation_identity(self.binding))
        restoration_semantics(snapshot)
        try:
            with self.store._lock('public.lock'):
                ticket, previous = self._read()
                if (ticket.epoch, ticket.operation_token) != (expected.epoch, expected.operation_token):
                    raise PublicationConflict()
                _require(expected.generation <= ticket.generation)
                if observation_only:
                    if ticket.generation != expected.generation:
                        raise PublicationConflict()
                    if previous is not None:
                        for field in ('operation', 'safety'):
                            snapshot[field] = copy.deepcopy(previous[field])
                    return self._commit(snapshot, ticket, previous, epoch=ticket.epoch, token=ticket.operation_token)
                if ticket.generation != expected.generation:
                    # Only control/heartbeat writes can have intervened. Rebase
                    # their fields, never their old operation or observations.
                    blocked = snapshot['automation']['state'] == 'blocked'
                    snapshot['policy'] = copy.deepcopy(previous['policy'])
                    snapshot['automation'] = copy.deepcopy(previous['automation'])
                    snapshot['generated_at'] = max(snapshot['generated_at'], previous['generated_at'])
                    automation = snapshot['automation']
                    until = automation['paused_until']
                    paused = automation['pause_indefinite'] or until is not None and until > snapshot['generated_at']
                    effective = ('disabled' if not automation['enabled'] else 'paused' if paused
                                 else 'manual' if automation['mode'] == 'manual' else 'blocked' if blocked else 'enabled')
                    automation['state'] = effective
                    if effective == 'enabled':
                        state = self.store.read_state()
                        automation['next_check_at'] = stamp(state.retry_not_before or state.next_check_at)
                        if previous['automation']['state'] == 'paused':
                            automation['next_action_at'] = None
                    else:
                        automation['next_check_at'] = automation['next_action_at'] = until if effective == 'paused' else None
                return self._commit(snapshot, ticket, previous, epoch=ticket.epoch + 1, token=operation_token)
        except (StaleGeneration, StoreBusy):
            raise
        except Exception:
            raise PublicationError() from None

    def publish_controls(self, *, policy, now: datetime, last_tick_at=None):
        """RMW the newest private report; no ticket and no operation token needed.

        Read durable control/state under public.lock, not an earlier CLI capture.
        Preserve operation, safety and every observation/as-of timestamp exactly.
        This also supplies scheduler heartbeats, including pause expiry.
        """
        try:
            with self.store._lock('public.lock'):
                ticket, previous = self._read()
                projected = build_status(self.binding, policy, self.store.read_control(), self.store.read_state(),
                                         now=now, previous=previous)
                if previous is None:
                    snapshot = projected
                else:
                    snapshot = copy.deepcopy(previous)
                    snapshot.update(generated_at=projected['generated_at'], policy=projected['policy'],
                                    automation=projected['automation'])
                if last_tick_at is not None:
                    snapshot['automation']['last_tick_at'] = stamp(last_tick_at)
                return self._commit(snapshot, ticket, previous, epoch=ticket.epoch, token=ticket.operation_token)
        except StoreBusy:
            raise
        except Exception:
            raise PublicationError() from None

    def heartbeat(self, *, now: datetime, last_tick_at=None, expected=None):
        """RMW only heartbeat fields. ``expected`` is accepted but never fences.

        Use publish_controls for effective controls (including pause expiry).
        """
        try:
            with self.store._lock('public.lock'):
                ticket, previous = self._read()
                _require(previous is not None)
                snapshot = copy.deepcopy(previous)
                snapshot['generated_at'] = stamp(now)
                if last_tick_at is not None:
                    snapshot['automation']['last_tick_at'] = stamp(last_tick_at)
                return self._commit(snapshot, ticket, previous, epoch=ticket.epoch, token=ticket.operation_token)
        except StoreBusy:
            raise
        except Exception:
            raise PublicationError() from None
