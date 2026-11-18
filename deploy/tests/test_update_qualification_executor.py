"""Daemon-free exact-pair wiring and raw-proof tests; not product qualification."""
import copy
import json
from pathlib import Path
import tempfile
import time
from dataclasses import replace
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_milestone as milestones
from scripts import update_rehearsal_qualification as q
from scripts import update_rehearsal_resources as resources


def envelope_fixture():
    entry = next(row for row in p.load_high_volume_registry() if row['database'] == 'postgres')
    key = ('postgres', 'public.' + entry['table'])
    plan = [{'app_label': entry['app_label'], 'name': '0001_unit_fixture'}]
    scope = [dict(plan[0], tables=[dict(engine=key[0], table=key[1])])]
    refs = {key: dict(engine=key[0], table=key[1], rows=100000, bytes=1000000)}
    tables = [dict(engine=key[0], table=key[1], reference_rows=100000, reference_bytes=1000000)]
    target = {'components': {'standalone': {'image': 'futureagi/standalone:v1.55.0', 'digest': 'sha256:'+'a'*64}}}
    images = {service: 'sha256:'+str(index)*64 for index,service in enumerate(('app','postgres','clickhouse'),1)}
    binding = dict(daemon='unit-daemon', project='unit-project', images=images,
        volumes={role: {'Name':'unit-project_'+role,'CreatedAt':'2026-10-29T00:00:00Z'} for role in p.VOLUME_ROLES},
        volume_root_ids={role: '1:'+str(index) for index,role in enumerate(p.VOLUME_ROLES,1)})
    inventory = {'binding':binding}
    inspection = dict(Id='sha256:'+'4'*64, Os='linux', Architecture='amd64',
        RepoDigests=['futureagi/standalone@'+target['components']['standalone']['digest']])
    containers = {}
    identity = dict(daemon_id=binding['daemon'], project=binding['project'],
        target_repository='futureagi/standalone', target_digest=target['components']['standalone']['digest'],
        target_app_image_id=inspection['Id'], containers={}, volumes={})
    for index,service in enumerate(('app','postgres','clickhouse'),1):
        cid=str(index)*64
        row=dict(Id=cid, Image=inspection['Id'] if service=='app' else images[service], Created='2026-10-29T00:00:01Z',
            RestartCount=0, State={'Running':True,'Health':{'Status':'healthy'}},
            Config={'Labels':{'com.docker.compose.project':binding['project'],'com.docker.compose.service':service}},
            GraphDriver={'Data':{'UpperDir':'/var/lib/docker/overlay2/'+cid+'/diff'}},
            HostConfig={'LogConfig':{'Type':'json-file'}}, LogPath='/var/lib/docker/containers/'+cid+'/'+cid+'-json.log')
        containers[service]=row
        footprint=dict(upper_dir=row['GraphDriver']['Data']['UpperDir'],log_driver='json-file',log_path=row['LogPath'])
        identity['containers'][service]=dict(id=cid,image_id=row['Image'],created=row['Created'],restart_count=0,
            storage_footprint_sha256=p.sha(p.canonical_json(footprint)))
    for role in p.VOLUME_ROLES:
        identity['volumes'][role]=dict(name=binding['volumes'][role]['Name'],
            created_at=binding['volumes'][role]['CreatedAt'],root_identity=binding['volume_root_ids'][role])
    samples=[dict(monotonic=at,boundary=boundary,disk_bytes=disk,wal_bytes=20,temp_bytes=10,memory_bytes=100)
             for at,boundary,disk in ((10.,'before_start',100),(10.5,'during',200),(11.1,'after_end',150))]
    measurement=resources.reduce_envelope(plan,tables,scope,samples,.9000000000000004,
        started_monotonic=10.1,completed_monotonic=11.)
    migration=dict(source_applied_set_sha256='b'*64,target_inventory_sha256='c'*64,
        target_pending_plan_count=1,target_pending_plan_sha256=p.pending_migration_plan_hash(plan))
    fields=dict(predecessor={'platform':'linux/amd64'},migration_evidence=migration,migration_measurements=measurement)
    raw=dict(schema_version=1,measurement_basis=resources.MEASUREMENT_BASIS,
        snapshot_generation=p.sha(p.canonical_json(inventory)),target_digest=target['components']['standalone']['digest'],
        target_identity=identity,target_identity_sha256=p.sha(p.canonical_json(identity)),
        ordered_plan_sha256=p.pending_migration_plan_hash(plan),table_references_sha256=p.sha(p.canonical_json(tables)),
        migration_tables=scope,migration_table_scope_sha256=p.sha(p.canonical_json(scope)),
        counter_sources=resources.counter_sources(),started_monotonic=10.1,completed_monotonic=11.,
        measurement=copy.deepcopy(measurement),samples=samples)
    probe=dict(program_sha256=p.sha(p.target_table_scope_probe().encode()),
        target_image_digest=target['components']['standalone']['digest'],target_inventory_sha256='c'*64,
        source_applied_sha256='b'*64,database_schemas={'postgres':'public','clickhouse':'default'},
        pending_migration_tables=copy.deepcopy(scope))
    corpus=dict(target_pending_plan=plan,pending_migration_tables=scope,pending_table_scope_probe=probe,
        bootstrap_envelope=raw,source_snapshot_inventory=inventory,target_image_inspection=inspection,
        target_container_inspections=containers)
    return fields,corpus,target,refs


