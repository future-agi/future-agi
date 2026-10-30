"""Engine/runner component integration, NOT scheduler/product-image certification.

Real local-key signatures, ReleaseDiscovery/cache, plan/guard, cold-backup tar
verification, envcommit, receipts and baseline transactions. Docker, storage,
structural probes, and HTTP delivery are explicitly fixed fixture adapters. Each
tick is a fresh bounded Python process. No eligibility/plan/proof validation is
patched out, and no private facts/proof file is edited between A, B and C.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from unittest import mock

from deploy.tests.test_update_apply import FakeInstallation, Group
from deploy.tests.test_update_compatibility import bind_proof, restore_release
from deploy.tests.test_update_standalone_backup import Daemon, archive_bytes
from deploy.tests.test_update_trust import release as release_record
from deploy.update.automatic import AutomaticLifecycle, BackupConfiguration
from deploy.update.baseline import BaselineStore, StructuralObservation
from deploy.update.compatibility import Observations, RuntimeImage, topology_profile
from deploy.update.contracts import HostSizeObservation, HostTableSize, RuntimeObservationV2, installation_binding_hash
from deploy.update.discovery import ReleaseDiscovery
from deploy.update.envfile import read_env
from deploy.update.httpcache import HttpCache
from deploy.update.install import CommandResult
from deploy.update.manifest import BUNDLE_ASSET, MANIFEST_ASSET, canonical_json, manifest_hash, parse_image_ref
from deploy.update.orchestrator import fingerprints, guarded_updater
from deploy.update.readiness import NotReady
from deploy.update.runner import HostRunner
from deploy.update.runner_state import InstallationBinding, LauncherConfig, Registration, RunnerStateStore
from deploy.update.settings import load_settings
from deploy.update import standalone_backup as backup
from deploy.update.trust import HttpResponse, Verifier

VERSIONS = ('v1.51.0', 'v1.52.0', 'v1.53.0')
ROOT = Path(__file__).resolve().parents[2]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


FIXTURE_WALL = None
FIXTURE_MONOTONIC = None
CLOCK_SHIFT = 0


def now():
    if FIXTURE_WALL is not None:
        return FIXTURE_WALL + timedelta(seconds=time.monotonic() - FIXTURE_MONOTONIC + CLOCK_SHIFT)
    return datetime.now(timezone.utc)


def advance_fixture_clock(current, seconds):
    """Model a positive sleep without stalling below float clock precision.

    Real sleep always advances monotonic time. A fractional simulated interval
    can round back to the same float, causing an otherwise valid readiness loop
    to busy-spin forever. Move by one representable tick only in that case;
    retain the real readiness/stability/deadline assertions without tolerances.
    """
    import math
    assert math.isfinite(current) and math.isfinite(seconds) and seconds > 0
    following = current + seconds
    return following if following > current else math.nextafter(current, math.inf)


def image_id(version):
    return 'sha256:' + sha(('fixture-image-' + version).encode())


def layout_files():
    return {name: name.encode() for name in (*backup.CONFIG_FILES[1:], '.env.example')}


class ComponentDaemon(Daemon):
    """Finite Docker protocol, including exact-ID filesystem helpers.

    All storage/container data is synthetic. Shipping helper authorization,
    ledger, archive verification and streamed-FD checks remain unmodified.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trees = {}

    def _helper_payload(self, row, input_fd=None):
        import io
        import tarfile
        from deploy.update.archive_inventory import verify_archive
        key = next(k for k in backup.VOLUMES if self.project + '_' + k == row['Mounts'][0]['Name'])
        command = row['Config']['Cmd']
        tar = row['Config']['Entrypoint'] == ['/usr/bin/timeout']
        purpose = ('extract' if '-xpf' in command else 'archive') if tar else command[4]
        self.events.append(['helper', purpose, key, row['Id']])
        if purpose == 'inspect':
            payload = canonical_json(dict(root_identity='1:123', filesystem='fixture-device',
                                         free_bytes=10**15, free_inodes=10**9))
        elif purpose == 'archive':
            payload = self.trees.get(key, self.tar)
            if self.mode == 'partial':
                payload = payload[:600]
        elif purpose == 'inventory':
            with tempfile.NamedTemporaryFile(dir=self.root) as archive, tempfile.NamedTemporaryFile(dir=self.root) as members:
                archive.write(self.trees.get(key, self.tar)); archive.flush()
                verify_archive(archive.fileno(), members.fileno(), backup.BackupPolicy(),
                    deadline=time.monotonic()+30, volume_root=backup.VOLUMES[key][1], complete_tree=True)
                members.seek(0); payload = members.read()
        elif purpose == 'extract':
            assert input_fd is not None
            os.lseek(input_fd, 0, os.SEEK_SET)
            chunks = []
            while True:
                chunk = os.read(input_fd, 65536)
                if not chunk: break
                chunks.append(chunk)
            self.trees[key] = b''.join(chunks)
            payload = b''
        elif purpose == 'clear':
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
                root = tarfile.TarInfo('.'); root.type = tarfile.DIRTYPE; archive.addfile(root)
            self.trees[key] = stream.getvalue()
            payload = b'empty\n'
        elif purpose == 'empty':
            with tarfile.open(fileobj=io.BytesIO(self.trees[key])) as archive:
                assert [m.name for m in archive] == ['.']
            payload = b'empty\n'
        elif purpose == 'sync':
            payload = b'synchronized\n'
        else:
            raise AssertionError(purpose)
        row['State'].update(Running=False, Status='exited', ExitCode=0)
        return payload

    def __call__(self, args, **kwargs):
        words = list(args[3:])
        if words[:2] == ['volume','inspect']:
            self._command(args); self.calls.append(words)
            return subprocess.CompletedProcess(args,0,canonical_json([self.volumes[n] for n in words[2:]]),b'')
        if words and words[0] == 'create':
            self._command(args); self.calls.append(words)
            value = lambda flag: words[words.index(flag)+1]
            image = words[words.index('--entrypoint')+2]
            command = words[words.index(image)+1:]
            mount = dict(p.split('=',1) for p in value('--mount').split(',') if '=' in p)
            volume = mount['source']
            cid = sha(('helper-'+value('--name')).encode())
            assert cid not in self.containers
            host = dict(NetworkMode=value('--network'), ReadonlyRootfs='--read-only' in words,
                RestartPolicy={'Name': value('--restart')}, CapDrop=[value('--cap-drop')],
                CapAdd=[words[i+1] for i,v in enumerate(words) if v == '--cap-add'],
                SecurityOpt=[value('--security-opt')], NanoCpus=int(value('--cpus'))*10**9,
                Privileged=False, Binds=None, Devices=None, PidMode='', IpcMode='private',
                Memory=256*1024**2, PidsLimit=int(value('--pids-limit')),
                Tmpfs={words[i+1].split(':',1)[0]:words[i+1].split(':',1)[1]
                       for i,v in enumerate(words) if v == '--tmpfs'})
            self.containers[cid] = dict(Id=cid, Name='/'+value('--name'), Image=image,
                Created='fixture-created', RestartCount=0,
                Config=dict(Labels=dict([value('--label').split('=',1)]),
                    Entrypoint=[value('--entrypoint')], Cmd=command), HostConfig=host,
                Mounts=[dict(Type='volume', Name=volume, Source=self.volumes[volume]['Mountpoint'],
                             Destination=mount['target'], RW='readonly' not in value('--mount').split(','))],
                State=dict(Running=False, Status='created', ExitCode=0))
            return subprocess.CompletedProcess(args,0,(cid+'\n').encode(),b'')
        if words[:2] == ['start','-a']:
            self._command(args); self.calls.append(words)
            row = self.find(words[-1]); assert row['State']['Status'] == 'created'
            return subprocess.CompletedProcess(args,0,self._helper_payload(row),b'')
        result = super().__call__(args, **kwargs)
        if words and words[0] == 'start':
            row = self.find(words[-1])
            if row['Config'].get('Labels', {}).get('com.docker.compose.service') == 'app':
                version = next(v for v in VERSIONS if row['Image'] == image_id(v))
                self.events.append(['start', version, row['Id']])
                if version != VERSIONS[0]:
                    import io, tarfile
                    for key in backup.VOLUMES:
                        with tarfile.open(fileobj=io.BytesIO(self.trees.get(key,self.tar))) as archive:
                            contents = {m.name:archive.extractfile(m).read() for m in archive if m.isfile()}
                        contents['./candidate-only'] = version.encode()
                        self.trees[key] = archive_bytes(contents)
        return result

    def stream_to(self, args, output_fd, **kwargs):
        if list(args[3:5]) != ['start','-a']:
            return super().stream_to(args, output_fd, **kwargs)
        self._command(args); self.calls.append(list(args[3:]))
        row = self.find(args[-1]); assert row['State']['Status'] == 'created'
        payload = self._helper_payload(row)
        code = 'import os; data='+repr(payload)+'; os.write(1,data)'
        if self.mode == 'timeout':
            code += '; import time; time.sleep(10)'; kwargs['timeout'] = 0.05
        from deploy.update.processes import ProcessSupervisor
        result = ProcessSupervisor.stream_to(self,[sys.executable,'-I','-c',code],output_fd,**kwargs)
        if self.after_archive: self.after_archive()
        return result

    def stream_from(self, args, input_fd, **kwargs):
        self._command(args); self.calls.append(list(args[3:]))
        row = self.find(args[-1]); assert row['State']['Status'] == 'created'
        assert backup._file_identity(os.fstat(input_fd)) == kwargs['expected_identity']
        payload = self._helper_payload(row,input_fd)
        return subprocess.CompletedProcess(args,0,payload,b'')


