"""In-place storage adapter, NOT an updater. Caller holds runner -> update locks.

Never starts an application/dependency, pulls an image, replaces a volume, copies
failed candidate data, or chooses forward vs restore. The original sealed proof
is the authority; age/pause/window do not revoke mandatory recovery. Every Docker
helper is created, independently inspected, then started by its full ID with a
write-ahead intent. Unknown operations block instead of guessing a retry is safe.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import time
import uuid

from .archive_inventory import verify_archive
from .backup_proof import validate_backup_proof
from .manifest import canonical_json
from .recovery import RecoveryError, RecoveryLedger
from .standalone_backup import (BackupBinding, BackupPolicy, VOLUMES, CONFIG_FILES,
    _command, _directory, _files, _file_identity, _hash, _identity, _ID, _IMAGE, _json,
    _new, _private, _read, _volume, _all_containers, sync_file)

RESTORE_FS_SHA256 = '3439e946a0869ed618ea33b881da9cd5c3bc85bd3ff86a7c1ef9660327e53db6'
HELPER_LABEL = 'ai.futureagi.restore-attempt'


class RestoreError(RecoveryError):
    pass


def _need(value, reason='recovery_required'):
    if not value:
        raise RestoreError(reason)


@dataclass(frozen=True)
class VolumeStorage:
    """Actual daemon capacity; filesystem is the backing-device grouping identity.

    The default fixed probe supplies Linux st_dev, not merely a label like ext4.
    An injected callback may return this or docker_host.VolumeFilesystem; the
    latter is checked for the exact enrolled role/name and grouped by device.
    """
    root_identity: str
    filesystem: str
    free_bytes: int
    free_inodes: int


@dataclass(frozen=True)
class RestoreInspection:
    direction: str
    phase: str
    backup_sealed: bool
    previous_start_armed: bool
    pending_operation: bool


class _StorageHelpers:
    """Shared fixed helper protocol used for backup and restore. No --rm/run."""
    def __init__(self, installation, binding, ledger, *, policy, helper_image_id=None):
        self.installation, self.b, self.ledger, self.policy = installation, binding, ledger, policy
        self.context = binding['context']
        self.python_image = helper_image_id or binding['images']['app']
        _need(self.python_image in binding['images'].values() and _IMAGE.fullmatch(self.python_image),
              'restore_child_ambiguous')
        self.deadline = min(installation.runner.deadline, time.monotonic() + policy.timeout_seconds)
        source = Path(__file__).with_name('probes') / 'restore_fs.py'
        with source.open('rb') as stream:
            raw = stream.read(65537)
        _need(len(raw) <= 65536 and _hash(raw) == RESTORE_FS_SHA256, 'restore_verification_failed')
        self.source = raw.decode('utf-8')

    def command(self, *args):
        _need(time.monotonic() < self.deadline, 'restore_child_ambiguous')
        return _command(self.installation, self.context, *args,
                        timeout=max(0, self.deadline - time.monotonic()))

    def known_helpers(self):
        return self.ledger.snapshot['helpers']

    def _retire(self, cid):
        """Retain inspections/IDs, not foreign stopped volume attachments.

        Normal runtime observation must not need an exemption for updater
        helpers. Never use rm -v/--force: no volume or running object is removed.
        """
        doc = self.ledger.snapshot
        source_and_target = list(doc.get('containers', {}).values()) + list(doc.get('target_containers', {}).values())
        _need(cid not in {row['Id'] for row in source_and_target}, 'restore_child_ambiguous')
        pending = doc['pending']
        if pending is not None:
            _need(pending['kind'] == 'helper_remove' and pending['identity'] == {'id': cid},
                  'restore_child_ambiguous')
        ids = self.command('ps', '-aq', '--no-trunc', '--filter', 'id=' + cid).split()
        _need(not ids or ids == [cid], 'restore_child_ambiguous')
        if ids:
            row = _json(self.installation, self.context, 'inspect', cid)[0]
            _need(cid in self.known_helpers() and _identity(row) == self.known_helpers()[cid]['identity']
                  and not any(row['State'].get(k) for k in ('Running', 'Paused', 'Restarting', 'Dead'))
                  and row['State']['Status'] in ('created', 'exited'), 'restore_child_ambiguous')
        if pending is None:
            sequence = self.ledger.begin('helper_remove', identity={'id': cid})
        else:
            sequence = pending['sequence']
        if ids:
            self.command('rm', cid)
        _need(not self.command('ps', '-aq', '--no-trunc', '--filter', 'id=' + cid).strip(),
              'restore_child_ambiguous')
        self.ledger.complete(sequence, {'id': cid, 'absent': True})

    def retire_helpers(self):
        """Metadata-only reconciliation of positively identified completed helpers."""
        pending = self.ledger.snapshot['pending']
        if pending is not None:
            _need(pending['kind'] == 'helper_remove' and pending['identity']['id'] in self.known_helpers(),
                  'restore_child_ambiguous')
            self._retire(pending['identity']['id'])
        present = {c['Id'] for c in _all_containers(self.installation, self.context)}
        for cid in self.known_helpers():
            if cid in present:
                self._retire(cid)

    def assert_no_running_consumers(self, key, *, except_id=None):
        volume = self.b['volumes'][key]
        sources = volume['Mountpoint']
        known = list(self.ledger.snapshot.get('containers', {}).values())
        known += list(self.ledger.snapshot.get('target_containers', {}).values())
        known_ids = {value['Id']: value for value in known}
        helpers = self.known_helpers()
        for container in _all_containers(self.installation, self.context):
            mounts = any(m.get('Name') == volume['Name'] or m.get('Source') and (
                m['Source'] == sources or m['Source'].startswith(sources + '/')
                or sources.startswith(m['Source'].rstrip('/') + '/')) for m in container['Mounts'])
            if not mounts:
                continue
            cid = container['Id']
            if cid == except_id:
                _need(cid in helpers and _identity(container) == helpers[cid]['identity'], 'restore_child_ambiguous')
                continue
            _need(cid in known_ids or cid in helpers, 'restore_child_ambiguous')
            expected = known_ids.get(cid) if cid in known_ids else helpers[cid]['identity']
            _need(_identity(container) == expected, 'restore_child_ambiguous')
            _need(not any(container['State'].get(k) for k in ('Running', 'Paused', 'Restarting', 'Dead')),
                  'restore_child_ambiguous')

    def _image_volumes(self, image):
        row = _json(self.installation, self.context, 'image', 'inspect', image)[0]
        _need(row['Id'] == image and row.get('Os') == 'linux', 'restore_child_ambiguous')
        return sorted((row.get('Config') or {}).get('Volumes') or {})

    def execute(self, key, purpose, *, writable=False, tar=False, archive_fd=None,
                output_fd=None, max_output=None, archive_identity=None):
        _need(key in VOLUMES and self.ledger.snapshot['pending'] is None, 'restore_child_ambiguous')
        if purpose in ('clear', 'extract'):
            self.ledger.assert_data_mutable()
        self.assert_no_running_consumers(key)
        root = VOLUMES[key][1]
        image = self.b['images']['postgres'] if tar else self.python_image
        sequence = self.ledger.snapshot['sequence'] + 1
        name = self.b['project'] + '-restore-' + self.ledger.snapshot['attempt_id'][:12] + '-%04d' % sequence
        entrypoint = '/bin/tar' if tar else '/usr/local/bin/python3'
        if purpose == 'archive':
            command = ['--format=pax', '--numeric-owner', '--acls', '--xattrs', '--sparse', '-C', root, '-cpf', '-', '.']
        elif purpose == 'extract':
            command = ['--numeric-owner', '--same-owner', '--acls', '--xattrs', '--delay-directory-restore', '-xpf', '-', '-C', root]
        else:
            # Joined -c avoids confusing Docker's global context short-option
            # guard; it does not weaken the supervisor's routing validation.
            command = ['-I', '-B', '-S', '-c' + self.source, purpose, root]
            if purpose not in ('inspect',):
                command.append(self.b['volume_root_ids'][key])
            if purpose == 'inventory':
                command.extend([str(self.policy.max_members), str(self.policy.max_archive_bytes)])
        helper_seconds = max(1, min(86400, int(self.deadline - time.monotonic())))
        if tar:
            entrypoint = '/usr/bin/timeout'
            command = ['--signal=TERM', '--kill-after=5', str(helper_seconds), '/bin/tar', *command]
        else:
            command.append(str(helper_seconds))
        args = ['create', '--name', name, '--label', HELPER_LABEL + '=' + self.ledger.snapshot['attempt_id'],
                '--pull', 'never', '--network', 'none', '--read-only', '--restart', 'no',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '32',
                '--memory', '256m', '--cpus', '1', '--user', '0:0']
        # Numeric-owner metadata restoration requires exactly these capabilities;
        # read-only scanners only need to bypass stored DB directory permissions.
        capabilities = ['DAC_READ_SEARCH'] if not writable else ['DAC_OVERRIDE', 'CHOWN', 'FOWNER', 'FSETID']
        for cap in capabilities:
            args.extend(['--cap-add', cap])
        args.extend(['--mount', 'type=volume,source=' + self.b['volumes'][key]['Name'] + ',target=' + root
                     + (',volume-nocopy' if writable else ',readonly,volume-nocopy')])
        unrelated = self._image_volumes(image)
        for path in unrelated:
            if path != root:
                _need(path.startswith('/') and '..' not in path.split('/') and ',' not in path, 'restore_child_ambiguous')
                args.extend(['--tmpfs', path + ':rw,noexec,nosuid,nodev,size=1m'])
        if archive_fd is not None:
            args.append('-i')
        args.extend(['--entrypoint', entrypoint, image, *command])
        op = self.ledger.begin('helper_create', identity=dict(name=name, key=key, purpose=purpose,
                               image=image, argv=args), postconditions={'status': 'created'})
        cid = self.command(*args).strip()
        _need(_ID.fullmatch(cid), 'restore_child_ambiguous')
        observed = _json(self.installation, self.context, 'inspect', cid)[0]
        self.ledger._evidence('helper-%04d-created.json' % sequence, observed)
        host, config = observed['HostConfig'], observed['Config']
        mounts = [m for m in observed['Mounts'] if m['Type'] == 'volume']
        _need(observed['Id'] == cid and observed['Name'] == '/' + name and observed['Image'] == image
              and config.get('Entrypoint') == [entrypoint] and config.get('Cmd') == command
              and config.get('Labels', {}).get(HELPER_LABEL) == self.ledger.snapshot['attempt_id']
              and host.get('NetworkMode') == 'none' and host.get('ReadonlyRootfs') is True
              and host.get('RestartPolicy', {}).get('Name') == 'no'
              and host.get('CapDrop') == ['ALL']
              and {cap.removeprefix('CAP_') for cap in host.get('CapAdd') or []} == set(capabilities)
              and host.get('SecurityOpt') == ['no-new-privileges']
              and host.get('NanoCpus') == 1000000000
              and host.get('Privileged') is False and not host.get('Binds') and not host.get('Devices')
              and host.get('PidMode') in ('', None) and host.get('IpcMode') in ('private', '')
              and host.get('Memory') == 256 * 1024 ** 2 and host.get('PidsLimit') == 32
              and len(mounts) == 1 and mounts[0].get('Name') == self.b['volumes'][key]['Name']
              and mounts[0].get('Source') == self.b['volumes'][key]['Mountpoint']
              and mounts[0].get('Destination') == root and mounts[0].get('RW') is writable
              and all(m['Type'] in ('volume', 'tmpfs') for m in observed['Mounts'])
              and set(host.get('Tmpfs') or {}) == set(unrelated) - {root}
              and observed['State']['Status'] == 'created' and not observed['State']['Running'],
              'restore_child_ambiguous')
        helpers = self.known_helpers()
        helpers[cid] = dict(identity=_identity(observed), key=key, purpose=purpose)
        self.ledger.checkpoint(helpers=helpers)
        self.ledger.complete(op, {'id': cid, 'identity_sha256': _hash(canonical_json(_identity(observed)))})
        self.assert_no_running_consumers(key)
        op = self.ledger.begin(purpose if purpose in ('clear', 'extract') else 'helper_start',
                               identity={'id': cid, 'key': key, 'purpose': purpose})
        runner = self.installation.runner
        call = [*self.installation.docker, 'start', '-a']
        if archive_fd is not None:
            call.append('-i')
        call.append(cid)
        kwargs = dict(timeout=max(0, self.deadline - time.monotonic()))
        if archive_fd is not None:
            _need(archive_identity is not None, 'backup_inventory_mismatch')
            result = runner.stream_from(call, archive_fd, max_bytes=self.policy.max_archive_bytes,
                                        expected_identity=archive_identity, **kwargs)
        elif output_fd is not None:
            result = runner.stream_to(call, output_fd, max_bytes=max_output or self.policy.max_inventory_bytes,
                                      reserve_bytes=self.policy.reserve_bytes, **kwargs)
        else:
            result = runner(call, capture_output=True, **kwargs)
        exited = _json(self.installation, self.context, 'inspect', cid)[0]
        self.ledger._evidence('helper-%04d-exited.json' % sequence, exited)
        self.ledger._evidence('helper-%04d-diagnostics.json' % sequence, dict(
            returncode=result.returncode, stderr=result.stderr.decode('utf-8', 'replace')[:65536],
            stdout=getattr(result, 'stdout', b'').decode('utf-8', 'replace')[:65536]))
        _need(_identity(exited) == helpers[cid]['identity'] and not exited['State']['Running']
              and exited['State']['Status'] == 'exited' and exited['State']['ExitCode'] == result.returncode
              and not exited['State'].get('OOMKilled') and result.returncode == 0 and not result.stderr,
              'restore_verification_failed')
        self.ledger.complete(op, {'id': cid, 'exit_code': result.returncode})
        self._retire(cid)
        return result

    def inspect(self, key):
        result = self.execute(key, 'inspect')
        try:
            value = json.loads(result.stdout)
            storage = VolumeStorage(**value)
            _need(re.fullmatch(r'[0-9]+:[0-9]+', storage.root_identity)
                  and type(storage.free_bytes) is int and storage.free_bytes >= 0
                  and type(storage.free_inodes) is int and storage.free_inodes >= 0
                  and isinstance(storage.filesystem, str) and storage.filesystem, 'restore_capacity_unknown')
            _need(storage.root_identity == self.b['volume_root_ids'][key], 'restore_verification_failed')
            return storage
        except (ValueError, TypeError, KeyError):
            raise RestoreError('restore_capacity_unknown') from None

    def inventory(self, key, directory_fd):
        leaf = 'verified-tree-' + key + '-' + uuid.uuid4().hex + '.jsonl'
        fd = _new(directory_fd, leaf)
        try:
            result = self.execute(key, 'inventory', output_fd=fd)
            sync_file(fd)
            return dict(sha256=result.sha256, size=result.size, file=leaf)
        finally:
            os.close(fd)


class StandaloneRestore:
    def __init__(self, installation, binding: BackupBinding, destination, *, policy=BackupPolicy(),
                 clock=None, helper_image_id=None, storage_inspector=None):
        self.installation, self.binding, self.destination = installation, binding, Path(destination)
        self.policy = policy
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.b = json.loads(binding.raw)
        _need(self.b['root'] == str(installation.root) and not self.b['profiles'], 'unsupported_installation')
        _need(installation.runner.docker_context == self.b['context'], 'restore_child_ambiguous')
        self.helper_image_id, self.storage_inspector = helper_image_id, storage_inspector
        self.restore_deadline = min(installation.runner.deadline, time.monotonic() + policy.timeout_seconds)
        self._archive_fds = {}
        self.inventory = self.ledger = self.helpers = None
        self._verified_inputs = False

    def close(self):
        for fd in self._archive_fds.values():
            os.close(fd)
        self._archive_fds.clear()
        self._verified_inputs = False

    def inspect(self, ledger):
        _need(isinstance(ledger, RecoveryLedger))
        doc = ledger.snapshot
        return RestoreInspection(doc['direction'], doc['phase'], doc['backup_seal'] is not None,
                                 doc['previous_start_armed'], doc['pending'] is not None)

    def _attach(self, expected, ledger):
        _need(isinstance(ledger, RecoveryLedger) and ledger.directory == self.destination / expected.attempt_id)
        doc = ledger.snapshot
        _need(ledger.direction == 'restore_decided' and ledger.backup_seal is not None)
        _need(doc['expectation'] == dict(attempt_id=expected.attempt_id,
              installation_fingerprint=expected.installation_fingerprint,
              target_manifest_hash=expected.target_manifest_hash, source_version=expected.source_version,
              reserved_at=expected.reserved_at.isoformat(), covered_domains=list(expected.covered_domains))
              and doc['binding_sha256'] == ledger.backup_seal.binding_sha256, 'backup_inventory_mismatch')
        _need(ledger.direction == 'restore_decided' and ledger.backup_seal is not None)
        _need(_json(self.installation, self.b['context'], 'info', '--format', '{{json .ID}}') == self.b['daemon'],
              'restore_child_ambiguous')
        self.ledger = ledger
        self.helpers = _StorageHelpers(self.installation, self.b, ledger, policy=self.policy,
                                        helper_image_id=self.helper_image_id)
        self.helpers.deadline = min(self.restore_deadline, self.helpers.deadline)

    def resume(self, expected, ledger, *, proof_raw=None, env_recovery=None):
        """Idempotent storage-only recovery; post-start is metadata/startup-only.

        A pending remote operation without its durable inspected result remains
        ambiguous, including with a fresh supervisor. Engine owns source health
        and metadata completion; this method never calls it restored by itself.
        """
        self._attach(expected, ledger)
        if ledger.snapshot['previous_start_armed']:
            return self.inspect(ledger)
        _need(not self.installation.runner.uncertain, 'restore_child_ambiguous')
        self.helpers.retire_helpers()
        _need(ledger.snapshot['pending'] is None, 'restore_child_ambiguous')
        self.quiesce_attempt(expected, ledger)
        self.verify_inputs(expected, ledger, proof_raw=proof_raw, env_recovery=env_recovery)
        self.restore_volumes()
        self.restore_configuration(env_recovery=env_recovery)
        self.verify_restored()
        return self.inspect(ledger)

    def verify_inputs(self, expected, ledger, *, proof_raw=None, env_recovery=None):
        """Verify EVERY archive/config/member list before the FIRST destructive op."""
        self._verified_inputs = False
        self._attach(expected, ledger)
        _need(ledger.snapshot['pending'] is None and not self.installation.runner.uncertain, 'restore_child_ambiguous')
        self.close()
        seal = ledger.backup_seal
        try:
            with _directory(ledger.directory) as fd:
                original = _read(fd, 'proof.json')
                _need(proof_raw is None or proof_raw == original, 'backup_inventory_mismatch')
                proof = validate_backup_proof(original, expected, now=self.clock(), seal=seal, for_recovery=True)
                raw = _read(fd, 'inventory.json')
                _need(_hash(raw) == seal.inventory_sha256, 'backup_inventory_mismatch')
                inventory = json.loads(raw)
                _need(inventory['schema_version'] == inventory['inventory_version'] == 2
                      and inventory['binding'] == self.b and 'proof_sha256' not in inventory
                      and inventory['binding_sha256'] == seal.binding_sha256
                      and inventory['source_observation_sha256'] == seal.source_observation_sha256
                      and inventory['quiescence_evidence_sha256'] == seal.quiescence_evidence_sha256
                      and all(inventory[key] == json.loads(proof.raw)[key]
                              for key in ('started_at', 'snapshot_at', 'completed_at'))
                      and inventory['containers'] == ledger.snapshot['containers']
                      and inventory['expectation'] == ledger.snapshot['expectation']
                      and inventory['restore_procedure_id'] == seal.restore_procedure_id
                      and set(inventory['archives']) == set(VOLUMES), 'backup_inventory_mismatch')
                _need(_hash(canonical_json(inventory['quiescence'])) == seal.quiescence_evidence_sha256,
                      'backup_inventory_mismatch')
                for key, entry in inventory['archives'].items():
                    archive_fd = os.open(key + '.tar', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    self._archive_fds[key] = archive_fd
                    member_fd = _new(fd, 'checked-members-' + key + '-' + uuid.uuid4().hex + '.jsonl')
                    try:
                        actual = verify_archive(archive_fd, member_fd, self.policy, deadline=self.helpers.deadline,
                                                volume_root=VOLUMES[key][1], complete_tree=True)
                    finally:
                        os.close(member_fd)
                    _need(all(actual[k] == entry[k] for k in actual)
                          and entry['volume'] == self.b['volumes'][key], 'backup_inventory_mismatch')
                    members_fd = os.open(key + '.members.jsonl', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    try:
                        _private(members_fd)
                        before = os.fstat(members_fd)
                        digest, size = hashlib.sha256(), 0
                        while True:
                            chunk = os.read(members_fd, 65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            _need(size <= self.policy.max_inventory_bytes, 'backup_inventory_mismatch')
                            digest.update(chunk)
                        _need(size == entry['members_bytes'] and digest.hexdigest() == entry['members_sha256']
                              and _file_identity(before) == _file_identity(os.fstat(members_fd)), 'backup_inventory_mismatch')
                    finally:
                        os.close(members_fd)
                _need([f['name'] for f in inventory['config_files']] == list(CONFIG_FILES), 'backup_inventory_mismatch')
                for index, record in enumerate(inventory['config_files']):
                    _need(record['file'] == 'config-' + str(index), 'backup_inventory_mismatch')
                    config = _read(fd, 'config-' + str(index))
                    _need(len(config) == record['size'] and _hash(config) == record['sha256'], 'backup_inventory_mismatch')
            self._archive_identities = {key: _file_identity(os.fstat(value)) for key, value in self._archive_fds.items()}
            self.inventory = inventory
            self.env_recovery = copy_env = json.loads(canonical_json(env_recovery)) if env_recovery is not None else None
            self._verify_configuration(env_recovery=copy_env, allow_postimage=True)
            self._verify_stores()
            if not ledger.snapshot['previous_start_armed']:
                ledger.checkpoint('restore_inputs_verified')
            self._verified_inputs = True
            return inventory
        except (OSError, ValueError, TypeError, KeyError, EOFError, tarfile.TarError):
            self.close()
            raise RestoreError('backup_inventory_mismatch') from None
        except BaseException:
            self.close()
            raise

    def _verify_stores(self):
        _need(_json(self.installation, self.b['context'], 'info', '--format', '{{json .ID}}') == self.b['daemon'],
              'restore_child_ambiguous')
        for key in VOLUMES:
            raw = _json(self.installation, self.b['context'], 'volume', 'inspect', self.b['volumes'][key]['Name'])[0]
            _need(_volume(raw) == self.b['volumes'][key], 'restore_verification_failed')
        installed = _json(self.installation, self.b['context'], 'image', 'inspect', *sorted(set(self.b['images'].values())))
        _need({i['Id'] for i in installed} == set(self.b['images'].values()), 'restore_child_ambiguous')

    def quiesce_attempt(self, expected, ledger):
        """Stop only exact recorded source/target identities, never a name match."""
        self._attach(expected, ledger)
        _need(not ledger.snapshot['previous_start_armed'], 'restore_start_barrier')
        _need(ledger.snapshot['pending'] is None and not self.installation.runner.uncertain, 'restore_child_ambiguous')
        ledger.checkpoint('restore_quiescing')
        doc = ledger.snapshot
        known = list(doc.get('target_containers', {}).values()) + list(doc.get('containers', {}).values())
        by_id = {value['Id']: value for value in known}
        current = _all_containers(self.installation, self.b['context'])
        for row in current:
            if row['Id'] not in by_id:
                continue
            _need(_identity(row) == by_id[row['Id']] and
                  row['HostConfig']['RestartPolicy']['Name'] in ('unless-stopped', 'no'), 'restore_child_ambiguous')
            if row['State'].get('Running'):
                operation = ledger.begin('stop', identity=by_id[row['Id']])
                self.helpers.command('stop', '--time', '60', row['Id'])
                stopped = _json(self.installation, self.b['context'], 'inspect', row['Id'])[0]
                _need(_identity(stopped) == by_id[row['Id']] and not stopped['State']['Running']
                      and stopped['State']['Status'] == 'exited', 'restore_child_ambiguous')
                ledger.complete(operation, {'id': row['Id'], 'stopped': True})
        for key in VOLUMES:
            self.helpers.assert_no_running_consumers(key)
        ledger.checkpoint('restore_quiesced')

    def preflight_capacity(self):
        _need(self._verified_inputs and self.inventory is not None and self.ledger is not None)
        self._verify_stores()
        groups, measured = {}, {}
        for key, entry in self.inventory['archives'].items():
            self.helpers.assert_no_running_consumers(key)
            storage = self.storage_inspector(key) if self.storage_inspector else self.helpers.inspect(key)
            if self.storage_inspector is not None and not isinstance(storage, VolumeStorage):
                from .docker_host import VolumeFilesystem
                _need(isinstance(storage, VolumeFilesystem) and storage.role == key
                      and storage.name == self.b['volumes'][key]['Name'], 'restore_capacity_unknown')
                storage = VolumeStorage(storage.root_identity, storage.device, storage.free_bytes, storage.free_inodes)
            _need(isinstance(storage, VolumeStorage) and storage.root_identity == self.b['volume_root_ids'][key],
                  'restore_capacity_unknown')
            _need(type(storage.free_bytes) is int and storage.free_bytes >= 0
                  and type(storage.free_inodes) is int and storage.free_inodes >= 0
                  and isinstance(storage.filesystem, str) and storage.filesystem,
                  'restore_capacity_unknown')
            group = groups.setdefault(storage.filesystem, dict(bytes=0, inodes=0,
                available=storage.free_bytes, available_inodes=storage.free_inodes))
            # Conservative full extra generation, even while old data still exists.
            group['bytes'] += entry['logical_bytes'] + entry['members'] * 8192
            group['inodes'] += entry['members']
            group['available'] = min(group['available'], storage.free_bytes)
            group['available_inodes'] = min(group['available_inodes'], storage.free_inodes)
            measured[key] = storage.__dict__
        for group in groups.values():
            _need(group['available'] >= group['bytes'] + self.policy.reserve_bytes
                  and group['available_inodes'] >= group['inodes'] + 1024, 'restore_capacity_insufficient')
        with _directory(self.ledger.directory) as fd:
            space = os.fstatvfs(fd)
            _need(space.f_bavail * space.f_frsize >= self.policy.reserve_bytes + sum(
                e['members_bytes'] for e in self.inventory['archives'].values())
                and space.f_favail >= 1024, 'restore_capacity_insufficient')
        self.ledger.checkpoint(storage_capacity=measured)
        return measured

    def restore_volumes(self):
        _need(self._verified_inputs and self.inventory is not None and set(self._archive_fds) == set(VOLUMES))
        self.ledger.assert_data_mutable()
        _need(self.ledger.snapshot['pending'] is None and not self.installation.runner.uncertain, 'restore_child_ambiguous')
        self._verify_configuration(env_recovery=self.env_recovery, allow_postimage=True)
        self.preflight_capacity()
        self.ledger.checkpoint('restoring_volumes')
        with _directory(self.ledger.directory) as fd:
            for key, entry in self.inventory['archives'].items():
                self.ledger.assert_data_mutable()
                self._verify_stores()
                self.helpers.assert_no_running_consumers(key)
                self.helpers.inspect(key)  # root identity is checked again immediately before mutation
                self._verify_configuration(env_recovery=self.env_recovery, allow_postimage=True)
                archive_fd = self._archive_fds[key]
                before = os.fstat(archive_fd)
                os.lseek(archive_fd, 0, os.SEEK_SET)
                digest = hashlib.sha256()
                while True:
                    _need(time.monotonic() < self.helpers.deadline, 'restore_child_ambiguous')
                    chunk = os.read(archive_fd, 65536)
                    if not chunk:
                        break
                    digest.update(chunk)
                _need(before.st_size == entry['size'] and digest.hexdigest() == entry['sha256']
                      and _file_identity(before) == _file_identity(os.fstat(archive_fd)), 'backup_inventory_mismatch')
                phase = self.ledger.snapshot['volumes'].get(key)
                if phase == 'verified':
                    actual = self.helpers.inventory(key, fd)
                    _need(actual['sha256'] == entry['members_sha256'] and actual['size'] == entry['members_bytes'],
                          'restore_verification_failed')
                    continue
                self.ledger.volume_phase(key, 'clear_intent', restart=phase is not None)
                self.helpers.execute(key, 'clear', writable=True)
                self.helpers.execute(key, 'empty')
                self.ledger.volume_phase(key, 'empty')
                self.ledger.volume_phase(key, 'extract_intent')
                # FD pinned at verify_inputs; stream_from refuses concurrent changes.
                self.helpers.execute(key, 'extract', writable=True, tar=True, archive_fd=self._archive_fds[key],
                                     archive_identity=self._archive_identities[key])
                self.helpers.execute(key, 'sync', writable=True)
                self.ledger.volume_phase(key, 'extracted')
                actual = self.helpers.inventory(key, fd)
                _need(actual['sha256'] == entry['members_sha256'] and actual['size'] == entry['members_bytes'],
                      'restore_verification_failed')
                self.ledger.volume_phase(key, 'verified')
        self.ledger.checkpoint('restore_files_verified')

    def _verify_configuration(self, *, env_recovery=None, allow_postimage=False):
        env_recovery = env_recovery if env_recovery is not None else getattr(self, 'env_recovery', None)
        if env_recovery is not None:
            from . import envfile
            _need(env_recovery.get('commit') is not None
                  and envfile.check_commit(self.installation.root / '.env', env_recovery, durable=True) != 'legacy',
                  'restore_verification_failed')
        current = _files(self.installation.root)  # retains qualify_retained_env qualification
        for record in self.inventory['config_files']:
            name = record['name']
            digest = _hash(current[name])
            info = (self.installation.root / name).stat()
            metadata = record['metadata']
            _need(info.st_uid == metadata['uid'] and info.st_gid == metadata['gid']
                  and stat.S_IMODE(info.st_mode) == metadata['mode'], 'restore_verification_failed')
            if name == '.env' and allow_postimage and env_recovery is not None:
                from . import envfile
                _need(env_recovery['before_sha256'] == record['sha256'], 'restore_verification_failed')
                restored = envfile.recovery_text(self.installation.root / '.env', env_recovery)
                _need(_hash(restored.encode('utf-8', 'surrogateescape')) == record['sha256'], 'restore_verification_failed')
            else:
                _need(digest == record['sha256'], 'restore_verification_failed')
            if name != '.env' or env_recovery is None:
                _need([info.st_dev, info.st_ino, info.st_mtime_ns] == metadata['identity'], 'restore_verification_failed')
        return current

    def restore_configuration(self, *, env_recovery=None):
        _need(self._verified_inputs)
        self.ledger.assert_data_mutable()
        _need(set(self.ledger.snapshot['volumes']) == set(VOLUMES)
              and all(p == 'verified' for p in self.ledger.snapshot['volumes'].values()))
        self._verify_configuration(env_recovery=env_recovery, allow_postimage=True)
        self.ledger.checkpoint('config_revert_pending')
        if env_recovery is not None:
            from . import envfile
            operation = self.ledger.begin('env_restore', identity={
                'before_sha256': env_recovery['before_sha256'], 'after_sha256': env_recovery['after_sha256']})
            envfile.restore_changes(self.installation.root / '.env', env_recovery)
            self._verify_configuration()
            self.ledger.complete(operation, {'restored': True})
        self._verify_configuration()
        self.ledger.checkpoint('config_reverted')

    def verify_restored(self):
        """Final complete comparison, BEFORE the engine may arm any source start."""
        _need(self._verified_inputs)
        self.ledger.assert_data_mutable()
        _need(self.ledger.phase == 'config_reverted' and self.ledger.snapshot['pending'] is None)
        self._verify_stores()
        self._verify_configuration()
        with _directory(self.ledger.directory) as fd:
            for key, entry in self.inventory['archives'].items():
                self.helpers.assert_no_running_consumers(key)
                self.helpers.inspect(key)
                actual = self.helpers.inventory(key, fd)
                _need(actual['sha256'] == entry['members_sha256'] and actual['size'] == entry['members_bytes'],
                      'restore_verification_failed')
        self.ledger.checkpoint('restore_data_committed')
        return self.ledger.backup_seal