class EnvelopeProvenanceTests(unittest.TestCase):
    def test_real_reducer_and_independent_scope_bind_the_measurement(self):
        fields,corpus,target,refs=envelope_fixture()
        self.assertEqual(q.validate_bootstrap_envelope(fields,corpus,target,refs),fields['migration_measurements'])

    def test_empty_reference_claim_cannot_erase_a_registered_touched_table(self):
        fields,corpus,target,refs=envelope_fixture()
        fields['migration_measurements']['tables']=[]
        fields['migration_measurements']['migration_tables'][0]['tables']=[]
        raw=corpus['bootstrap_envelope']
        raw['measurement']=copy.deepcopy(fields['migration_measurements'])
        raw['table_references_sha256']=p.sha(p.canonical_json([]))
        raw['migration_tables']=copy.deepcopy(fields['migration_measurements']['migration_tables'])
        raw['migration_table_scope_sha256']=p.sha(p.canonical_json(raw['migration_tables']))
        with self.assertRaises(p.RehearsalError):
            q.validate_bootstrap_envelope(fields,corpus,target,refs)

    def test_cross_pair_scope_snapshot_identity_and_counter_drift_refuse(self):
        for field in ('target_digest','snapshot_generation','ordered_plan_sha256','target_identity_sha256',
                      'table_references_sha256','migration_table_scope_sha256'):
            with self.subTest(field=field):
                fields,corpus,target,refs=envelope_fixture()
                corpus['bootstrap_envelope'][field]='f'*64
                with self.assertRaises(p.RehearsalError):
                    q.validate_bootstrap_envelope(fields,corpus,target,refs)
        fields,corpus,target,refs=envelope_fixture()
        corpus['bootstrap_envelope']['counter_sources']['disk_bytes']['source']='filesystem-wide free space'
        with self.assertRaises(p.RehearsalError):
            q.validate_bootstrap_envelope(fields,corpus,target,refs)

    def test_missing_probe_or_restarted_target_cannot_be_certified(self):
        fields,corpus,target,refs=envelope_fixture()
        del corpus['pending_table_scope_probe']
        with self.assertRaises(p.ManualRelease):
            q.validate_bootstrap_envelope(fields,corpus,target,refs)
        fields,corpus,target,refs=envelope_fixture()
        corpus['target_container_inspections']['app']['RestartCount']=1
        with self.assertRaises(p.RehearsalError):
            q.validate_bootstrap_envelope(fields,corpus,target,refs)