class RecordedInstallation(FakeInstallation):
    def __init__(self, root, daemon, artifacts):
        super().__init__(root, root / '.env', [],
            (Group('standalone', 'FUTURE_AGI_VERSION', ('app',), 'docker.io/futureagi/standalone', VERSIONS[0]),),
            mode='standalone', project='test-backup')
        self.daemon, self.runner, self.artifacts = daemon, daemon, artifacts
        self.environ = daemon.environment
        self.docker = ['docker', '--context', 'test-backup']
        self.third_party = {s: 'docker.io/library/synthetic-' + s + ':1' for s in ('postgres', 'clickhouse')}
        self.up_pull_never = True
        self.local_images = daemon.local_images
        for version in VERSIONS:
            manifest = json.loads((artifacts / version / MANIFEST_ASSET).read_bytes())
            item = manifest['components']['standalone']
            self.metadata[image_id(version)] = {'version': version,
                'repo_digests': [parse_image_ref(item['image']).name + '@' + item['digest']]}
            self.registry[item['image']] = image_id(version)
        for service, ref in self.third_party.items():
            self.metadata[daemon.images[service]] = {'version': '', 'repo_digests': [ref.split(':')[0] + '@sha256:' + 'c'*64]}
        self.running = {c['Config']['Labels']['com.docker.compose.service']:
            {'image_id': c['Image'], 'image': self.reference(c['Image'])} for c in daemon.containers.values()
            if 'com.docker.compose.service' in c['Config'].get('Labels', {})}

    def reference(self, iid):
        for version in VERSIONS:
            if image_id(version) == iid:
                return 'docker.io/futureagi/standalone:' + version
        return next(ref for s, ref in self.third_party.items() if self.daemon.images[s] == iid)

    def fresh(self):
        return RecordedInstallation(self.root, self.daemon, self.artifacts)

    def resolved_images(self):
        return {'app': 'docker.io/futureagi/standalone:' + self.env['FUTURE_AGI_VERSION']}

    def config(self, refresh=False):
        result = copy.deepcopy(self.daemon.model)
        result['services']['app']['image'] = self.resolved_images()['app']
        for service, ref in self.third_party.items():
            result['services'][service]['image'] = ref
        for service in result['services'].values():
            service['restart'] = 'unless-stopped'
        return result

    def docker_run(self, *args, check=True):
        if args == ('info', '--format', '{{.ID}}'):
            return CommandResult([], 0, 'daemon-1', '')
        if args[0] in ('inspect', 'start', 'stop', 'volume'):
            result = self.daemon([*self.docker, *args])
            return CommandResult([], result.returncode, result.stdout.decode(), result.stderr.decode())
        return super().docker_run(*args, check=check)

    def containers(self):
        return [dict(id=c['Id'], service=c['Config']['Labels']['com.docker.compose.service'], image_id=c['Image'],
                     image=self.reference(c['Image']), status=c['State']['Status'], exit_code=0, restart_count=0,
                     health=c['State'].get('Health', {}).get('Status', ''), started_at=c['State'].get('StartedAt', ''),
                     command=[], oneoff=False) for c in self.daemon.containers.values()
                if c['Config'].get('Labels', {}).get('com.docker.compose.project') == self.project]

    def compose_run(self, *args, **kwargs):
        if args[0] == 'pull':
            self.daemon.events.append(['pull', self.env['FUTURE_AGI_VERSION']])
            self.local_images.update({self.resolved_images()['app']: image_id(self.env['FUTURE_AGI_VERSION'])})
            if self.daemon.fault == 'window_after_prefetch':
                global CLOCK_SHIFT
                CLOCK_SHIFT += 7200
            if self.daemon.fault == 'pause_after_prefetch':
                RunnerStateStore(self.root, self.env_file).change_control('pause', now())
            if self.daemon.fault == 'config_after_prefetch':
                with self.env_file.open('a') as out:
                    out.write('FOREIGN_CHANGE=1\n')
        elif args[0] == 'create':
            assert args == ('create', '--no-build', '--pull', 'never', '--force-recreate')
            self.daemon.events.append(['up', self.env['FUTURE_AGI_VERSION']])
            for service in ('app','postgres','clickhouse'):
                old = self.daemon.find(service)
                del self.daemon.containers[old['Id']]
                old['Id'] = sha(('container-'+service+'-'+self.env['FUTURE_AGI_VERSION']).encode())
                if service == 'app':
                    old['Image'] = self.daemon.images['app'] = image_id(self.env['FUTURE_AGI_VERSION'])
                old['State'].update(Running=False, Status='created', StartedAt='')
                self.daemon.containers[old['Id']] = old
                self.running[service] = {'image_id': old['Image'], 'image': self.reference(old['Image'])}
        return CommandResult([], 0, '', '')


class FixedObserver:
    def __init__(self, registration):
        self.registration = registration

    def observe(self, inst, *, current_release, execution_deployment, boundary):
        # These are fixed structural fixture measurements, not production attestations.
        if inst.daemon.fault == 'unavailable_after_success':
            raise NotReady('B unavailable after its durable success decision')
        from deploy.update.receipt import StateDir
        assert StateDir(inst.root).lock_holder(automation=True) is not None, 'observation escaped update.lock'
        config, profile, _ = fingerprints(inst)
        layout = topology_profile('standalone', ())
        products, dependencies = {}, {}
        for container in inst.containers():
            selected = inst.image(inst.config()['services'][container['service']]['image'])
            from deploy.update.orchestrator import require
            from deploy.update.contracts import ReasonCode
            require(selected is not None and selected['id'] == container['image_id'], ReasonCode.IMAGE_DIGEST_MISMATCH)
            info = inst.image(container['image_id'])
            ref = parse_image_ref(info['repo_digests'][0])
            image = RuntimeImage(container['image'], ref.digest, container['image_id'])
            (products if container['service'] == 'app' else dependencies)[container['service']] = (image,)
        facts = Observations(now().replace(microsecond=0), platform='linux/amd64', product_images=products, dependency_images=dependencies,
            state_epochs={d: 7 for d in layout.domains}, persistence=dict(layout.persistence),
            deployment_files={n: sha((inst.root/n).read_bytes()) for n in execution_deployment['files']}, env=inst.env,
            installation_fingerprint=self.registration.binding.installation_fingerprint, config_fingerprint=config,
            profile_fingerprint=profile, trust_fingerprint=current_release.trust_fingerprint,
            owner_confirmed=True, topology_qualified=True, runtime_complete=True, no_source_build=True,
            no_pending_migrations=inst.daemon.fault != 'pending_migrations', no_pending_transaction=True)
        inventory, applied = {VERSIONS[0]: ('1', '3'), VERSIONS[1]: ('2', '4'), VERSIONS[2]: ('a', 'b')}[current_release.version]
        containers = inst.containers()
        runtime = RuntimeObservationV2(current_release.version, current_release.manifest_sha256, facts.now,
            installation_binding_hash(self.registration.binding), facts.platform, products, dependencies,
            inventory * 64, ('0' if inst.daemon.fault == 'pending_migrations' else applied) * 64, '5' * 64,
            manifest_hash({'files': dict(facts.deployment_files)}), sha(inst.env_file.read_bytes()), config, 'd' * 64,
            'e' * 64, {c['id']: c['restart_count'] for c in containers},
            {c['id']: 'unless-stopped' for c in containers}, True, True)
        return StructuralObservation(replace(facts, runtime_observation=runtime, executor_sha256='7' * 64,
            binding_sha256=runtime.binding_sha256), inst.env_file.read_bytes(), {},
            {d: sha(('volume-' + d).encode()) for d in layout.backup_domains}, 'standalone-runtime-v2', True)

    def measure_update_sizes(self, installation, *, measurements, backup_destination):
        tables = {(m.engine, t.table): HostTableSize(m.engine, t.table, t.reference_rows, t.reference_bytes)
                  for m in measurements.migration_measurements for t in m.tables}
        return HostSizeObservation(tuple(tables.values()), dict(measurements.backup_measurement.reference_volume_bytes),
                                   10**15, 10**15)


