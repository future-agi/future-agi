"""Bounded, nonqualifying milestones: cold A snapshot, then guarded real B start.

This is deliberately NOT the six-suite executor or a release-admission bypass.
No release manifest/signature/qualification is invented. The source observation's
manifest_sha256 names the labeled, local source descriptor below; the backup
expectation names the milestone intent, NOT a candidate release. Neither can be
used for automatic admission. Only run_quiesced's real BackupProof is emitted.
Runtime mutations are restricted to a new owned native Actions fixture. Local
--dry-run is daemon-free. Do not import or execute historical archive contents.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sys
import tarfile
import time
from types import SimpleNamespace
import uuid

from scripts.update_rehearsal_shipping import (
    ShippingRehearsal, ProductWitnesses, configuration_preimage, producer, wire,
)
from deploy.tests.update_e2e.real_automatic import ProductHarness

VERSION = 'v1.53.0'
PLATFORM = 'linux/amd64'
DIGEST = 'sha256:1bcaf9deccd6ff8b999a66e8ebd44657811d91419aeb5339a6d6327d1cbef7fd'
IMAGE = 'futureagi/standalone:'+VERSION+'@'+DIGEST
PREFIX = 'fagi-upd-e2e-driver-m1-'
TOTAL_SECONDS = 7800  # Leaves 20 minutes of the 150-minute job for upload/teardown.
STAGES = ('assemble_retained_a', 'install_and_boot_a', 'seed_and_enumerate',
          'observe_source', 'source_snapshot', 'verify_cold_hold')


TARGET_VERSION = 'v1.54.0'
TARGET_DIGEST = 'sha256:c2b0f344b4e9f60435c6ce503764fbc5be793c9d44776cb6ff12b7c92bdd3492'
TARGET_IMAGE = 'futureagi/standalone:'+TARGET_VERSION+'@'+TARGET_DIGEST
TARGET_PREFIX = 'fagi-upd-e2e-m2-'
TARGET_STAGES = STAGES[:4] + ('prefetch_target',) + STAGES[4:] + ('prepare_target', 'target_readiness')


def label(*, milestone='source_snapshot', **values):
    return dict(schema_version=1, producer='standalone-'+milestone.replace('_','-')+'-milestone-v1',
                milestone=milestone, qualification_evidence=False,
                eligible=False, completed_rows=0, **values)


def plan(boot_timeout=4200, through='source_snapshot'):
    if through == 'target_readiness':
        result = plan(boot_timeout)
        result.update(label(milestone=through), stages=list(TARGET_STAGES),
            target_version=TARGET_VERSION, target_image=TARGET_IMAGE, resources_prefix=TARGET_PREFIX,
            automatic_start_api='Updater.start_automatic_target_locked -> AutomaticRuntime.start_and_wait',
            guards=['deadline', 'migration_stall', 'disk_watermark', 'health', 'restart_stability'],
            success_decision='RecoveryLedger.decide_success (unsigned milestone, not enrolled admission)',
            runtime_budget_is_not_signed_preflight=True, stability_period_seconds=15,
            source_stays_stopped_until_target_start=True)
        return result
    return label(status='planned', platform=PLATFORM, source_version=VERSION,
        source_image=IMAGE, source_deployment='git archive refs/tags/v1.53.0^{commit}',
        updater='current checkout; retained Compose and configuration bytes unchanged',
        boot_ceiling_seconds=boot_timeout, total_ceiling_seconds=TOTAL_SECONDS,
        docker_context='colima-fagi-update', docker_config='private ephemeral native runner directory',
        stages=list(STAGES), backup_api='StandaloneBackup.run_quiesced',
        resources_prefix=PREFIX, source_stays_stopped_after_snapshot=True,
        witness_operations=['accounts signup/token/user-info/keys', 'collector OTLP/HTTP',
                            'bundled MinIO S3 API', 'completed product Temporal workflow'],
        allowed_gaps=['collector/telemetry files without an attributable product operation'],
        forbidden_outputs=['proof-row.json', 'evidence.json', 'policy.json'],
        qualification_suites={s:'unrun' for s in producer().SUITES})


def record(directory, name, value):
    raw = producer().canonical_json(value)
    producer().need(len(raw) <= 16*1024*1024, 'milestone evidence bound exceeded')
    path = Path(directory)/name
    path.write_bytes(raw+b'\n')
    path.chmod(0o600)


def archive_deployment(destination):
    """Extract only fixed regular tag DATA, with size/path/link/duplicate bounds."""
    from deploy.update.standalone_backup import CONFIG_FILES
    from deploy.update.manifest import hash_path
    p = producer()
    names = list(CONFIG_FILES[1:])
    commit = p.command(['git', '-C', p.ROOT, 'rev-parse', 'refs/tags/'+VERSION+'^{commit}'], timeout=30).decode().strip()
    p.need(re.fullmatch('[a-f0-9]{40}', commit) is not None, 'source tag commit unavailable')
    raw = p.command(['git', '-C', p.ROOT, 'archive', '--format=tar', commit, '--', *names], timeout=60)
    destination.mkdir(mode=0o700)
    total, seen = 0, set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for member in archive:
            path = Path(member.name)
            p.need(not path.is_absolute() and '..' not in path.parts and
                   member.name not in seen and len(seen) < 1024 and
                   (member.isfile() or member.isdir()) and member.size >= 0,
                   'unsafe retained deployment archive')
            p.need(member.isdir() or member.name in names, 'unexpected retained deployment file')
            total += member.size
            p.need(total <= 8*1024*1024, 'retained deployment archive exceeds bound')
            seen.add(member.name)
            target = destination/path
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open('xb') as out:
                    shutil.copyfileobj(source, out, 65536)
                # Native ClickHouse uid 101 must read its read-only XML bind.
                target.chmod(0o644)
    p.need(all((destination/name).is_file() for name in names), 'incomplete retained deployment')
    return commit, {'files':{name:hash_path(destination,name) for name in names}}


class SnapshotMilestone(ShippingRehearsal):
    """Reuse fixed product operations, diagnostics and shipping backup primitives.

    No factory flag is changed, no full suite is marked passed, and no Updater
    transaction is claimed. ProductHarness supplies updater assembly and HTTP;
    ShippingRehearsal supplies container operations and exact-project cleanup.
    """
    def __init__(self, output, *, context, boot_timeout):
        from deploy.update.contracts import AutomationPolicy
        # Shared by boot and cold backup; shipping stop probes require >=70s.
        self.policy = AutomationPolicy(probe_timeout_seconds=90, wait_timeout_seconds=4200,
            recovery_startup_timeout_seconds=4200, stability_period_seconds=5,
            qualification_policy='standalone-cold-restore-v1')
        self.output = Path(output)
        self.directory = self.output/'workspace'
        self.directory.mkdir(mode=0o700)
        self.root = self.directory/'installation'
        self.context = context
        self.project = PREFIX+uuid.uuid4().hex[:12]
        self.deadline = time.monotonic()+TOTAL_SECONDS
        self.boot_timeout = boot_timeout
        self.source = producer().ROOT  # ProductHarness.install_updater source.
        self._secret_values = set()
        self.cleanup_verified = False
        self.corpus = {}
        self.owned_containers, self.owned_volumes, self.owned_networks = {}, {}, {}
        self._diagnostic_data = label(active_phase='not_started', phase_timings=[],
            first_boot_budget_seconds=boot_timeout, starting_status=[], resources=[],
            product_logs={}, product_operations=[], stack_inspect={}, collection_errors=[])

    def record(self, name, value):
        # Retained source/updater inventories and phase data only. Never dump
        # command argv, API tokens or arbitrary ProductHarness private logs.
        record(self.output, name, value)

    def assemble(self):
        commit, self.execution = archive_deployment(self.root)
        self.source_manifest = {'deployment':self.execution}
        ProductHarness.install_updater(self)
        self.descriptor = label(kind='local-source-descriptor-not-release-manifest',
            version=VERSION, source_sha=commit, image=IMAGE, platform=PLATFORM,
            deployment=self.execution)
        self.record('source-descriptor.json', self.descriptor)

    def install_and_boot(self):
        import base64
        from deploy.update.install import Installation, resolve_compose_command
        from deploy.update.processes import ProcessSupervisor
        p = producer()
        self.ports = {}
        sockets = []
        try:
            for key in ('FRONTEND_PORT','BACKEND_PORT','FI_COLLECTOR_OTLP_PORT',
                        'FI_COLLECTOR_OTLP_HTTP_PORT','AGENTCC_GATEWAY_PORT','MINIO_API_PORT'):
                sock = socket.socket(); sock.bind(('127.0.0.1',0)); sockets.append(sock)
                self.ports[key] = sock.getsockname()[1]
        finally:
            for sock in sockets: sock.close()
        values = dict(self.ports, COMPOSE_PROJECT_NAME=self.project, COMPOSE_PROFILES='',
            FUTURE_AGI_VERSION=VERSION+'@'+DIGEST, GOOGLE_APPLICATION_CREDENTIALS='/dev/null',
            FUTURE_AGI_TELEMETRY_DISABLED='true')
        values['INTEGRATION_ENCRYPTION_KEY'] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        for key in ('SECRET_KEY','REDIS_PASSWORD','PG_PASSWORD','CH_PASSWORD','MINIO_ROOT_PASSWORD',
                    'AGENTCC_INTERNAL_API_KEY','AGENTCC_ADMIN_TOKEN'):
            values[key] = secrets.token_hex(32)
        self._secret_values.update(str(v) for k,v in values.items() if re.search('KEY|PASSWORD|SECRET|TOKEN',k))
        path = self.root/'.env'
        path.write_text(''.join(str(k)+'='+str(v)+'\n' for k,v in values.items()))
        path.chmod(0o600)
        env = {k:os.environ[k] for k in ('PATH','HOME','TMPDIR','DOCKER_CONFIG') if k in os.environ}
        env['DOCKER_CONTEXT'] = self.context
        self.runner = ProcessSupervisor(self.policy, docker_context=self.context, environment=env)
        self.runner.deadline = self.deadline
        self.installation = Installation(self.root, environ=env, runner=self.runner,
            docker=['docker','--context',self.context], compose=resolve_compose_command())
        for args in (('ps','-aq'),('volume','ls','-q'),('network','ls','-q')):
            p.need(not self._docker(*args,'--filter','label=com.docker.compose.project='+self.project).strip(),
                   'pre-existing milestone resource')
        info = self._json('info','--format','{{json .}}')
        p.need(info['OSType']=='linux' and info['Architecture'] in ('x86_64','amd64'), 'native amd64 daemon required')
        self.daemon = info['ID']
        model = self.installation.config(refresh=True)
        p.need(set(model['services'])=={'app','postgres','clickhouse'} and
               set(model['volumes'])==set(p.VOLUME_ROLES), 'unsupported retained deployment')
        # The native historical /dev/null bind is retained, not transformed.
        for service in model['services'].values():
            p.need(not service.get('build') and not service.get('privileged') and
                   service.get('restart','no') in ('no','unless-stopped'), 'unsupported retained service')
            for mount in service.get('volumes',[]):
                if mount['type']=='bind':
                    p.need(mount.get('read_only') and (mount['source']=='/dev/null' or
                           Path(mount['source']).resolve().is_relative_to(self.root)), 'foreign retained bind')
        p.equal(model['services']['app']['image'], IMAGE, 'source is not digest-pinned')
        self.image_inspections = {}
        for name, service in model['services'].items():
            self.installation.docker_run('pull','--platform',PLATFORM,service['image'])
            image = self._json('image','inspect',service['image'])[0]
            p.equal(image['Os']+'/'+image['Architecture'], PLATFORM, 'non-native source image')
            self.image_inspections[name] = {k:image[k] for k in ('Id','Os','Architecture','RepoDigests')}
        p.need(any(ref.endswith('@'+DIGEST) for ref in self.image_inspections['app']['RepoDigests']),
               'source digest identity unavailable')
        self.record('image-inspections.json', self.image_inspections)
        self._wait_healthy()
        self._remember_resources()
        from deploy.update.contracts import InstallationBinding
        identity = self.installation.identity()
        binding = InstallationBinding(uuid.uuid4().hex, p.manifest_hash(identity), p.manifest_hash(identity),
            self.root, self.root/'.env', self.root/'.env', 'standalone', False, self.project,
            (self.root/'docker-compose.yml',), (), self.context, self.daemon, os.geteuid())
        self.registration = SimpleNamespace(binding=binding)

    def _wait_healthy(self):
        p = producer()
        started = time.monotonic()
        deadline = min(self.deadline, started+self.boot_timeout)
        # This first start is the installer's job, as on a real host: the
        # updater's compose_run proves ownership from existing containers and
        # rightly refuses an empty project, so it only manages A afterwards.
        self.installation.run(self.installation.compose_args('up','-d','--no-build','--pull','never'),
                              timeout=min(180,self.boot_timeout))
        last, next_sample = None, 0
        try:
            while time.monotonic()<deadline:
                from urllib.error import URLError
                try:
                    status, body = ProductHarness.request(self, self.ports['FRONTEND_PORT'],
                        '/starting-status.json', timeout=min(5,max(.1,deadline-time.monotonic())))
                    progress = {k:self._redact(body[k],512) for k in
                                ('phase','migration','current_migration','message','detail') if k in body}
                except (OSError, URLError):
                    status, progress = None, {'phase':'unreachable'}
                if (status,progress) != last:
                    p.need(len(self._diagnostic_data['starting_status'])<4096, 'starting status evidence bound')
                    self._diagnostic_data['starting_status'].append(dict(seconds=time.monotonic()-started,
                        http_status=status, progress=progress))
                    self.record('phase-timings.json', label(events=self._diagnostic_data['starting_status']))
                    last = (status,progress)
                rows = self.installation.containers()
                records = [self._json('inspect',r['id'])[0] for r in rows]
                p.need(not any(r['State'].get('OOMKilled') or r['RestartCount'] or
                               r['State']['Status'] in ('dead','exited') for r in records), 'source boot failed')
                if len(records)==3 and all(r['State']['Running'] and
                    r['State'].get('Health',{}).get('Status')=='healthy' for r in records):
                    healthy, _ = ProductHarness.request(self,self.ports['BACKEND_PORT'],'/health/',timeout=5)
                    if healthy==200 and time.monotonic()<deadline:
                        self.record('startup-result.json',label(status='ready',seconds=time.monotonic()-started))
                        return
                if time.monotonic()>=next_sample:
                    self._collect_diagnostics(); next_sample = time.monotonic()+30
                time.sleep(min(5,max(0,deadline-time.monotonic())))
            raise p.ManualRelease('suite_timed_out')
        finally:
            ended = time.monotonic()-started
            events = self._diagnostic_data['starting_status']
            durations = [dict(phase=event['progress'].get('phase'),
                first_observed_seconds=event['seconds'],
                observed_duration_seconds=(events[index+1]['seconds'] if index+1<len(events) else ended)-event['seconds'])
                for index,event in enumerate(events)]
            self.record('phase-timings.json',label(events=events,intervals=durations,
                elapsed_seconds=ended,poll_interval_seconds=5,measurement='sampled-product-starting-status-endpoint'))
            self._collect_diagnostics()

    def _collect_diagnostics(self):
        super()._collect_diagnostics()
        if not hasattr(self,'installation'): return
        try:
            # A bounded sanitized docker-inspect projection, not Env/Config
            # dumps. The exact backup inventory is separately private evidence.
            from deploy.update.docker_host import bounded_installation
            inst = bounded_installation(self.installation,15)
            inspect = {}
            for item in inst.containers()[:3]:
                row = json.loads(inst.docker_run('inspect',item['id']).stdout)[0]
                labels = row['Config']['Labels']
                producer().equal(labels.get('com.docker.compose.project'),self.project,'foreign inspect resource')
                inspect[labels['com.docker.compose.service']] = dict(Id=row['Id'],Image=row['Image'],
                    Created=row['Created'], RestartCount=row['RestartCount'],
                    State={k:v for k,v in row['State'].items() if k!='Health'},
                    HealthStatus=row['State'].get('Health',{}).get('Status'),
                    Mounts=row['Mounts'], RestartPolicy=row['HostConfig']['RestartPolicy'])
            self._diagnostic_data['stack_inspect'] = inspect
        except Exception as exc:
            self._diagnostic_data['collection_errors'].append(dict(exception_type=type(exc).__name__))
            self._diagnostic_data['collection_errors'] = self._diagnostic_data['collection_errors'][-16:]
        if hasattr(self,'ledger'):
            checkpoint = self.ledger.snapshot
            pending = checkpoint['pending']
            self._diagnostic_data['backup_checkpoint'] = dict(phase=checkpoint['phase'],
                direction=checkpoint['direction'],helpers=len(checkpoint['helpers']),
                pending_operation=pending['kind'] if pending else None,
                sealed=checkpoint['backup_seal'] is not None)
            # Retain exact existing small leaves on failures too. Missing or
            # partial evidence is never synthesized into a successful snapshot.
            for name in ('proof.json','inventory.json','backup-seal.json','recovery.json'):
                source = self.ledger.directory/name
                if source.is_file() and not source.is_symlink() and source.stat().st_size<=16*1024*1024:
                    target = self.output/('snapshot-'+name)
                    shutil.copyfile(source,target); target.chmod(0o600)
        record(self.output,'diagnostics.json',self.diagnostics())

    def seed(self):
        p = producer()
        self.witnesses = ProductWitnesses(self)
        try:
            rows = self.witnesses.create('source')
        except p.ManualRelease as exc:
            # A4 allows an explicit product-operation gap, never a failed core
            # domain. No fake spool or substitute workflow is introduced.
            if str(exc)!='witness_operation_unavailable' or 'source' not in self.witnesses.namespaces:
                raise
            rows = self.witnesses.read('source')
        p.need({'postgres_app','clickhouse','object_store','temporal'} <= {r['domain'] for r in rows},
               'missing core persistent domain witness')
        # Also enumerate the collector-created PostgreSQL project (read-only
        # ORM enumeration, NOT an ORM seed), with an exact controlled namespace.
        from scripts.update_rehearsal_shipping import DJANGO_PREFIX
        namespace = self.witnesses.namespaces['source']['namespace']
        projects = self.app_python(DJANGO_PREFIX+'''
from tracer.models.project import Project
rows=list(Project.no_workspace_objects.filter(name=a['namespace'],organization_id=a['organization_id'],
    workspace_id=a['workspace_id']).values('id','name','organization_id','workspace_id'))
assert len(rows)==1
print(json.dumps(rows,default=str))
''', {'namespace':namespace,'organization_id':self.witnesses.namespaces['source']['headers']['X-Organization-Id'],
      'workspace_id':self.witnesses.namespaces['source']['headers']['X-Workspace-Id']})
        rows.extend(dict(domain='postgres_app',origin='product_api',identity='project/'+r['id'],
            payload_sha256=p.manifest_hash(r)) for r in projects)
        self.project_witnesses = projects
        p.equal(self.witnesses.read('source'),[r for r in rows if not r['identity'].startswith('project/')],
                'source witnesses changed before snapshot')
        self.corpus['source_witnesses'] = sorted(rows,key=lambda r:(r['domain'],r['identity']))
        self.record('witness-inventory.private.json',label(witnesses=self.corpus['source_witnesses'],
            gaps=self.corpus.get('witness_gaps',[])))

    def observe_source(self):
        """Bounded unsigned producer observation, never authenticated admission.

        Use the shipping read-only runtime/ownership checks and the real product
        MigrationLoader. Storage identity is the shipping cold-backup binding.
        This does not claim fixed-observer/admission or A6 capacity qualification;
        those remain unrun. In particular do not invent a verified release just
        to cross StandaloneStateObserver's signature gate, or launch off-lane
        observer helper containers. Every Docker resource stays in this project.
        """
        from deploy.update.standalone_state import _runtime
        from deploy.update.standalone_backup import capture_binding
        from deploy.update.contracts import RuntimeObservationV2, installation_binding_hash
        from scripts.update_rehearsal_shipping import DJANGO_PREFIX
        p = producer(); binding = self.registration.binding
        layout = p.topology_profile('standalone',())
        images, identities, volumes = _runtime(self.installation,binding,None,layout)
        p.equal({s:r['image'] for s,r in identities.items()},
                {s:r['Id'] for s,r in self.image_inspections.items()},'source image changed after pull')
        migration = self.source_migrations = ProductHarness.migration_inventory(self)
        p.need(migration['planned'] and not migration['pending'] and migration['planned']==migration['applied'],
               'source migrations are incomplete')
        mapping = self.app_python(DJANGO_PREFIX+'''
from django.apps import apps
rows=sorted([{'label':app.label,'name':app.name,'path':app.path} for app in apps.get_app_configs()],key=lambda r:r['label'])
assert rows and len(rows)<256
print(json.dumps(rows))
''', {})
        self.binding = capture_binding(self.installation,
            installation_fingerprint=binding.installation_fingerprint,docker_context=self.context,
            daemon_id=self.daemon,project=self.project,profiles=(),
            verified_images={s:r['Id'] for s,r in self.image_inspections.items()},
            deployment_hashes=self.execution['files'])
        storage = json.loads(self.binding.raw)
        started = time.monotonic()
        while time.monotonic()-started<5:
            p.equal(_runtime(self.installation,binding,None,layout),(images,identities,volumes),
                    'source changed during stability')
            time.sleep(.5)
        preimage = configuration_preimage(self.installation)
        storage_preimage = {k:storage[k] for k in ('daemon','volumes','volume_root_ids')}
        self.source_observation = RuntimeObservationV2(VERSION,p.manifest_hash(self.descriptor),datetime.now(timezone.utc),
            installation_binding_hash(binding),PLATFORM,{'app':images['app']},
            {s:images[s] for s in ('postgres','clickhouse')},migration['inventory_sha256'],
            migration['applied_sha256'],p.manifest_hash(mapping),p.manifest_hash(self.execution),
            p.sha((self.root/'.env').read_bytes()),p.manifest_hash(preimage),
            p.manifest_hash(storage_preimage),p.manifest_hash(identities),
            {r['id']:r['restart_count'] for r in identities.values()},
            {r['id']:r['restart_policy'] for r in identities.values()},True,True)
        self.record('source-observation.private.json',label(observation=wire(self.source_observation),
            observation_scope='milestone-health-loader-and-cold-backup-binding',
            storage_preimage=storage_preimage,active_apps=mapping,
            source_descriptor_sha256=p.manifest_hash(self.descriptor),
            source_is_authenticated_release=False, real_loader=migration))

    def service_id(self, service):
        rows = [r['id'] for r in self.installation.containers() if r['service']==service]
        producer().equal(len(rows),1,'ambiguous product service')
        return rows[0]

    @contextmanager
    def locks(self):
        import fcntl
        from deploy.update.receipt import StateDir
        path = self.directory/'runner.lock'
        fd = os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with StateDir(self.root).lock(automation=True):
                yield
        finally:
            os.close(fd)

    def take_snapshot(self):
        from deploy.update.standalone_backup import capture_binding, StandaloneBackup, BackupPolicy, create_destination
        from deploy.update.backup_proof import BackupExpectation
        from deploy.update.recovery import RecoveryLedger
        p = producer()
        self.binding = capture_binding(self.installation,
            installation_fingerprint=self.registration.binding.installation_fingerprint,
            docker_context=self.context, daemon_id=self.daemon, project=self.project, profiles=(),
            verified_images={s:r['Id'] for s,r in self.image_inspections.items()},
            deployment_hashes=self.execution['files'],previous=self.binding)
        self.intent = label(kind='snapshot-only-intent-not-target-manifest', source=self.descriptor,
                            project=self.project)
        self.record('snapshot-intent.json',self.intent)
        self.expected = BackupExpectation(uuid.uuid4().hex,self.registration.binding.installation_fingerprint,
            p.manifest_hash(self.intent),VERSION,datetime.now(timezone.utc).replace(microsecond=0),
            p.topology_profile('standalone',()).backup_domains)
        self.destination, self.proofs = self.directory/'backups', self.directory/'proofs'
        create_destination(self.destination); create_destination(self.proofs)
        self.backup = StandaloneBackup(self.installation,self.binding,self.destination,
            policy=BackupPolicy(timeout_seconds=2400,readiness_seconds=180))
        # Fresh fixture only; runner -> update locks follow shipping ordering.
        # These are local lock files, not an enrolled scheduler or host service.
        with self.locks():
            self.ledger = RecoveryLedger.reserve(self.destination,self.expected,
                operation_token=uuid.uuid4().hex,run_id=uuid.uuid4().hex,
                binding_sha256=self.source_observation.binding_sha256)
            fd = os.open(self.proofs,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                self.artifact = self.backup.run_quiesced(self.expected,self.ledger,self.source_observation,
                                                        proof_directory_fd=fd)
            finally: os.close(fd)
            self.backup.retire_helpers(self.ledger)
        # Preserve exact proof/inventory bytes, NOT redacted or synthesized
        # lookalikes. Config is generated disposable data; mark these PRIVATE.
        for name in ('proof.json','inventory.json','backup-seal.json','recovery.json'):
            shutil.copyfile(self.ledger.directory/name,self.output/('snapshot-'+name))
            (self.output/('snapshot-'+name)).chmod(0o600)
        members = {}
        for role in p.VOLUME_ROLES:
            source = self.ledger.directory/(role+'.members.jsonl')
            p.need(source.stat().st_size<=16*1024*1024,'snapshot member evidence bound exceeded')
            target = self.output/('snapshot-'+role+'.members.jsonl')
            shutil.copyfile(source,target); target.chmod(0o600)
            members[role] = dict(sha256=p.sha(target.read_bytes()),bytes=target.stat().st_size)
        self.record('snapshot-members.json',label(roles=members))

    def verify_snapshot(self):
        p = producer()
        from deploy.update.backup_proof import validate_backup_proof
        proof_raw = (self.output/'snapshot-proof.json').read_bytes()
        inventory_raw = (self.output/'snapshot-inventory.json').read_bytes()
        proof = validate_backup_proof(proof_raw,self.expected,now=datetime.now(timezone.utc))
        inventory = json.loads(inventory_raw)
        p.equal(proof.inventory_sha256,p.sha(inventory_raw),'snapshot inventory is not proof-bound')
        p.equal(set(inventory['archives']),set(p.VOLUME_ROLES),'incomplete cold snapshot stores')
        p.equal(set(proof.covered_domains),set(p.topology_profile('standalone',()).backup_domains),
                'incomplete cold snapshot coverage')
        with self.locks():
            hold = self.backup.verify_hold(self.ledger,self.source_observation)
            self.backup.retire_helpers(self.ledger)
        p.equal(self.ledger.backup_seal,self.artifact.seal,'snapshot seal differs')
        p.equal(self.ledger.direction,'forward_pending','snapshot unexpectedly changed direction')
        self.record('cold-hold.json',label(hold=wire(hold),source_stays_stopped=True,
            proof_sha256=p.sha(proof_raw),inventory_sha256=p.sha(inventory_raw),
            archive_bytes=sum(r['size'] for r in inventory['archives'].values()),
            archive_roles=sorted(inventory['archives'])))

    def cleanup(self):
        if not hasattr(self,'installation'): return
        from deploy.update.processes import ProcessSupervisor
        from deploy.update.standalone_backup import _identity
        from deploy.update.standalone_restore import HELPER_LABEL
        # Teardown is NOT recovery/qualification. It has a separate bounded
        # supervisor and removes only this disposable fixture's exact helpers.
        # Never reset the original supervisor's uncertainty or claim a suite.
        inst = copy.copy(self.installation)
        inst.runner = ProcessSupervisor(self.policy,docker_context=self.context,
            environment=self.runner.environment,grace_seconds=1)
        inst.runner.deadline = time.monotonic()+180
        self.installation = inst
        if hasattr(self,'ledger'):
            expected = self.ledger.snapshot['helpers']
            attempt = self.ledger.snapshot['attempt_id']
            ids = self._docker('ps','-aq','--no-trunc','--filter','label='+HELPER_LABEL+'='+attempt).split()
            for cid in ids:
                row = self._json('inspect',cid)[0]
                producer().need(cid in expected and _identity(row)==expected[cid]['identity'] and
                    row['Name'].startswith('/'+self.project+'-restore-') and
                    row['Config']['Labels'].get(HELPER_LABEL)==attempt,'cleanup helper identity unconfirmed')
                self._docker('rm','-f',cid)
            producer().need(not self._docker('ps','-aq','--filter','label='+HELPER_LABEL+'='+attempt).strip(),
                            'cleanup helper absence unconfirmed')
        super().__exit__(None,None,None)


class TargetMilestone(SnapshotMilestone):
    """Same A/backup/volume objects; unsigned producer runtime evidence only."""
    def __init__(self, *args, **kwargs):
        from dataclasses import replace
        super().__init__(*args, **kwargs)
        self.project = TARGET_PREFIX+uuid.uuid4().hex[:12]
        self.policy = replace(self.policy, wait_timeout_seconds=1200,
            stability_period_seconds=15, migration_timeout_seconds=1200,
            migration_stall_timeout_seconds=300)
        self._diagnostic_data.update(label(milestone='target_readiness'))
        self.guard_events = []
        self.loader_cleanup_verified = True

    def cleanup(self):
        super().cleanup()
        if not self.loader_cleanup_verified:
            self.cleanup_verified = False
            raise producer().RehearsalError('loader cleanup unverified')

    def record(self, name, value):
        if isinstance(value, dict) and value.get('milestone') == 'source_snapshot':
            value.update(label(milestone='target_readiness'))
        super().record(name, value)

    def _collect_diagnostics(self):
        super()._collect_diagnostics()
        if not hasattr(self,'target_started'): return
        events = [r for r in self.guard_events if r['kind']=='progress']
        changes = [r for index,r in enumerate(events) if index==0 or
                   (r['phase'],r['current_migration']) !=
                   (events[index-1]['phase'],events[index-1]['current_migration'])]
        end = time.monotonic()-self.target_started
        self.record('target-phase-timings.json',label(events=changes,elapsed_seconds=end,
            measurement='sampled shipping HostRuntimeObserver product status and PostgreSQL progress',
            intervals=[dict(phase=r['phase'],current_migration=r['current_migration'],
                first_observed_seconds=r['seconds'],observed_duration_seconds=(changes[index+1]['seconds']
                if index+1<len(changes) else end)-r['seconds']) for index,r in enumerate(changes)]))

    def image_plan(self):
        """Networkless, no store mounts, read-only B loader. Not a B boot."""
        from deploy.update.docker_host import bounded_installation
        from scripts.update_rehearsal_shipping import TARGET_LOADER_PLAN, decode_product_output
        p = producer()
        name, token = self.project+'-loader-'+uuid.uuid4().hex[:12], uuid.uuid4().hex
        marker = 'futureagi.rehearsal.measurement'
        source = TARGET_LOADER_PLAN
        env = self.installation.subprocess_env()
        values = dict(self.installation.config(refresh=True)['services']['app']['environment'])
        env.update({k:str(v) for k,v in values.items() if v is not None})
        argv = ['create','-i','--name',name,'--label',marker+'='+token,
            '--pull','never','--restart','no','--no-healthcheck','--read-only','--network','none',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128',
            '--memory','1g','--cpus','1','--tmpfs','/tmp:rw,nosuid,nodev,size=64m']
        for path in sorted(self.target_inspection.get('Config',{}).get('Volumes') or {}):
            p.need(path.startswith('/') and path not in ('/','/tmp') and
                not any(c in path for c in (',',':','..')), 'unsupported image volume')
            argv.extend(('--tmpfs',path+':rw,noexec,nosuid,nodev,size=1m'))
        for key in sorted(values):
            p.need(re.fullmatch('[A-Z_][A-Z0-9_]*',key) is not None,'invalid image environment')
            if values[key] is not None: argv.extend(('--env',key))
        argv.extend(('--entrypoint','/usr/local/bin/python3',self.target_inspection['Id'],
                     '-I','-B','-c'+source))
        cid = None
        self.loader_cleanup_verified = False
        try:
            result = self.runner([*self.installation.docker,*argv],cwd=str(self.root),env=env,
                                 capture_output=True,text=True,timeout=30)
            p.need(result.returncode==0,'target loader create failed')
            cid = result.stdout.strip()
            p.need(re.fullmatch('[a-f0-9]{64}',cid) is not None,'target loader identity unavailable')
            row = self._json('inspect',cid)[0]
            p.need(row['Name']=='/'+name and row['Image']==self.target_inspection['Id'] and
                row['Config']['Labels'].get(marker)==token and not row['State']['Running'] and
                row['HostConfig']['ReadonlyRootfs'] and row['HostConfig']['NetworkMode']=='none' and
                row['HostConfig']['RestartPolicy']['Name']=='no' and
                not any(m['Type']!='tmpfs' for m in row['Mounts']), 'unsafe target loader')
            result = self.runner([*self.installation.docker,'start','-a','-i',cid],
                input=p.canonical_json({'applied':self.source_migrations['applied']}),
                cwd=str(self.root),env=env,capture_output=True,timeout=180)
            row = self._json('inspect',cid)[0]
            if result.returncode or row['State'].get('OOMKilled'):
                self._diagnostic_data['product_operations'].append(dict(
                    operation='offline_target_loader',exit_code=result.returncode,
                    stderr=self._redact(result.stderr,4096)))
            p.need(result.returncode==0 and not row['State']['Running'] and
                   row['State']['ExitCode']==0 and not row['State'].get('OOMKilled'),
                   'target loader failed')
            return decode_product_output(result.stdout)
        finally:
            # Reconcile this random name even if create timed out. Never adopt
            # a helper on name alone; remove only the matching private token.
            inst = bounded_installation(self.installation,15,inherit_deadline=False)
            ids = inst.docker_run('ps','-aq','--no-trunc','--filter','name=^/'+name+'$').stdout.split()
            p.need(len(ids)<=1,'ambiguous loader cleanup')
            for found in ids:
                row = json.loads(inst.docker_run('inspect',found).stdout)[0]
                p.need(row['Name']=='/'+name and row['Config']['Labels'].get(marker)==token and
                       row['Image']==self.target_inspection['Id'] and (cid is None or cid==found),
                       'loader cleanup identity unconfirmed')
                inst.docker_run('rm','-f',found)
            p.need(not inst.docker_run('ps','-aq','--filter','name=^/'+name+'$').stdout.strip(),
                   'loader cleanup unverified')
            self.loader_cleanup_verified = True

    def prefetch_target(self):
        p = producer()
        # Match production: registry I/O is entirely before downtime.
        for ref in (TARGET_IMAGE,'futureagi/standalone:'+TARGET_VERSION):
            self.installation.docker_run('pull','--platform',PLATFORM,ref)
        self.target_inspection = self._json('image','inspect',TARGET_IMAGE)[0]
        tagged = self._json('image','inspect','futureagi/standalone:'+TARGET_VERSION)[0]
        p.equal(tagged['Id'],self.target_inspection['Id'],'target tag differs from fixed digest')
        p.equal(self.target_inspection['Os']+'/'+self.target_inspection['Architecture'],PLATFORM,
                'non-native target image')
        p.need(any(ref.endswith('@'+TARGET_DIGEST) for ref in self.target_inspection['RepoDigests']),
               'target digest identity unavailable')
        self.record('target-image-inspection.json',label(image={k:self.target_inspection[k] for k in
            ('Id','Os','Architecture','RepoDigests')},prefetched_before_quiescence=True))

    def prepare_target(self):
        p = producer()
        p.need(self.ledger.direction=='forward_pending' and self.ledger.backup_seal is not None,
               'target requires sealed cold source')
        self.target_plan = self.image_plan()
        p.need(self.target_plan['planned'] and self.target_plan['inventory'], 'empty target loader inventory')
        p.equal(self.target_plan['inventory_sha256'],p.manifest_hash(self.target_plan['inventory']),
                'target loader inventory hash differs')
        self.record('target-plan.json',label(target_version=TARGET_VERSION,target_image=TARGET_IMAGE,
            source_applied_sha256=self.source_migrations['applied_sha256'],
            measurement='B MigrationLoader/MigrationExecutor against independently observed cold A applied set',
            plan=self.target_plan,expected_pending_count=0,assumed_pending_count=False))
        # The real hold is checked again after the read-only loader.
        with self.locks():
            self.backup.verify_hold(self.ledger,self.source_observation)
            self.backup.retire_helpers(self.ledger)

    def guard_event(self, kind, **values):
        producer().need(len(self.guard_events)<4096,'guard evidence bound exceeded')
        self.guard_events.append(dict(kind=kind,seconds=time.monotonic()-self.target_started,
            remaining_deadline_seconds=self.runner.deadline-time.monotonic(),**values))
        self.record('target-guard-samples.json',label(events=self.guard_events,
            guards_active=['deadline','migration_stall','disk_watermark','health','restart_stability'],
            reserve_bytes=self.policy.disk_watermark_reserve_bytes,
            migration_deadline_seconds=self.policy.migration_timeout_seconds,
            stall_timeout_seconds=self.policy.migration_stall_timeout_seconds,
            stability_period_seconds=self.policy.stability_period_seconds))

    def verify_target(self):
        """Live target oracle invoked inside shipping readiness's guarded interval."""
        from scripts.update_rehearsal_shipping import DJANGO_PREFIX
        p = producer()
        migration = ProductHarness.migration_inventory(self)
        self.record('target-migrations.json',label(before=self.target_plan,after=migration,
            pending_count=len(self.target_plan['pending']),
            executed=[n for n in migration['applied'] if n not in self.source_migrations['applied']],
            measurement='real B loader and recorder, before/after automatic bootstrap',
            per_migration_execution=[] if not self.target_plan['pending'] else None))
        p.equal(migration['planned'],self.target_plan['planned'],'target on-image migration set changed')
        p.equal(migration['inventory_sha256'],self.target_plan['inventory_sha256'],
                'target migration sources differ from on-image inventory')
        p.need(not migration['pending'] and migration['applied']==self.target_plan['planned'],
               'target applied migrations differ from B inventory')
        # This fixed historical pair normally has a measured empty plan. A
        # nonempty plan is recorded, never silently treated as measured zero.
        # Exact execution boundaries are a later migration_forward milestone.
        if self.target_plan['pending']:
            raise p.ManualRelease('migration_measurement_unavailable')
        rows = self.witnesses.read('source')
        n = self.witnesses.namespaces['source']
        projects = self.app_python(DJANGO_PREFIX+'''
from tracer.models.project import Project
rows=list(Project.no_workspace_objects.filter(name=a['namespace'],organization_id=a['organization_id'],
    workspace_id=a['workspace_id']).values('id','name','organization_id','workspace_id'))
assert len(rows)==1
print(json.dumps(rows,default=str))
''', {'namespace':n['namespace'],'organization_id':n['headers']['X-Organization-Id'],
      'workspace_id':n['headers']['X-Workspace-Id']})
        p.equal(projects,self.project_witnesses,'source project changed under target')
        rows.extend(dict(domain='postgres_app',origin='product_api',identity='project/'+r['id'],
            payload_sha256=p.manifest_hash(r)) for r in projects)
        p.equal(sorted(rows,key=lambda r:(r['domain'],r['identity'])),self.corpus['source_witnesses'],
                'source witnesses changed under target')
        for port,path in (('BACKEND_PORT','/health/'),('FRONTEND_PORT','/')):
            status,_ = ProductHarness.request(self,self.ports[port],path,timeout=5)
            p.equal(status,200,'target HTTP readiness failed')
        self.record('target-witnesses.private.json',label(witnesses=rows,identical_to_source=True,
            gaps=self.corpus.get('witness_gaps',[])))
        return migration

    def target_readiness(self):
        from deploy.update.apply import Updater
        from deploy.update.automatic_runtime import HostRuntimeObserver
        from deploy.update.contracts import AutomaticUpdateEstimate, ReasonCode
        from deploy.update.orchestrator import check_binding, require
        from deploy.update.receipt import StateDir
        from deploy.update.manifest import hash_path
        from deploy.update import envfile
        p, driver = producer(), self
        self.target_started = time.monotonic()

        class MeasuredObserver(HostRuntimeObserver):
            def free_bytes(self):
                value = super().free_bytes()
                driver.guard_event('watermark',free_bytes=value)
                return value
            def progress(self, roster):
                phase,migration,pg = super().progress(roster)
                driver.guard_event('progress',phase=phase,current_migration=migration,postgres_progress=pg)
                if not hasattr(self,'next_diagnostics') or time.monotonic()>=self.next_diagnostics:
                    driver._collect_diagnostics(); self.next_diagnostics=time.monotonic()+30
                return phase,migration,pg

        # A scoped producer guard, NOT an enrolled admission guard or signed
        # release. Every mutation-side safety check is retained in Updater and
        # AutomaticRuntime. Full qualify remains unavailable.
        class MilestoneGuard:
            automatic = SimpleNamespace(registration=driver.registration)
            approved_env_sha256 = p.sha((driver.root/'.env').read_bytes())
            dependency_image_ids = {s:frozenset((r['Id'],)) for s,r in driver.image_inspections.items() if s!='app'}
            def context(self, engine): return SimpleNamespace(engine=engine,receipt_metadata={})
            def validate(self, boundary, context):
                require(boundary=='before_rollout',ReasonCode.CONTROL_INVALID)
                check_binding(context.engine.installation,driver.registration.binding)
                require(all(hash_path(driver.root,name)==digest for name,digest in driver.execution['files'].items()),
                        ReasonCode.DEPLOYMENT_FILES_MISMATCH)
                recovery = context.engine.active_record['env_recovery']
                require(envfile.check_commit(driver.root/'.env',recovery,durable=True)=='accepted',ReasonCode.ENV_CONFLICT)
                require(p.sha((driver.root/'.env').read_bytes())==recovery['after_sha256'],ReasonCode.ENV_CONFLICT)
                driver.record('target-selection.json',label(env_value=TARGET_VERSION,target_image=TARGET_IMAGE,
                    env_before_sha256=recovery['before_sha256'],env_after_sha256=recovery['after_sha256'],
                    native_commit='accepted',shipping_engine='Updater.start_automatic_target_locked'))

        class ReadySession:
            def __init__(self,runtime): self.runtime=runtime; self.signature=None
            def __enter__(self): return self
            def __exit__(self,*exc): return False
            def check(self):
                require(time.monotonic()<driver.runner.deadline,ReasonCode.READINESS_FAILED)
                sample = self.runtime.sample(self.roster,progress=False)
                require(sample.free_bytes>=driver.policy.disk_watermark_reserve_bytes,ReasonCode.DISK_WATERMARK_ABORT)
                signature = sorted((cid,*row) for cid,row in sample.containers.items())
                require(len(signature)==3 and all(row[1] and row[2] and row[3]==0 for row in signature),
                        ReasonCode.READINESS_FAILED)
                require(self.signature is None or self.signature==signature,ReasonCode.READINESS_FAILED)
                self.signature=signature
                require(time.monotonic()<driver.runner.deadline,ReasonCode.READINESS_FAILED)
                self.runtime._safe(); self.runtime._stores()
            def ready(self,roster):
                self.roster=roster
                self.check()
                driver.verify_target()
                self.check()

        # These are explicitly UNSIGNED runtime ceilings, not invented A6
        # throughput/preflight measurements. A nonzero phase budget monitors
        # bootstrap stall even when B's measured Django plan is empty.
        estimate = AutomaticUpdateEstimate(0,0,0,0,0,0,self.policy.disk_watermark_reserve_bytes,
            2400,0,2400,self.policy.migration_timeout_seconds,self.policy.migration_stall_timeout_seconds)
        self.record('target-runtime-budget.json',label(estimate=wire(estimate),policy=wire(self.policy),
            signed_preflight=False,unused_estimate_fields_are_not_measurements=True,
            scope='bounded target milestone only'))
        guard = MilestoneGuard()
        observer = MeasuredObserver(self.installation,self.registration,self.backup.b,timeout=30)
        self.engine = Updater(self.installation,SimpleNamespace(automation_policy=self.policy),StateDir(self.root),
            out=io.StringIO(),automation_guard=guard,guard_context=guard.context,
            built_in_backup=self.backup,runtime_observer=observer)
        self.engine.automatic_ledger = self.ledger
        self.engine.active_record = dict(kind='apply',run_id=self.ledger.snapshot['run_id'],
            automatic_transaction=True,stage='prepare',status='running',target_version=TARGET_VERSION,
            env_changes={'FUTURE_AGI_VERSION':TARGET_VERSION},**self.installation.identity())
        plan = {'components':[dict(variable='FUTURE_AGI_VERSION',target_tag=TARGET_VERSION,
            component='standalone',target_image='futureagi/standalone:'+TARGET_VERSION,target_digest=TARGET_DIGEST)]}
        with self.locks():
            self.backup.verify_hold(self.ledger,self.source_observation)
            self.backup.retire_helpers(self.ledger)
            with self.engine.start_automatic_target_locked(ledger=self.ledger,plan=plan,
                    estimate=estimate,observe=ReadySession) as (runtime,roster,session):
                session.check()
                expected = dict(self.backup.b['images'],app=self.target_inspection['Id'])
                p.equal({s:r['Image'] for s,r in roster.items()},expected,'running target image IDs differ')
                records = {s:runtime._exact(identity) for s,identity in roster.items()}
                p.need(all(r['State']['Running'] and r['State']['Health']['Status']=='healthy' and
                    r['RestartCount']==0 for r in records.values()),'final target health or restart differs')
                self.record('target-final-identities.json',label(images=expected,
                    source_volumes=self.backup.b['volumes'],
                    roster={s:dict(Id=r['Id'],Image=r['Image'],RestartCount=r['RestartCount'],
                        StartedAt=r['State']['StartedAt'],RestartPolicy=r['HostConfig']['RestartPolicy'])
                        for s,r in records.items()},stable=True))
                session.check()
                self.engine.active_record.update(status='succeeded')
                self.ledger.decide_success(label(kind='unsigned-target-milestone-success-not-release-admission',
                    run_id=self.ledger.snapshot['run_id'],record=self.engine.active_record,
                    target_image=TARGET_IMAGE,source_snapshot_seal=wire(self.artifact.seal)))
            p.equal(self.ledger.direction,'success_decided','target success decision missing')
            self.record('target-success-decision.json',label(direction=self.ledger.direction,
                phase=self.ledger.phase,seal=self.ledger.snapshot['success_seal'],
                elapsed_seconds=time.monotonic()-self.target_started))
        self._remember_resources()


