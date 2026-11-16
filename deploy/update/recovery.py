"""Private attempt-linked write-ahead recovery authority (caller holds both locks).

The checkpoint is bounded; immutable operation evidence is never overwritten.
A checkpoint write is a CAS against the bytes originally read. An I/O error
poisons this instance: reopen and reconcile, never continue from memory. No
method starts Docker or infers that a timed-out remote operation has finished.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .contracts import BackupSeal
from .manifest import canonical_json

MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_OPERATIONS = 4096
PHASES = (
    'attempt_reserved', 'quiescing', 'quiesced', 'archiving', 'backup_sealed',
    'env_commit_pending', 'env_selected', 'target_create_pending', 'target_created',
    'target_starting', 'target_verifying', 'success_decided', 'success_metadata_committed',
    'succeeded', 'restore_decided', 'restore_quiescing', 'restore_quiesced',
    'restore_inputs_verified', 'restoring_volumes', 'restore_files_verified',
    'config_revert_pending', 'config_reverted', 'restore_data_committed',
    'previous_create_pending', 'previous_created', 'previous_start_armed',
    'previous_starting', 'previous_verifying', 'restore_verified',
    'restore_metadata_committed', 'restored', 'abort_quiescence', 'failed_available')
VOLUME_PHASES = ('clear_intent', 'empty', 'extract_intent', 'extracted', 'verified')


class RecoveryError(RuntimeError):
    def __init__(self, code='recovery_required'):
        self.code = code
        self.recovery_required = True
        super().__init__(code)


def _need(value, code='recovery_required'):
    if not value:
        raise RecoveryError(code)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(RecoveryError()))


def _serial(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serial(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serial(v) for v in value]
    return value


class RecoveryLedger:
    """One authority in destination/<reserved attempt>/recovery.json.

    reserve() must run before ANY Docker mutation. The caller supplies the
    enrolled path, not a path from a release/proof. open() checks exact attempt,
    token, run and binding when supplied. Use snapshot (defensive copy), phase,
    direction, backup_seal, checkpoint(), begin()/complete(), decide_restore(),
    decide_success(), volume_phase(), and arm_previous_start().
    """

    def __init__(self, directory, document, raw):
        self.directory = Path(directory)
        self._doc, self._raw, self._poisoned = document, raw, False

    @classmethod
    def reserve(cls, destination, expected, *, operation_token, run_id, binding_sha256,
                old_head=None, source_observation_sha256=None):
        from .standalone_backup import _directory, _atomic, sync_dir
        _need(re.fullmatch(r'[0-9a-f]{32}', expected.attempt_id) is not None)
        _need(isinstance(operation_token, str) and 1 <= len(operation_token) <= 128)
        _need(isinstance(run_id, str) and 1 <= len(run_id) <= 128)
        _need(re.fullmatch(r'[0-9a-f]{64}', binding_sha256) is not None)
        doc = dict(schema_version=2, attempt_id=expected.attempt_id,
                   operation_token=operation_token, run_id=run_id, binding_sha256=binding_sha256,
                   expectation=dict(attempt_id=expected.attempt_id,
                       installation_fingerprint=expected.installation_fingerprint,
                       target_manifest_hash=expected.target_manifest_hash,
                       source_version=expected.source_version, reserved_at=expected.reserved_at.isoformat(),
                       covered_domains=list(expected.covered_domains)),
                   old_head=old_head, source_observation_sha256=source_observation_sha256,
                   direction='forward_pending', phase='attempt_reserved', last_verified_phase='attempt_reserved',
                   sequence=0, pending=None, backup_seal=None, success_seal=None,
                   previous_start_armed=False, volumes={}, helpers={}, evidence={}, blocked_reason=None)
        raw = canonical_json(doc)
        _need(len(raw) <= MAX_LEDGER_BYTES)
        with _directory(Path(destination)) as parent:
            os.mkdir(expected.attempt_id, 0o700, dir_fd=parent)
            sync_dir(parent)
        directory = Path(destination) / expected.attempt_id
        with _directory(directory) as fd:
            _atomic(fd, 'attempt-intent.json', raw)
            _atomic(fd, 'recovery.json', raw)
        return cls(directory, doc, raw)

    @classmethod
    def open(cls, directory, *, expected=None, operation_token=None, run_id=None, binding_sha256=None):
        from .standalone_backup import _directory, _read
        try:
            with _directory(Path(directory)) as fd:
                raw = _read(fd, 'recovery.json', MAX_LEDGER_BYTES)
                intent = _json(_read(fd, 'attempt-intent.json', MAX_LEDGER_BYTES))
            doc = _json(raw)
            _need(doc['schema_version'] == 2 and doc['phase'] in PHASES)
            _need(doc['direction'] in ('forward_pending', 'restore_decided', 'success_decided'))
            for key in ('attempt_id', 'operation_token', 'run_id', 'binding_sha256', 'expectation', 'old_head'):
                _need(doc[key] == intent[key])
            _need(Path(directory).name == doc['attempt_id'])
            _need(type(doc['sequence']) is int and 0 <= doc['sequence'] <= MAX_OPERATIONS)
            if expected is not None:
                _need(doc['attempt_id'] == expected.attempt_id)
                _need(doc['expectation'] == dict(attempt_id=expected.attempt_id,
                    installation_fingerprint=expected.installation_fingerprint,
                    target_manifest_hash=expected.target_manifest_hash, source_version=expected.source_version,
                    reserved_at=expected.reserved_at.isoformat(), covered_domains=list(expected.covered_domains)))
            for key, value in (('operation_token', operation_token), ('run_id', run_id), ('binding_sha256', binding_sha256)):
                if value is not None:
                    _need(doc[key] == value)
            _need(not doc['previous_start_armed'] or doc['direction'] == 'restore_decided')
            ledger = cls(directory, doc, raw)
            # Immutable decisions/barrier win over a stale checkpoint. Never
            # roll a decision back after a crash between evidence and checkpoint.
            with _directory(Path(directory)) as fd:
                leaves = set(os.listdir(fd))
                if 'backup-seal.json' in leaves:
                    saved = _json(_read(fd, 'backup-seal.json'))
                    _need(all(saved[k] == doc[k] for k in ('attempt_id', 'operation_token', 'run_id', 'binding_sha256')))
                    _need(doc['backup_seal'] in (None, saved))
                    if doc['backup_seal'] is None:
                        ledger._save(dict(doc, backup_seal=saved, phase='backup_sealed'))
                        doc = ledger._doc
                _need(not {'restore-decision.json', 'success-decision.json'} <= leaves)
                for leaf, direction in (('restore-decision.json', 'restore_decided'),
                                        ('success-decision.json', 'success_decided')):
                    if leaf in leaves:
                        seal = _json(_read(fd, leaf))
                        _need(seal['attempt_id'] == doc['attempt_id'] and seal['operation_token'] == doc['operation_token'])
                        _need(doc['direction'] in ('forward_pending', direction))
                        if direction == 'restore_decided':
                            _need(seal['backup_seal'] == doc['backup_seal'])
                        elif doc['success_seal'] is not None:
                            _need(seal['success_seal'] == doc['success_seal'])
                        if doc['direction'] != direction:
                            ledger._save(dict(doc, direction=direction, phase=direction,
                                              success_seal=seal.get('success_seal', doc['success_seal'])))
                            doc = ledger._doc
                _need(doc['backup_seal'] is None or 'backup-seal.json' in leaves)
                _need(doc['direction'] == 'forward_pending' or
                      ('restore-decision.json' if doc['direction'] == 'restore_decided' else 'success-decision.json') in leaves)
                _need(not doc['previous_start_armed'] or 'previous-start-armed.json' in leaves)
                if 'previous-start-armed.json' in leaves:
                    _need(doc['direction'] == 'restore_decided')
                    barrier = _json(_read(fd, 'previous-start-armed.json'))
                    _need(all(barrier[k] == doc[k] for k in ('attempt_id', 'operation_token', 'backup_seal')))
                    if not doc['previous_start_armed']:
                        ledger._save(dict(doc, previous_start_armed=True, phase='previous_start_armed'))
                        doc = ledger._doc
                if doc['pending'] is not None:
                    pending = doc['pending']
                    intent_leaf = 'op-%04d-intent.json' % pending['sequence']
                    _need(intent_leaf in leaves and _json(_read(fd, intent_leaf)) == pending)
                    result_leaf = 'op-%04d-result.json' % pending['sequence']
                    if result_leaf in leaves:
                        result_raw = _read(fd, result_leaf)
                        result = _json(result_raw)
                        _need(result['operation'] == pending)
                        completed = ledger._completed_document(pending, result['result'])
                        ledger._save(dict(completed, last_operation_sha256=hashlib.sha256(result_raw).hexdigest()))
            final = ledger.snapshot
            if final['phase'] in ('success_decided', 'success_metadata_committed', 'succeeded'):
                _need(final['direction'] == 'success_decided')
            if final['phase'] in PHASES[PHASES.index('restore_decided'):PHASES.index('abort_quiescence')]:
                _need(final['direction'] == 'restore_decided')
            if final['phase'] in PHASES[PHASES.index('previous_start_armed'):PHASES.index('abort_quiescence')]:
                _need(final['previous_start_armed'])
            return ledger
        except (OSError, KeyError, ValueError, TypeError, RecursionError):
            raise RecoveryError() from None

    @property
    def snapshot(self):
        return copy.deepcopy(self._doc)

    @property
    def direction(self):
        return self._doc['direction']

    @property
    def phase(self):
        return self._doc['phase']

    @property
    def backup_seal(self):
        value = self._doc['backup_seal']
        if value is None:
            return None
        value = dict(value)
        for key in ('snapshot_at', 'authorised_at'):
            value[key] = datetime.fromisoformat(value[key])
        return BackupSeal(**value)

    def _save(self, doc):
        from .standalone_backup import _directory, _read, _atomic
        _need(not self._poisoned)
        raw = canonical_json(doc)
        _need(len(raw) <= MAX_LEDGER_BYTES)
        try:
            with _directory(self.directory) as fd:
                _need(_read(fd, 'recovery.json', MAX_LEDGER_BYTES) == self._raw, 'recovery_checkpoint_conflict')
                _atomic(fd, 'recovery.json', raw, replace=True)
        except BaseException:
            self._poisoned = True
            raise
        self._doc, self._raw = doc, raw

    def _evidence(self, leaf, value):
        from .standalone_backup import _directory, _read, _atomic
        raw = canonical_json(_serial(value))
        _need(len(raw) <= MAX_LEDGER_BYTES and not self._poisoned)
        try:
            with _directory(self.directory) as fd:
                if leaf in os.listdir(fd):
                    _need(_read(fd, leaf, MAX_LEDGER_BYTES) == raw)
                else:
                    _atomic(fd, leaf, raw)
        except BaseException:
            self._poisoned = True
            raise
        return hashlib.sha256(raw).hexdigest()

    def checkpoint(self, phase=None, **fields):
        """Store bounded adapter evidence; authority fields have dedicated methods."""
        _need(not set(fields) & {'direction', 'phase', 'attempt_id', 'expectation', 'operation_token',
              'run_id', 'binding_sha256', 'old_head', 'pending', 'sequence', 'backup_seal',
              'success_seal', 'previous_start_armed', 'volumes'})
        doc = self.snapshot
        if phase is not None:
            _need(phase in PHASES)
            if self.direction == 'success_decided':
                _need(phase in ('success_decided', 'success_metadata_committed', 'succeeded'))
            elif self.direction == 'restore_decided':
                _need(phase in PHASES[PHASES.index('restore_decided'):])
            else:
                _need(phase in PHASES[:PHASES.index('success_decided')] + ('abort_quiescence', 'failed_available'))
            post_start = PHASES[PHASES.index('previous_start_armed'):PHASES.index('abort_quiescence')]
            if phase in post_start:
                _need(doc['previous_start_armed'], 'restore_start_barrier')
            if doc['previous_start_armed']:
                _need(phase in post_start)
            doc['phase'] = doc['last_verified_phase'] = phase
        fields = _serial(fields)
        for key in ('source_observation_sha256', 'containers', 'start_times', 'restart_counts',
                    'snapshot_at', 'quiescence_evidence_sha256'):
            if key in fields and doc.get(key) is not None:
                _need(fields[key] == doc[key])
        for key in ('helpers', 'target_containers'):
            if key in fields:
                _need(isinstance(fields[key], dict) and all(fields[key].get(k) == v for k, v in doc.get(key, {}).items()))
        doc.update(fields)
        self._save(doc)

    def begin(self, kind, *, identity=None, preconditions=None, postconditions=None):
        _need(self._doc['pending'] is None and self._doc['sequence'] < MAX_OPERATIONS)
        _need(isinstance(kind, str) and re.fullmatch(r'[a-z_]{1,64}', kind) is not None)
        if kind in ('clear', 'extract'):
            self.assert_data_mutable()
        if kind == 'start' and self.direction == 'restore_decided':
            _need(self._doc['previous_start_armed'], 'restore_start_barrier')
        sequence = self._doc['sequence'] + 1
        operation = dict(sequence=sequence, kind=kind, identity=_serial(identity),
                         preconditions=_serial(preconditions), postconditions=_serial(postconditions))
        self._evidence('op-%04d-intent.json' % sequence, operation)
        self._save(dict(self.snapshot, sequence=sequence, pending=operation))
        return sequence

    def _completed_document(self, operation, result):
        """Derive the completion checkpoint identically for live and crash replay.

        A source stop's independently verified result is also its availability
        bookkeeping authority. Publishing pending=None without that bookkeeping
        would strand a stopped source if the adapter died before its next write.
        No incomplete, foreign or target stop grants pre-seal restart authority.
        """
        doc = self.snapshot
        _need(doc['pending'] == operation)
        if (doc['direction'] == 'forward_pending' and doc['phase'] == 'quiescing'
                and operation['kind'] == 'stop'
                and operation['identity'] in doc.get('containers', {}).values()):
            cid = operation['identity']['Id']
            _need(isinstance(result, dict) and result.get('id') == cid and result.get('stopped') is True)
            stopped = doc.get('stopped_container_ids', [])
            if cid not in stopped:
                doc['stopped_container_ids'] = stopped + [cid]
        doc['pending'] = None
        return doc

    def complete(self, sequence, result):
        pending = self._doc['pending']
        _need(pending is not None and pending['sequence'] == sequence)
        result = _serial(result)
        completed = self._completed_document(pending, result)
        digest = self._evidence('op-%04d-result.json' % sequence, dict(operation=pending, result=result))
        self._save(dict(completed, last_operation_sha256=digest))

    def seal_backup(self, seal):
        _need(isinstance(seal, BackupSeal) and self.direction == 'forward_pending' and self._doc['pending'] is None)
        for key in ('attempt_id', 'operation_token', 'run_id', 'binding_sha256'):
            _need(getattr(seal, key) == self._doc[key])
        _need(seal.target_manifest_hash == self._doc['expectation']['target_manifest_hash']
              and seal.source_version == self._doc['expectation']['source_version'])
        value = _serial(asdict(seal))
        if self._doc['backup_seal'] is not None:
            _need(self._doc['backup_seal'] == value)
            return
        self._evidence('backup-seal.json', value)
        self._save(dict(self.snapshot, backup_seal=value, phase='backup_sealed', last_verified_phase='backup_sealed'))

    def decide_restore(self, reason='interrupted'):
        # A signal can land after immutable decision fsync but before the
        # checkpoint call even starts. Do not trust this instance's old direction.
        from .standalone_backup import _directory
        with _directory(self.directory) as fd:
            _need('success-decision.json' not in os.listdir(fd))
        _need(self.direction != 'success_decided')
        if self.direction == 'restore_decided':
            return
        _need(self.backup_seal is not None)
        self._evidence('restore-decision.json', dict(attempt_id=self._doc['attempt_id'],
            operation_token=self._doc['operation_token'], backup_seal=self._doc['backup_seal'], reason=reason))
        self._save(dict(self.snapshot, direction='restore_decided', phase='restore_decided'))

    def decide_success(self, seal):
        from .standalone_backup import _directory
        with _directory(self.directory) as fd:
            _need('restore-decision.json' not in os.listdir(fd))
        _need(self.direction != 'restore_decided' and self._doc['pending'] is None and self.backup_seal is not None)
        _need(isinstance(seal, dict) and bool(seal))
        value = _serial(seal)
        if self.direction == 'success_decided':
            _need(self._doc['success_seal'] == value)
            return
        self._evidence('success-decision.json', dict(attempt_id=self._doc['attempt_id'],
            operation_token=self._doc['operation_token'], success_seal=value))
        self._save(dict(self.snapshot, direction='success_decided', phase='success_decided', success_seal=value))

    def assert_data_mutable(self):
        _need(self.direction == 'restore_decided' and self.backup_seal is not None
              and not self._doc['previous_start_armed'], 'restore_start_barrier')
        # Re-read immutable barrier too: an old object cannot evade a barrier
        # persisted by another instance before this instance's next write.
        from .standalone_backup import _directory
        with _directory(self.directory) as fd:
            leaves = set(os.listdir(fd))
            _need('restore-decision.json' in leaves and 'success-decision.json' not in leaves)
            _need('previous-start-armed.json' not in leaves, 'restore_start_barrier')

    def volume_phase(self, volume, phase, *, restart=False):
        self.assert_data_mutable()
        _need(volume in ('app-data', 'postgres-data', 'clickhouse-data') and phase in VOLUME_PHASES)
        doc = self.snapshot
        previous = doc['volumes'].get(volume)
        if restart:
            _need(phase == 'clear_intent' and doc['pending'] is None and previous != 'verified')
        elif previous is None:
            _need(phase == 'clear_intent')
        else:
            _need(VOLUME_PHASES.index(phase) in (VOLUME_PHASES.index(previous), VOLUME_PHASES.index(previous) + 1))
        doc['volumes'][volume] = phase
        self._save(doc)

    def arm_previous_start(self):
        _need(self.direction == 'restore_decided' and self._doc['pending'] is None)
        _need(set(self._doc['volumes']) == {'app-data', 'postgres-data', 'clickhouse-data'}
              and all(v == 'verified' for v in self._doc['volumes'].values()))
        _need(self.phase in ('restore_data_committed', 'previous_create_pending', 'previous_created', 'previous_start_armed'))
        self._evidence('previous-start-armed.json', dict(attempt_id=self._doc['attempt_id'],
            operation_token=self._doc['operation_token'], backup_seal=self._doc['backup_seal']))
        self._save(dict(self.snapshot, previous_start_armed=True, phase='previous_start_armed'))

    def block(self, reason='recovery_required', guidance_id='review_recovery'):
        _need(re.fullmatch(r'[a-z_]{1,64}', reason) is not None
              and guidance_id in ('review_recovery', 'inspect_host'))
        self._save(dict(self.snapshot, blocked_reason=reason, guidance_id=guidance_id))

    def resume_direction(self):
        """Interrupted forward never retries; unsealed attempts need abort reconciliation."""
        if self.direction == 'forward_pending' and self.backup_seal is not None:
            self.decide_restore()
        return self.direction