def render(inst, raw):
    fresh = inst.fresh()
    # Same fixed Compose fixture evaluation, including every env byte's parsed value.
    from deploy.update.envfile import _definitions
    fresh.env = {d.key: d.value for d in _definitions(raw.decode())}
    return fingerprints(fresh)[0]


class FileTransport:
    """Recorded release endpoints; real discovery/verifier, no external network."""
    def __init__(self, root, artifacts, fault):
        self.root, self.artifacts, self.fault = root, artifacts, fault

    def __call__(self, url, headers, timeout, **kwargs):
        visible = (self.root / 'publisher-visible').read_text()
        versions = VERSIONS[:VERSIONS.index(visible)+1]
        if '/repos/' in url:
            records = [release_record(v, MANIFEST_ASSET, BUNDLE_ASSET) for v in versions]
            if '/tags/' in url:
                records = next((r for r in records if r['tag_name'] == url.rsplit('/', 1)[-1]), None)
            return HttpResponse(200 if records is not None else 404, canonical_json(records), final_url=url)
        version, asset = url.split('/')[-2:]
        path = self.artifacts / version / asset
        if version not in versions or not path.exists() or self.fault == 'missing_proof' and asset == 'future-agi-update-compatibility.json':
            return HttpResponse(404, final_url=url)
        raw = path.read_bytes()
        if self.fault == 'bad_signature' and version != VERSIONS[0] and asset == BUNDLE_ASSET:
            changed = json.loads(raw)
            changed['messageSignature']['signature'] = 'AAAA'
            raw = canonical_json(changed)
        return HttpResponse(200, raw, final_url=url)


def process_tick(root, artifacts, operation='tick', fault='', hooks=None):
    global FIXTURE_WALL, FIXTURE_MONOTONIC, CLOCK_SHIFT
    hooks = hooks or {}
    CLOCK_SHIFT = 0
    # Deterministic scheduler clock, not an installed OS timer. Publication
    # advances simulated time seven hours; no runner/private baseline file edits.
    visible = (root/'publisher-visible').read_text()
    FIXTURE_WALL = datetime.now(timezone.utc) + timedelta(hours=7*VERSIONS.index(visible))
    FIXTURE_MONOTONIC = time.monotonic()
    settings = load_settings(root, environ={})
    store = RunnerStateStore(root, root / '.env')
    daemon = ComponentDaemon(root)
    daemon.policy = settings.automation_policy
    daemon.fault, daemon.events = fault, []
    business = root/'business-rows.fixture'
    if business.exists():
        daemon.tar = archive_bytes({'./records': business.read_bytes()})
    daemon.environment.update(HOME=str(Path.home()), PATH=os.defpath, USER='fixture', LOGNAME='fixture')
    state_path = root / 'recorded-daemon.json'
    if state_path.exists():
        data = json.loads(state_path.read_bytes())
        for name in ('containers', 'images', 'local_images'):
            setattr(daemon, name, data[name])
        daemon.trees = {k:bytes.fromhex(v) for k,v in data.get('trees',{}).items()}
        if business.exists():
            daemon.trees = {key:daemon.tar for key in backup.VOLUMES}
    else:
        daemon.find('app')['Image'] = daemon.images['app'] = image_id(VERSIONS[0])
        daemon.local_images = {'docker.io/futureagi/standalone:' + VERSIONS[0]: image_id(VERSIONS[0]),
                              'docker.io/library/synthetic-postgres:1': daemon.images['postgres'],
                              'docker.io/library/synthetic-clickhouse:1': daemon.images['clickhouse']}
    if fault in ('backup_failed','preseal_terminal_before_state'):
        daemon.mode = 'partial'
    if fault == 'backup_uncertain':
        daemon.mode = 'timeout'
    if operation == 'enroll':
        binding = InstallationBinding('1'*32, store.selection_hash, '2'*64, root, root/'.env', root/'.env',
            'standalone', False, 'test-backup', (root/'docker-compose.yml',), (), 'test-backup', 'daemon-1', os.geteuid())
        registration = Registration(1, binding, LauncherConfig(Path(sys.executable), Path.home(), os.defpath, 'fixture'), now())
        store.register(registration)
    verifier = Verifier(settings, host_only=True, which=lambda _: os.environ['FUTURE_AGI_UPDATE_TEST_COSIGN'],
                        environ=dict(os.environ, COSIGN_PASSWORD=''))
    if fault == 'expired_discovery_budget':
        daemon.after_archive = lambda: setattr(verifier, 'deadline', time.monotonic() - 1)
    discovery = ReleaseDiscovery(HttpCache(store.path/'cache', transport=FileTransport(root, artifacts, fault),
                                           clock=lambda: now().timestamp()), verifier)
    inst = RecordedInstallation(root, daemon, artifacts)
    if operation == 'enroll':
        baseline = BaselineStore(store, verifier=verifier, clock=now)
        with baseline.locked():
            current = discovery.refresh(VERSIONS[0]).candidate
            observation = FixedObserver(registration).observe(inst, current_release=current,
                execution_deployment=current.manifest['deployment'], boundary='enrollment')
            binding = backup.capture_binding(inst, installation_fingerprint='2'*64, docker_context='test-backup',
                daemon_id='daemon-1', project='test-backup', profiles=(), verified_images=daemon.images,
                deployment_hashes=current.manifest['deployment']['files'])
            config = BackupConfiguration(binding, root/'backups', backup.BackupPolicy(reserve_bytes=1))
            baseline.enroll(current, observation, backup_state=config.raw)
        store.change_control('enable', now())
        result = {'enrolled': True}
    else:
        registration = store.read_registration()
        def capture_successor(reader, **kwargs):
            # Pure-read production adapters must remain separate from mutation
            # supervision. Route that adapter to the recorded daemon, not a
            # real Docker subprocess in this component fixture.
            from deploy.update.docker_host import ReadOnlyRunner
            assert isinstance(reader.runner, ReadOnlyRunner)
            class RecordedReadOnlyRunner(ReadOnlyRunner):
                def __call__(self, *args, **kw):
                    return daemon(*args, **kw)
            reader.runner = RecordedReadOnlyRunner(30)
            return backup.capture_binding(reader, **kwargs)
        def lifecycle(*args):
            life = AutomaticLifecycle(*args, observer=FixedObserver(registration), render_config=render,
                capture=capture_successor,
                size_observer_factory=lambda *unused: FixedObserver(registration), executor_digest=lambda root: '7' * 64)
            resume = life.resume
            def diagnosed_resume(*args, **kwargs):
                try: return resume(*args, **kwargs)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    raise
            life.resume = diagnosed_resume
            facts = life.facts
            def hooked_facts(inst, boundary):
                if hooks.get('before_facts'): hooks['before_facts'](life,boundary)
                value = facts(inst,boundary)
                if hooks.get('facts'): value = hooks['facts'](life,boundary,value)
                return value
            life.facts = hooked_facts
            return life
        class Readiness:
            def wait_standalone(self, previous_ids=()):
                if fault in ('readiness_failed', 'restore_decided', 'restore_after_cas'):
                    raise NotReady('fixed fixture readiness failure')
                assert all(c['State']['Running'] for c in daemon.containers.values())
        def runtime(life, ledger, backend):
            from deploy.update.automatic_runtime import AutomaticRuntime
            class Observer:
                def free_bytes(self): return 10**15
                def progress(self, roster):
                    if hooks.get('readiness'): hooks['readiness'](life, ledger, daemon)
                    if fault in ('readiness_failed', 'restore_decided', 'restore_after_cas'): raise NotReady('fixed fixture readiness failure')
                    return 'ready', '', ''
            timer = [time.monotonic()]
            def advance(seconds):
                timer[0] = advance_fixture_clock(timer[0], seconds)
            return AutomaticRuntime(inst, ledger, backend.b, observer=Observer(),
                clock=lambda: timer[0], sleep=advance)
        def engine(*args):
            instance = guarded_updater(*args, readiness=Readiness())
            instance._automatic_runtime = runtime
            original_backup = instance._backup
            def after_backup(*args, **kwargs):
                result = original_backup(*args, **kwargs)
                if hooks.get('backup'): hooks['backup'](instance)
                return result
            instance._backup = after_backup
            apply = instance.apply
            def diagnosed(*a, **kw):
                try:
                    return apply(*a, **kw)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    raise
            instance.apply = diagnosed
            if hooks.get('engine'):
                hooks['engine'](instance)
            return instance
        from deploy.update.schedule import Clock
        from deploy.update.public_status import PublicStatusPublisher
        runner = HostRunner(store, settings=settings, publisher=PublicStatusPublisher(store, root/'public'), clock=Clock(now, lambda: time.monotonic() + CLOCK_SHIFT), discovery_factory=lambda *args: discovery,
            installation_factory=lambda *args, **kwargs: inst, automatic_factory=lifecycle,
            recovery_factory=lambda store, settings, registration, supervisor, clock: lifecycle(
                store, type('RetainedTrust', (), {'verifier': verifier})(), registration, now),
            space_available=lambda _: None if fault == 'capacity_unknown' else 10**12)
        refusal = runner._refusal
        def diagnosed_refusal(exc):
            import traceback
            traceback.print_exception(type(exc), exc, exc.__traceback__)
            return refusal(exc)
        runner._refusal = diagnosed_refusal
        if fault == 'slow_resolution':
            # Resolving the registered installation is several separately bounded
            # probes. Model one that outlasts the verifier window, not the budget.
            resolve = runner.installation_factory
            def slow_resolve(*args, **kwargs):
                if not (root/'slow-resolution.fixture').exists():
                    real, shift = time.monotonic, settings.automation_policy.probe_timeout_seconds + 1
                    time.monotonic = lambda: real() + shift
                    (root/'slow-resolution.fixture').write_text(str(shift))
                return resolve(*args, **kwargs)
            runner.installation_factory = slow_resolve
        if hooks.get('runner'): hooks['runner'](runner)
        original_write = store._write
        original_sync = runner.engine_state.sync_finished
        def write(directory, name, value):
            if fault == 'terminal_before_head' and name == 'state.json' and getattr(value, 'baseline_head', None) != store.read_state().baseline_head:
                persist_data(root, daemon)
                os._exit(73)
            original_write(directory, name, value)
            if (fault == 'restore_after_cas' and name == 'state.json'
                    and getattr(value, 'failed_target_latch', None) is not None
                    and getattr(value, 'active_attempt_id', None) is None):
                persist_data(root, daemon)
                os._exit(78)
            if fault == 'preseal_terminal_before_state' and getattr(value,'outcome',None) == 'failed_available':
                persist_data(root,daemon)
                os._exit(76)
            if fault == 'head_after_commit' and name == 'state.json' and getattr(value, 'baseline_head', None) and store.read_state().active_attempt_id is None and getattr(value, 'last_window_occurrence', None):
                persist_data(root, daemon)
                os._exit(74)
        def sync(record, path):
            original_sync(record, path)
            if fault == 'receipt_before_head':
                persist_data(root, daemon)
                os._exit(72)
        store._write = write
        runner.engine_state.sync_finished = sync
        from deploy.update.recovery import RecoveryLedger
        decide = RecoveryLedger.decide_success
        def decide_success(ledger, value):
            decide(ledger, value)
            if fault == 'decision_without_receipt':
                persist_data(root, daemon)
                os._exit(71)
        restore_decision = RecoveryLedger.decide_restore
        def decide_restore(ledger, *args, **kwargs):
            restore_decision(ledger, *args, **kwargs)
            if fault == 'restore_decided':
                persist_data(root, daemon)
                os._exit(77)
        with mock.patch.object(RecoveryLedger, 'decide_success', decide_success), \
             mock.patch.object(RecoveryLedger, 'decide_restore', decide_restore), \
             mock.patch('deploy.update.runner.ProcessSupervisor', side_effect=lambda *args, **kwargs: daemon), \
             mock.patch('deploy.update.runner.guarded_updater', side_effect=engine), \
             mock.patch('deploy.update.apply.Updater._automatic_runtime', side_effect=runtime), \
             mock.patch('deploy.update.apply._now', side_effect=lambda: now().strftime('%Y-%m-%dT%H:%M:%SZ')):
            result = runner.tick()
        result = {'disposition': result.disposition, 'reasons': [r.value for r in result.reason_codes],
                  'code': result.code, 'forward': result.forward_result}
    persist_data(root, daemon)
    (root/'worker-result.json').write_text(json.dumps(result))
    if operation != 'enroll':
        from types import SimpleNamespace
        return SimpleNamespace(root=root, store=store, daemon=daemon, inst=inst, runner=runner,
                               result=result, publisher=runner.publisher)

    # Referenced by fault callbacks only after this function's declarations are bound.


