"""Canonical Docker names must not relax immutable image identity checks."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from deploy.tests.test_update_qualification_executor import envelope_fixture
from deploy.tests.test_update_qualify_assembly import SyntheticExecutor
from deploy.update.manifest import parse_image_ref
from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_milestone as milestones
from scripts import update_rehearsal_qualification as q
from scripts import update_rehearsal_resources as resources


DIGEST = 'sha256:'+'a'*64
IMAGE = 'docker.io/futureagi/standalone:v1.54.0@'+DIGEST
ALIASES = ('futureagi/standalone', 'docker.io/futureagi/standalone',
           'index.docker.io/futureagi/standalone', 'registry-1.docker.io/futureagi/standalone')


class ImageIdentityTests(unittest.TestCase):
    def source_boot(self, reference, repo_digest=None):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            driver = object.__new__(milestones.SnapshotMilestone)
            driver.root = Path(temporary).resolve()
            driver.project, driver.context, driver.platform = 'unit-image', 'colima-fagi-update', 'linux/amd64'
            driver.source_version, driver.source_digest, driver.source_image = 'v1.54.0', DIGEST, IMAGE
            driver._secret_values, driver.policy, driver.deadline = set(), object(), 100
            model = dict(services={name: dict(image=reference if name=='app' else name+':unit')
                                  for name in ('app', 'postgres', 'clickhouse')},
                         volumes={role:{} for role in p.VOLUME_ROLES})
            installation = mock.Mock()
            installation.config.return_value = model
            driver._docker = mock.Mock(return_value='')
            inspection = dict(Id='sha256:'+'b'*64, Os='linux', Architecture='amd64',
                              RepoDigests=[repo_digest or 'futureagi/standalone@'+DIGEST])
            driver._json = lambda *args: (dict(ID='unit-daemon', OSType='linux', Architecture='amd64')
                                         if args[0]=='info' else [inspection])
            driver.record = mock.Mock()
            driver._wait_healthy = mock.Mock(side_effect=StopIteration('validated before boot'))
            with mock.patch('deploy.update.install.Installation', return_value=installation), \
                 mock.patch('deploy.update.install.resolve_compose_command', return_value=['docker','compose']), \
                 mock.patch('deploy.update.processes.ProcessSupervisor'), \
                 mock.patch.object(milestones.socket, 'socket') as socket:
                socket.return_value.getsockname.return_value = ('127.0.0.1', 12345)
                driver.install_and_boot()

    def test_source_boot_accepts_equivalent_hub_names(self):
        for name in ALIASES:
            with self.subTest(name=name), self.assertRaisesRegex(StopIteration, 'validated before boot'):
                self.source_boot(name+':v1.54.0@'+DIGEST)

    def test_source_boot_refuses_other_registry_repository_tag_or_digest(self):
        for reference in ('example.com/futureagi/standalone:v1.54.0@'+DIGEST,
                          'futureagi/other:v1.54.0@'+DIGEST,
                          'futureagi/standalone:v1.55.0@'+DIGEST,
                          'futureagi/standalone:v1.54.0@sha256:'+'c'*64):
            with self.subTest(reference=reference), self.assertRaisesRegex(p.RehearsalError, 'not digest-pinned'):
                self.source_boot(reference)
        with self.assertRaisesRegex(p.RehearsalError, 'source digest identity unavailable'):
            self.source_boot(IMAGE, 'example.com/futureagi/standalone@'+DIGEST)

    def test_snapshot_accepts_hub_and_short_library_repo_digests(self):
        for prefix in ('', 'docker.io/', 'index.docker.io/', 'registry-1.docker.io/'):
            fixture = SyntheticExecutor()
            fields = dict(fixture.row.binding(), **fixture.fields)
            for inspection in fixture.corpus['source_image_inspections'].values():
                ref = parse_image_ref(inspection['RepoDigests'][0])
                repository = ref.repository.removeprefix('library/') if not prefix else ref.repository
                inspection['RepoDigests'] = [prefix+repository+'@'+ref.digest]
            with self.subTest(prefix=prefix):
                p.validate_snapshot_evidence(fields, fixture.corpus, target=fixture.target,
                                             predecessor_raw=fixture.row.release.raw_manifest)

    def test_snapshot_repo_digest_must_match_registry_repository_and_digest(self):
        for service in ('app', 'postgres', 'clickhouse'):
            for change in ('registry', 'repository', 'digest'):
                fixture = SyntheticExecutor()
                fields = dict(fixture.row.binding(), **fixture.fields)
                inspection = fixture.corpus['source_image_inspections'][service]
                ref = parse_image_ref(inspection['RepoDigests'][0])
                inspection['RepoDigests'] = [('example.com/' if change=='registry' else 'docker.io/')+
                    ('other/image' if change=='repository' else ref.repository)+'@'+
                    ('sha256:'+'f'*64 if change=='digest' else ref.digest)]
                with self.subTest(service=service, change=change), self.assertRaises(p.RehearsalError):
                    p.validate_snapshot_evidence(fields, fixture.corpus, target=fixture.target,
                                                 predecessor_raw=fixture.row.release.raw_manifest)

    def test_target_envelope_repo_digest_is_canonical_but_exact(self):
        for name in ALIASES+('example.com/futureagi/standalone', 'futureagi/other'):
            fields, corpus, target, refs = envelope_fixture()
            corpus['target_image_inspection']['RepoDigests'] = [name+'@'+target['components']['standalone']['digest']]
            with self.subTest(name=name):
                if name in ALIASES:
                    q.validate_bootstrap_envelope(fields, corpus, target, refs)
                else:
                    with self.assertRaises(p.RehearsalError):
                        q.validate_bootstrap_envelope(fields, corpus, target, refs)

    def sampler_fixture(self, reference, repo_digest=None, target_ref=IMAGE.split('@')[0]):
        _, corpus, _, _ = envelope_fixture()
        binding = copy.deepcopy(corpus['source_snapshot_inventory']['binding'])
        rows = copy.deepcopy(corpus['target_container_inspections'])
        project = binding['project']
        for role, volume in binding['volumes'].items():
            volume.update(Driver='local', Scope='local', Options=None,
                Mountpoint='/var/lib/docker/volumes/'+volume['Name']+'/_data',
                Labels={'com.docker.compose.project': project, 'com.docker.compose.volume':role})
        for service, row in rows.items():
            row['Config']['Image'] = reference if service=='app' else service+':unit'
            row['GraphDriver']['Name'] = 'overlay2'
            row['HostConfig']['RestartPolicy'] = {'Name':'unless-stopped'}
            row['State'] = dict(Status='created', Pid=0)
            role = {'app':'app-data', 'postgres':'postgres-data', 'clickhouse':'clickhouse-data'}[service]
            volume = binding['volumes'][role]
            row['Mounts'] = [dict(Type='volume', Name=volume['Name'], Source=volume['Mountpoint'], RW=True,
                Destination={'app':'/data', 'postgres':'/var/lib/postgresql/data', 'clickhouse':'/var/lib/clickhouse'}[service])]
        def docker_json(*args):
            if args[0]=='inspect': return list(rows.values())
            if args[0]=='volume': return list(binding['volumes'].values())
            if args[0]=='image':
                return [dict(Id=rows['app']['Image'], RepoDigests=[repo_digest or 'futureagi/standalone@'+DIGEST])]
            self.fail('unexpected Docker fixture call')
        with mock.patch.object(resources, '_native_amd64_actions', return_value=True), \
             mock.patch.object(resources.NormalBootstrapEnvelopeSampler, '_docker_json', side_effect=docker_json), \
             mock.patch.object(resources.NormalBootstrapEnvelopeSampler, '_daemon_info',
                               return_value=(binding['daemon'], '/var/lib/docker', 'unix:///unit')):
            return resources.NormalBootstrapEnvelopeSampler(
                SimpleNamespace(runner=SimpleNamespace(docker_context='colima-fagi-update')),
                binding=binding, roster=rows, target_image=dict(image=target_ref, digest=DIGEST),
                snapshot_generation='a'*64, pending_plan=[], tables=[], migration_tables=[])

    def test_live_sampler_uses_canonical_config_image_and_exact_repo_digest(self):
        for name in ALIASES:
            with self.subTest(name=name):
                self.sampler_fixture(name+':v1.54.0@'+DIGEST)
        for reference in ('example.com/futureagi/standalone:v1.54.0@'+DIGEST,
                          'futureagi/standalone:v1.55.0@'+DIGEST,
                          'futureagi/standalone:v1.54.0@sha256:'+'f'*64):
            with self.subTest(reference=reference), self.assertRaises(resources.ResourceObservationError):
                self.sampler_fixture(reference)
        with self.assertRaises(resources.ResourceObservationError):
            self.sampler_fixture(IMAGE, 'example.com/futureagi/standalone@'+DIGEST)
        self.sampler_fixture(IMAGE, target_ref=IMAGE)
        with self.assertRaises(resources.ResourceObservationError):
            self.sampler_fixture(IMAGE, target_ref=IMAGE.split('@')[0]+'@sha256:'+'f'*64)


if __name__ == '__main__':
    unittest.main()
