"""Synthetic CONTRACT/TRANSPORT fixtures, never product image qualification.

This module needs only stdlib; the explicit update_e2e.test_automatic_publisher
lane separately runs real private-CA TLS and host cosign verification. No Docker,
registry writes, production signatures, host facts or scheduler installation.
A synthetic proof tests binding/rejection, not compatibility of any image.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from deploy.update.compatibility import (CompatibilityError, SUITES, authorize_execution, inputs_hash,
                                         topology_profile, validate_release_proof)
from deploy.update.manifest import canonical_json, manifest_hash
from deploy.tests.test_update_compatibility import default_v2, restore_release
from scripts import build_update_compatibility_proof as packager
from scripts import rehearse_update_compatibility as producer


def versioned(manifest, version, *, deployment=None):
    result = copy.deepcopy(manifest)
    old = result['version']
    result['version'] = version
    result['channel'] = 'beta' if '-' in version else 'stable'
    result['source_sha'] = hashlib.sha256(version.encode()).hexdigest()[:40]
    result['helm']['version'] = version[1:]
    result['release_notes'] = result['release_notes'].replace(old,version)
    for component in result['components'].values():
        component['image'] = component['image'].replace(':'+old,':'+version)
    if deployment is not None:
        result['deployment'] = copy.deepcopy(deployment)
    return result


def fixture(directory, *, previous=None, version='v1.52.0', execution=None, pending_count=3):
    """Deliberately authored positive XML, labeled synthetic at every boundary."""
    target, old_raw, proof, _ = restore_release(pending_count)
    old = json.loads(old_raw) if previous is None else previous
    execution = copy.deepcopy(execution or old['deployment'])
    candidate = versioned(default_v2(),version)
    candidate['deployment']['files']['.env.example'] = hashlib.sha256(version.encode()).hexdigest()
    policy = {'schema_version':1,'migration':copy.deepcopy(target['migration']),
              'compatibility':copy.deepcopy(target['compatibility'])}
    compatibility = policy['compatibility']
    compatibility.update(contract_version=2,validation=None)
    predecessor = compatibility['predecessors'][0]
    predecessor.update(version=old['version'],source_sha=old['source_sha'],manifest_hash=manifest_hash(old),
                       raw_manifest_sha256=hashlib.sha256(canonical_json(old)).hexdigest(),
                       execution_deployment_hash=manifest_hash(execution))
    approved = copy.deepcopy(candidate)
    approved.update(migration=policy['migration'],compatibility=compatibility)
    binding = {key:copy.deepcopy(proof['predecessors'][0][key]) for key in ('dependencies','persistence')}
    binding.update(predecessor=predecessor,execution_deployment=execution)
    implementation = packager.implementation_inventory()
    for key in packager.MEASURED_FIELDS:
        binding[key] = copy.deepcopy(proof['predecessors'][0][key])
    binding['executor_sha256'] = packager.inventory_hash(implementation)
    common = {'inputs_hash':inputs_hash(approved),'repository':producer.REPOSITORY,'run_id':'42',
              'candidate_sha256':hashlib.sha256(canonical_json(candidate)).hexdigest(),
              'producer_sha256':hashlib.sha256(Path(producer.__file__).read_bytes()).hexdigest(),'lane':'synthetic',
              'executor_sha256':binding['executor_sha256'],
              'high_volume_registry_sha256':hashlib.sha256((producer.ROOT/'deploy/update/high-volume-tables.json').read_bytes()).hexdigest()}
    directory.mkdir()
    corpus = canonical_json({'purpose':'SYNTHETIC-CONTRACT-ONLY-NOT-PRODUCT-EVIDENCE'})
    (directory/'synthetic-corpus.json').write_bytes(corpus)
    binding['evidence_inventory'] = {'synthetic-corpus.json':hashlib.sha256(corpus).hexdigest()}
    binding['evidence_inventory_sha256'] = packager.inventory_hash(binding['evidence_inventory'])
    (directory/(predecessor['raw_manifest_sha256']+'.json')).write_bytes(canonical_json(old))
    reports = {}
    for suite in SUITES:
        cases = packager.required_cases(suite,topology_profile('standalone',()))
        root = ET.Element('testsuite',name=suite,tests=str(len(cases)),failures='0',errors='0',skipped='0')
        properties = ET.SubElement(root,'properties')
        for key,value in dict(common,binding_hash=manifest_hash(binding)).items():
            ET.SubElement(properties,'property',name=key,value=value)
        for name in sorted(cases):
            ET.SubElement(root,'testcase',name=name,classname=suite,assertions='1')
        raw = ET.tostring(root)
        name = suite+'.xml'
        (directory/name).write_bytes(raw)
        reports[suite] = {'path':name,'sha256':hashlib.sha256(raw).hexdigest()}
    envelope = dict(common,schema_version=2,run_id=42,rows=[dict(binding,reports=reports)],
                    executor_inventory=implementation)
    (directory/'evidence.json').write_bytes(canonical_json(envelope))
    raw, certified = packager.build(candidate,policy,directory,run_id=42,lane='synthetic')
    return candidate,policy,envelope,json.loads(certified),json.loads(raw)


class ProducerFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root/'evidence'
        self.candidate,self.policy,self.envelope,self.certified,self.proof = fixture(self.evidence)

    def build(self, **kwargs):
        return packager.build(self.candidate,self.policy,self.evidence,run_id=42,**dict(lane='synthetic',**kwargs))

    def save(self):
        (self.evidence/'evidence.json').write_bytes(canonical_json(self.envelope))


class RetainedLayoutProducerTests(ProducerFixture):
    def test_v2_exact_retained_map_and_authenticated_published_map_are_distinct(self):
        raw,_ = self.build()
        row = self.proof['predecessors'][0]
        previous = (self.evidence/(row['predecessor']['raw_manifest_sha256']+'.json')).read_bytes()
        self.assertNotEqual(row['execution_deployment'],self.certified['deployment'])
        authorization = authorize_execution(canonical_json(self.certified),previous,raw,
            repository=producer.REPOSITORY,mode='standalone',profiles=(),platform='linux/amd64',
            deployment_files=row['execution_deployment']['files'])
        self.assertEqual(dict(authorization.execution_deployment['files']),row['execution_deployment']['files'])
        from deploy.update.compatibility import shape, schema
        shape(self.proof,schema('compatibility-proof.schema.json'))

    def test_B_to_C_preserves_A_layout_but_authenticates_B_published_map(self):
        _,_,_,c,p = fixture(self.root/'c',previous=self.certified,version='v1.53.0',
                            execution=self.proof['predecessors'][0]['execution_deployment'])
        row = p['predecessors'][0]
        self.assertEqual(row['predecessor_deployment'],self.certified['deployment'])
        self.assertNotEqual(row['predecessor_deployment'],row['execution_deployment'])
        self.assertEqual(row['predecessor']['raw_manifest_sha256'],hashlib.sha256(canonical_json(self.certified)).hexdigest())
        validate_release_proof(canonical_json(p),c,repository=producer.REPOSITORY)

    def test_each_layout_digest_row_and_producer_binding_change_refused(self):
        original = copy.deepcopy(self.envelope)
        changes = [(('rows',0,'execution_deployment','files','.env.example'),'e'*64),
                   (('rows',0,'predecessor','execution_deployment_hash'),'e'*64),
                   (('rows',0,'dependencies',0,'digest'),'sha256:'+'e'*64),
                   (('candidate_sha256',),'e'*64),(('producer_sha256',),'e'*64),
                   (('run_id',),43),(('lane',),'product')]
        for path,value in changes:
            self.envelope = copy.deepcopy(original)
            dest = self.envelope
            for key in path[:-1]:
                dest = dest[key]
            dest[path[-1]] = value
            self.save()
            with self.subTest(path=path),self.assertRaises(CompatibilityError):
                self.build()

    def test_every_suite_and_every_case_must_execute(self):
        original = copy.deepcopy(self.envelope)
        for suite in SUITES:
            self.envelope = copy.deepcopy(original)
            del self.envelope['rows'][0]['reports'][suite]
            self.save()
            with self.subTest(suite=suite),self.assertRaises(CompatibilityError):
                self.build()
        for child in ('failure','error','skipped'):
            self.envelope = copy.deepcopy(original)
            report = self.envelope['rows'][0]['reports'][SUITES[0]]
            path = self.evidence/report['path']
            original_xml = path.read_bytes()
            root = ET.fromstring(original_xml)
            ET.SubElement(root.find('testcase'),child)
            raw = ET.tostring(root); path.write_bytes(raw)
            report['sha256'] = hashlib.sha256(raw).hexdigest(); self.save()
            with self.subTest(child=child),self.assertRaises(CompatibilityError):
                self.build()
            path.write_bytes(original_xml)

    def test_synthetic_reports_are_refused_by_default_product_packaging(self):
        with self.assertRaises(CompatibilityError):
            packager.build(self.candidate,self.policy,self.evidence,run_id=42)

    def test_failed_or_unrun_assertion_cannot_be_rendered(self):
        result = producer.ExecutedCases()
        with self.assertRaisesRegex(producer.RehearsalError,'unrun'):
            result.render('migration_forward',topology_profile('standalone',()),{})
        def failure():
            raise RuntimeError('executed failure')
        with self.assertRaises(RuntimeError):
            result.check('migration_forward','pending_plan_measured',failure)
        self.assertEqual(result.cases['migration_forward'],{})
        with self.assertRaises(producer.RehearsalError):
            producer.measured_policy([],[])

    def test_classified_manual_release_never_emits_affirmative_metadata(self):
        candidate = self.candidate
        decision = {'schema_version':1,'eligible':False,'reason':'suite_unrun',
                    'candidate_sha256':hashlib.sha256(canonical_json(candidate)).hexdigest(),
                    'run_id':42,'repository':producer.REPOSITORY}
        raw = packager.manual_candidate(candidate,decision,run_id=42)
        self.assertEqual(json.loads(raw),candidate)
        self.assertFalse(json.loads(raw)['compatibility']['automatic_apply'])
        for reason in ('failed','skipped','unrun','waiver'):
            with self.subTest(reason=reason),self.assertRaises(CompatibilityError):
                packager.manual_candidate(candidate,dict(decision,reason=reason),run_id=42)
        with self.assertRaises(CompatibilityError):
            packager.manual_candidate(candidate,decision,run_id=43)
        with self.assertRaises(CompatibilityError):
            packager.manual_candidate(self.certified,dict(decision,candidate_sha256=hashlib.sha256(
                canonical_json(self.certified)).hexdigest()),run_id=42)

    def test_required_cases_cover_all_persistent_domains_and_protocols(self):
        layout = topology_profile('standalone',())
        for domain in layout.backup_domains:
            self.assertIn('new_reads_old:'+domain,packager.required_cases('migration_forward',layout))
            self.assertIn('restored_witness:'+domain,packager.required_cases('failure_restore',layout))
            self.assertIn('restored_witness:'+domain,packager.required_cases('interruption_restore',layout))
        self.assertEqual(set(SUITES), {'source_snapshot','migration_forward','target_readiness',
                                     'failure_restore','interruption_restore','backup_integrity'})
        self.assertNotIn('second_startup', packager.required_cases('target_readiness',layout))


def complete_snapshot_fields(fields):
    """Expand small schema-only fixtures into a fully retained synthetic layout."""
    from deploy.update.standalone_backup import CONFIG_FILES
    for name in CONFIG_FILES[1:]:
        fields['execution_deployment']['files'].setdefault(name, producer.sha(('SYNTHETIC-'+name).encode()))
    fields['predecessor']['execution_deployment_hash'] = manifest_hash(fields['execution_deployment'])
    return fields


def snapshot_evidence_fixture(fields=None, *, target=None, predecessor_raw=None, volume_bytes=None):
    """Complete SYNTHETIC shipping-format evidence, never product qualification.

    Unlike the old three-field snapshot this supplies all retained source inputs,
    original identities, seal/proof and normalized member trees. Every expected
    transition input still comes from the caller, not these self-authored bytes.
    """
    from dataclasses import fields as members
    from datetime import datetime, timezone
    from deploy.update.contracts import (InstallationBinding, RuntimeImage, RuntimeObservationV2,
                                         installation_binding_hash, RESTORE_PROCEDURE_ID)
    from deploy.update.standalone_backup import CONFIG_FILES, BINDS, VOLUMES
    from deploy.update.probes.restore_fs import canonical as canonical_tree
    from deploy.update.manifest import parse_image_ref
    _, default_raw, proof, _ = restore_release(pending_count=1)
    fields = fields or complete_snapshot_fields(proof['predecessors'][0])
    old = json.loads(predecessor_raw or default_raw)
    target = target or default_v2()
    volume_bytes = volume_bytes or fields['backup_measurement']['reference_volume_bytes']
    stamp = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    root = Path('/SYNTHETIC-NO-HOST-READS/fagi-upd-e2e-release-unit')
    project = 'fagi-upd-e2e-release-unit'
    registered = InstallationBinding('1'*32, '2'*64, '3'*64, root, root/'.env', root/'.env',
        'standalone', False, project, (root/'docker-compose.yml',), (), 'colima-fagi-update', 'synthetic-daemon', 501)
    ids = dict(app='sha256:'+'a'*64, postgres='sha256:'+'b'*64, clickhouse='sha256:'+'c'*64)
    expected_images = dict(app=(old['components']['standalone']['image'], old['components']['standalone']['digest']))
    expected_images.update({d['service']: (d['image'], d['digest']) for d in fields['dependencies']})
    runtime_images = {s:(RuntimeImage(image, digest, ids[s]),) for s,(image,digest) in expected_images.items()}
    files = {name: fields['execution_deployment']['files'].get(name, producer.sha(('SYNTHETIC-'+name).encode())) for name in CONFIG_FILES}
    binding = dict(schema_version=1, installation_fingerprint=registered.installation_fingerprint, root=str(root),
        context=registered.docker_context, daemon=registered.daemon_identity, project=project, profiles=[], images=ids,
        volumes={}, volume_root_ids={role:'1:'+str(i+1) for i,role in enumerate(VOLUMES)}, files=files,
        compose_sha256='e'*64)
    containers = {}
    for index,(role,(service,destination)) in enumerate(VOLUMES.items(),1):
        volume = dict(Name=project+'_'+role, Driver='local', Scope='local', Options=None,
            CreatedAt='2026-10-27T00:00:00Z', Mountpoint='/var/lib/docker/volumes/'+project+'_'+role+'/_data',
            Labels={'com.docker.compose.project':project,'com.docker.compose.volume':role})
        binding['volumes'][role] = volume
        mounts = [dict(Type='volume',Name=volume['Name'],Source=volume['Mountpoint'],Destination=destination,RW=True)]
        mounts += [dict(Type='bind',Source=p if p=='/dev/null' else str(root/p),Destination=d,RW=False) for d,p in BINDS[service].items()]
        containers[service] = dict(Id=str(index)*64, Image=ids[service], Created='2026-10-27T00:00:00Z', Mounts=mounts,
            Config={'Env':[], 'Labels':{'com.docker.compose.project':project,'com.docker.compose.service':service,
                'com.docker.compose.oneoff':'False','com.docker.compose.project.working_dir':str(root),
                'com.docker.compose.project.config_files':str(root/'docker-compose.yml')}},
            HostConfig={'RestartPolicy':{'Name':'unless-stopped'}})
    identities = {s:dict(id=c['Id'], image=c['Image'], started='2026-10-27T00:00:00Z',
        mounts=sorted(copy.deepcopy(c['Mounts']),key=lambda m:m['Destination']), config=copy.deepcopy(c['Config']),
        restart_count=0, restart_policy='unless-stopped', bind_hashes={}, volume=copy.deepcopy(binding['volumes'][role]),
        published_ports={}, network={'id':'9'*64,'name':project+'_default','driver':'bridge',
            'containers':sorted(row['Id'] for row in containers.values())})
        for role,(s,_) in VOLUMES.items() for c in [containers[s]]}
    storage = {role:dict(name=v['Name'], root_identity=binding['volume_root_ids'][role], device='1:1',
        filesystem='ext4',backing_sha256='8'*64) for role,v in binding['volumes'].items()}
    configuration = dict(config={'name':project,'services':{}},env={},files={},environment={},updater_config=None)
    binding['compose_sha256'] = manifest_hash(configuration['config'])
    preimages = dict(configuration=configuration,containers=identities,volume_storage=storage)
    volumes_hash = {s:manifest_hash(dict(daemon=binding['daemon'],name=i['volume']['Name'],mountpoint=i['volume']['Mountpoint'],
        created_at=i['volume']['CreatedAt'],driver=i['volume']['Driver'],readonly_config=i['bind_hashes'])) for s,i in identities.items()}
    migration = fields['migration_evidence']
    source = RuntimeObservationV2(release_version=old['version'],manifest_sha256=manifest_hash(old),observed_at=stamp,
        binding_sha256=installation_binding_hash(registered),platform=fields['predecessor']['platform'],
        product_images={'app':runtime_images['app']},dependency_images={s:runtime_images[s] for s in ('postgres','clickhouse')},
        migration_inventory_sha256=migration['source_inventory_sha256'],applied_migrations_sha256=migration['source_applied_set_sha256'],
        active_app_mapping_sha256=migration['active_app_mapping_sha256'],deployment_sha256=manifest_hash(fields['execution_deployment']),
        env_sha256=files['.env'],config_sha256=manifest_hash(configuration),
        storage_sha256=manifest_hash({'volumes':volumes_hash,'roots':manifest_hash(storage)}),
        container_roster_sha256=manifest_hash(identities),
        restart_counts={c['Id']:0 for c in containers.values()},restart_policies={c['Id']:'unless-stopped' for c in containers.values()},
        ready=True,stable=True)
    def wire(value):
        from collections.abc import Mapping
        if hasattr(value,'__dataclass_fields__'): return {f.name:wire(getattr(value,f.name)) for f in members(value)}
        if isinstance(value,Mapping): return {k:wire(v) for k,v in value.items()}
        if isinstance(value,(tuple,list)): return [wire(v) for v in value]
        if isinstance(value,(datetime,Path)): return value.isoformat() if isinstance(value,datetime) else str(value)
        return value
    expectation = dict(attempt_id='7'*32,installation_fingerprint=registered.installation_fingerprint,
        target_manifest_hash=manifest_hash(target),source_version=old['version'],reserved_at=stamp.isoformat(),
        covered_domains=list(topology_profile('standalone',()).backup_domains))
    quiescence = dict(containers=containers,start_times={s:'2026-10-27T00:00:00Z' for s in containers},
        restart_counts=dict(source.restart_counts),restart_policies=dict(source.restart_policies),
        daemon=binding['daemon'],volumes=binding['volumes'],roots=binding['volume_root_ids'],snapshot_at='2026-10-27T12:00:00Z')
    trees, archives = {}, {}
    for role,logical in volume_bytes.items():
        rows = [dict(name='.',type='directory',size=0,uid=0,gid=0,mode=0o755,mtime_ns=0,link=None,sha256=None,hardlinks=[],xattrs={},acls={}),
                dict(name='synthetic-record',type='file',size=logical,uid=999,gid=999,mode=0o600,mtime_ns=0,
                     link=None,sha256=producer.sha(b'SYNTHETIC-NOT-PRODUCT'),hardlinks=[],xattrs={},acls={})]
        raw = b''.join(canonical_tree(row)+b'\n' for row in rows)
        trees[role] = rows
        archives[role] = dict(size=((logical+2047)//512)*512,sha256='a'*64,members=2,logical_bytes=logical,
            members_sha256=producer.sha(raw),members_bytes=len(raw),inventory_version=2,volume=binding['volumes'][role])
    snapshot = dict(schema_version=2,inventory_version=2,binding=binding,binding_sha256=source.binding_sha256,expectation=expectation,
        automatic_layout_sha256=producer.sha((producer.ROOT/'deploy/update/automatic-layout.json').read_bytes()),
        source_observation_sha256=source.sha256,quiescence_evidence_sha256=producer.sha(canonical_json(quiescence)),
        quiescence=quiescence,containers=containers,archives=archives,
        config_files=[dict(name=name,file='config-'+str(i),sha256=files[name],size=1,
            metadata=dict(uid=501,gid=20,mode=0o600,identity=[1,i+1,0])) for i,name in enumerate(CONFIG_FILES)],
        started_at='2026-10-27T12:00:00Z',snapshot_at='2026-10-27T12:00:00Z',completed_at='2026-10-27T12:00:00Z',
        restore_procedure_id=RESTORE_PROCEDURE_ID)
    backup_proof = {k:expectation[k] for k in ('attempt_id','installation_fingerprint','target_manifest_hash','source_version','covered_domains')}
    backup_proof.update(schema_version=2,backup_id='standalone-'+expectation['attempt_id'],verification='passed',
        restore_procedure_id=RESTORE_PROCEDURE_ID,inventory_version=2,binding_sha256=source.binding_sha256,
        inventory_sha256=producer.sha(canonical_json(snapshot)),source_observation_sha256=source.sha256,
        quiescence_evidence_sha256=snapshot['quiescence_evidence_sha256'],
        **{k:snapshot[k] for k in ('started_at','snapshot_at','completed_at')})
    seal = {k:backup_proof[k] for k in ('attempt_id','target_manifest_hash','source_version','binding_sha256','inventory_sha256',
        'source_observation_sha256','quiescence_evidence_sha256','snapshot_at','inventory_version','restore_procedure_id')}
    seal.update(operation_token='synthetic-token',run_id='synthetic-engine-run',authorised_at=stamp.isoformat(),
        proof_sha256=producer.sha(canonical_json(backup_proof)))
    return dict(source_snapshot_inventory=snapshot,source_runtime_observation=wire(source),source_installation_binding=wire(registered),
        source_image_inspections={s:dict(Id=ids[s],Os='linux',Architecture=source.platform.split('/')[1],
            RepoDigests=[parse_image_ref(image).repository+'@'+digest]) for s,(image,digest) in expected_images.items()},
        source_backup_proof=backup_proof,source_backup_seal=seal,source_snapshot_members=trees,
        source_observation_preimages=preimages)


def snapshot_fixture():
    return snapshot_evidence_fixture()['source_snapshot_inventory']


def validate_measurements(fields, corpus):
    _, old_raw, _, _ = restore_release(pending_count=1)
    return producer.validate_executed_measurements(fields, corpus, target=default_v2(), predecessor_raw=old_raw)


def transfer_fixture(fields, snapshot=None):
    snapshot = snapshot or snapshot_fixture()
    return dict(reference_volume_bytes=copy.deepcopy(fields['backup_measurement']['reference_volume_bytes']),
                started_monotonic=10,completed_monotonic=13,snapshot_generation=producer.sha(canonical_json(snapshot)),
                measurement_basis=producer.TRANSFER_BASIS)


def refresh_snapshot_hashes(corpus):
    """Adversarial rehash: repeated claims cannot substitute expected inputs."""
    snapshot = corpus['source_snapshot_inventory']
    snapshot['source_observation_sha256'] = manifest_hash(corpus['source_runtime_observation'])
    snapshot['quiescence_evidence_sha256'] = producer.sha(canonical_json(snapshot['quiescence']))
    proof = corpus['source_backup_proof']
    proof.update({k:snapshot['expectation'][k] for k in ('attempt_id','installation_fingerprint','target_manifest_hash','source_version','covered_domains')})
    proof.update({k:snapshot[k] for k in ('binding_sha256','source_observation_sha256','quiescence_evidence_sha256','started_at','snapshot_at','completed_at')})
    generation = producer.sha(canonical_json(snapshot))
    proof['inventory_sha256'] = generation
    seal = corpus['source_backup_seal']
    seal.update({k:proof[k] for k in ('attempt_id','target_manifest_hash','source_version','binding_sha256','inventory_sha256',
        'source_observation_sha256','quiescence_evidence_sha256','snapshot_at')})
    seal['proof_sha256'] = producer.sha(canonical_json(proof))
    corpus['large_volume_reference']['snapshot_generation'] = generation
    for sample in corpus['resource_samples']: sample['snapshot_generation'] = generation
    for role in ('backup','restore'): corpus[role+'_transfer']['snapshot_generation'] = generation


def reference_fixture(extra_tables=()):
    """SYNTHETIC evidence for reducer tests, never shipping qualification."""
    tables = [{'engine': r['database'], 'table': ('public.' if r['database'] == 'postgres' else 'default.') + r['table'],
               'rows': producer.REFERENCE_ROWS, 'bytes': 1000000} for r in producer.load_high_volume_registry()]
    measured = {(r['engine'], r['table']): r for r in tables}
    measured.update({(r['engine'], r['table']): copy.deepcopy(r) for r in extra_tables})
    tables = list(measured.values())
    return {'snapshot_generation': producer.sha(canonical_json(snapshot_fixture())), 'reference_rows': producer.REFERENCE_ROWS,
            'registry_sha256': producer.sha((producer.ROOT/'deploy/update/high-volume-tables.json').read_bytes()),
            'tables': tables}


def timed_samples(samples, duration):
    for index, sample in enumerate(samples):
        sample.update(monotonic=10 + index * duration / max(1, len(samples) - 1),
                      boundary='before_start' if index == 0 else 'after_end' if index == len(samples)-1 else 'during')
    return samples


class MeasurementAndExecutorTests(unittest.TestCase):
    """Small SYNTHETIC reducer/fault fixtures, not product qualification."""

    def test_registry_is_read_only_and_contains_both_engines(self):
        path = producer.ROOT/'deploy/update/high-volume-tables.json'
        before = path.read_bytes()
        rows = producer.load_high_volume_registry()
        self.assertEqual({r['database'] for r in rows}, {'postgres','clickhouse'})
        self.assertEqual(len(rows), len({(r['database'],r['table']) for r in rows}))
        self.assertEqual(path.read_bytes(), before)

    def test_missing_registry_is_explicit_notify_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(producer.ManualRelease,'high_volume_reference_unavailable'):
                producer.load_high_volume_registry(Path(tmp)/'absent.json')

    def test_registry_rejects_duplicates_sql_paths_and_unknown_engines(self):
        valid = {'database':'postgres','table':'synthetic_table','source':'not-executed.py','reason':'synthetic'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'registry.json'
            for entries in ([valid, valid], [dict(valid,table='public.table')],
                            [dict(valid,table='x;DROP')], [dict(valid,database='mysql')]):
                path.write_bytes(canonical_json({'schema_version':1,'table_identity':'synthetic','tables':entries}))
                with self.subTest(entries=entries), self.assertRaises(producer.RehearsalError):
                    producer.load_high_volume_registry(path)

    def test_reference_seeds_every_table_and_independently_measures_scale(self):
        registry = ({'database':'postgres','table':'synthetic_table'},
                    {'database':'clickhouse','table':'synthetic_table'})
        called = []
        def seed(entry, rows):
            called.append((entry['database'],rows))
        def measure(entry):
            return {'engine':entry['database'],'table':'public.'+entry['table'],'rows':100000,'bytes':1000000}
        result = producer.seed_reference_tables(registry,seed,measure)
        self.assertEqual(called,[('postgres',100000),('clickhouse',100000)])
        self.assertEqual(len(result),2)
        for change in ({'rows':99999},{'bytes':0},{'engine':'mysql'}):
            with self.subTest(change=change), self.assertRaises(producer.ManualRelease):
                producer.seed_reference_tables(registry,seed,lambda entry: dict(measure(entry),**change))
        with self.assertRaises(producer.RehearsalError):
            producer.seed_reference_tables(registry,seed,measure,reference_rows=1)

    def test_migration_reducer_keeps_peaks_not_only_final_disk_and_rss(self):
        samples = [{'disk_bytes':1000,'wal_bytes':10,'temp_bytes':0,'rss_bytes':50,'processed_bytes':0},
                   {'disk_bytes':1400,'wal_bytes':30,'temp_bytes':70,'rss_bytes':90,'processed_bytes':100},
                   {'disk_bytes':1100,'wal_bytes':30,'temp_bytes':0,'rss_bytes':30,'processed_bytes':100}]
        result = producer.migration_measurement({'app_label':'synthetic_app','name':'0001_change'},
            [{'table':'public.synthetic_table','reference_rows':50,'reference_bytes':100}],timed_samples(samples, 2),
            engine='postgres',risk_class='rewrite',duration_seconds=2,
            started_monotonic=10, completed_monotonic=12)
        self.assertEqual(result['throughput'],{'unit':'rows_per_second','value':25})
        self.assertEqual(result['peak_disk_amplification_factor'],5)
        self.assertEqual((result['wal_bytes'],result['temp_bytes']),(20,70))
        self.assertEqual(samples[1]['rss_bytes'],90)  # raw samples retained privately, not dropped

    def test_unmeasured_metadata_and_nonfinite_duration_do_not_get_invented_rate(self):
        samples = timed_samples([dict(disk_bytes=1,wal_bytes=0,temp_bytes=0,rss_bytes=1,processed_bytes=0)
                                 for _ in range(2)], 1)
        identity = {'app_label':'synthetic_app','name':'0001_change'}
        for duration in (0,True,float('inf'),float('nan')):
            with self.subTest(duration=duration), self.assertRaises(producer.RehearsalError):
                producer.migration_measurement(identity,[],samples,engine='postgres',risk_class='metadata_only',duration_seconds=duration)
        with self.assertRaisesRegex(producer.ManualRelease,'migration_measurement_unavailable'):
            producer.migration_measurement(identity,[],samples,engine='postgres',risk_class='metadata_only',duration_seconds=1,
                                           started_monotonic=10, completed_monotonic=11)

    def test_signed_metrics_match_complete_raw_plan_memory_and_transfer_timing(self):
        _,_,proof,_ = restore_release(pending_count=1)
        fields = complete_snapshot_fields(proof['predecessors'][0])
        migration = fields['migration_measurements'][0]
        item = {k:copy.deepcopy(migration[k]) for k in ('app_label','name','engine','risk_class','tables','duration_seconds')}
        item['samples'] = [dict(disk_bytes=1000,wal_bytes=0,temp_bytes=0,rss_bytes=100,processed_bytes=0),
                           dict(disk_bytes=2000,wal_bytes=100,temp_bytes=50,rss_bytes=120,processed_bytes=1000)]
        transfer = transfer_fixture(fields)
        # Preserve the wire fixture's exact metrics, adding complete execution
        # timing and independently measured table sizes to its private evidence.
        duration = item['duration_seconds']
        baseline, final = item['samples']
        item['samples'] = timed_samples([baseline] + [dict(final) for _ in range(int(duration))], duration)
        item.update(started_monotonic=10, completed_monotonic=10+duration,
                    snapshot_generation=producer.sha(canonical_json(snapshot_fixture())))
        reference = reference_fixture([dict(engine=item['engine'], table=t['table'],
            rows=t['reference_rows'], bytes=t['reference_bytes']) for t in item['tables']])
        corpus = {'resource_samples':[item],'target_pending_plan':[{k:item[k] for k in ('app_label','name')}],
                  'backup_transfer':transfer,'restore_transfer':copy.deepcopy(transfer),
                  'large_volume_reference':reference, **snapshot_evidence_fixture(fields)}
        validate_measurements(fields,corpus)
        for change in ('rss','order','unrun','transfer'):
            bad = copy.deepcopy(corpus)
            if change=='rss': bad['resource_samples'][0]['samples'][0]['rss_bytes'] = 0
            elif change=='order': bad['target_pending_plan'][0]['name'] = '0000_other'
            elif change=='unrun': bad['resource_samples'] = []
            else: bad['restore_transfer']['completed_monotonic'] = 14
            with self.subTest(change=change), self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,bad)

    def test_transfer_times_actual_operation_and_preserves_logical_roles(self):
        events, times = [], iter((10,12))
        volumes = {v:100 for v in producer.VOLUME_ROLES}
        corpus = snapshot_evidence_fixture(volume_bytes=volumes)
        def capture():
            self.assertEqual(events, ['executed'])  # numerator is captured AFTER backup
            return corpus['source_snapshot_inventory'], corpus['source_snapshot_members']
        result, measured, raw = producer.measure_transfer(lambda: events.append('executed') or 'snapshot',capture,clock=lambda:next(times))
        self.assertEqual((result,events),('snapshot',['executed']))
        self.assertEqual(measured,{'reference_volume_bytes':volumes,'throughput_bytes_per_second':150})
        self.assertEqual(raw, dict(reference_volume_bytes=volumes,started_monotonic=10,completed_monotonic=12,
            snapshot_generation=producer.sha(canonical_json(corpus['source_snapshot_inventory'])),measurement_basis=producer.TRANSFER_BASIS))
        with self.assertRaises(producer.RehearsalError):
            producer.measure_transfer(lambda:None,{'docker_volume_name':100})

    def test_witnesses_require_every_domain_and_real_origins(self):
        origins = {'postgres_app':'product_api','clickhouse':'collector_ingestion','object_store':'bundled_s3_api',
                   'temporal':'completed_product_workflow','shared_files':'product_spool'}
        rows = [dict(domain=d,identity='synthetic-'+d,payload_sha256='1'*64,origin=o) for d,o in origins.items()]
        self.assertEqual(len(producer.witness_inventory(rows)),5)
        with self.assertRaises(producer.ManualRelease):
            producer.witness_inventory(rows[:-1])
        with self.assertRaises(producer.RehearsalError):
            producer.witness_inventory(rows+[rows[0]])
        for domain,origin in (('shared_files','arbitrary_file_named_spool'),('temporal','unfinished_signal')):
            bad = copy.deepcopy(rows)
            next(r for r in bad if r['domain']==domain)['origin'] = origin
            with self.assertRaises(producer.RehearsalError):
                producer.witness_inventory(bad)

    def test_only_executed_assertions_are_recorded_and_verdicts_refuse(self):
        cases = producer.ExecutedCases()
        for value in (True,False,'passed',{'passed':True}):
            with self.assertRaises(producer.RehearsalError):
                cases.check('target_readiness','health',lambda:value)
        self.assertEqual(cases.cases['target_readiness'],{})
        cases.check('target_readiness','health',lambda:None)
        with self.assertRaises(producer.RehearsalError):
            cases.check('target_readiness','health',lambda:None)

    def test_suite_timeout_and_incomplete_callback_set_never_render(self):
        executor = mock.Mock()
        executor.suite_assertions.return_value = {}
        with self.assertRaisesRegex(producer.ManualRelease,'suite_timed_out'):
            producer.exercise(executor,deadline=0)
        with self.assertRaisesRegex(producer.RehearsalError,'unrun'):
            producer.exercise(executor,deadline=float('inf'))

    def test_missing_shipping_executor_has_no_manual_restore_substitute(self):
        exc = ModuleNotFoundError('missing',name='scripts.update_rehearsal_shipping')
        with mock.patch.object(producer.importlib,'import_module',side_effect=exc):
            with self.assertRaisesRegex(producer.ManualRelease,'shipping_executor_unavailable'):
                producer.shipping_factory()
        with mock.patch.object(producer.importlib,'import_module',return_value=object()):
            with self.assertRaises(producer.ManualRelease):
                producer.shipping_factory()

    def test_missing_executor_keeps_outputs_notify_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(producer,'_validate_context'), mock.patch.object(producer,'shipping_factory',
                    side_effect=producer.ManualRelease('shipping_executor_unavailable')):
                with self.assertRaises(producer.ManualRelease):
                    producer.produce(default_v2(),[],root,root/'evidence',run_id=42)
            self.assertEqual({p.name for p in (root/'evidence').iterdir()},{'failure.json'})
            failed = json.loads((root/'evidence/failure.json').read_bytes())
            self.assertFalse(failed['eligible'])
            self.assertEqual(failed['completed_rows'],0)
            self.assertEqual(failed['reason'],'shipping_executor_unavailable')

    def test_synthetic_pr_executor_exercises_envelope_but_cannot_be_shipping_evidence(self):
        # This in-memory adapter exercises producer plumbing ONLY. It never
        # opens Docker or produces product-lane evidence, even on a positive run.
        from types import SimpleNamespace
        from deploy.update.apply import Updater
        from deploy.update.standalone_restore import StandaloneRestore
        target, old_raw, proof, _ = restore_release(pending_count=0)
        candidate = default_v2()
        old = json.loads(old_raw)
        row = producer.Row(SimpleNamespace(manifest=old,raw_manifest=old_raw,raw_bundle=b'SYNTHETIC-NOT-A-SIGNATURE'),
                           copy.deepcopy(old['deployment']),old['source_sha'],tuple(proof['predecessors'][0]['dependencies']))
        values = {k:copy.deepcopy(proof['predecessors'][0][k]) for k in packager.MEASURED_FIELDS
                  if k not in ('executor_sha256','evidence_inventory_sha256')}
        origins = {'postgres_app':'product_api','clickhouse':'collector_ingestion','object_store':'bundled_s3_api',
                   'temporal':'completed_product_workflow','shared_files':'product_spool'}
        witnesses = [dict(domain=d,identity='SYNTHETIC-CONTRACT-ONLY-'+d,payload_sha256='1'*64,origin=o)
                     for d,o in origins.items()]
        binding = row.binding()
        binding.update(values)
        complete_snapshot_fields(binding)
        row.execution_deployment['files'].update(binding['execution_deployment']['files'])
        snapshot_corpus = snapshot_evidence_fixture(binding, target=candidate, predecessor_raw=old_raw)
        generation = producer.sha(canonical_json(snapshot_corpus['source_snapshot_inventory']))
        transfer = transfer_fixture(values, snapshot_corpus['source_snapshot_inventory'])
        class SyntheticExecutor:
            shipping_executor_class, shipping_restore_class = Updater, StandaloneRestore
            dependencies = row.dependencies
            cleanup_verified = False
            def __enter__(self): return self
            def __exit__(self,*args): self.cleanup_verified = True
            def seed_reference_table(self,entry,rows):
                if rows != 100000: raise AssertionError('wrong synthetic scale')
            def measure_reference_table(self,entry):
                return {'engine':'postgres','table':'public.synthetic_table','rows':100000,'bytes':1000000}
            def suite_assertions(self,suite):
                return {name:lambda:None for name in packager.required_cases(suite,topology_profile('standalone',()))}
            def measurements(self): return values
            def private_evidence(self):
                reference = reference_fixture([{'engine':'postgres','table':'public.synthetic_table','rows':100000,'bytes':1000000}])
                reference['snapshot_generation'] = generation
                return {'source_witnesses':witnesses,'restored_witnesses':witnesses,
                        'resource_samples':[],'target_pending_plan':[],
                        'backup_transfer':transfer,'restore_transfer':transfer,
                        **snapshot_corpus, 'large_volume_reference':reference}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'evidence'
            with mock.patch.object(producer,'_validate_context'), \
                 mock.patch.object(producer,'load_high_volume_registry',return_value=({'database':'postgres','table':'synthetic_table'},)), \
                 mock.patch.object(producer,'shipping_factory',return_value=lambda *args,**kwargs:SyntheticExecutor()):
                policy = producer.produce(candidate,[row],Path(tmp),output,run_id=42,purpose='pr-validation')
            envelope = json.loads((output/'evidence.json').read_bytes())
            self.assertEqual(envelope['lane'],'pr-validation')
            docs = packager.check_private_inventory(output,envelope['rows'][0])
            self.assertEqual(docs['0-resource_samples.json'],{'value':[]})
            self.assertEqual(policy['migration'],candidate['migration'])
            with self.assertRaises(CompatibilityError):
                packager.build(candidate,policy,output,run_id=42)

    def test_main_missing_executor_emits_only_notify_decision_without_network(self):
        import os
        target = default_v2()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root/'synthetic-candidate.json'
            candidate.write_bytes(canonical_json(target))
            env = {'GITHUB_REPOSITORY':producer.REPOSITORY,'GITHUB_RUN_ID':'42',
                   'GITHUB_REF':'refs/tags/'+target['version'],'GITHUB_SHA':target['source_sha'],
                   'GITHUB_EVENT_NAME':'workflow_call'}
            with mock.patch.dict(os.environ,env), mock.patch.object(producer,'_validate_context'), \
                 mock.patch.object(producer,'shipping_factory',side_effect=producer.ManualRelease('shipping_executor_unavailable')), \
                 mock.patch.object(producer,'ReleaseDiscovery',side_effect=AssertionError('network must not run')):
                code = producer.main(['--target',str(candidate),'--workspace',str(root/'work'),
                    '--output',str(root/'output'),'--run-id','42'])
            self.assertEqual(code,0)
            self.assertEqual({p.name for p in (root/'output').iterdir()},{'manual.json'})
            decision = json.loads((root/'output/manual.json').read_bytes())
            self.assertEqual(decision['reason'],'shipping_executor_unavailable')
            self.assertEqual(json.loads(packager.manual_candidate(target,decision,run_id=42)),target)

    def test_last_assertion_finishing_after_deadline_cannot_pass(self):
        executor = mock.Mock()
        layout = topology_profile('standalone',())
        executor.suite_assertions.return_value = {name:lambda:None for name in packager.required_cases('source_snapshot',layout)}
        # Exercise's pre-suite/pre-case clocks, case timer start/end, then the
        # post-assertion budget check. No real waiting or product timeout claim.
        with mock.patch.object(producer.time,'monotonic',side_effect=[0,0,0,2,2]):
            with self.assertRaisesRegex(producer.ManualRelease,'suite_timed_out'):
                producer.exercise(executor,deadline=1)

    def test_real_process_interruption_requires_durable_boundary_and_fresh_pid(self):
        import subprocess
        import sys
        def child():
            proc = subprocess.Popen([sys.executable,'-I','-c',
                "import sys; print('synthetic-child',flush=True); sys.stdin.read()"],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertEqual(proc.stdout.readline(),b'synthetic-child\n')
            return proc
        proc = child()
        fresh = None
        try:
            with self.assertRaisesRegex(producer.RehearsalError,'boundary'):
                producer.interrupt_updater(proc,boundary='backup_sealed',checkpoint=lambda:'quiescing',restart=child)
            self.assertIsNone(proc.poll())
            fresh = producer.interrupt_updater(proc,boundary='backup_sealed',checkpoint=lambda:'backup_sealed',restart=child)
            self.assertNotEqual(fresh.pid,proc.pid)
        finally:
            for process in (proc,fresh):
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                    process.communicate(timeout=10)

    def test_direct_activation_and_complete_budget_bounds(self):
        self.assertEqual(producer.Budgets().startup_seconds,4200)
        for values in ({'startup_seconds':0},{'startup_seconds':7201},{'total_seconds':21001},
                       {'total_seconds':True},{'stability_seconds':0}):
            with self.subTest(values=values), self.assertRaises(producer.RehearsalError):
                producer.Budgets(**values)
        self.assertIn('normal_compose_direct_start',packager.required_cases('target_readiness',topology_profile('standalone',())))


class ResourceCoverageRegressionTests(unittest.TestCase):
    """Adversarial private evidence, including the reviewer's scale/peak probes."""
    def valid(self):
        _, _, proof, _ = restore_release(pending_count=1)
        fields = complete_snapshot_fields(copy.deepcopy(proof['predecessors'][0]))
        reference = reference_fixture()
        observed = next(r for r in reference['tables'] if r['engine'] == 'postgres')
        item = {k: copy.deepcopy(fields['migration_measurements'][0][k])
                for k in ('app_label', 'name', 'engine', 'risk_class', 'duration_seconds')}
        item['tables'] = [dict(table=observed['table'], reference_rows=observed['rows'],
                               reference_bytes=observed['bytes'])]
        duration = item['duration_seconds']
        item.update(snapshot_generation=reference['snapshot_generation'], started_monotonic=10,
                    completed_monotonic=10+duration)
        raw = [dict(disk_bytes=1000,wal_bytes=0,temp_bytes=0,rss_bytes=100,processed_bytes=0)]
        raw += [dict(disk_bytes=2000,wal_bytes=100,temp_bytes=50,rss_bytes=120,processed_bytes=1000)
                for _ in range(int(duration))]
        item['samples'] = timed_samples(raw, duration)
        fields['migration_measurements'] = [producer.migration_measurement(
            {k:item[k] for k in ('app_label','name')}, item['tables'], item['samples'],
            **{k:item[k] for k in ('engine','risk_class','duration_seconds','started_monotonic','completed_monotonic')})]
        transfer = transfer_fixture(fields)
        corpus = dict(resource_samples=[item], target_pending_plan=[{k:item[k] for k in ('app_label','name')}],
                      backup_transfer=transfer, restore_transfer=copy.deepcopy(transfer), large_volume_reference=reference,
                      **snapshot_evidence_fixture(fields))
        validate_measurements(fields,corpus)
        return fields, corpus

    def test_registered_measurement_cannot_inflate_independent_work_denominator(self):
        fields, corpus = self.valid()
        item = corpus['resource_samples'][0]
        item['tables'][0].update(reference_rows=1000000000, reference_bytes=1000000000)
        fields['migration_measurements'][0] = producer.migration_measurement(
            {k:item[k] for k in ('app_label','name')}, item['tables'], item['samples'],
            **{k:item[k] for k in ('engine','risk_class','duration_seconds','started_monotonic','completed_monotonic')})
        with self.assertRaisesRegex(producer.RehearsalError, 'independent reference scale'):
            validate_measurements(fields,corpus)

    def test_reference_inventory_is_required_complete_unique_and_generation_bound(self):
        for change in ('missing', 'one_missing', 'duplicate', 'underseeded', 'schema', 'generation', 'registry',
                       'snapshot_missing', 'snapshot_substituted'):
            fields, corpus = self.valid()
            ref = corpus['large_volume_reference']
            if change == 'missing': del corpus['large_volume_reference']
            elif change == 'one_missing': ref['tables'].pop()
            elif change == 'duplicate': ref['tables'].append(copy.deepcopy(ref['tables'][0]))
            elif change == 'underseeded': ref['tables'][0]['rows'] = 99999
            elif change == 'schema': corpus['resource_samples'][0]['tables'][0]['table'] = 'wrong.' + corpus['resource_samples'][0]['tables'][0]['table'].split('.')[1]
            elif change == 'generation': corpus['resource_samples'][0]['snapshot_generation'] = 'b'*64
            elif change == 'registry': ref['registry_sha256'] = 'b'*64
            elif change == 'snapshot_missing': del corpus['source_snapshot_inventory']
            else: corpus['source_snapshot_inventory']['expectation']['target_manifest_hash'] = 'b'*64
            with self.subTest(change=change), self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,corpus)

    def test_single_baseline_or_missing_execution_interval_cannot_claim_zero_growth(self):
        for change in ('one', 'no_final', 'no_baseline', 'gap', 'duration', 'late_start', 'early_end',
                       'unordered', 'missing_time', 'wal_reset', 'processed_reset'):
            fields, corpus = self.valid()
            item = corpus['resource_samples'][0]
            samples = item['samples']
            if change == 'one': item['samples'] = samples[:1]
            elif change == 'no_final': samples[-1]['boundary'] = 'during'
            elif change == 'no_baseline': samples[0]['boundary'] = 'during'
            elif change == 'gap': del samples[1:4]
            elif change == 'duration': item['duration_seconds'] += 1
            elif change == 'late_start': samples[0]['monotonic'] += 0.1
            elif change == 'early_end': samples[-1]['monotonic'] -= 0.1
            elif change == 'unordered': samples[1]['monotonic'] = samples[0]['monotonic']
            elif change == 'wal_reset': samples[-1]['wal_bytes'] = 0
            elif change == 'processed_reset': samples[-1]['processed_bytes'] = 0
            else: del samples[0]['monotonic']
            with self.subTest(change=change), self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,corpus)

    def test_continuous_sampler_records_intermediate_peak_and_execution_boundaries(self):
        import threading
        peak = threading.Event()
        observed = threading.Event()
        def read():
            high = peak.is_set()
            if high: observed.set()
            return dict(disk_bytes=900 if high else 100, wal_bytes=90 if observed.is_set() else 0,
                        temp_bytes=50 if high else 0, rss_bytes=123, processed_bytes=100)
        sampler = producer.ContinuousResourceSampler(read, interval_seconds=0.005)
        def operation():
            peak.set()
            self.assertTrue(observed.wait(1), 'sampler must observe while operation runs')
            peak.clear()
            return 'real-operation-return'
        value, evidence = sampler.measure(operation)
        self.assertEqual(value,'real-operation-return')
        self.assertGreaterEqual(len(evidence['samples']),3)
        self.assertEqual(max(r['disk_bytes'] for r in evidence['samples']),900)
        self.assertEqual(evidence['samples'][-1]['disk_bytes'],100)
        producer.validate_sampling_interval(evidence['samples'], evidence['duration_seconds'],
            started_monotonic=evidence['started_monotonic'], completed_monotonic=evidence['completed_monotonic'])

    def test_sampler_error_or_operation_error_never_turns_into_zero_peak(self):
        import threading
        called = threading.Event()
        count = [0]
        def read():
            count[0] += 1
            if count[0] > 1:
                called.set()
                raise OSError('synthetic counter read failure')
            return dict(disk_bytes=1,wal_bytes=0,temp_bytes=0,rss_bytes=1,processed_bytes=1)
        sampler = producer.ContinuousResourceSampler(read, interval_seconds=0.005)
        with self.assertRaisesRegex(producer.RehearsalError,'sampling failed'):
            sampler.measure(lambda: self.assertTrue(called.wait(1)))
        def fail(): raise OSError('operation failed')
        sampler = producer.ContinuousResourceSampler(lambda:dict(disk_bytes=1,wal_bytes=0,temp_bytes=0,rss_bytes=1,processed_bytes=1))
        with self.assertRaisesRegex(OSError,'operation failed'):
            sampler.measure(fail)

    def test_invalid_live_baseline_refuses_before_migration_is_released(self):
        for value in (-1, True, float('nan'), '1'):
            operation = mock.Mock()
            sampler = producer.ContinuousResourceSampler(lambda:dict(disk_bytes=value,wal_bytes=0,
                temp_bytes=0,rss_bytes=1,processed_bytes=0))
            with self.subTest(value=value), self.assertRaises(producer.RehearsalError):
                sampler.measure(operation)
            operation.assert_not_called()

    def test_product_packager_rejects_reviewer_inputs_even_with_matching_report_hashes(self):
        from types import SimpleNamespace
        from deploy.update.trust import Verifier
        from deploy.update.settings import load_settings
        fields, valid = self.valid()
        origins = {'postgres_app':'product_api','clickhouse':'collector_ingestion',
                   'object_store':'bundled_s3_api','temporal':'completed_product_workflow','shared_files':'product_spool'}
        witnesses = [dict(domain=d,identity='SYNTHETIC-REVIEW-ONLY-'+d,payload_sha256='1'*64,origin=o)
                     for d,o in origins.items()]
        for change in ('valid', 'missing_reference', 'inflated_reference', 'single_sample', 'three_field_snapshot',
                       'wrong_target', 'wrong_source', 'wrong_platform', 'wrong_image', 'wrong_dependency', 'wrong_layout',
                       'missing_source', 'inflated_transfer', 'archive_counter_inflation', 'wrong_transfer_basis', 'wrong_transfer_generation'):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'evidence'
                candidate,policy,envelope,_,_ = fixture(path,pending_count=1)
                envelope['lane'] = 'product'
                row = envelope['rows'][0]
                for key in ('migration_measurements','backup_measurement','restore_measurement'):
                    row[key] = copy.deepcopy(fields[key])
                corpus = copy.deepcopy(valid)
                complete_snapshot_fields(row)
                approved = copy.deepcopy(candidate)
                approved.update(migration=policy['migration'],compatibility=policy['compatibility'])
                envelope['inputs_hash'] = inputs_hash(approved)
                old_raw = (path/(row['predecessor']['raw_manifest_sha256']+'.json')).read_bytes()
                corpus.update(snapshot_evidence_fixture(row, target=candidate, predecessor_raw=old_raw))
                generation = producer.sha(canonical_json(corpus['source_snapshot_inventory']))
                corpus['large_volume_reference']['snapshot_generation'] = generation
                for item in corpus['resource_samples']: item['snapshot_generation'] = generation
                for role in ('backup','restore'): corpus[role+'_transfer']['snapshot_generation'] = generation
                corpus.update(source_witnesses=witnesses,restored_witnesses=witnesses)
                if change == 'missing_reference': del corpus['large_volume_reference']
                elif change == 'inflated_reference':
                    item = corpus['resource_samples'][0]
                    item['tables'][0].update(reference_rows=1000000000,reference_bytes=1000000000)
                    row['migration_measurements'][0] = producer.migration_measurement(
                        {k:item[k] for k in ('app_label','name')}, item['tables'], item['samples'],
                        **{k:item[k] for k in ('engine','risk_class','duration_seconds','started_monotonic','completed_monotonic')})
                elif change == 'single_sample':
                    item = corpus['resource_samples'][0]
                    item['samples'] = item['samples'][:1]
                    row['migration_measurements'][0].update(peak_disk_amplification_factor=1.0,wal_bytes=0,temp_bytes=0)
                elif change == 'three_field_snapshot':
                    corpus['source_snapshot_inventory'] = {'schema_version':2,'inventory_version':2,'purpose':'SYNTHETIC'}
                    generation = producer.sha(canonical_json(corpus['source_snapshot_inventory']))
                    corpus['large_volume_reference']['snapshot_generation'] = generation
                    for item in corpus['resource_samples']: item['snapshot_generation'] = generation
                    for role in ('backup','restore'): corpus[role+'_transfer']['snapshot_generation'] = generation
                elif change in ('wrong_target','wrong_source','wrong_platform','wrong_image','wrong_dependency','wrong_layout','archive_counter_inflation'):
                    snapshot = corpus['source_snapshot_inventory']
                    source = corpus['source_runtime_observation']
                    if change == 'wrong_target': snapshot['expectation']['target_manifest_hash'] = 'e'*64
                    elif change == 'wrong_source':
                        snapshot['expectation']['source_version'] = source['release_version'] = 'v9.99.0'
                    elif change == 'wrong_platform':
                        source['platform'] = 'linux/arm64'
                        for image in corpus['source_image_inspections'].values(): image['Architecture'] = 'arm64'
                    elif change in ('wrong_image','wrong_dependency'):
                        source['product_images' if change == 'wrong_image' else 'dependency_images']['app' if change == 'wrong_image' else 'postgres'][0]['digest'] = 'sha256:'+'e'*64
                    elif change == 'wrong_layout': source['deployment_sha256'] = 'e'*64
                    else: snapshot['archives']['app-data']['logical_bytes'] = 1000000000000
                    refresh_snapshot_hashes(corpus)  # all copies/hash claims agree; expected row remains immutable
                elif change == 'missing_source': del corpus['source_runtime_observation']
                elif change == 'inflated_transfer':
                    for role in ('backup','restore'):
                        corpus[role+'_transfer']['reference_volume_bytes'] = {r:1000000000000 for r in producer.VOLUME_ROLES}
                        row[role+'_measurement'] = {'reference_volume_bytes':copy.deepcopy(corpus[role+'_transfer']['reference_volume_bytes']),
                                                  'throughput_bytes_per_second':1000000000000}
                elif change == 'wrong_transfer_basis': corpus['restore_transfer']['measurement_basis'] = 'allocated-blocks'
                elif change == 'wrong_transfer_generation': corpus['restore_transfer']['snapshot_generation'] = 'e'*64
                inventory = {}
                for key,value in corpus.items():
                    name = '0-'+key+'.json'; raw = canonical_json({'value':value})
                    (path/name).write_bytes(raw); inventory[name] = producer.sha(raw)
                row.update(evidence_inventory=inventory,evidence_inventory_sha256=packager.inventory_hash(inventory))
                properties = {k:str(v) for k,v in envelope.items() if k not in ('schema_version','rows','executor_inventory')}
                properties['binding_hash'] = manifest_hash({k:v for k,v in row.items() if k != 'reports'})
                for suite in SUITES:
                    xml = ET.Element('testsuite',name=suite,tests=str(len(packager.required_cases(suite,topology_profile('standalone',())))),failures='0',errors='0',skipped='0')
                    props = ET.SubElement(xml,'properties')
                    for key,value in properties.items(): ET.SubElement(props,'property',name=key,value=value)
                    for case in sorted(packager.required_cases(suite,topology_profile('standalone',()))):
                        ET.SubElement(xml,'testcase',classname=suite,name=case,assertions='1')
                    report = row['reports'][suite]; raw = ET.tostring(xml)
                    (path/report['path']).write_bytes(raw); report['sha256'] = producer.sha(raw)
                (path/(row['predecessor']['raw_manifest_sha256']+'.sigstore.json')).write_bytes(b'{}')
                (path/'evidence.json').write_bytes(canonical_json(envelope))
                verifier = Verifier(load_settings(producer.ROOT,environ={}),host_only=True)
                with mock.patch.object(verifier,'verify',return_value=SimpleNamespace(verified=True)):
                    if change == 'valid':
                        _,manifest = packager.build(candidate,policy,path,run_id=42,verifier=verifier)
                        self.assertTrue(json.loads(manifest)['compatibility']['automatic_apply'])
                    else:
                        with self.subTest(change=change), self.assertRaises(CompatibilityError):
                            packager.build(candidate,policy,path,run_id=42,verifier=verifier)