def persist_data(root, daemon):
    data = {n: getattr(daemon, n) for n in ('containers', 'images', 'local_images')}
    data['trees'] = {k:v.hex() for k,v in daemon.trees.items()}
    (root/'recorded-daemon.json').write_text(json.dumps(data))
    with (root/'recorded-events.jsonl').open('a') as out:
        out.write(json.dumps({'events': daemon.events, 'commands': daemon.calls})+'\n')


class VirtualClockPrecisionTests(unittest.TestCase):
    def test_positive_subquantum_sleeps_always_advance(self):
        import math
        for exponent in range(-10, 30):
            current = math.ldexp(1.0, exponent)
            delay = math.ulp(current)/4
            with self.subTest(exponent=exponent):
                self.assertEqual(current+delay, current)
                self.assertGreater(advance_fixture_clock(current, delay), current)
        for delay in (0, -1, float('nan'), float('inf')):
            with self.subTest(delay=delay), self.assertRaises(AssertionError):
                advance_fixture_clock(1.0, delay)

    def test_fractional_stability_boundary_passes_only_after_the_full_period(self):
        from deploy.update.readiness import AutomaticReadiness, RuntimeSample
        from deploy.update.contracts import AutomationPolicy
        for start in (8.36605154099999, 8.07897945799999, 11.06133837499999):
            timer, sleeps = [start], []
            def sleep(seconds):
                self.assertLess(len(sleeps), 20, 'virtual readiness clock stopped progressing')
                sleeps.append(seconds)
                timer[0] = advance_fixture_clock(timer[0], seconds)
            AutomaticReadiness(clock=lambda: timer[0], sleep=sleep).wait(
                lambda: RuntimeSample('ready', '', '', 10**12, {'container': (True, True, 0, 'started')}),
                policy=AutomationPolicy(stability_period_seconds=60), estimate=None,
                deadline=start+100, previous=True, expected_ids={'container'})
            self.assertGreaterEqual(timer[0]-start, 60)
            self.assertLess(timer[0]-start, 61)