def run(args):
    from deploy.update.apply import Interrupted
    p = producer()
    through = getattr(args,'through','source_snapshot')
    stamp = lambda **values: label(milestone=through,**values)
    output = args.out.absolute()
    if output.exists() or output.is_symlink():
        print('milestone refused: output_must_be_new',file=sys.stderr)
        return 2
    output.mkdir(mode=0o700,parents=True)
    record(output,'job-plan.json',plan(args.boot_timeout,through))
    if args.dry_run:
        record(output,'milestone.json',stamp(status='planned',executed_stages=[],cleanup_verified=False))
        return 0
    driver, phase, failure = None, 'context_validation', None
    stages = []
    previous_term = signal.getsignal(signal.SIGTERM)
    def terminated(signum, frame):
        raise Interrupted(signal.Signals(signum).name)
    signal.signal(signal.SIGTERM,terminated)
    try:
        p.need(sys.platform=='linux' and os.environ.get('GITHUB_ACTIONS')=='true' and
               os.environ.get('RUNNER_ARCH')=='X64' and args.docker_context=='colima-fagi-update',
               'milestone requires isolated native amd64 Actions')
        p.need(output.resolve().is_relative_to(Path.home().resolve()),'home-backed milestone evidence required')
        cls = TargetMilestone if through=='target_readiness' else SnapshotMilestone
        driver = cls(output,context=args.docker_context,boot_timeout=args.boot_timeout)
        operations = list(zip(STAGES,(driver.assemble,driver.install_and_boot,driver.seed,
                              driver.observe_source,driver.take_snapshot,driver.verify_snapshot)))
        if through=='target_readiness':
            operations.insert(4,('prefetch_target',driver.prefetch_target))
            operations.extend((('prepare_target',driver.prepare_target),('target_readiness',driver.target_readiness)))
        for phase, method in operations:
            p.need(time.monotonic()<driver.deadline,'milestone deadline exceeded')
            driver._phase(phase,method)
            p.need(time.monotonic()<driver.deadline,'milestone deadline exceeded')
            stages.append(phase)
            record(output,'milestone.json',stamp(status='running',stage=phase,executed_stages=stages))
    except (Exception, Interrupted, KeyboardInterrupt) as exc:
        # ProcessSupervisor temporarily installs the shipping signal handler,
        # whose Interrupted derives from BaseException, not Exception.
        code = getattr(exc,'code',getattr(exc,'reason_code',None))
        code = getattr(code,'value',code) or p.failure_diagnostics(exc)['refusal_code']
        failure = dict(stage=phase,reason=code,exception_type=type(exc).__name__)
        # Only fixed producer assertions, never raw product/subprocess errors.
        if isinstance(exc,p.RehearsalError): failure['detail'] = str(exc)[:512]
        # Updater-side install/process refusals name the failing step; a CI run
        # cannot be inspected afterwards, so keep them, with generated secrets
        # and credential shapes redacted.
        from deploy.update.install import InstallError
        if isinstance(exc,InstallError) and driver is not None:
            failure['detail'] = driver._redact(str(exc),1024)
    finally:
        signal.signal(signal.SIGTERM,previous_term)
        if driver is not None:
            driver._collect_diagnostics()
            try:
                driver.cleanup()
            except Exception as exc:
                failure = failure or dict(stage='cleanup',reason='cleanup_unverified',exception_type=type(exc).__name__)
                record(output,'cleanup-failure.json',stamp(reason='cleanup_unverified',exception_type=type(exc).__name__))
            record(output,'diagnostics.json',driver.diagnostics())
    result = stamp(status='failed' if failure else 'passed',executed_stages=stages,
        cleanup_verified=bool(driver and driver.cleanup_verified),failure=failure,
        qualification_suites={s:'unrun' for s in p.SUITES})
    record(output,'milestone.json',result)
    if failure:
        record(output,'failure.json',result)
        print('milestone refused: '+failure['reason']+' ('+failure['stage']+')',file=sys.stderr)
        return 2
    return 0