class SnapshotTransferRegressionTests(unittest.TestCase):
    valid = ResourceCoverageRegressionTests.valid

    def test_shipping_backup_inventory_with_matching_inputs_accepts_and_substitution_refuses(self):
        """Real shipping archive/seal generation on a SYNTHETIC daemon, no product claim."""
        from dataclasses import fields as dataclass_fields, replace
        from datetime import datetime
        from deploy.tests.test_update_standalone_backup import BackupTests, NOW
        from deploy.tests.test_update_standalone_restore import LocalHelpers
        from deploy.update import standalone_restore
        from deploy.update.contracts import InstallationBinding, RuntimeImage, RuntimeObservationV2, installation_binding_hash
        from deploy.update.recovery import RecoveryLedger
        f = BackupTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        fields, corpus = self.valid()
        _,old_raw,_,_ = restore_release(1)
        fields['execution_deployment']['files'].update(json.loads(f.binding.raw)['files'])
        # .env is private, not a signed deployment asset.
        fields['execution_deployment']['files'].pop('.env',None)
        fields['predecessor']['execution_deployment_hash'] = manifest_hash(fields['execution_deployment'])
        captured = snapshot_evidence_fixture(fields,predecessor_raw=old_raw)
        registration = captured['source_installation_binding']
        registration.update(checkout=str(f.root),env_alias=str(f.root/'.env'),env_target=str(f.root/'.env'),
            compose_files=[str(f.root/'docker-compose.yml')],compose_project='test-backup',docker_context='test-backup',
            daemon_identity='daemon-1',installation_fingerprint=f.expected.installation_fingerprint)
        typed_registration = dict(registration)
        for key in ('checkout','env_alias','env_target'): typed_registration[key] = Path(typed_registration[key])
        typed_registration['compose_files'] = tuple(Path(v) for v in typed_registration['compose_files'])
        source_doc = captured['source_runtime_observation']
        source_doc.update(binding_sha256=installation_binding_hash(InstallationBinding(**typed_registration)),
            env_sha256=json.loads(f.binding.raw)['files']['.env'])
        # Capture this independent healthy-source preimage before the shipping
        # backup. Do not derive it from the inventory produced below.
        from deploy.update.standalone_backup import VOLUMES
        b = json.loads(f.binding.raw)
        preimages = captured['source_observation_preimages']
        preimages['configuration']['config'] = copy.deepcopy(f.daemon.model)
        volume_hashes = {}
        for role,(service,_) in VOLUMES.items():
            c = f.daemon.find(service)
            i = preimages['containers'][service]
            i.update(id=c['Id'],image=c['Image'],started=c['State']['StartedAt'],
                mounts=sorted(copy.deepcopy(c['Mounts']),key=lambda m:m['Destination']),
                config=copy.deepcopy(c['Config']),volume=copy.deepcopy(b['volumes'][role]))
            preimages['volume_storage'][role].update(name=b['volumes'][role]['Name'],root_identity=b['volume_root_ids'][role])
            volume_hashes[service] = manifest_hash(dict(daemon=b['daemon'],name=i['volume']['Name'],mountpoint=i['volume']['Mountpoint'],
                created_at=i['volume']['CreatedAt'],driver=i['volume']['Driver'],readonly_config=i['bind_hashes']))
        source_doc.update(config_sha256=manifest_hash(preimages['configuration']),
            container_roster_sha256=manifest_hash(preimages['containers']),
            storage_sha256=manifest_hash({'volumes':volume_hashes,'roots':manifest_hash(preimages['volume_storage'])}))
        source_args = copy.deepcopy(source_doc)
        source_args['observed_at'] = datetime.fromisoformat(source_args['observed_at'])
        for key in ('product_images','dependency_images'):
            source_args[key] = {s:tuple(RuntimeImage(**i) for i in rows) for s,rows in source_args[key].items()}
        source = RuntimeObservationV2(**source_args)
        f.expected = replace(f.expected,target_manifest_hash=manifest_hash(default_v2()))
        ledger = RecoveryLedger.reserve(f.destination,f.expected,operation_token='synthetic-token',run_id='synthetic-run',binding_sha256=source.binding_sha256)
        with mock.patch.object(standalone_restore,'_StorageHelpers',LocalHelpers):
            artifact = f.adapter.run_quiesced(f.expected,ledger,source,proof_directory_fd=f.proof_fd)
        directory = ledger.directory
        captured['source_snapshot_inventory'] = json.loads((directory/'inventory.json').read_bytes())
        captured['source_backup_proof'] = json.loads((directory/'proof.json').read_bytes())
        captured['source_backup_seal'] = {member.name:(getattr(artifact.seal,member.name).isoformat() if
            isinstance(getattr(artifact.seal,member.name),datetime) else getattr(artifact.seal,member.name)) for member in dataclass_fields(artifact.seal)}
        captured['source_snapshot_members'] = {role:[json.loads(line) for line in (directory/(role+'.members.jsonl')).read_bytes().splitlines()]
            for role in producer.VOLUME_ROLES}
        corpus.update(captured)
        generation = producer.sha(canonical_json(captured['source_snapshot_inventory']))
        corpus['large_volume_reference']['snapshot_generation'] = generation
        for sample in corpus['resource_samples']: sample['snapshot_generation'] = generation
        volumes = {role:entry['logical_bytes'] for role,entry in captured['source_snapshot_inventory']['archives'].items()}
        for role in ('backup','restore'):
            corpus[role+'_transfer'].update(reference_volume_bytes=volumes,snapshot_generation=generation)
            fields[role+'_measurement'] = dict(reference_volume_bytes=volumes,throughput_bytes_per_second=sum(volumes.values())/3)
        validate_measurements(fields,corpus)
        self.assertEqual(set(volumes.values()),{len(b'private persistent record')})
        corpus['source_snapshot_inventory']['expectation']['target_manifest_hash'] = 'e'*64
        refresh_snapshot_hashes(corpus)
        with self.assertRaisesRegex(producer.RehearsalError,'another qualified transition'):
            validate_measurements(fields,corpus)

    def test_resealed_physical_storage_or_container_configuration_cannot_borrow_source_observation(self):
        for change in ('root_inode','volume_created','volume_mountpoint','command','environment','entrypoint','compose'):
            fields,corpus = self.valid()
            snapshot = corpus['source_snapshot_inventory']
            binding = snapshot['binding']
            if change == 'root_inode':
                binding['volume_root_ids']['app-data'] = '1:999999'
                snapshot['quiescence']['roots']['app-data'] = '1:999999'
            elif change in ('volume_created','volume_mountpoint'):
                key = 'CreatedAt' if change == 'volume_created' else 'Mountpoint'
                value = '2026-10-27T11:00:00Z' if change == 'volume_created' else '/replaced/physical/data'
                for v in (binding['volumes']['app-data'],snapshot['archives']['app-data']['volume'],
                          snapshot['quiescence']['volumes']['app-data']): v[key] = value
                if change == 'volume_mountpoint':
                    for roster in (snapshot['containers'],snapshot['quiescence']['containers']):
                        next(m for m in roster['app']['Mounts'] if m['Type']=='volume')['Source'] = value
            elif change == 'compose': binding['compose_sha256'] = 'e'*64
            else:
                key,value = {'command':('Cmd',['unexpected-command']), 'entrypoint':('Entrypoint',['/unexpected']),
                             'environment':('Env',['PRODUCT_STATE=substituted'])}[change]
                for roster in (snapshot['containers'],snapshot['quiescence']['containers']): roster['app']['Config'][key] = value
            refresh_snapshot_hashes(corpus)
            with self.subTest(change=change),self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,corpus)

    def test_source_preimages_are_required_and_cannot_change_independently(self):
        for section in ('configuration','containers','volume_storage'):
            fields,corpus = self.valid()
            del corpus['source_observation_preimages'][section]
            with self.subTest(missing=section),self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,corpus)
        fields,corpus = self.valid()
        del corpus['source_observation_preimages']
        with self.assertRaises(producer.RehearsalError): validate_measurements(fields,corpus)
        for section in ('configuration','containers','volume_storage'):
            fields,corpus = self.valid()
            corpus['source_observation_preimages'][section]['unexpected'] = 'substituted'
            with self.subTest(changed=section),self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,corpus)

    def test_every_role_and_transfer_rejects_jointly_inflated_claims(self):
        for role in producer.VOLUME_ROLES:
            for transfer in ('backup','restore'):
                fields, corpus = self.valid()
                raw = corpus[transfer+'_transfer']
                raw['reference_volume_bytes'][role] = 1000000000000
                fields[transfer+'_measurement'] = dict(reference_volume_bytes=copy.deepcopy(raw['reference_volume_bytes']),
                    throughput_bytes_per_second=sum(raw['reference_volume_bytes'].values())/3)
                with self.subTest(role=role,transfer=transfer), self.assertRaisesRegex(producer.RehearsalError,'independently captured snapshot members'):
                    validate_measurements(fields,corpus)

    def test_archive_counters_cannot_override_independent_members_after_reseal(self):
        for role in producer.VOLUME_ROLES:
            fields, corpus = self.valid()
            corpus['source_snapshot_inventory']['archives'][role]['logical_bytes'] = 1000000000000
            refresh_snapshot_hashes(corpus)
            with self.subTest(role=role), self.assertRaisesRegex(producer.RehearsalError,'archive counters differ'):
                validate_measurements(fields,corpus)

    def test_complete_snapshot_and_source_evidence_are_mandatory(self):
        required = ('source_snapshot_members','source_runtime_observation','source_installation_binding',
                    'source_image_inspections','source_backup_proof','source_backup_seal')
        for key in required:
            fields, corpus = self.valid()
            del corpus[key]
            with self.subTest(key=key), self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,corpus)
        for key in snapshot_fixture():
            fields, corpus = self.valid()
            del corpus['source_snapshot_inventory'][key]
            with self.subTest(snapshot_field=key), self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,corpus)

    def test_member_tree_counter_and_identity_substitutions_refuse(self):
        for change in ('duplicate','missing_root','missing_file','wrong_payload_size','wrong_archive_volume',
                       'wrong_binding_root','wrong_source_image_id','wrong_config','wrong_migrations','wrong_mount','extra_volume'):
            fields, corpus = self.valid()
            snapshot = corpus['source_snapshot_inventory']
            tree = corpus['source_snapshot_members']['app-data']
            if change == 'duplicate': tree.append(copy.deepcopy(tree[-1]))
            elif change == 'missing_root': tree.pop(0)
            elif change == 'missing_file': tree.pop()
            elif change == 'wrong_payload_size': tree[-1]['size'] += 1
            elif change == 'wrong_archive_volume': snapshot['archives']['app-data']['volume'] = {}
            elif change == 'wrong_binding_root': snapshot['binding']['root'] += '-substituted'
            elif change == 'wrong_source_image_id': corpus['source_image_inspections']['app']['Id'] = 'sha256:'+'e'*64
            elif change == 'wrong_config': snapshot['binding']['files']['docker-compose.yml'] = 'e'*64
            elif change == 'wrong_migrations': corpus['source_runtime_observation']['migration_inventory_sha256'] = 'e'*64
            elif change == 'wrong_mount': snapshot['containers']['app']['Mounts'][0]['Source'] += '-substituted'
            else: snapshot['binding']['volumes']['extra'] = {}
            refresh_snapshot_hashes(corpus)
            with self.subTest(change=change), self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,corpus)

    def test_supported_metadata_survives_but_unsupported_normalized_rows_refuse(self):
        from deploy.update.probes.restore_fs import canonical as canonical_tree
        fields, corpus = self.valid()
        row = corpus['source_snapshot_members']['app-data'][-1]
        row['xattrs'] = {'user.synthetic':'c3ludGhldGlj'}
        row['acls'] = {'access':[['group',None,0],['other',None,0],['user',None,6]]}
        def bind_members():
            rows = corpus['source_snapshot_members']['app-data']
            raw = b''.join(canonical_tree(r)+b'\n' for r in rows)
            corpus['source_snapshot_inventory']['archives']['app-data'].update(members_sha256=producer.sha(raw),members_bytes=len(raw))
            refresh_snapshot_hashes(corpus)
        bind_members()
        validate_measurements(fields,corpus)
        original = copy.deepcopy(row)
        for attrs, acls in (({'security.selinux':'c3ludGhldGlj'},{}),({'user.synthetic':'not-base64'},{}),
                            ({},{'other':[]}),({},{'access':[['user',None,8]]})):
            row.update(xattrs=attrs,acls=acls)
            bind_members()
            with self.subTest(attrs=attrs,acls=acls), self.assertRaises(producer.RehearsalError):
                validate_measurements(fields,corpus)
            row.update(copy.deepcopy(original))

    def test_hardlinks_count_one_payload_and_sparse_logical_sizes_are_not_blocks(self):
        from deploy.update.probes.restore_fs import canonical as canonical_tree
        fields, corpus = self.valid()
        snapshot = corpus['source_snapshot_inventory']
        rows = corpus['source_snapshot_members']['app-data']
        linked = copy.deepcopy(rows[-1])
        linked['name'] = 'z-hardlink'
        rows[-1]['hardlinks'] = linked['hardlinks'] = ['synthetic-record','z-hardlink']
        rows.append(linked)
        raw = b''.join(canonical_tree(r)+b'\n' for r in rows)
        archive = snapshot['archives']['app-data']
        archive.update(members=3,members_sha256=producer.sha(raw),members_bytes=len(raw))
        refresh_snapshot_hashes(corpus)
        validate_measurements(fields,corpus)
        self.assertEqual(producer._snapshot_volume_bytes(snapshot,corpus['source_snapshot_members'])['app-data'],1000)
        # An apparent sparse length must not be replaced with its small block count.
        corpus['backup_transfer']['reference_volume_bytes']['app-data'] = 1
        with self.assertRaises(producer.RehearsalError): validate_measurements(fields,corpus)