class TransactionPathTests(unittest.TestCase):
    def test_request_log_reference_requires_a_product_ingested_sample(self):
        entry = next(row for row in p.load_high_volume_registry() if row['table']=='agentcc_request_log')
        driver = object.__new__(q.QualificationRehearsal)
        driver.installation = SimpleNamespace(env={'AGENTCC_WEBHOOK_SECRET':'unit-secret'})
        driver.witnesses = SimpleNamespace(namespaces={'source':{'headers':{'X-Organization-Id':'unit-org'}}},
            _http=mock.Mock(return_value={'ingested':1}))
        driver.app_python = mock.Mock(return_value={'seeded_rows':100000})
        driver.corpus = {}
        driver.seed_reference_table(entry, 100000)
        self.assertEqual(driver.witnesses._http.call_args.args, ('/agentcc/webhook/logs/',))
        payload = driver.witnesses._http.call_args.kwargs['payload']['logs'][0]
        self.assertEqual(payload['metadata'], {'org_id':'unit-org','qualification_fixture':True})
        self.assertEqual((payload['model'],payload['provider']), ('',''))
        self.assertEqual(driver.app_python.call_args.args[1]['sample_request_id'], payload['request_id'])
        self.assertNotIn('unit-secret', json.dumps(driver.corpus))
        driver.witnesses._http.return_value = {'ingested':0}
        driver.app_python.reset_mock()
        with self.assertRaises(p.RehearsalError):
            driver.seed_reference_table(entry, 100000)
        driver.app_python.assert_not_called()

    def test_unexpected_forward_failure_recovers_under_lock_and_stays_red(self):
        driver = object.__new__(q.QualificationRehearsal)
        held = [False]
        @contextmanager
        def lock():
            held[0] = True
            try: yield
            finally: held[0] = False
        error = p.RehearsalError('synthetic forward failure')
        @contextmanager
        def failing_start():
            raise error
            yield
        driver.locks = lock
        driver.forward_budget = nullcontext
        driver.running_target = failing_start
        driver.record = mock.Mock()
        driver.recover = mock.Mock(side_effect=lambda exc: self.assertTrue(held[0]))
        with self.assertRaises(p.RehearsalError) as caught:
            driver.run_forward_and_failure()
        self.assertIs(caught.exception, error)
        driver.recover.assert_called_once_with(error)
        self.assertFalse(held[0])

    def test_forward_budget_is_restored_before_recovery_even_on_failure(self):
        driver = object.__new__(q.QualificationRehearsal)
        driver.runner = SimpleNamespace(deadline=100)
        driver.deadline = 100
        driver.policy = SimpleNamespace(required_recovery_budget_seconds=30)
        with mock.patch.object(q.time, 'monotonic', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'forward failed'):
                with driver.forward_budget():
                    self.assertEqual(driver.runner.deadline, 70)
                    raise RuntimeError('forward failed')
        self.assertEqual(driver.runner.deadline, 100)

    def test_next_fault_case_can_run_without_reserving_all_hypothetical_maxima(self):
        with tempfile.TemporaryDirectory() as temporary:
            driver = object.__new__(q.QualificationRehearsal)
            driver.output = Path(temporary)
            driver.deadline = 15
            driver.policy = SimpleNamespace(required_cold_budget_seconds=10)
            driver.child_process = mock.Mock(side_effect=RuntimeError('unit boundary stop'))
            with mock.patch.object(q.time, 'monotonic', return_value=0), \
                 self.assertRaisesRegex(RuntimeError, 'unit boundary stop'):
                driver.interruption_restore()
            driver.child_process.assert_called_once()
            summary = json.loads((driver.output/'interruption-summary.json').read_bytes())
            self.assertEqual(summary['boundaries'], [])
            self.assertEqual(summary['unrun'], list(milestones.INTERRUPTION_BOUNDARIES))

    def test_integrity_attempt_does_not_overwrite_primary_projections(self):
        with tempfile.TemporaryDirectory() as temporary:
            driver = object.__new__(q.QualificationRehearsal)
            driver.evidence_root = Path(temporary)
            primary = driver.evidence_root/'primary'; primary.mkdir()
            driver.output = primary
            original = primary/'snapshot-proof.json'; original.write_bytes(b'primary-proof')
            driver.take_snapshot = lambda: (driver.output/'snapshot-proof.json').write_bytes(b'integrity-proof')
            driver.verify_snapshot = mock.Mock()
            driver.check = mock.Mock()  # This test checks destinations, not suite outcomes.
            driver.measurements = lambda: {}
            driver.row = SimpleNamespace(binding=lambda: {})
            driver.integrity_suite()
            self.assertEqual(original.read_bytes(), b'primary-proof')
            self.assertEqual((driver.evidence_root/'integrity/snapshot-proof.json').read_bytes(), b'integrity-proof')


