"""Dedicated small vendor DB storage tests, NOT product/release qualification.

Existing explicit opt-in: FAGI_UPDATE_BACKUP_E2E=1 and an owned 0700 HOME
FAGI_UPDATE_BACKUP_EVIDENCE directory. Only colima-fagi-update; no pulls/builds.
One real database at a time. Source/target observation envelope has synthetic
release hashes; record queries, cold backup, in-place restore and tree checks
are real. Synthetic blob files do not qualify collector/Temporal/MinIO domains.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update import standalone_backup as backup
from deploy.update.backup_proof import BackupExpectation
from deploy.update.contracts import AutomationPolicy, RuntimeImage, RuntimeObservationV2
from deploy.update.install import Installation
from deploy.update.processes import ProcessSupervisor
from deploy.update.recovery import RecoveryLedger, RecoveryError
from deploy.update.standalone_restore import StandaloneRestore, RestoreError

CONTEXT = 'colima-fagi-update'
IMAGES = {'postgres': 'sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97',
          'clickhouse': 'sha256:a4e8cf46e526a06f4f52d22f7fa8d07472b55d3c896d7ca3f159e249be38ae62',
          'app': 'sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c'}
BUSYBOX = 'sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e'
SECRET = 'small-restore-fixture-not-customer-data'


def observation(binding, containers, now):
    """Explicit synthetic admission envelope around real vendor DB fixtures."""
    b = json.loads(binding.raw)
    pins = {s: (RuntimeImage('fixture/' + s, 'sha256:' + 'a' * 64, image),) for s, image in b['images'].items()}
    return RuntimeObservationV2(release_version='v1.53.0', manifest_sha256='1' * 64, observed_at=now,
        binding_sha256='2' * 64, platform='linux/arm64', product_images={'standalone': pins['app']},
        dependency_images={s: p for s, p in pins.items() if s != 'app'},
        migration_inventory_sha256='3' * 64, applied_migrations_sha256='3' * 64,
        active_app_mapping_sha256='4' * 64, deployment_sha256=b['compose_sha256'],
        env_sha256=b['files']['.env'], config_sha256='5' * 64, storage_sha256='6' * 64,
        container_roster_sha256='7' * 64, restart_counts={c['Id']: c['RestartCount'] for c in containers.values()},
        restart_policies={c['Id']: c['HostConfig']['RestartPolicy']['Name'] for c in containers.values()}, ready=True, stable=True)


def recovery_worker(root, destination, attempt_id, cut):
    """A fresh shipping-storage executor; only disposable fixture paths supplied."""
    root, destination = Path(root), Path(destination)
    assert str(root).startswith(str(Path.home()) + '/')
    runner = ProcessSupervisor(AutomationPolicy(probe_timeout_seconds=90, wait_timeout_seconds=600),
        docker_context=CONTEXT, environment={'PATH': os.environ['PATH'], 'HOME': str(Path.home())}, grace_seconds=.2)
    installation = Installation(root, environ=runner.environment, runner=runner,
        docker=[shutil.which('docker'), '--context', CONTEXT], compose=[shutil.which('docker-compose')])
    ledger = RecoveryLedger.open(destination / attempt_id)
    doc = ledger.snapshot['expectation']
    expected = BackupExpectation(doc['attempt_id'], doc['installation_fingerprint'], doc['target_manifest_hash'],
        doc['source_version'], datetime.fromisoformat(doc['reserved_at']), tuple(doc['covered_domains']))
    binding = backup.BackupBinding((root / 'fixture-binding.json').read_bytes())
    if cut == 'metadata_after_source_write':
        ledger.checkpoint('previous_verifying')
        os.kill(os.getpid(), signal.SIGKILL)
    real_save = ledger._save

    def save(document):
        previous = ledger.snapshot
        real_save(document)
        matched = (document['phase'] == cut and previous['phase'] != cut)
        if cut.startswith('volume:'):
            _, volume, phase = cut.split(':')
            matched = document['volumes'].get(volume) == phase and previous['volumes'].get(volume) != phase
        if matched:
            os.kill(os.getpid(), signal.SIGKILL)
    ledger._save = save
    adapter = StandaloneRestore(installation, binding, destination,
        policy=backup.BackupPolicy(max_archive_bytes=512 * 1024 ** 2, timeout_seconds=600))
    try:
        adapter.resume(expected, ledger)
        ledger.arm_previous_start()
    finally:
        adapter.close()
    raise AssertionError('crash boundary was not reached')


@unittest.skipUnless(os.environ.get('FAGI_UPDATE_BACKUP_E2E') == '1', 'explicit dedicated Docker opt-in required')
class DedicatedBackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.evidence = Path(os.environ['FAGI_UPDATE_BACKUP_EVIDENCE'])
        self.assertTrue(str(self.evidence).startswith(str(Path.home()) + '/'))
        with backup._directory(self.evidence):
            pass
        self.runner = ProcessSupervisor(AutomationPolicy(probe_timeout_seconds=90, wait_timeout_seconds=600),
            docker_context=CONTEXT, environment={'PATH': os.environ['PATH'], 'HOME': str(Path.home())}, grace_seconds=0.2)
        self.docker, self.compose = shutil.which('docker'), shutil.which('docker-compose')
        self.assertTrue(self.docker and self.compose)
        self.assertFalse(self.cmd('ps', '-q').strip(), 'dedicated VM has an active stack; do not share it')
        self.initial_containers = set(self.cmd('ps', '-aq', '--no-trunc').split())
        self.initial_volumes = set(self.cmd('volume', 'ls', '-q').split())
        self.initial_images = set(self.cmd('image', 'ls', '-q', '--no-trunc').split())
        self.daemon = json.loads(self.cmd('info', '--format', '{{json .ID}}'))
        for image in [*IMAGES.values(), BUSYBOX]:
            self.assertEqual(json.loads(self.cmd('image', 'inspect', image))[0]['Id'], image)
        self.prefix = 'fagi-upd-e2e-snapshot-restore-ledger-' + uuid.uuid4().hex[:8]
        self.owned_volumes = []
        self.executor_sources = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [Path(__file__), *(ROOT / 'deploy/update' / name for name in (
                'standalone_backup.py', 'standalone_restore.py', 'recovery.py', 'processes.py',
                'archive_inventory.py', 'backup_proof.py', 'probes/restore_fs.py'))]}
        self.report = dict(context=CONTEXT, kind='small-vendor-storage-fixture-not-product-qualification',
            prefix=self.prefix, images=IMAGES, executor_sources=self.executor_sources, rounds=[], cleanup={})
        self.addCleanup(self.cleanup_resources)

    def cmd(self, *args):
        result = self.runner([self.docker, '--context', CONTEXT, *args], capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace')[:2000])
        return result.stdout.decode()

    def save_report(self):
        path = self.evidence / (self.prefix + '.json')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            backup._write_all(fd, json.dumps(self.report, indent=2).encode())
            backup.sync_file(fd)
        finally:
            os.close(fd)

    def cleanup_resources(self):
        removed = []
        for cid in self.cmd('ps', '-aq', '--no-trunc').split():
            row = json.loads(self.cmd('inspect', cid))[0]
            if row['Name'].startswith('/' + self.prefix + '-'):
                self.cmd('rm', '-f', cid)
                removed.append(cid)
        for volume in reversed(self.owned_volumes):
            self.assertTrue(volume.startswith(self.prefix + '-'))
            if volume in self.cmd('volume', 'ls', '-q').split():
                self.cmd('volume', 'rm', volume)
        self.report['cleanup'] = dict(removed_container_ids=removed, removed_volumes=self.owned_volumes,
            container_inventory_unchanged=set(self.cmd('ps', '-aq', '--no-trunc').split()) == self.initial_containers,
            volume_inventory_unchanged=set(self.cmd('volume', 'ls', '-q').split()) == self.initial_volumes,
            image_inventory_unchanged=set(self.cmd('image', 'ls', '-q', '--no-trunc').split()) == self.initial_images,
            executor_sources_unchanged=all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                                           for name, digest in self.executor_sources.items()))
        self.save_report()
        self.assertTrue(all(self.report['cleanup'][k] for k in ('container_inventory_unchanged', 'volume_inventory_unchanged',
                                                               'image_inventory_unchanged', 'executor_sources_unchanged')))

    def service(self, root, project, service, config, volumes, *, target=False, home_binds_only=False):
        args = ['create', '--name', project + ('-target-' if target else '-') + service,
            '--pull', 'never', '--network', 'none', '--restart', 'unless-stopped', '--pids-limit', '256',
            '--memory', '512m' if service == 'clickhouse' else '256m' if service == 'postgres' else '64m', '--cpus', '1',
            '--label', 'com.docker.compose.project=' + project, '--label', 'com.docker.compose.service=' + service,
            '--label', 'com.docker.compose.oneoff=False', '--label', 'com.docker.compose.project.working_dir=' + str(root),
            '--label', 'com.docker.compose.project.config_files=' + str(root / 'docker-compose.yml'),
            '--health-cmd', config['healthcheck']['test'][1], '--health-interval', '1s', '--health-timeout', '3s', '--health-retries', '60']
        for key, value in config['environment'].items():
            args.extend(['--env', key + '=' + value])
        for key, (owner, path) in backup.VOLUMES.items():
            if owner == service:
                args.extend(['--mount', 'type=volume,source=' + volumes[key] + ',target=' + path + ',volume-nocopy'])
        for path, source in backup.BINDS.get(service, {}).items():
            if home_binds_only and source == '/dev/null':
                continue  # this storage-only fixture does not qualify the product bind topology
            source = source if source == '/dev/null' else str(root / source)
            if home_binds_only:
                self.assertTrue(source.startswith(str(Path.home()) + '/'))
            args.extend(['--mount', 'type=bind,source=' + source + ',target=' + path + ',readonly'])
        if config.get('entrypoint'):
            args.extend(['--entrypoint', config['entrypoint']])
        args.extend([IMAGES[service], *config.get('command', [])])
        cid = self.cmd(*args).strip()
        self.cmd('start', cid)
        self.wait_ready(cid)
        return cid

    def wait_ready(self, cid):
        end = time.monotonic() + 120
        while time.monotonic() < end:
            state = json.loads(self.cmd('inspect', cid))[0]['State']
            self.assertTrue(state['Running'], str(state))
            if state.get('Health', {}).get('Status') == 'healthy':
                return
            time.sleep(0.2)
        self.fail('small vendor fixture readiness timed out')

    def query(self, db, cid, sql):
        return self.cmd('exec', cid, *(['psql', '-U', 'futureagi', '-d', 'futureagi', '-At', '--command=' + sql]
            if db == 'postgres' else ['clickhouse-client', '--query', sql])).strip()

    def kill_recovery(self, root, destination, attempt, cut):
        code = ('import sys; sys.path.insert(0,sys.argv[1]); '
                'from deploy.tests.test_update_e2e_backup import recovery_worker; recovery_worker(*sys.argv[2:])')
        child = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT), str(root),
                                str(destination), attempt, cut], capture_output=True, timeout=600)
        fd = os.open(root / ('crash-' + cut.replace(':', '-') + '.log'),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            backup._write_all(fd, child.stdout + child.stderr)
            backup.sync_file(fd)
        finally:
            os.close(fd)
        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr.decode('utf-8', 'replace')[-4000:])
        return RecoveryLedger.open(destination / attempt)

    def home_bound_fixture_binding(self, installation, project, source):
        """Measured storage fixture, NOT capture_binding/product admission.

        The product's fixed /dev/null bind is outside the allowed HOME-only test
        mounts. This component fixture omits that bind instead of bypassing or
        weakening the shipping topology guard. Every stored Docker identity,
        volume/root identity and config hash below is measured from the fixture.
        """
        files = backup._files(installation.root)
        doc = dict(schema_version=1, installation_fingerprint='2' * 64,
            root=str(installation.root), context=CONTEXT, daemon=self.daemon, project=project,
            profiles=[], images=IMAGES,
            volumes={key: backup._volume(json.loads(self.cmd('volume', 'inspect', project + '_' + key))[0])
                     for key in backup.VOLUMES},
            volume_root_ids={key: self.cmd('exec', source[owner], '/bin/stat', '-c%d:%i', '--', path).strip()
                             for key, (owner, path) in backup.VOLUMES.items()},
            files={key: backup._hash(raw) for key, raw in files.items()},
            compose_sha256=backup._hash((installation.root / 'docker-compose.yml').read_bytes()))
        return backup.BackupBinding(backup.canonical_json(doc))

    def seal_storage_fixture(self, installation, binding, expected, ledger, source, policy):
        """Build real cold archives via shipping helpers; no product backup claim.

        The release/observation envelope is synthetic, as in the full vendor
        fixture. No qualifying compatibility proof is constructed. This is test
        setup for the shipping StandaloneRestore, not an alternate updater.
        """
        from types import SimpleNamespace
        from deploy.update.contracts import BackupSeal, RESTORE_PROCEDURE_ID
        from deploy.update.standalone_restore import _StorageHelpers
        started = datetime.now(timezone.utc).replace(microsecond=0)
        containers = {s: json.loads(self.cmd('inspect', cid))[0] for s, cid in source.items()}
        b = json.loads(binding.raw)
        ledger.checkpoint('quiescing', containers={s: backup._identity(c) for s, c in containers.items()},
                          stopped_container_ids=[])
        with backup._directory(ledger.directory) as fd:
            records = []
            for index, (name, raw) in enumerate(backup._files(installation.root).items()):
                backup._atomic(fd, 'config-' + str(index), raw)
                info = (installation.root / name).stat()
                records.append(dict(name=name, file='config-' + str(index), sha256=backup._hash(raw), size=len(raw),
                    metadata=dict(uid=info.st_uid, gid=info.st_gid, mode=info.st_mode & 0o7777,
                                  identity=[info.st_dev, info.st_ino, info.st_mtime_ns])))
            for service in ('app', 'postgres', 'clickhouse'):
                identity = backup._identity(containers[service])
                operation = ledger.begin('stop', identity=identity)
                self.cmd('stop', '--time', '60', identity['Id'])
                stopped = json.loads(self.cmd('inspect', identity['Id']))[0]
                self.assertEqual(backup._identity(stopped), identity)
                self.assertEqual(stopped['State']['Status'], 'exited')
                self.assertFalse(stopped['State']['Running'])
                self.assertIn(stopped['State']['ExitCode'], (0, 143))
                self.assertFalse(stopped['State'].get('OOMKilled'))
                ledger.complete(operation, {'id': identity['Id'], 'stopped': True})
            snapshot = datetime.now(timezone.utc).replace(microsecond=0)
            quiescence = dict(containers=ledger.snapshot['containers'], daemon=self.daemon,
                volumes=b['volumes'], roots=b['volume_root_ids'], snapshot_at=backup._stamp(snapshot))
            quiescence_hash = backup._hash(backup.canonical_json(quiescence))
            source_hash = observation(binding, containers, started).sha256
            ledger.checkpoint('quiesced', snapshot_at=backup._stamp(snapshot),
                source_observation_sha256=source_hash, quiescence_evidence_sha256=quiescence_hash)
            helpers = _StorageHelpers(installation, b, ledger, policy=policy)
            ledger.checkpoint('archiving')
            archives = {}
            for key, (_, root) in backup.VOLUMES.items():
                archive_fd = backup._new(fd, key + '.tar')
                member_fd = backup._new(fd, key + '.members.jsonl')
                try:
                    result = helpers.execute(key, 'archive', tar=True, output_fd=archive_fd,
                                             max_output=policy.max_archive_bytes)
                    backup.sync_file(archive_fd)
                    entry = backup.verify_archive(archive_fd, member_fd, policy, deadline=helpers.deadline,
                                                  volume_root=root, complete_tree=True)
                    actual = helpers.inventory(key, fd)
                    self.assertEqual((result.sha256, result.size), (entry['sha256'], entry['size']))
                    self.assertEqual((actual['sha256'], actual['size']), (entry['members_sha256'], entry['members_bytes']))
                    entry['volume'] = b['volumes'][key]
                    archives[key] = entry
                finally:
                    os.close(archive_fd)
                    os.close(member_fd)
            completed = datetime.now(timezone.utc).replace(microsecond=0)
            inventory = dict(schema_version=2, inventory_version=2, binding=b, binding_sha256='2' * 64,
                expectation=ledger.snapshot['expectation'], source_observation_sha256=source_hash,
                quiescence_evidence_sha256=quiescence_hash, quiescence=quiescence,
                containers=ledger.snapshot['containers'], archives=archives, config_files=records,
                started_at=backup._stamp(started), snapshot_at=backup._stamp(snapshot),
                completed_at=backup._stamp(completed), restore_procedure_id=RESTORE_PROCEDURE_ID)
            inventory_raw = backup.canonical_json(inventory)
            backup._atomic(fd, 'inventory.json', inventory_raw)
            proof_doc = {k: ledger.snapshot['expectation'][k] for k in ('attempt_id', 'installation_fingerprint',
                         'target_manifest_hash', 'source_version', 'covered_domains')}
            proof_doc.update(schema_version=2, backup_id='storage-fixture-' + expected.attempt_id,
                started_at=backup._stamp(started), completed_at=backup._stamp(completed), verification='passed',
                restore_procedure_id=RESTORE_PROCEDURE_ID, inventory_version=2, binding_sha256='2' * 64,
                inventory_sha256=backup._hash(inventory_raw), source_observation_sha256=source_hash,
                quiescence_evidence_sha256=quiescence_hash, snapshot_at=backup._stamp(snapshot))
            raw = backup.canonical_json(proof_doc)
            proof = backup.validate_backup_proof(raw, expected, now=completed)
            backup._atomic(fd, 'proof.json', raw)
            doc = ledger.snapshot
            seal = BackupSeal(expected.attempt_id, doc['operation_token'], doc['run_id'], '2' * 64,
                expected.target_manifest_hash, expected.source_version, proof.sha256, backup._hash(inventory_raw),
                source_hash, quiescence_hash, snapshot, completed)
            ledger.seal_backup(seal)
            return SimpleNamespace(proof=proof, seal=seal)

    def test_home_bound_storage_helpers_and_crash_matrices(self):
        self.test_small_postgres_and_clickhouse_same_volume_round_trip(storage_fixture=True)

    def test_small_postgres_and_clickhouse_same_volume_round_trip(self, *, storage_fixture=False):
        self.report['snapshot_path'] = ('shipping-storage-helpers-with-measured-fixture-binding' if storage_fixture
                                        else 'shipping-run-quiesced-with-product-topology')
        for db in ('postgres', 'clickhouse'):
            with self.subTest(database=db):
                project = self.prefix + '-' + db[:2]
                root = self.evidence / project
                root.mkdir(mode=0o700)
                for name in backup.CONFIG_FILES:
                    path = root / name
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    path.write_bytes(b'FUTURE_AGI_VERSION=v1.53.0\nSECRET_KEY=fixture\n' if name == '.env' else
                                     b'{}' if name == 'docker-compose.yml' else (ROOT / name).read_bytes())
                    path.chmod(0o600)
                services = {}
                for service in IMAGES:
                    env = dict(backup.LOCAL_ENV) if service == 'app' else {}
                    if service == 'postgres':
                        env.update(PGDATA='/var/lib/postgresql/data', POSTGRES_USER='futureagi', POSTGRES_DB='futureagi', POSTGRES_PASSWORD=SECRET)
                    if service == 'clickhouse':
                        env.update(CLICKHOUSE_SKIP_USER_SETUP='1', CLICKHOUSE_PASSWORD='')
                    cfg = dict(image=IMAGES[service], restart='unless-stopped', environment=env, entrypoint='/bin/sh',
                        command=['-ec', "trap 'exit 0' TERM INT; sleep 86400 & wait"], healthcheck={'test': ['CMD-SHELL', 'true']})
                    if service == db:
                        cfg.pop('entrypoint')
                        cfg['command'] = ['postgres', '--max_connections=20', '--shared_buffers=16MB'] if db == 'postgres' else []
                        cfg['healthcheck']['test'][1] = 'pg_isready -U futureagi -d futureagi' if db == 'postgres' else 'wget --spider -q http://127.0.0.1:8123/ping'
                    cfg['volumes'] = [key + ':' + path for key, (owner, path) in backup.VOLUMES.items() if owner == service]
                    cfg['volumes'] += [(source if source == '/dev/null' else './' + source) + ':' + path + ':ro'
                                       for path, source in backup.BINDS.get(service, {}).items()
                                       if not storage_fixture or source != '/dev/null']
                    services[service] = cfg
                (root / 'docker-compose.yml').write_text(json.dumps(dict(name=project, services=services, volumes={k: {} for k in backup.VOLUMES})))
                volumes = {}
                for key in backup.VOLUMES:
                    volume = project + '_' + key
                    self.cmd('volume', 'create', '--label', 'com.docker.compose.project=' + project, '--label', 'com.docker.compose.volume=' + key, volume)
                    volumes[key] = volume
                    self.owned_volumes.append(volume)
                source = {s: self.service(root, project, s, cfg, volumes, home_binds_only=storage_fixture)
                          for s, cfg in services.items()}
                if storage_fixture:
                    for cid in source.values():
                        row = json.loads(self.cmd('inspect', cid))[0]
                        self.assertTrue(all(m['Source'].startswith(str(Path.home()) + '/')
                                            for m in row['Mounts'] if m['Type'] == 'bind'))
                self.cmd('exec', source['app'], 'python', '-Ic',
                    "import os,pathlib,struct; p=pathlib.Path('/data'); (p/'blob').write_bytes(b'cold-blob'); os.link(p/'blob',p/'hardlink'); "
                    "os.symlink('blob',p/'symlink'); os.setxattr(p/'blob','user.fixture',b'xattr'); "
                    "(p/'café.txt').write_bytes(b'unicode-persisted'); os.link(p/'café.txt',p/'資料'); "
                    "os.symlink('café.txt',p/'unicode-link'); os.setxattr(p/'café.txt','user.café',b'unicode-xattr'); "
                    "acl=struct.pack('<I',2)+b''.join(struct.pack('<HHI',*e) for e in [(1,6,4294967295),(2,4,1234),(4,4,4294967295),(16,4,4294967295),(32,0,4294967295)]); "
                    "os.setxattr(p/'blob','system.posix_acl_access',acl); "
                    "f=open(p/'sparse','wb'); f.seek(1024*1024); f.write(b'z'); f.close(); os.utime(p/'blob',ns=(1700000000123456789,1700000000123456789))")
                if db == 'postgres':
                    self.query(db, source[db], "CREATE TABLE restore_fixture(id integer,payload text); INSERT INTO restore_fixture VALUES(41,'persisted');")
                else:
                    self.query(db, source[db], 'CREATE TABLE restore_fixture(id UInt32,payload String) ENGINE=MergeTree ORDER BY id')
                    self.query(db, source[db], "INSERT INTO restore_fixture VALUES(41,'persisted')")
                query = 'SELECT id,payload FROM restore_fixture ORDER BY id'
                before = self.query(db, source[db], query)
                installation = Installation(root, environ=self.runner.environment, runner=self.runner,
                    docker=[self.docker, '--context', CONTEXT], compose=[self.compose])
                if storage_fixture:
                    binding = self.home_bound_fixture_binding(installation, project, source)
                else:
                    binding = backup.capture_binding(installation, installation_fingerprint='2' * 64,
                        docker_context=CONTEXT, daemon_id=self.daemon, project=project, profiles=(), verified_images=IMAGES,
                        deployment_hashes={k: backup._hash((root / k).read_bytes()) for k in backup.CONFIG_FILES[1:]})
                (root / 'fixture-binding.json').write_bytes(binding.raw)
                (root / 'fixture-binding.json').chmod(0o600)
                now = datetime.now(timezone.utc).replace(microsecond=0)
                expected = BackupExpectation(uuid.uuid4().hex, '2' * 64, '3' * 64, 'v1.53.0', now, backup.DOMAINS)
                destination = root / 'backups'
                backup.create_destination(destination)
                ledger = RecoveryLedger.reserve(destination, expected, operation_token=uuid.uuid4().hex, run_id=uuid.uuid4().hex, binding_sha256='2' * 64)
                obs = observation(binding, {s: json.loads(self.cmd('inspect', c))[0] for s, c in source.items()}, now)
                proof_dir = root / 'engine-proof'
                proof_dir.mkdir(mode=0o700)
                fd = os.open(proof_dir, os.O_RDONLY | os.O_DIRECTORY)
                policy = backup.BackupPolicy(max_archive_bytes=512 * 1024 ** 2, timeout_seconds=600)
                try:
                    if storage_fixture:
                        artifact = self.seal_storage_fixture(installation, binding, expected, ledger, source, policy)
                    else:
                        artifact = backup.StandaloneBackup(installation, binding, destination, policy=policy).run_quiesced(
                            expected, ledger, obs, proof_directory_fd=fd)
                finally:
                    os.close(fd)
                self.assertTrue(all(not json.loads(self.cmd('inspect', c))[0]['State']['Running'] for c in source.values()))
                if not storage_fixture:
                    backup.StandaloneBackup(installation, binding, destination, policy=policy).verify_hold(ledger, obs)
                self.assertFalse(self.cmd('ps', '-aq', '--filter', 'label=ai.futureagi.restore-attempt=' + expected.attempt_id).strip(),
                                 'completed backup helpers must not remain foreign volume attachments')
                inventory = json.loads((ledger.directory / 'inventory.json').read_bytes())
                self.assertNotIn('proof_sha256', inventory)
                self.assertEqual(artifact.proof.inventory_sha256, backup._hash((ledger.directory / 'inventory.json').read_bytes()))
                target = self.service(root, project, db, services[db], volumes, target=True, home_binds_only=storage_fixture)
                self.query(db, target, "INSERT INTO restore_fixture VALUES(99,'candidate-only')")
                failed_writer = self.cmd('create', '--name', project + '-target-files', '--pull', 'never',
                    '--network', 'none', '--read-only', '--restart', 'no', '--memory', '32m', '--pids-limit', '16',
                    '--mount', 'type=volume,source=' + volumes['app-data'] + ',target=/data,volume-nocopy',
                    '--entrypoint', '/bin/sh', BUSYBOX, '-ec',
                    'printf candidate > /data/blob; printf candidate > /data/candidate-only').strip()
                self.cmd('start', '-a', failed_writer)
                ledger.checkpoint(target_containers={db: backup._identity(json.loads(self.cmd('inspect', target))[0]),
                    'fixture-files': backup._identity(json.loads(self.cmd('inspect', failed_writer))[0])})
                ledger.decide_restore('fixture_failure_after_database_write')
                restore = StandaloneRestore(installation, binding, destination, policy=policy)
                self.addCleanup(restore.close)
                restore.quiesce_attempt(expected, ledger)
                # Corrupt and truncated archives refuse before clearing any store.
                archive = ledger.directory / 'app-data.tar'
                original = archive.read_bytes()
                refusals = []
                for kind, payload in (('tampered', original[:1024] + bytes([original[1024] ^ 1]) + original[1025:]), ('truncated', original[:1024])):
                    archive.write_bytes(payload)
                    try:
                        with self.assertRaises((RuntimeError, ValueError, EOFError)):
                            restore.verify_inputs(expected, ledger)
                        self.assertEqual(ledger.snapshot['volumes'], {})
                        refusals.append(kind)
                    finally:
                        archive.write_bytes(original)
                crash_boundaries = []
                restore.close()
                # Both vendors: terminate real fresh executors at shared-file
                # AND database-volume destructive boundaries, then at the
                # startup barrier. The next child reopens the same real ledger
                # and restores the same original volume objects.
                for cut in ('volume:app-data:clear_intent', 'volume:app-data:extract_intent',
                            'volume:' + db + '-data:clear_intent', 'volume:' + db + '-data:extract_intent',
                            'previous_start_armed'):
                    ledger = self.kill_recovery(root, destination, expected.attempt_id, cut)
                    crash_boundaries.append(cut)
                self.assertTrue(ledger.snapshot['previous_start_armed'])
                after_stores = {}
                for key, volume in volumes.items():
                    after_stores[key] = backup._volume(json.loads(self.cmd('volume', 'inspect', volume))[0])
                self.assertEqual(after_stores, json.loads(binding.raw)['volumes'])
                self.assertFalse(self.cmd('ps', '-aq', '--filter', 'label=ai.futureagi.restore-attempt=' + expected.attempt_id).strip(),
                                 'completed restore helpers must not remain foreign volume attachments')
                # Durable source-start barrier BEFORE even a DB starts.
                if not ledger.snapshot['previous_start_armed']:
                    ledger.arm_previous_start()
                for cid in source.values():
                    self.cmd('start', cid)
                    self.wait_ready(cid)
                self.assertEqual(self.query(db, source[db], query), before)
                self.cmd('exec', source['app'], 'python', '-Ic',
                    "import os; from pathlib import Path; p=Path('/data'); assert (p/'blob').read_bytes()==b'cold-blob'; "
                    "assert not (p/'candidate-only').exists(); assert (p/'café.txt').read_bytes()==b'unicode-persisted'; "
                    "assert os.path.samefile(p/'café.txt',p/'資料'); assert os.readlink(p/'unicode-link')=='café.txt'; "
                    "assert os.getxattr(p/'café.txt','user.café')==b'unicode-xattr'")
                self.query(db, source[db], "INSERT INTO restore_fixture VALUES(77,'after-restore')")
                # Fresh recovery cannot erase writes after prior services may start.
                ledger = self.kill_recovery(root, destination, expected.attempt_id, 'metadata_after_source_write')
                crash_boundaries.append('metadata_after_source_write')
                fresh = RecoveryLedger.open(ledger.directory, expected=expected)
                replacement = StandaloneRestore(installation, binding, destination, policy=policy)
                self.addCleanup(replacement.close)
                with self.assertRaises(RecoveryError):
                    replacement.quiesce_attempt(expected, fresh)
                self.assertIn('after-restore', self.query(db, source[db], query))
                for key, (owner, path) in backup.VOLUMES.items():
                    actual_root = self.cmd('exec', source[owner], '/bin/stat', '-c%d:%i', '--', path).strip()
                    self.assertEqual(actual_root, json.loads(binding.raw)['volume_root_ids'][key])
                ledger.checkpoint('previous_verifying')
                self.report['rounds'].append(dict(database=db, ledger=str(ledger.directory),
                    same_volume_metadata=True, root_inode_preserved=True, original_record_restored=True,
                    candidate_record_absent=True, complete_tree_verified=True, hardlink_acl_xattr_sparse_mtime_verified=True,
                    unicode_path_link_hardlink_xattr_verified=True, home_binds_only=storage_fixture,
                    busybox_failed_state_writer=BUSYBOX,
                    archive_refusals=refusals, real_process_sigkill_boundaries=crash_boundaries,
                    post_start_writes_preserved=True, completed_helper_attachments_removed=True,
                    proof_sha256=artifact.proof.sha256,
                    original_volumes=json.loads(binding.raw)['volumes']))
                self.save_report()
                restore.close()
                # Serial fixtures: no active stack from the previous database.
                for cid in self.cmd('ps', '-aq', '--no-trunc').split():
                    row = json.loads(self.cmd('inspect', cid))[0]
                    if row['Name'].startswith('/' + project + '-'):
                        self.cmd('rm', '-f', cid)


if __name__ == '__main__':
    unittest.main()