class SignedMeasurementPackagingTests(ProducerFixture):
    def test_nonzero_plan_and_frozen_legacy_migration_are_preserved(self):
        raw, certified = self.build()
        self.assertEqual(json.loads(certified)['migration'],self.candidate['migration'])
        self.assertTrue(json.loads(certified)['migration']['requires_bootstrap'])
        row = json.loads(raw)['predecessors'][0]
        self.assertEqual(row['migration_evidence']['target_pending_plan_count'],3)
        self.assertEqual(len(row['migration_measurements']),3)
        self.assertNotIn('state_domains',json.loads(raw))
        self.assertEqual(json.loads(certified)['compatibility']['automatic_rollback'],{'strategy':'restore_backup'})

    def test_positively_measured_zero_plan_has_explicit_empty_measurements(self):
        _,_,_,certified,proof = fixture(self.root/'zero',pending_count=0)
        row = proof['predecessors'][0]
        self.assertEqual(row['migration_measurements'],[])
        self.assertEqual(row['migration_evidence']['target_pending_plan_sha256'],producer.pending_migration_plan_hash([]))
        self.assertEqual(row['migration_evidence']['target_pending_plan_count'],0)
        self.assertTrue(certified['migration']['requires_bootstrap'])
        self.assertLessEqual(len(canonical_json(proof)),256*1024)

    def test_measurements_missing_nonfinite_incomplete_or_wrong_order_refuse(self):
        original = copy.deepcopy(self.envelope)
        for field in ('migration_measurements','backup_measurement','restore_measurement'):
            self.envelope = copy.deepcopy(original)
            del self.envelope['rows'][0][field]
            self.save()
            with self.subTest(field=field), self.assertRaises(CompatibilityError):
                self.build()
        for action in ('count','order','nan','snapshot'):
            self.envelope = copy.deepcopy(original)
            row = self.envelope['rows'][0]
            if action=='count': row['migration_measurements'].pop()
            elif action=='order': row['migration_measurements'].reverse()
            elif action=='nan': row['migration_measurements'][0]['duration_seconds'] = float('nan')
            else: row['restore_measurement']['reference_volume_bytes']['app-data'] += 1
            self.save()
            with self.subTest(action=action), self.assertRaises(CompatibilityError):
                self.build()

    def test_complete_executor_helper_inventory_is_independently_recomputed(self):
        for key in ('executor_inventory','executor_sha256','high_volume_registry_sha256'):
            original = copy.deepcopy(self.envelope)
            if key=='executor_inventory':
                self.envelope[key]['deploy/update/standalone_restore.py'] = 'f'*64
            else:
                self.envelope[key] = 'f'*64
            self.save()
            with self.subTest(key=key), self.assertRaises(CompatibilityError):
                self.build()
            self.envelope = original

    def test_private_evidence_substitution_is_not_certified(self):
        (self.evidence/'synthetic-corpus.json').write_bytes(b'{}')
        with self.assertRaises(CompatibilityError):
            self.build()

    def test_policy_cannot_rewrite_legacy_migration_for_eligibility(self):
        self.policy['migration']['requires_bootstrap'] = False
        with self.assertRaises(CompatibilityError):
            self.build()

    def test_all_incomplete_reasons_are_notify_only_and_cannot_emit_proof(self):
        for reason in packager.MANUAL_REASONS:
            decision = producer.notify_decision(self.candidate,reason,42)
            self.assertEqual(json.loads(packager.manual_candidate(self.candidate,decision,run_id=42)),self.candidate)
        for reason in ('pending_target_migrations','state_format_changed','waiver'):
            with self.assertRaises(producer.RehearsalError):
                producer.notify_decision(self.candidate,reason,42)