class ExactPairPlanTests(unittest.TestCase):
    def test_host_and_producer_hash_the_new_complete_helper_closure(self):
        from deploy.update.host_facts import executor_implementation_sha256
        from scripts.build_update_compatibility_proof import implementation_inventory, inventory_hash
        inventory = implementation_inventory()
        for module in ('qualification', 'resources', 'spool', 'milestone'):
            self.assertIn('scripts/update_rehearsal_'+module+'.py', inventory)
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary).resolve()
            for name in inventory:
                path = root/name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.write_bytes((p.ROOT/name).read_bytes())
                path.chmod(0o600)
            self.assertEqual(executor_implementation_sha256(root), inventory_hash(inventory))

    def test_exact_executor_assembles_the_authenticated_retained_map_without_docker(self):
        from deploy.tests.test_update_qualify_assembly import SyntheticExecutor
        from deploy.update.manifest import hash_path
        from deploy.update.standalone_backup import BINDS
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary).resolve()
            source = root/'retained'
            _, execution = milestones.archive_deployment(source)
            fixture = SyntheticExecutor()
            old = copy.deepcopy(fixture.row.release.manifest)
            release = SimpleNamespace(**vars(fixture.row.release), version=old['version'])
            row = replace(fixture.row, release=release, execution_deployment=execution, deployment_directory=source)
            output = root/'out'; output.mkdir(mode=0o700)
            args = SimpleNamespace(out=output, docker_context='colima-fagi-update', boot_timeout=4200,
                budgets=p.Budgets(), row=row, target=fixture.target, registry=p.load_high_volume_registry(),
                qualification=None, deadline=time.monotonic()+19800)
            driver = q.QualificationRehearsal(args)
            from scripts.update_rehearsal_spool import ObservedSpoolWitness
            ObservedSpoolWitness(driver)  # Constructor ownership contract only; no product operations.
            with mock.patch.object(driver, '_docker') as docker:
                driver.assemble()
            docker.assert_not_called()
            self.assertEqual(driver.source_version, old['version'])
            self.assertEqual(driver.descriptor, old)
            self.assertEqual(driver.execution, execution)
            for name,digest in execution['files'].items():
                self.assertEqual(hash_path(driver.root,name),digest)
            for name in BINDS['clickhouse'].values():
                self.assertEqual((driver.root/name).stat().st_mode & 0o777,0o644)
            self.assertFalse((driver.root/'.env').exists())
            for module in ('qualification', 'resources', 'spool', 'milestone'):
                self.assertEqual((driver.root/('scripts/update_rehearsal_'+module+'.py')).read_bytes(),
                                 (p.ROOT/('scripts/update_rehearsal_'+module+'.py')).read_bytes())

    def test_full_plan_is_exact_pair_and_dry_run_does_not_execute(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            args=SimpleNamespace(out=Path(temporary)/'plan',dry_run=True,platform='linux/amd64',boot_timeout=4200,
                source_ref='futureagi/standalone:v1.54.0@sha256:'+'a'*64,
                target_ref='futureagi/standalone:v1.55.0@sha256:'+'b'*64)
            with mock.patch.object(q,'QualificationRehearsal') as driver:
                self.assertEqual(milestones.run_milestone('qualification',args),0)
            driver.assert_not_called()
            plan=json.loads((args.out/'job-plan.json').read_bytes())
            self.assertEqual(plan['source_image'],args.source_ref)
            self.assertEqual(plan['target_image'],args.target_ref)
            self.assertEqual(plan['qualification_suites'],p.SIX_SUITES_PLANNED)
            self.assertFalse(plan['eligible']);self.assertFalse(plan['qualification_evidence'])
            for name in ('proof-row.json','policy.json','evidence.json'):
                self.assertFalse((args.out/name).exists())

    def test_unpinned_pair_and_non_native_platform_are_not_advertised(self):
        args=SimpleNamespace(source_ref='futureagi/standalone:v1.54.0',
            target_ref='futureagi/standalone:v1.55.0@sha256:'+'b'*64,platform='linux/amd64',boot_timeout=4200)
        with self.assertRaises(p.RehearsalError): q.qualification_plan(args)
        args.source_ref+='@sha256:'+'a'*64
        args.platform='linux/arm64'
        with self.assertRaises(p.RehearsalError): q.qualification_plan(args)

    def test_full_executor_is_not_a_bounded_through_bypass(self):
        with tempfile.TemporaryDirectory() as temporary,self.assertRaises(SystemExit):
            p.qualify(['--through','qualification','--dry-run','--out',str(Path(temporary)/'unused')])


if __name__ == '__main__':
    unittest.main()


class TransferSpanTests(unittest.TestCase):
    def test_signed_restore_span_is_exactly_one_shipping_storage_restore(self):
        from deploy.update.standalone_restore import StandaloneRestore
        original = StandaloneRestore.resume
        with mock.patch.object(StandaloneRestore, 'resume', lambda self, *a, **k: 'restored'):
            with q.QualificationRehearsal.storage_restore_span(object()) as interval:
                with self.assertRaisesRegex(p.RehearsalError, 'exactly once'):
                    interval()
                self.assertEqual(StandaloneRestore.resume(None), 'restored')
                started, completed = interval()
                self.assertLess(started, completed)
                StandaloneRestore.resume(None)
                with self.assertRaisesRegex(p.RehearsalError, 'exactly once'):
                    interval()
            self.assertEqual(StandaloneRestore.resume(None), 'restored')
        self.assertIs(StandaloneRestore.resume, original)

    def test_helper_seconds_accumulate_every_role_operation_without_changing_role_timing(self):
        from deploy.update.standalone_restore import _StorageHelpers
        original = _StorageHelpers.execute
        timings, seconds = p.VolumeRoleTransfers(), {}
        with mock.patch.object(_StorageHelpers, 'execute', lambda helper, role, operation, *a, **k: operation):
            with q.QualificationRehearsal.transfer_roles(object(), 'extract', timings, seconds):
                for role in p.VOLUME_ROLES:
                    for operation in ('clear', 'extract', 'sync', 'clear'):
                        self.assertEqual(_StorageHelpers.execute(None, role, operation), operation)
        self.assertEqual(set(timings.timings), set(p.VOLUME_ROLES))
        self.assertEqual({role: set(row) for role, row in seconds.items()},
                         {role: {'clear', 'extract', 'sync'} for role in p.VOLUME_ROLES})
        self.assertTrue(all(value >= 0 for row in seconds.values() for value in row.values()))
        self.assertIs(_StorageHelpers.execute, original)


class AppDataReferencePayloadTests(unittest.TestCase):
    def run_operation(self, root, name, size):
        import subprocess, sys
        source = q.APP_DATA_REFERENCE_OPERATION.replace("os.path.join('/data', a['name'])",
                                                        "os.path.join(%r, a['name'])" % str(root))
        self.assertNotEqual(source, q.APP_DATA_REFERENCE_OPERATION)
        return subprocess.run([sys.executable, '-I', '-B', '-c', source], input=json.dumps(
            dict(name=name, bytes=size)).encode(), capture_output=True, timeout=60)

    def test_payload_is_a_real_fully_written_file_and_never_overwrites(self):
        import hashlib
        self.assertEqual(q.APP_DATA_REFERENCE_BYTES, 256 * 1024 * 1024)
        self.assertTrue(q.APP_DATA_REFERENCE_NAME.startswith('.') and '/' not in q.APP_DATA_REFERENCE_NAME)
        self.assertNotIn(q.APP_DATA_REFERENCE_NAME, ('.bootstrap-ok', 'temporal', 'objects', 'collector', 'telemetry'))
        with tempfile.TemporaryDirectory() as directory:
            size = 3 * 1024 * 1024 + 17
            result = self.run_operation(directory, q.APP_DATA_REFERENCE_NAME, size)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout.decode().strip().splitlines()[-1])
            payload = (Path(directory) / q.APP_DATA_REFERENCE_NAME).read_bytes()
            self.assertEqual((report['bytes'], len(payload)), (size, size))
            self.assertGreaterEqual(report['allocated'], size)
            self.assertEqual(report['sha256'], hashlib.sha256(payload).hexdigest())
            self.assertNotEqual(payload[:4096], bytes(4096))
            again = self.run_operation(directory, q.APP_DATA_REFERENCE_NAME, size)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual((Path(directory) / q.APP_DATA_REFERENCE_NAME).read_bytes(), payload)