class BookkeepingDockerComponentTests(unittest.TestCase):
    """Fake Docker/restore callback tests, NOT shipping-engine/product qualification.

    The public baseline APIs, local-key signature verification, exact row
    selection, native envcommit/inverse, receipts and atomic state are real.
    Storage copying and container startup here are explicitly synthetic.
    """
    from deploy.tests.test_update_baseline import BaselineCoreTests as _Core
    setUpClass = classmethod(_Core.setUpClass.__func__)
    setUp = _Core.setUp
    observation = _Core.observation
    prepare = _Core.prepare
    receipt = _Core.receipt
    render_config = staticmethod(_Core.render_config)

    class Docker:
        def __init__(self, source):
            self.running = source
            self.rows = {'source-witness': 'retained'}
            self.calls = []
        def snapshot(self):
            self.calls.append(('cold_snapshot', self.running))
            return self.running, dict(self.rows)
        def start(self, target):
            self.running = target
            self.rows['candidate-only'] = target
            self.calls.append(('start', target))
        def restore(self, snapshot):
            self.running, self.rows = snapshot[0], dict(snapshot[1])
            self.calls.append(('restore', self.running))

    def test_fake_Docker_A_B_C_commits_each_actual_target_migration_digest(self):
        docker = self.Docker(self.a.version)
        before_registration = (self.store.path / 'registration.json').read_bytes()
        head, source = self.head, self.obs
        for target in (self.b_release, self.c_release):
            docker.snapshot()
            intent, post = self.prepare(target, source, head)
            docker.start(target.version)
            receipt = self.receipt(intent, post)
            next_head = self.baseline.commit(intent, receipt_name=receipt, observation=post)
            self.assertNotEqual(next_head.structure_sha256, head.structure_sha256)
            self.assertEqual(next_head.structure_sha256, post.structure_sha256)
            self.assertEqual(post.volumes, source.volumes)
            self.assertEqual(docker.rows['source-witness'], 'retained')
            head, source = next_head, post
        self.assertEqual(head.generation, 2)
        self.assertEqual(docker.running, self.c_release.version)
        self.assertEqual((self.store.path / 'registration.json').read_bytes(), before_registration)
        self.assertIsNone(self.store.read_state().active_attempt_id)

    def test_fake_Docker_forward_failure_restores_A_latches_B_and_rejects_B_C_row(self):
        from deploy.update import envfile
        from deploy.update.compatibility import CompatibilityError
        docker = self.Docker(self.a.version)
        snapshot = docker.snapshot()
        intent, post = self.prepare()
        changes = {p.name: p.value for p in intent.pins}
        inverse = envfile.recovery_record(self.root / '.env', changes)
        envfile.write_changes(self.root / '.env', changes, recovery=inverse)
        docker.start(self.b_release.version)
        self.assertIn('candidate-only', docker.rows)
        restore = self.baseline.prepare_restore(intent.attempt, expected_head=self.head.identity, run_id=intent.run_id,
            source_observation=self.obs, backup_raw=self.last_backup, backup_seal=self.last_seal,
            env_inverse_raw=canonical_json(inverse))
        docker.restore(snapshot)
        envfile.restore_changes(self.root / '.env', inverse)
        restored = self.observation(self.a, self.env_bytes)
        receipt = self.baseline.engine_state.write_receipt({'kind': 'restore', 'status': 'restored',
            'run_id': intent.run_id, 'from_version': self.a.version, 'target_version': self.b_release.version,
            'manifest_hash': self.b_release.manifest_hash, 'project': intent.attempt.binding.compose_project,
            'mode': 'standalone', 'production': False, 'finished_at': self.time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'target_image_ids': {s: v[0].image_id for s, v in restored.facts.product_images.items()}})
        self.baseline.commit_restore(restore, receipt_name=receipt.name, observation=restored)
        self.assertEqual(self.baseline.current(), self.head)
        self.assertEqual(docker.running, self.a.version)
        self.assertEqual(docker.rows, {'source-witness': 'retained'})
        self.assertEqual(self.store.read_state().failed_target_latch, intent.attempt.target)
        with self.assertRaises(CompatibilityError):
            self.baseline.observe(restored, self.c_release)
        self.assertEqual([call[0] for call in docker.calls], ['cold_snapshot', 'start', 'restore'])


class AutomaticIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='automatic-signatures-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.artifacts = Path(cls.temp.name).resolve()
        cls.cosign = os.environ.get('FUTURE_AGI_UPDATE_TEST_COSIGN') or shutil.which('cosign')
        if not cls.cosign:
            raise RuntimeError('real local-key integration requires FUTURE_AGI_UPDATE_TEST_COSIGN')
        signenv = dict(os.environ, COSIGN_PASSWORD='')
        subprocess.run([cls.cosign, 'generate-key-pair'], cwd=cls.artifacts, env=signenv,
                       capture_output=True, check=True, timeout=30)
        target, old, proof, _ = restore_release()
        for index, measurement in enumerate(proof['predecessors'][0]['migration_measurements']):
            measurement['tables'][0]['table'] = 'public.synthetic_table_%d' % index
        previous = json.loads(old)
        physical = {'files': {n: sha(raw) for n, raw in layout_files().items()}}
        previous['deployment'] = copy.deepcopy(physical)
        cls.manifests = {}
        for index, version in enumerate(VERSIONS):
            manifest = copy.deepcopy(previous if index == 0 else target)
            manifest['version'], manifest['helm']['version'] = version, version[1:]
            manifest['minimum_version'] = 'v1.0.0'
            for name, component in manifest['components'].items():
                component['image'] = component['image'].rsplit(':', 1)[0] + ':' + version + ('-gpu' if name == 'serving_gpu' else '')
                component['digest'] = 'sha256:' + sha((version+name).encode())
            rawproof = None
            if index:
                manifest['compatibility']['contract_version'] = 2
                manifest['deployment'] = copy.deepcopy(physical)
                manifest['deployment']['files']['.env.example'] = sha(('published-only-' + version).encode())
                row = manifest['compatibility']['predecessors'][0]
                row.update(version=previous['version'], manifest_hash=manifest_hash(previous),
                    raw_manifest_sha256=sha(canonical_json(previous)), source_sha=previous['source_sha'],
                    execution_deployment_hash=manifest_hash(physical))
                p = copy.deepcopy(proof)
                p.update(schema_version=2, tag=version, components=copy.deepcopy(manifest['components']),
                         deployment=copy.deepcopy(manifest['deployment']))
                p['predecessors'][0].update(predecessor=copy.deepcopy(row), predecessor_components=copy.deepcopy(previous['components']),
                    predecessor_deployment=copy.deepcopy(previous['deployment']), execution_deployment=copy.deepcopy(physical))
                p['predecessors'][0]['migration_evidence'].update(
                    source_inventory_sha256=('1' if index == 1 else '2') * 64,
                    source_applied_set_sha256=('3' if index == 1 else '4') * 64,
                    target_inventory_sha256=('2' if index == 1 else 'a') * 64,
                    target_applied_set_sha256=('4' if index == 1 else 'b') * 64)
                rawproof = bind_proof(manifest, p)
            directory = cls.artifacts/version
            directory.mkdir(mode=0o700)
            path = directory/MANIFEST_ASSET
            path.write_bytes(canonical_json(manifest))
            if rawproof:
                (directory/'future-agi-update-compatibility.json').write_bytes(rawproof)
            subprocess.run([cls.cosign, 'sign-blob', '--yes', '--key', str(cls.artifacts/'cosign.key'),
                '--use-signing-config=false', '--tlog-upload=false', '--bundle', str(directory/BUNDLE_ASSET), str(path)],
                cwd=cls.artifacts, env=signenv, check=True, capture_output=True, timeout=30)
            cls.manifests[version] = manifest
            previous = manifest

    def setUp(self, mode='apply', window='always'):
        temp = tempfile.TemporaryDirectory(prefix='automatic-engine-state-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name, raw in layout_files().items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        # This example has no keys; release required_env remains enforced separately.
        (self.root/'.env').write_text('FUTURE_AGI_VERSION=v1.51.0\nSECRET=unchanged\n')
        (self.root/'update.conf').write_text('\n'.join([
            'FUTURE_AGI_UPDATE_AUTOMATION_MODE=' + mode, 'FUTURE_AGI_UPDATE_ENABLED=false',
            'FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW=' + window, 'FUTURE_AGI_UPDATE_PROBE_TIMEOUT_SECONDS=90',
            'FUTURE_AGI_UPDATE_COSIGN_OFFLINE=true',
            'FUTURE_AGI_UPDATE_QUALIFICATION_POLICY=standalone-cold-restore-v1',
            'FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=' + str(self.artifacts/'cosign.pub')])+'\n')
        backup.create_destination(self.root/'backups')
        (self.root/'publisher-visible').write_text(VERSIONS[0])
        self.worker('enroll')
        self.store = RunnerStateStore(self.root, self.root/'.env')
        self.initial = self.store.read_state().baseline_head
        self.registration = (self.store.path/'registration.json').read_bytes()
        (self.root/'publisher-visible').write_text(VERSIONS[1])

    def worker(self, operation='tick', fault='', expected=0):
        script = ('import sys, faulthandler; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
                  'faulthandler.dump_traceback_later(30, repeat=True); '
                  'from deploy.tests.test_update_automatic_integration import process_tick; '
                  'process_tick(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],sys.argv[5])')
        try:
            done = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(ROOT), str(self.root),
                str(self.artifacts), operation, fault], capture_output=True, text=True, timeout=110)
        except subprocess.TimeoutExpired as exc:
            # Preserve the existing deadline and every assertion. Retain stacks
            # on slow hosts so a timeout is diagnosable rather than waived.
            diagnostic = exc.stderr or b''
            if isinstance(diagnostic, bytes):
                diagnostic = diagnostic.decode('utf-8', errors='replace')
            raise RuntimeError('component_worker_timeout\n' + diagnostic) from exc
        self.assertEqual(done.returncode, expected, done.stderr + done.stdout)
        if expected != 0:
            return None
        result = json.loads((self.root/'worker-result.json').read_text())
        if done.stderr:
            result['fixture_diagnostic'] = done.stderr
        return result

    def events(self):
        return [e for line in (self.root/'recorded-events.jsonl').read_text().splitlines() for e in json.loads(line)['events']]

    def test_one_enrollment_successive_processes_A_B_C(self):
        control = self.store.read_control()
        initial_binding = json.loads(BackupConfiguration.load(BaselineStore(self.store).backup_state()).binding.raw)
        for version in VERSIONS[1:]:
            (self.root/'publisher-visible').write_text(version)
            result = self.worker(fault='expired_discovery_budget' if version == VERSIONS[1] else '')
            self.assertEqual(result['disposition'], 'applied', result)
            baseline = BaselineStore(self.store)
            self.assertEqual(baseline.current().release.version, version)
            config = BackupConfiguration.load(baseline.backup_state())
            binding = json.loads(config.binding.raw)
            self.assertEqual(binding['images']['app'], image_id(version))
            self.assertEqual(binding['files']['.env'], sha(backup._files(self.root)['.env']))
            self.assertEqual((self.root / '.env').stat().st_nlink, 2)
            for key in ('volumes', 'volume_root_ids'):
                self.assertEqual(binding[key], initial_binding[key])
            self.assertIsNone(self.store.read_state().active_attempt_id)
            self.assertIsNone(self.store.read_state().failed_target_latch)
            self.assertEqual(self.store.read_control(), control)
            # Normal business writes do not alter structural identity.
            (self.root/'business-rows.fixture').write_text(version)
            time.sleep(0.01)
        self.assertEqual(self.registration, (self.store.path/'registration.json').read_bytes())
        self.assertFalse((self.store.path/'facts.json').exists())
        self.assertEqual([e for e in self.events() if e[0] == 'up'], [['up', v] for v in VERSIONS[1:]])
        inventories = [json.loads(p.read_bytes()) for p in (self.root/'backups').glob('*/inventory.json')]
        self.assertEqual(len(inventories), 2)
        self.assertEqual({i['expectation']['source_version']: i['binding']['images']['app'] for i in inventories},
                         {v: image_id(v) for v in VERSIONS[:2]})
        self.assertEqual(inventories[0]['binding']['volumes'], inventories[1]['binding']['volumes'])
        self.assertNotEqual(inventories[0]['archives']['app-data']['sha256'], inventories[1]['archives']['app-data']['sha256'])
        self.assertEqual(read_env(self.root/'.env')['SECRET'], 'unchanged')

    def test_refusals_do_not_rollout_or_advance(self):
        result = self.worker(fault='capacity_unknown')
        self.assertIn('disk_space_insufficient', result['reasons'])
        self.assertEqual(self.store.read_state().baseline_head, self.initial)
        self.assertFalse(self.events())

    def test_authenticated_wrong_predecessor_is_not_permission(self):
        # C genuinely names B. An A host cannot skip B just because C is newest.
        (self.root/'publisher-visible').write_text(VERSIONS[2])
        result = self.worker()
        self.assertEqual(result['disposition'], 'blocked', result)
        self.assertIn('transition_not_rehearsed', result['reasons'])
        self.assertFalse(self.events())
        self.assertEqual(self.store.read_state().baseline_head, self.initial)

    def test_missing_proof_prevents_prefetch_and_backup(self):
        result = self.worker(fault='missing_proof')
        self.assertEqual(result['disposition'], 'blocked', result)
        self.assertFalse(self.events())
        self.assertIsNone(self.store.read_state().active_attempt_id)

    def test_bad_signature_prevents_helpers(self):
        result = self.worker(fault='bad_signature')
        self.assertEqual(result['disposition'], 'blocked', result)
        self.assertIn('signature_rejected', result['reasons'])
        self.assertFalse(self.events())

    def test_local_physical_drift_is_not_target_layout_adoption(self):
        with (self.root/'docker-compose.yml').open('a') as out:
            out.write('operator change')
        result = self.worker()
        self.assertEqual(result['disposition'], 'blocked', result)
        self.assertFalse(self.events())
        self.assertEqual(self.store.read_state().baseline_head, self.initial)

    def test_pending_migrations_prevents_prefetch(self):
        result = self.worker(fault='pending_migrations')
        self.assertIn('migration_inventory_mismatch', result['reasons'])
        self.assertFalse(self.events())

    def test_env_change_after_prefetch_prevents_backup(self):
        result = self.worker(fault='config_after_prefetch')
        self.assertEqual(result['disposition'], 'blocked', result)
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))
        self.assertEqual(self.store.read_state().baseline_head, self.initial)

    def test_ambiguous_backup_stays_active_never_reruns(self):
        result = self.worker(fault='backup_uncertain')
        self.assertEqual(result['disposition'], 'recovery_required', result)
        attempt = self.store.read_state().active_attempt_id
        self.assertIsNotNone(attempt)
        before = self.events()
        result = self.worker()
        self.assertEqual(result['disposition'], 'recovery_required', result)
        self.assertEqual(self.store.read_state().active_attempt_id, attempt)
        self.assertEqual(self.events(), before)
        self.assertEqual(self.store.read_state().baseline_head, self.initial)

    def test_restore_terminal_CAS_recovers_before_disabled_controls(self):
        from deploy.update.recovery import RecoveryLedger
        self.worker(fault='restore_after_cas', expected=78)
        state = self.store.read_state()
        self.assertIsNone(state.active_attempt_id)
        self.assertEqual(state.baseline_head, self.initial)
        directory, = (self.root/'backups').glob('*/recovery.json')
        self.assertEqual(RecoveryLedger.open(directory.parent).phase, 'restore_verified')
        self.store.change_control('disable', now())
        before = self.events()
        result = self.worker()
        self.assertEqual(result['disposition'], 'restored', result)
        self.assertEqual(RecoveryLedger.open(directory.parent).phase, 'restored')
        self.assertEqual(self.events(), before)  # no second clear/extract/start
        self.assertEqual(self.store.read_state().failed_target_latch, state.failed_target_latch)
        public = json.loads((self.root/'public'/self.store.read_registration().binding.slot_id/'status.json').read_bytes())
        self.assertEqual(public['last_run']['restore_result'], 'verified')

    def test_restore_replay_reverifies_after_slow_installation_resolution(self):
        from deploy.update.recovery import RecoveryLedger
        self.worker(fault='restore_after_cas', expected=78)
        directory, = (self.root/'backups').glob('*/recovery.json')
        self.assertEqual(RecoveryLedger.open(directory.parent).phase, 'restore_verified')
        self.store.change_control('disable', now())
        result = self.worker(fault='slow_resolution')
        self.assertTrue((self.root/'slow-resolution.fixture').exists())
        self.assertEqual(result['disposition'], 'restored', result)
        self.assertEqual(RecoveryLedger.open(directory.parent).phase, 'restored')
        self.assertIsNone(self.store.read_state().active_attempt_id)

    def test_failed_B_latches_then_exact_A_C_row_advances_without_reenrollment(self):
        result = self.worker(fault='readiness_failed')
        self.assertEqual(result['disposition'], 'restored', result)
        control = self.store.read_control()
        latch = self.store.read_state().failed_target_latch
        self.assertEqual(latch.version, VERSIONS[1])
        before = self.events()
        self.assertEqual(self.worker()['disposition'], 'blocked')
        self.assertEqual(self.events(), before)
        # A separate synthetic publisher branch carries an exact authenticated
        # A->C row. The normal fixture still proves that B->C alone denies A.
        artifacts = self.root/'branched-publisher'
        shutil.copytree(self.artifacts, artifacts)
        self.artifacts = artifacts
        path = artifacts/VERSIONS[2]/MANIFEST_ASSET
        manifest = json.loads(path.read_bytes())
        proof_path = path.parent/'future-agi-update-compatibility.json'
        proof = json.loads(proof_path.read_bytes())
        old_raw = (artifacts/VERSIONS[0]/MANIFEST_ASSET).read_bytes()
        old = json.loads(old_raw)
        row = copy.deepcopy(manifest['compatibility']['predecessors'][0])
        row.update(version=old['version'], manifest_hash=manifest_hash(old), raw_manifest_sha256=sha(old_raw),
                   source_sha=old['source_sha'])
        manifest['compatibility']['predecessors'].append(row)
        evidence = copy.deepcopy(proof['predecessors'][0])
        evidence.update(predecessor=copy.deepcopy(row), predecessor_components=copy.deepcopy(old['components']),
                        predecessor_deployment=copy.deepcopy(old['deployment']))
        evidence['migration_evidence'].update(source_inventory_sha256='1'*64, source_applied_set_sha256='3'*64)
        proof['predecessors'].append(evidence)
        proof_path.write_bytes(bind_proof(manifest, proof))
        path.write_bytes(canonical_json(manifest))
        subprocess.run([self.cosign, 'sign-blob', '--yes', '--key', str(artifacts/'cosign.key'),
            '--use-signing-config=false', '--tlog-upload=false', '--bundle', str(path.parent/BUNDLE_ASSET), str(path)],
            env=dict(os.environ, COSIGN_PASSWORD=''), capture_output=True, check=True, timeout=30)
        (self.root/'publisher-visible').write_text(VERSIONS[2])
        result = self.worker()
        self.assertEqual(result['disposition'], 'applied', result)
        self.assertEqual(BaselineStore(self.store).current().release.version, VERSIONS[2])
        self.assertEqual(self.store.read_state().failed_target_latch, latch)
        self.assertEqual(self.store.read_control(), control)
        self.assertEqual(self.registration, (self.store.path/'registration.json').read_bytes())

    def test_readiness_failure_no_successor(self):
        result = self.worker(fault='readiness_failed')
        self.assertEqual(result['disposition'], 'restored', result)
        self.assertEqual(result['forward'], 'failed')
        self.assertIn('restored_after_update_failure', result['reasons'])
        self.assertEqual(self.store.read_state().baseline_head, self.initial)
        self.assertEqual(self.store.read_state().failed_target_latch.version, VERSIONS[1])
        self.assertIsNone(self.store.read_state().active_attempt_id)
        self.assertEqual(read_env(self.root/'.env')['FUTURE_AGI_VERSION'], VERSIONS[0])
        import io, tarfile
        data = json.loads((self.root/'recorded-daemon.json').read_bytes())
        for raw in data['trees'].values():
            with tarfile.open(fileobj=io.BytesIO(bytes.fromhex(raw))) as archive:
                records = {m.name.lstrip('./'):archive.extractfile(m).read() for m in archive if m.isfile()}
                self.assertEqual(records['records'], b'private persistent record')
                self.assertNotIn('candidate-only', records)
        self.assertEqual([e[1] for e in self.events() if e[0] == 'start'], [VERSIONS[1],VERSIONS[0]])

    def test_success_metadata_crashes_reconcile_without_mutation(self):
        for fault, code in (('decision_without_receipt', 71), ('receipt_before_head', 72),
                            ('terminal_before_head', 73), ('head_after_commit', 74)):
            with self.subTest(fault=fault):
                if fault != 'decision_without_receipt':
                    self.setUp()
                control = self.store.read_control()
                self.worker(fault=fault, expected=code)
                before = self.events()
                result = self.worker(fault='unavailable_after_success' if fault != 'head_after_commit' else '')
                self.assertIn(result['disposition'], ('applied', 'skipped'), result)
                self.assertEqual(self.events(), before)
                self.assertEqual(BaselineStore(self.store).current().release.version, VERSIONS[1])
                self.assertIsNone(self.store.read_state().active_attempt_id)
                public=json.loads((self.root/'public'/self.store.read_registration().binding.slot_id/'status.json').read_bytes())
                self.assertEqual(public['versions']['running_release'],VERSIONS[1])
                self.assertEqual(public['versions']['selected_release'],VERSIONS[1])
                self.assertEqual(public['last_run']['outcome'],'succeeded')
                self.assertEqual(public['last_run']['effective_version'],VERSIONS[1])
                self.assertEqual(public['operation']['phase'],'succeeded')
                self.assertEqual(public['safety']['readiness'],'passed')
                self.assertEqual(public['safety']['backup_state'],'sealed')
                self.assertEqual(len(list((self.root/'backups').glob('*/inventory.json'))), 1)
                binding_b = json.loads(BackupConfiguration.load(BaselineStore(self.store).backup_state()).binding.raw)
                self.assertEqual(binding_b['images']['app'], image_id(VERSIONS[1]))
                self.assertEqual(binding_b['files']['.env'], sha(backup._files(self.root)['.env']))
                self.assertEqual(self.store.read_control(), control)
                self.assertIsNone(self.store.read_state().failed_target_latch)
                (self.root/'publisher-visible').write_text(VERSIONS[2])
                self.assertEqual(self.worker()['disposition'], 'applied')
                self.assertEqual(BaselineStore(self.store).current().release.version, VERSIONS[2])
                inventories = [json.loads(p.read_bytes()) for p in (self.root/'backups').glob('*/inventory.json')]
                self.assertEqual(sorted(i['expectation']['source_version'] for i in inventories), list(VERSIONS[:2]))
                self.assertTrue(all(i['binding']['volumes'] == binding_b['volumes'] for i in inventories))
                self.assertEqual([e for e in self.events() if e[0] == 'up'], [['up', v] for v in VERSIONS[1:]])
                self.assertEqual([e for e in self.events() if e[0] == 'pull'], [['pull', v] for v in VERSIONS[1:]])
                self.assertEqual(self.registration, (self.store.path/'registration.json').read_bytes())
                self.assertEqual(self.store.read_control(), control)
                self.assertIsNone(self.store.read_state().active_attempt_id)
                self.assertIsNone(self.store.read_state().failed_target_latch)

    def _tick_with_unavailable_registered_daemon(self):
        """Exercise the production factory; a live probe would fail, never bypass it."""
        from deploy.update.contracts import ReasonCode
        from deploy.update.orchestrator import GuardRefused, registered_installation
        prior = FIXTURE_WALL, FIXTURE_MONOTONIC, CLOCK_SHIFT
        self.addCleanup(lambda: globals().update(zip(
            ('FIXTURE_WALL', 'FIXTURE_MONOTONIC', 'CLOCK_SHIFT'), prior)))
        daemon_calls = []
        def configure(runner):
            inst = runner.installation_factory(None, None)
            def unavailable(*args, **kwargs):
                daemon_calls.append(args)
                raise GuardRefused(ReasonCode.PROCESS_FAILED)
            inst.docker_run = unavailable
            runner.installation_factory = registered_installation
            installation.return_value = inst
        with mock.patch('shutil.which', return_value='/usr/bin/docker'), \
             mock.patch('deploy.update.orchestrator.resolve_compose_command', return_value=['docker', 'compose']), \
             mock.patch('deploy.update.orchestrator.Installation') as installation:
            context = process_tick(self.root, self.artifacts, fault='unavailable_after_success',
                                   hooks={'runner': configure})
        return context, daemon_calls, installation

    def test_decided_success_default_factory_recovers_without_live_Docker(self):
        # Real shipping-engine decisions/AcceptedSuccess and process deaths, not
        # a success callback standing in for durable recovery. All four cuts
        # must complete even when controls disable forward work and Docker is down.
        from deploy.update.recovery import RecoveryLedger
        for fault, code in (('decision_without_receipt', 71), ('receipt_before_head', 72),
                            ('terminal_before_head', 73), ('head_after_commit', 74)):
            with self.subTest(fault=fault):
                if fault != 'decision_without_receipt':
                    self.setUp()
                self.worker(fault=fault, expected=code)
                attempt_id = self.store.read_state().active_attempt_id or BaselineStore(self.store).current().attempt_id
                self.assertTrue((self.store.path/'transitions'/(attempt_id+'-accepted.json')).is_file())
                self.store.change_control('disable', now())
                before = self.events()
                context, daemon_calls, installation = self._tick_with_unavailable_registered_daemon()
                self.assertEqual(context.result['disposition'], 'applied', context.result)
                self.assertEqual(context.result['forward'], 'succeeded')
                self.assertEqual(daemon_calls, [])
                installation.assert_not_called()
                self.assertEqual(context.daemon.calls, [])
                self.assertEqual(self.events(), before)
                self.assertEqual(BaselineStore(self.store).current().release.version, VERSIONS[1])
                self.assertIsNone(self.store.read_state().active_attempt_id)
                self.assertIsNone(self.store.read_state().failed_target_latch)
                self.assertEqual(RecoveryLedger.open(self.root/'backups'/attempt_id).phase, 'succeeded')
                self.assertEqual(len(context.runner.engine_state.history(context.inst.identity())), 1)
                public = json.loads((self.root/'public'/self.store.read_registration().binding.slot_id/'status.json').read_bytes())
                self.assertEqual(public['versions']['selected_release'], VERSIONS[1])
                self.assertEqual(public['versions']['running_release'], VERSIONS[1])
                self.assertEqual(public['last_run']['outcome'], 'succeeded')
                self.assertEqual(public['last_run']['effective_version'], VERSIONS[1])
                self.assertEqual(public['operation']['phase'], 'succeeded')
                self.assertEqual(public['safety']['readiness'], 'passed')
                self.assertEqual(public['safety']['backup_state'], 'sealed')
                self.assertEqual(self.worker()['disposition'], 'skipped')
                self.assertEqual(self.events(), before)

    def test_undecided_recovery_still_requires_live_registered_binding(self):
        self.assertEqual(self.worker(fault='backup_uncertain')['disposition'], 'recovery_required')
        attempt_id = self.store.read_state().active_attempt_id
        self.store.change_control('disable', now())
        before = self.events()
        context, daemon_calls, installation = self._tick_with_unavailable_registered_daemon()
        self.assertEqual(context.result['disposition'], 'blocked', context.result)
        self.assertEqual(context.result['reasons'], ['process_failed'])
        self.assertEqual(daemon_calls, [('info', '--format', '{{.ID}}')])
        installation.assert_called_once()
        self.assertEqual(self.store.read_state().active_attempt_id, attempt_id)
        self.assertEqual(self.store.read_state().baseline_head, self.initial)
        self.assertEqual(self.events(), before)

    def test_missing_accepted_success_fails_closed_without_live_Docker_or_restore(self):
        self.worker(fault='decision_without_receipt', expected=71)
        attempt_id = self.store.read_state().active_attempt_id
        accepted = self.store.path/'transitions'/(attempt_id+'-accepted.json')
        retained = accepted.read_bytes()
        accepted.unlink()  # Explicit loss-of-private-evidence fault in this disposable fixture.
        self.store.change_control('disable', now())
        before = self.events()
        context, daemon_calls, installation = self._tick_with_unavailable_registered_daemon()
        self.assertEqual(context.result['disposition'], 'recovery_required', context.result)
        self.assertEqual(context.result['forward'], 'succeeded')
        self.assertEqual(context.result['reasons'], ['automatic_recovery_pending'])
        self.assertEqual(daemon_calls, [])
        installation.assert_not_called()
        self.assertEqual(self.events(), before)
        self.assertEqual(self.store.read_state().active_attempt_id, attempt_id)
        self.assertEqual(self.store.read_state().baseline_head, self.initial)
        self.assertEqual(context.runner.engine_state.history(context.inst.identity()), [])
        accepted.write_bytes(retained)
        accepted.chmod(0o600)
        replay, daemon_calls, installation = self._tick_with_unavailable_registered_daemon()
        self.assertEqual(replay.result['disposition'], 'applied', replay.result)
        self.assertEqual(daemon_calls, [])
        installation.assert_not_called()
        self.assertEqual(self.events(), before)
        self.assertIsNone(self.store.read_state().active_attempt_id)

    def test_restore_decided_recovery_still_requires_live_registered_binding(self):
        from deploy.update.recovery import RecoveryLedger
        self.worker(fault='restore_decided', expected=77)
        attempt_id = self.store.read_state().active_attempt_id
        self.assertEqual(RecoveryLedger.open(self.root/'backups'/attempt_id).direction, 'restore_decided')
        self.store.change_control('disable', now())
        before = self.events()
        context, daemon_calls, installation = self._tick_with_unavailable_registered_daemon()
        self.assertEqual(context.result['disposition'], 'blocked', context.result)
        self.assertEqual(context.result['reasons'], ['process_failed'])
        self.assertEqual(daemon_calls, [('info', '--format', '{{.ID}}')])
        installation.assert_called_once()
        self.assertEqual(self.store.read_state().active_attempt_id, attempt_id)
        self.assertEqual(self.store.read_state().baseline_head, self.initial)
        self.assertEqual(self.events(), before)
        self.assertEqual(self.worker()['disposition'], 'restored')
        self.assertIsNone(self.store.read_state().active_attempt_id)
        self.assertEqual(self.store.read_state().baseline_head, self.initial)
        self.assertEqual(self.store.read_state().failed_target_latch.version, VERSIONS[1])
        self.assertEqual(read_env(self.root/'.env')['FUTURE_AGI_VERSION'], VERSIONS[0])

    def test_plain_download_has_no_backup_env_mutation_or_restart(self):
        self.setUp(mode='download')
        before = (self.root/'.env').read_bytes()
        result = self.worker()
        self.assertEqual(result['disposition'], 'downloaded', result)
        self.assertEqual((self.root/'.env').read_bytes(), before)
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))
        self.assertEqual(self.events(), [['pull', VERSIONS[1]]])

    def test_disabled_pause_and_notify_do_not_take_backup(self):
        for action in ('disable', 'pause'):
            self.store.change_control(action, now())
            result = self.worker()
            self.assertEqual(result['disposition'], 'skipped', result)
            self.assertEqual(json.loads((self.root/'recorded-events.jsonl').read_text().splitlines()[-1])['commands'], [])
            self.store.change_control('resume' if action == 'pause' else 'enable', now())
        self.setUp(mode='notify')
        self.assertEqual(self.worker()['disposition'], 'available')
        self.assertFalse(self.events())

    def test_preseal_failed_available_recovery_finishes_once_before_disabled_controls(self):
        self.worker(fault='preseal_terminal_before_state',expected=76)
        attempt_id = self.store.read_state().active_attempt_id
        self.assertIsNotNone(attempt_id)
        self.assertEqual(self.store.read_attempt(attempt_id).outcome,'failed_available')
        self.store.change_control('disable',now())
        before=self.events()
        result=self.worker()
        self.assertEqual(result['disposition'],'failed_available',result)
        state=self.store.read_state()
        self.assertIsNone(state.active_attempt_id)
        self.assertEqual(state.failed_target_latch.version,VERSIONS[1])
        self.assertEqual(state.baseline_head,self.initial)
        self.assertEqual(self.events(),before)
        self.assertEqual(self.worker()['disposition'],'skipped')
        self.assertEqual(self.events(),before)
        self.store.retry_target(state.failed_target_latch)
        self.assertIsNone(self.store.read_state().failed_target_latch)

    def test_backup_failure_latches_exact_target_without_advance(self):
        result = self.worker(fault='backup_failed')
        self.assertEqual(result['disposition'], 'failed', result)
        self.assertEqual(self.store.read_state().baseline_head, self.initial)
        self.assertIsNotNone(self.store.read_state().failed_target_latch)
        self.assertFalse([e for e in self.events() if e[0] == 'up'])

    def test_prefetch_window_closure_rechecked_before_backup(self):
        at = now() + timedelta(hours=7)
        window = (at - timedelta(minutes=1)).strftime('%H:%M') + '-' + (at + timedelta(minutes=30)).strftime('%H:%M')
        self.setUp(window=window)
        result = self.worker(fault='window_after_prefetch')
        self.assertEqual(set(result['reasons']), {'backup_exceeds_window', 'migration_exceeds_window'})
        self.assertEqual(result['disposition'], 'available')  # host-specific A6 notify-only, no attempt
        self.assertEqual(self.events(), [['pull', VERSIONS[1]]])
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))
        self.assertEqual(self.store.read_state().baseline_head, self.initial)

    def test_prefetch_pause_rechecked_before_backup(self):
        result = self.worker(fault='pause_after_prefetch')
        self.assertIn('control_invalid', result['reasons'])
        self.assertFalse(list((self.root/'backups').glob('*/recovery.json')))
        self.assertEqual(self.store.read_state().baseline_head, self.initial)
