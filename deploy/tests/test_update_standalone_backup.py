"""Deterministic fake-daemon tests; real files, tar parsing, proof and stream supervision.

No product-image or database restore claim; see the opt-in dedicated Docker test.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update.backup_proof import BackupExpectation, read_backup_proof  # noqa: E402
from deploy.update.contracts import AutomationPolicy  # noqa: E402
from deploy.update.processes import ProcessSupervisor  # noqa: E402
from deploy.update import standalone_backup as backup  # noqa: E402

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


def archive_bytes(contents=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
        root = tarfile.TarInfo('.')
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for name, data in (contents or {'./records': b'private persistent record'}).items():
            member = tarfile.TarInfo(name)
            member.size, member.uid, member.gid, member.mode = len(data), 999, 999, 0o600
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class Daemon(ProcessSupervisor):
    """Daemon model, not an accept-all proof/backup stub. Archives use real children."""

    def __init__(self, root, profiles=()):
        super().__init__(AutomationPolicy(probe_timeout_seconds=90), docker_context='test-backup',
                         environment={'PATH': os.defpath}, grace_seconds=0.05)
        self.root, self.project = root, 'test-backup'
        self.calls, self.containers, self.volumes = [], {}, {}
        self.mode, self.after_stop, self.after_archive = 'ok', None, None
        self.image_defaults = {}
        self.model = {'name': self.project, 'services': {}, 'volumes': {}}
        self.images = {'app': 'sha256:' + 'a' * 64, 'postgres': 'sha256:' + 'b' * 64,
                       'clickhouse': 'sha256:' + 'c' * 64}
        if 'ml' in profiles:
            self.images['serving'] = 'sha256:' + 'd' * 64
        if 'sandbox' in profiles:
            self.images['code-executor'] = 'sha256:' + 'e' * 64
        for key in backup.VOLUMES:
            name = self.project + '_' + key
            self.model['volumes'][key] = {'name': name}
            self.volumes[name] = dict(Name=name, Driver='local', Scope='local', Options=None,
                                     CreatedAt='2026-10-27T01:00:00Z', Mountpoint='/var/lib/docker/volumes/' + name + '/_data',
                                     Labels={'com.docker.compose.project': self.project, 'com.docker.compose.volume': key})
        for index, (service, image) in enumerate(self.images.items(), 1):
            env = dict(backup.LOCAL_ENV) if service == 'app' else {}
            if service == 'postgres':
                env['PGDATA'] = '/var/lib/postgresql/data'
            self.model['services'][service] = {'image': image, 'environment': env}
            cid = str(index) * 64
            mounts = [dict(Type='volume', Name=self.project + '_' + key,
                           Source=self.volumes[self.project + '_' + key]['Mountpoint'], Destination=target, RW=True)
                      for key, (owner, target) in backup.VOLUMES.items() if owner == service]
            mounts += [dict(Type='bind', Source=source if source == '/dev/null' else str(root / source),
                            Destination=target, RW=False) for target, source in backup.BINDS.get(service, {}).items()]
            self.model['services'][service]['volumes'] = [
                dict(type=m['Type'], source=m['Name'][len(self.project) + 1:] if m['Type'] == 'volume' else m['Source'],
                     target=m['Destination'], read_only=not m['RW']) for m in mounts]
            self.containers[cid] = dict(Id=cid, Image=image, Name='/' + service, Created='2026-10-27T00:00:00Z',
                RestartCount=0, Mounts=mounts, HostConfig={'RestartPolicy': {'Name': 'unless-stopped'}},
                Config={'Env': [k + '=' + v for k, v in env.items()], 'Labels': {
                    'com.docker.compose.project': self.project, 'com.docker.compose.service': service,
                    'com.docker.compose.oneoff': 'False', 'com.docker.compose.project.working_dir': str(root),
                    'com.docker.compose.project.config_files': str(root / 'docker-compose.yml')}},
                State={'Running': True, 'Status': 'running', 'ExitCode': 0, 'StartedAt': 'original',
                       'Health': {'Status': 'healthy'}})
        self.tar = archive_bytes()

    def __call__(self, args, **kwargs):
        self._command(args)  # real context/argument validation even in this daemon model
        words = list(args[3:])
        self.calls.append(words)
        value = None
        if words == ['context', 'inspect', 'test-backup']:
            value = [{'Name': 'test-backup', 'Endpoints': {'docker': {'Host': 'unix:///test.sock'}}}]
        elif words == ['info', '--format', '{{json .ID}}']:
            value = 'daemon-1'
        elif words[:2] == ['volume', 'inspect']:
            value = [self.volumes[words[2]]]
        elif words[:2] == ['image', 'inspect']:
            value = [{'Id': image, 'Os': 'linux', 'Config': dict(
                {'Volumes': {'/var/lib/postgresql/data': {}}, 'Env': []}, **self.image_defaults.get(image, {}))}
                     for image in words[2:]]
        elif words[:3] == ['ps', '-aq', '--no-trunc']:
            ids = list(self.containers)
            if words[3:]:
                assert words[3] == '--filter' and words[4].startswith('id=') and len(words) == 5
                ids = [cid for cid in ids if cid == words[4][3:]]
            return subprocess.CompletedProcess(args, 0, '\n'.join(ids).encode(), b'')
        elif words[0] == 'inspect':
            value = [copy.deepcopy(self.find(name)) for name in words[1:]]
        elif words[0] == 'exec':
            text = ('tar (GNU tar) 1.34\n' if words[2:] == ['/bin/tar', '--version'] else
                    'timeout (GNU coreutils) 9.1\n' if words[2:] == ['/usr/bin/timeout', '--version'] else '1:123\n')
            return subprocess.CompletedProcess(args, 0, text.encode(), b'')
        elif words[0] in ('stop', 'start'):
            container = self.find(words[-1])
            running = words[0] == 'start'
            container['State'].update(Running=running, Status='running' if running else 'exited')
            if running:
                container['State']['StartedAt'] = 'restarted'
            if not running and self.after_stop:
                self.after_stop(container)
            return subprocess.CompletedProcess(args, 0, b'ok', b'')
        elif words[0] == 'rm':
            del self.containers[words[1]]
            return subprocess.CompletedProcess(args, 0, b'ok', b'')
        else:
            raise AssertionError(words)
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b'')

    def find(self, name):
        return next(c for c in self.containers.values() if c['Id'] == name or c['Name'] == '/' + name)

    def stream_to(self, args, output_fd, **kwargs):
        self._command(args)
        words = args[3:]
        self.calls.append(list(words))
        name = words[words.index('--name') + 1]
        mount = words[words.index('--mount') + 1]
        volume = next(part[7:] for part in mount.split(',') if part.startswith('source='))
        cid = 'f' * 64
        command = words[words.index(self.images['postgres']) + 1:]
        self.containers[cid] = dict(Id=cid, Name='/' + name, Image=self.images['postgres'],
            Config={'Labels': {backup._LABEL: words[words.index('--label') + 1].split('=', 1)[1]},
                    'Entrypoint': ['/bin/tar'], 'Cmd': command},
            HostConfig={'NetworkMode': 'none', 'ReadonlyRootfs': True},
            Mounts=[{'Name': volume, 'Source': self.volumes[volume]['Mountpoint'], 'RW': False,
                     'Destination': '/var/lib/postgresql/data'}],
            State={'Running': False, 'Status': 'exited', 'ExitCode': 1 if self.mode == 'exit1' else 0})
        payload = self.tar if self.mode != 'partial' else self.tar[:600]
        # A real supervised child sends the archive; no canned success proof.
        code = 'import os; data=' + repr(payload) + '; os.write(1,data)'
        if self.mode == 'exit1':
            code += '; raise SystemExit(1)'
        if self.mode == 'timeout':
            code += '; import time; time.sleep(10)'
            kwargs['timeout'] = 0.05
        result = super().stream_to([sys.executable, '-I', '-c', code], output_fd, **kwargs)
        if self.after_archive:
            self.after_archive()
        return result


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=getattr(self, 'temp_prefix', 'fagi-backup-unit-'), dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in backup.CONFIG_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'FUTURE_AGI_VERSION=v1.51.0\nSECRET_KEY=private\n' if name == '.env' else name.encode())
            path.chmod(0o600)
        self.destination = self.root / 'backups'
        backup.create_destination(self.destination)
        proof_dir = self.root / 'engine-proof'
        proof_dir.mkdir(mode=0o700)
        self.proof_dir = proof_dir
        self.proof_fd = os.open(proof_dir, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.proof_fd)
        self.daemon = Daemon(self.root)
        self.install = SimpleNamespace(root=self.root, runner=self.daemon, mode='standalone', production=False,
            docker=['docker', '--context', 'test-backup'], config=lambda refresh=False: copy.deepcopy(self.daemon.model))
        self.expected = BackupExpectation('1' * 32, '2' * 64, '3' * 64, 'v1.51.0', NOW, backup.DOMAINS)
        self.binding = self.capture()
        self.adapter = self.make_adapter()
        self.daemon.calls.clear()

    def capture(self, **kwargs):
        options = dict(installation_fingerprint='2' * 64, docker_context='test-backup', daemon_id='daemon-1',
                       project='test-backup', profiles=(), verified_images=self.daemon.images,
                       deployment_hashes={k: hashlib.sha256((self.root / k).read_bytes()).hexdigest()
                                          for k in backup.CONFIG_FILES[1:]})
        options.update(kwargs)
        return backup.capture_binding(self.install, **options)

    def make_adapter(self, **kwargs):
        return backup.StandaloneBackup(self.install, self.binding, self.destination, clock=lambda: NOW,
                                       policy=backup.BackupPolicy(reserve_bytes=1), **kwargs)

    def run_backup(self):
        return self.adapter.run(self.expected, proof_directory_fd=self.proof_fd)

    def mutation_calls(self):
        return [c for c in self.daemon.calls if c[0] in ('stop', 'start', 'run', 'rm')]

    def ledger(self):
        return json.loads((self.destination / self.expected.attempt_id / 'ledger.json').read_bytes())

    def test_success_real_archives_inventory_and_validator_original_containers_and_pins(self):
        originals = {cid: c['Image'] for cid, c in self.daemon.containers.items()}
        env = (self.root / '.env').read_bytes()
        proof = self.run_backup()
        checked = read_backup_proof(self.proof_fd, 'proof.json', self.expected, now=NOW, expected_uid=os.geteuid())
        self.assertEqual(proof.raw, checked.raw)
        directory = self.destination / self.expected.attempt_id
        inventory = json.loads((directory / 'inventory.json').read_bytes())
        self.assertEqual(set(inventory['archives']), set(backup.VOLUMES))
        for key, entry in inventory['archives'].items():
            self.assertEqual(entry['sha256'], hashlib.sha256((directory / (key + '.tar')).read_bytes()).hexdigest())
            self.assertEqual(entry['members_sha256'], hashlib.sha256((directory / (key + '.members.jsonl')).read_bytes()).hexdigest())
            self.assertEqual(entry['volume'], self.daemon.volumes['test-backup_' + key])
        self.assertEqual((directory / 'config-0').read_bytes(), env)
        self.assertEqual((self.root / '.env').read_bytes(), env)
        self.assertEqual({cid: c['Image'] for cid, c in self.daemon.containers.items()}, originals)
        self.assertTrue(all(c['State']['Running'] for c in self.daemon.containers.values()))
        operations = self.mutation_calls()
        self.assertEqual([c[-1] for c in operations if c[0] == 'stop'], ['1' * 64, '2' * 64, '3' * 64])
        self.assertEqual([c[-1] for c in operations if c[0] == 'start'], ['2' * 64, '3' * 64, '1' * 64])
        self.assertEqual(sum(c[0] == 'run' for c in operations), 3)
        self.assertEqual(self.ledger()['phase'], 'complete')
        self.assertNotIn('private', repr(proof))
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
        for call in (c for c in operations if c[0] == 'run'):
            self.assertIn('none', call)
            self.assertIn('never', call)
            self.assertIn('--read-only', call)
            self.assertIn('readonly,volume-nocopy', call[call.index('--mount') + 1])
            self.assertNotIn('docker.sock', ' '.join(call))
            self.assertEqual(call[call.index('--entrypoint') + 2], self.daemon.images['postgres'])

    def test_null_compose_inherits_image_entrypoint_but_runtime_override_refuses(self):
        self.daemon.image_defaults[self.daemon.images['postgres']] = {'Entrypoint': ['docker-entrypoint.sh']}
        self.daemon.containers['2' * 64]['Config']['Entrypoint'] = ['docker-entrypoint.sh']
        self.daemon.model['services']['postgres']['entrypoint'] = None
        self.capture()
        self.daemon.containers['2' * 64]['Config']['Entrypoint'] = ['/unqualified-custom-start']
        with self.assertRaisesRegex(backup.BackupError, 'runtime_configuration_changed'):
            self.capture()
        self.assertFalse(self.mutation_calls())

    def test_mount_inspect_order_is_not_a_storage_identity_change(self):
        original = self.daemon.containers['1' * 64]
        reordered = copy.deepcopy(original)
        reordered['Mounts'].reverse()
        self.assertEqual(backup._identity(original), backup._identity(reordered))
        reordered['Mounts'][0]['Source'] = '/different'
        self.assertNotEqual(backup._identity(original), backup._identity(reordered))

    def test_ledger_is_durable_before_the_first_stop(self):
        def check(_):
            ledger = self.ledger()
            self.assertEqual(ledger['pending']['kind'], 'stop')
            self.assertEqual(set(ledger['containers']), {'app', 'postgres', 'clickhouse'})
            self.assertEqual(ledger['expectation']['reserved_at'], NOW.isoformat())
            self.assertFalse((self.proof_dir / 'proof.json').exists())
        self.daemon.after_stop = check
        self.run_backup()

    def test_repeated_attempt_never_rearchives_or_refreshes(self):
        self.run_backup()
        raw = (self.proof_dir / 'proof.json').read_bytes()
        self.daemon.calls.clear()
        with self.assertRaises(backup.BackupError):
            self.run_backup()
        (self.proof_dir / 'proof.json').unlink()
        with self.assertRaisesRegex(backup.BackupError, 'backup_attempt_already_exists'):
            self.run_backup()
        self.assertFalse(self.mutation_calls())
        self.assertIn(b'2026-10-27T12:00:00Z', raw)

    def test_partial_or_failing_archive_restores_originals_but_never_proves_success(self):
        for mode in ('partial', 'exit1'):
            with self.subTest(mode=mode):
                self.expected = replace(self.expected, attempt_id=('4' if mode == 'partial' else '5') * 32)
                self.daemon.mode = mode
                with self.assertRaises(backup.BackupError) as caught:
                    self.run_backup()
                self.assertFalse(caught.exception.recovery_required)
                self.assertFalse((self.proof_dir / 'proof.json').exists())
                self.assertFalse((self.destination / self.expected.attempt_id / 'inventory.json').exists())
                self.assertTrue(all(c['State']['Running'] for c in self.daemon.containers.values()))
                self.assertEqual(self.ledger()['phase'], 'failed_available')

    def test_forced_stop_is_not_a_consistent_backup_even_after_safe_restart(self):
        def killed(container):
            container['State']['ExitCode'] = 137
        self.daemon.after_stop = killed
        with self.assertRaisesRegex(backup.BackupError, 'original_stop_unclean') as caught:
            self.run_backup()
        self.assertFalse(caught.exception.recovery_required)
        self.assertTrue(all(c['State']['Running'] for c in self.daemon.containers.values()))
        self.assertFalse(any(c[0] == 'run' for c in self.mutation_calls()))
        self.assertFalse((self.proof_dir / 'proof.json').exists())

    def test_finalized_recovery_does_not_undo_a_later_operator_stop(self):
        self.daemon.mode = 'partial'
        with self.assertRaises(backup.BackupError):
            self.run_backup()
        self.assertTrue(all(c['State']['Running'] for c in self.daemon.containers.values()))
        self.daemon.containers['1' * 64]['State'].update(Running=False, Status='exited')
        self.daemon.calls.clear()
        self.assertEqual(self.adapter.recover_availability(self.expected).state, 'recovery_required')
        self.assertFalse(self.mutation_calls())
        self.assertFalse(self.daemon.containers['1' * 64]['State']['Running'])

    def test_unknown_archive_child_does_not_guess_safe_recovery(self):
        self.daemon.mode = 'timeout'
        with self.assertRaises(backup.BackupError) as caught:
            self.run_backup()
        self.assertTrue(caught.exception.recovery_required)
        self.assertEqual(self.ledger()['pending']['kind'], 'archive')
        self.assertFalse((self.proof_dir / 'proof.json').exists())
        self.daemon.calls.clear()
        self.assertEqual(self.adapter.inspect_recovery(self.expected).state, 'recovery_required')
        self.assertEqual(self.adapter.recover_availability(self.expected).state, 'recovery_required')
        self.assertFalse(self.mutation_calls())

    def test_custom_external_missing_config_and_foreign_volume_users_refuse_before_stop(self):
        original = copy.deepcopy(self.daemon.model)
        cases = ('external', 'bind', 'env', 'extra', 'foreign', 'foreign-parent', 'changed-image')
        for case in cases:
            with self.subTest(case=case):
                self.expected = replace(self.expected, attempt_id=hashlib.md5(case.encode()).hexdigest())
                saved = copy.deepcopy(self.daemon.containers)
                if case == 'external':
                    self.daemon.model['volumes']['app-data']['external'] = True
                elif case == 'bind':
                    self.daemon.containers['1' * 64]['Mounts'][0]['Type'] = 'bind'
                elif case == 'env':
                    self.daemon.model['services']['app']['environment']['STORAGE_BACKEND'] = 's3'
                elif case == 'extra':
                    self.daemon.model['services']['custom'] = {}
                elif case.startswith('foreign'):
                    foreign = copy.deepcopy(saved['1' * 64])
                    foreign.update(Id='9' * 64, Config={'Labels': {}}, Name='/foreign')
                    if case == 'foreign-parent':
                        foreign['Mounts'] = [{'Type': 'bind', 'Source': '/var/lib/docker', 'Destination': '/host'}]
                    self.daemon.containers[foreign['Id']] = foreign
                else:
                    self.daemon.containers['1' * 64]['Image'] = 'sha256:' + '9' * 64
                with self.assertRaises(backup.BackupError):
                    self.run_backup()
                self.assertFalse(self.mutation_calls())
                self.daemon.model = copy.deepcopy(original)
                self.daemon.containers = saved

    def test_profiles_are_quiesced_and_must_own_no_additional_mounts(self):
        self.daemon = Daemon(self.root, ('ml', 'sandbox'))
        self.install.runner = self.daemon
        self.binding = self.capture(profiles=('ml', 'sandbox'))
        self.adapter = self.make_adapter()
        self.run_backup()
        self.assertEqual([c[-1] for c in self.mutation_calls() if c[0] == 'stop'],
                         ['1' * 64, '4' * 64, '5' * 64, '2' * 64, '3' * 64])
        self.daemon.containers['4' * 64]['Mounts'].append({'Type': 'volume', 'Name': 'custom'})
        with self.assertRaises(backup.BackupError):
            self.capture(profiles=('ml', 'sandbox'))

    def test_file_symlink_hardlink_and_mutated_config_are_not_copied(self):
        env = self.root / '.env'
        original = self.root / 'original-env'
        env.rename(original)
        env.symlink_to(original)
        with self.assertRaises(backup.BackupError):
            self.run_backup()
        self.assertFalse(self.mutation_calls())
        env.unlink()
        os.link(original, env)
        with self.assertRaises(backup.BackupError):
            self.capture()
        env.unlink()
        original.rename(env)
        env.write_text('SECRET_KEY=changed')
        self.expected = replace(self.expected, attempt_id='6' * 32)
        with self.assertRaises(backup.BackupError):
            self.run_backup()
        self.assertFalse(self.mutation_calls())

    def test_no_adoption_of_unsafe_destination_or_proof_file(self):
        self.destination.chmod(0o755)
        with self.assertRaises(backup.BackupError):
            backup.create_destination(self.destination)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o755)
        self.destination.chmod(0o700)
        (self.proof_dir / 'proof.json').symlink_to(self.root / '.env')
        with self.assertRaises(backup.BackupError):
            self.run_backup()
        self.assertFalse(self.mutation_calls())

    def test_capacity_failure_before_stop_and_archive_limit_after_stop(self):
        self.adapter.policy = replace(self.adapter.policy, reserve_bytes=2 ** 62)
        with self.assertRaisesRegex(backup.BackupError, 'backup_destination_full'):
            self.run_backup()
        self.assertFalse(self.mutation_calls())
        self.expected = replace(self.expected, attempt_id='7' * 32)
        self.adapter.policy = replace(self.adapter.policy, reserve_bytes=1, max_archive_bytes=1024)
        with self.assertRaises(backup.BackupError) as caught:
            self.run_backup()
        self.assertTrue(caught.exception.recovery_required)  # real streaming failure killed a client
        self.assertFalse((self.proof_dir / 'proof.json').exists())

    def test_crash_between_acknowledged_operations_can_only_restore_availability(self):
        # Simulate process death after the fsynced quiesced ledger, not a new proof.
        with mock.patch.object(self.adapter, '_archive', side_effect=SystemExit), \
                mock.patch.object(self.adapter, '_restart', side_effect=SystemExit):
            with self.assertRaises(backup.BackupError):
                self.run_backup()
        self.assertEqual(self.ledger()['phase'], 'quiesced')
        self.daemon.calls.clear()
        replacement = self.make_adapter()
        self.assertTrue(replacement.inspect_recovery(self.expected).can_restore_availability)
        self.assertEqual(replacement.recover_availability(self.expected).state, 'failed_available')
        self.assertTrue(all(c['State']['Running'] for c in self.daemon.containers.values()))
        self.assertFalse((self.proof_dir / 'proof.json').exists())
        self.assertFalse(any(c[0] in ('stop', 'run') for c in self.mutation_calls()))

    def test_changed_original_or_volume_after_crash_is_no_action(self):
        with mock.patch.object(self.adapter, '_archive', side_effect=SystemExit), \
                mock.patch.object(self.adapter, '_restart', side_effect=SystemExit):
            with self.assertRaises(backup.BackupError):
                self.run_backup()
        for change in ('image', 'volume', 'id'):
            originals, volumes = copy.deepcopy(self.daemon.containers), copy.deepcopy(self.daemon.volumes)
            if change == 'image':
                self.daemon.containers['1' * 64]['Image'] = 'sha256:' + '9' * 64
            elif change == 'id':
                self.daemon.containers['1' * 64]['Id'] = '8' * 64
            else:
                self.daemon.volumes['test-backup_app-data']['CreatedAt'] = 'new'
            self.daemon.calls.clear()
            self.assertEqual(self.adapter.recover_availability(self.expected).state, 'recovery_required')
            self.assertFalse(self.mutation_calls())
            self.daemon.containers, self.daemon.volumes = originals, volumes

    def test_stop_acknowledgement_gap_remains_ambiguous(self):
        def die(_):
            raise SystemExit()
        self.daemon.after_stop = die
        with self.assertRaises(backup.BackupError) as caught:
            self.run_backup()
        self.assertTrue(caught.exception.recovery_required)
        self.daemon.calls.clear()
        self.assertEqual(self.adapter.recover_availability(self.expected).state, 'recovery_required')
        self.assertFalse(self.mutation_calls())

    def test_unexpected_start_during_archiving_is_not_certified(self):
        def start_foreign():
            self.daemon.containers['1' * 64]['State'].update(Running=True, StartedAt='other-start')
        self.daemon.after_archive = start_foreign
        with self.assertRaises(backup.BackupError) as caught:
            self.run_backup()
        self.assertTrue(caught.exception.recovery_required)
        self.assertFalse((self.proof_dir / 'proof.json').exists())

    def test_successor_binding_retains_volume_identity_and_requires_qualified_hashes(self):
        new = self.capture(previous=self.binding)
        self.assertEqual(new.raw, self.binding.raw)
        self.daemon.volumes['test-backup_app-data']['CreatedAt'] = 'replacement'
        with self.assertRaisesRegex(backup.BackupError, 'enrolled_storage_changed'):
            self.capture(previous=self.binding)
        with self.assertRaises(backup.BackupError):
            self.capture(deployment_hashes={})

    def test_bad_domain_expectation_and_clock_never_stop(self):
        self.expected = replace(self.expected, covered_domains=('postgres_app',))
        with self.assertRaises(backup.BackupError):
            self.run_backup()
        self.expected = replace(self.expected, covered_domains=backup.DOMAINS,
                                reserved_at=NOW.replace(hour=13))
        with self.assertRaises(backup.BackupError):
            self.run_backup()
        self.assertFalse(self.mutation_calls())


class ArchiveTests(unittest.TestCase):
    def verify(self, raw, volume_root=None):
        with tempfile.NamedTemporaryFile() as stream, tempfile.NamedTemporaryFile() as members:
            stream.write(raw)
            stream.flush()
            return backup.verify_archive(stream.fileno(), members.fileno(), backup.BackupPolicy(reserve_bytes=1),
                                         deadline=time.monotonic() + 5, volume_root=volume_root)

    def test_clickhouse_internal_links_are_preserved_but_external_stores_refuse(self):
        for link in ('/var/lib/clickhouse/store/abc', '../../store/abc',
                     '/external/store/abc', '../../../../escape', '/var/lib/clickhouse/../../escape'):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
                member = tarfile.TarInfo('./data/default/table')
                member.type, member.linkname = tarfile.SYMTYPE, link
                archive.addfile(member)
            if link in ('/var/lib/clickhouse/store/abc', '../../store/abc'):
                self.assertEqual(self.verify(stream.getvalue(), '/var/lib/clickhouse')['members'], 1)
            else:
                with self.assertRaises(backup.BackupError):
                    self.verify(stream.getvalue(), '/var/lib/clickhouse')

    def test_walks_every_payload_and_rejects_truncation_appended_data_and_escaping_names(self):
        raw = archive_bytes({'./blob': b'x' * 100000})
        self.assertEqual(self.verify(raw)['logical_bytes'], 100000)
        for damaged in (raw[:512], raw[:-10240], raw + b'x' * 512,
                        archive_bytes({'../../escape': b'bad'}), b'x' * 10240):
            with self.subTest(size=len(damaged)), self.assertRaises((backup.BackupError, tarfile.TarError)):
                self.verify(damaged)


if __name__ == '__main__':
    unittest.main()