class FrozenCandidateTests(unittest.TestCase):
    def test_generator_never_replaces_stable_or_beta_candidates(self):
        import subprocess
        import sys
        from scripts import generate_update_manifest as generator
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for version in ('v1.52.0','v1.52.0-beta.2'):
                manifest = versioned(default_v2(),version)
                path = root/(version+'.json')
                args = [sys.executable,'-I','-B',generator.__file__,'--version',version,
                        '--source-sha',manifest['source_sha'],'--published-at',manifest['published_at'],
                        '--channel',manifest['channel'],'--helm-chart',manifest['helm']['chart'],
                        '--helm-digest',manifest['helm']['digest'],'--output',str(path)]
                for name,value in manifest['components'].items():
                    args += ['--component',name+'='+value['image']+'@'+value['digest']]
                first = subprocess.run(args,capture_output=True,text=True,timeout=30)
                self.assertEqual(first.returncode,0,first.stderr)
                original = path.read_bytes()
                again = subprocess.run(args,capture_output=True,text=True,timeout=30)
                self.assertEqual(again.returncode,0,again.stderr)
                args[args.index('--source-sha')+1]='f'*40
                changed = subprocess.run(args,capture_output=True,text=True,timeout=30)
                self.assertEqual(changed.returncode,2)
                self.assertIn('refusing to replace',changed.stderr)
                self.assertEqual(path.read_bytes(),original)

    def test_generator_rejects_symlinked_evidence(self):
        from scripts.generate_update_manifest import read_evidence
        from deploy.update.manifest import ManifestError
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'file').write_bytes(b'{}'); (root/'link').symlink_to(root/'file')
            with self.assertRaises(ManifestError):
                read_evidence(root/'link')


if __name__ == '__main__':
    unittest.main()
