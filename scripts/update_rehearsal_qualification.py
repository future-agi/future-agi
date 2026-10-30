"""Exact-pair product qualification; no downloaded execution or stage-label proofs."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def reference_scope(plan, scope, references):
    """Select only the independently established registered-table intersection."""
    from scripts import rehearse_update_compatibility as p
    keys = {(row['engine'], row['table']) for row in references}
    p.equal(len(scope), len(plan), 'missing ordered migration scope')
    result = []
    for identity, row in zip(plan, scope):
        p.equal({key: row[key] for key in ('app_label', 'name')}, identity,
                'migration scope order differs')
        tables = [dict(table) for table in row['tables'] if (table['engine'], table['table']) in keys]
        result.append(dict(identity, tables=sorted(tables, key=lambda table: (table['engine'], table['table']))))
    p.equal({(table['engine'], table['table']) for row in result for table in row['tables']}, keys,
            'registered reference table omitted from migration scope')
    return result


def validate_bootstrap_envelope(fields, corpus, target, references):
    """Re-reduce raw samples only after checking the independent loader/snapshot."""
    from scripts import rehearse_update_compatibility as p
    from scripts import update_rehearsal_resources as resources
    from deploy.update.manifest import parse_image_ref
    p.validate_scope_probe(fields, corpus, target)
    plan = corpus['target_pending_plan']
    tables = [dict(engine=engine, table=table, reference_rows=row['rows'], reference_bytes=row['bytes'])
              for (engine, table), row in sorted(references.items())]
    scope = reference_scope(plan, corpus['pending_migration_tables'], tables)
    raw = corpus.get('bootstrap_envelope')
    p._keys(raw, ('schema_version', 'measurement_basis', 'snapshot_generation', 'target_digest',
        'target_identity', 'target_identity_sha256', 'ordered_plan_sha256', 'table_references_sha256',
        'migration_tables', 'migration_table_scope_sha256', 'counter_sources', 'started_monotonic',
        'completed_monotonic', 'measurement', 'samples'), 'incomplete bootstrap resource envelope')
    p.need(type(raw['schema_version']) is int and raw['schema_version'] == 1,
           'unsupported bootstrap resource envelope')
    p.equal(raw['measurement_basis'], resources.MEASUREMENT_BASIS, 'resource measurement basis differs')
    p.equal(raw['snapshot_generation'], p.sha(p.canonical_json(corpus['source_snapshot_inventory'])),
            'bootstrap resources belong to another snapshot')
    p.equal(raw['target_digest'], target['components']['standalone']['digest'], 'resource target digest differs')
    p.equal(raw['ordered_plan_sha256'], p.pending_migration_plan_hash(plan), 'resource plan differs')
    p.equal(raw['table_references_sha256'], p.sha(p.canonical_json(tables)), 'resource table references differ')
    p.equal(raw['migration_tables'], scope, 'resource scope differs from the real loader and registry')
    p.equal(raw['migration_table_scope_sha256'], p.sha(p.canonical_json(scope)), 'resource scope hash differs')
    p.equal(raw['counter_sources'], resources.counter_sources(), 'resource counter provenance differs')
    identity = raw['target_identity']
    p._keys(identity, ('daemon_id', 'project', 'target_repository', 'target_digest', 'target_app_image_id',
                       'containers', 'volumes'), 'incomplete resource runtime identity')
    p.equal(raw['target_identity_sha256'], p.sha(p.canonical_json(identity)), 'resource identity hash differs')
    binding = corpus['source_snapshot_inventory']['binding']
    image = corpus.get('target_image_inspection')
    p._keys(image, ('Id', 'Os', 'Architecture', 'RepoDigests'), 'missing target image inspection')
    target_component = target['components']['standalone']
    repository = parse_image_ref(target_component['image']).repository
    p.need(any(parse_image_ref(ref).name == parse_image_ref(target_component['image']).name and
               parse_image_ref(ref).digest == target_component['digest'] for ref in image['RepoDigests']) and
           image['Os'] + '/' + image['Architecture'] == fields['predecessor']['platform'],
           'resource target image differs from the frozen candidate')
    p.equal((identity['daemon_id'], identity['project'], identity['target_repository'],
             identity['target_digest'], identity['target_app_image_id']),
            (binding['daemon'], binding['project'], repository, target_component['digest'], image['Id']),
            'resource runtime belongs to another attempt')
    inspections = corpus.get('target_container_inspections')
    p._keys(inspections, ('app', 'postgres', 'clickhouse'), 'missing independent target container inspections')
    p._keys(identity['containers'], inspections, 'incomplete resource container roster')
    expected_images = dict(binding['images'], app=image['Id'])
    for service, record in inspections.items():
        p.need(record['Image'] == expected_images[service] and record['RestartCount'] == 0 and
               record['State']['Running'] and record['State']['Health']['Status'] == 'healthy' and
               record['Config']['Labels']['com.docker.compose.project'] == binding['project'] and
               record['Config']['Labels']['com.docker.compose.service'] == service,
               'independent target inspection is not the healthy qualified roster')
        footprint = dict(upper_dir=record['GraphDriver']['Data']['UpperDir'],
                         log_driver=record['HostConfig']['LogConfig']['Type'], log_path=record['LogPath'])
        p.equal(identity['containers'][service], dict(id=record['Id'], image_id=record['Image'],
            created=record['Created'], restart_count=record['RestartCount'],
            storage_footprint_sha256=p.sha(p.canonical_json(footprint))), 'resource container identity differs')
    p._keys(identity['volumes'], p.VOLUME_ROLES, 'incomplete resource volume identities')
    for role in p.VOLUME_ROLES:
        volume = binding['volumes'][role]
        p.equal(identity['volumes'][role], dict(name=volume['Name'], created_at=volume['CreatedAt'],
            root_identity=binding['volume_root_ids'][role]), 'resource volume identity differs')
    try:
        reduced = resources.reduce_envelope(plan, tables, scope, raw['samples'],
            raw['completed_monotonic'] - raw['started_monotonic'],
            started_monotonic=raw['started_monotonic'], completed_monotonic=raw['completed_monotonic'])
    except (resources.ResourceObservationError, TypeError, ValueError, KeyError):
        raise p.ManualRelease('migration_measurement_unavailable') from None
    p.equal(raw['measurement'], reduced, 'bootstrap envelope differs from its raw observations')
    p.equal(fields['migration_measurements'], reduced, 'signed bootstrap metrics differ from raw observations')
    return reduced


from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
import os
import signal
import time
import uuid

from scripts import rehearse_update_compatibility as p
from scripts.update_rehearsal_milestone import (
    SnapshotMilestone, TargetMilestone, RestoreMilestone, INTERRUPTION_BOUNDARIES,
    INTEGRITY_CASES, label, record, validate_interruption_results,
)
from scripts.update_rehearsal_shipping import (
    ProductWitnesses, copy_retained_files, retain_source_preimages, wire, REDIS_OPERATION,
)
from deploy.tests.update_e2e.real_automatic import ProductHarness


class QualificationRehearsal(RestoreMilestone):
    """One authenticated pair, real product operations, and an in-process sink."""
    milestone = 'qualification'

    def __init__(self, args):
        super().__init__(args.out, context=args.docker_context, boot_timeout=args.boot_timeout,
                         total_seconds=args.budgets.total_seconds)
        self.row, self.target, self.registry, self.sink = args.row, copy.deepcopy(args.target), args.registry, args.qualification
        self.deadline = min(self.deadline, args.deadline)
        self.project = p.RESOURCE_PREFIX + uuid.uuid4().hex[:12]
        self.evidence_root = self.output
        self.output = self.evidence_root/'primary'
        self.output.mkdir(mode=0o700)
        self.platform = self.row.platform
        self.source_version = self.row.release.version
        source = self.row.release.manifest['components']['standalone']
        target = self.target['components']['standalone']
        self.source_digest, self.target_digest = source['digest'], target['digest']
        self.source_image = source['image'] + '@' + source['digest']
        self.target_image = target['image'] + '@' + target['digest']
        self.target_version = self.target['version']
        self.dependencies = tuple(self.row.dependencies)
        self.policy = replace(self.policy, stability_period_seconds=args.budgets.stability_seconds)
        self._primary_sealed = False
        self._primary_snapshot = self._primary_members = None
        self._bootstrap_measurement = None
        self.shipping_executor_class = self.shipping_restore_class = None

    def assemble(self):
        from deploy.update.standalone_backup import BINDS
        if self.row.deployment_directory is None:
            self.row = p.retained_deployment(self.row, self.directory/'retained-deployment')
        self.execution = copy.deepcopy(self.row.execution_deployment)
        self.root.mkdir(mode=0o700)
        copy_retained_files(Path(self.row.deployment_directory), self.root, self.execution)
        # These shipped XML files are non-secret and must be readable by UID 101.
        for name in BINDS['clickhouse'].values():
            (self.root/name).chmod(0o644)
        # install_updater checks the retained map without importing its files.
        self.source_manifest = dict(self.row.release.manifest, deployment=self.execution)
        ProductHarness.install_updater(self)
        self.descriptor = self.row.release.manifest

    def install_and_boot(self):
        from deploy.update.manifest import parse_image_ref
        super().install_and_boot()
        actual = []
        model = self.installation.config(refresh=True)
        for service in ('postgres', 'clickhouse'):
            image = model['services'][service]['image']
            repository = parse_image_ref(image).repository
            digests = {parse_image_ref(ref).digest for ref in self.image_inspections[service]['RepoDigests']
                       if parse_image_ref(ref).repository == repository}
            p.need(len(digests) == 1, 'dependency image digest is ambiguous')
            actual.append(dict(service=service, image=image, digest=digests.pop()))
        if self.dependencies:
            p.equal(sorted(actual, key=lambda row: row['service']),
                    sorted(self.dependencies, key=lambda row: row['service']), 'retained dependency lock differs')
        self.dependencies = tuple(sorted(actual, key=lambda row: row['service']))
        from scripts.update_rehearsal_spool import ObservedSpoolWitness
        self.spool_witness = ObservedSpoolWitness(self)

    def seed(self):
        super().seed()
        p.witness_inventory(self.corpus['source_witnesses'])
        self.redis_key = 'qualification:' + uuid.uuid4().hex
        p.equal(self.app_python(REDIS_OPERATION, dict(action='seed', key=self.redis_key,
            value=uuid.uuid4().hex)), {'seeded': True}, 'source Redis witness was not read')
        self.corpus['redis_source_seeded'] = True

    def seed_reference_table(self, entry, rows):
        if (entry['database'], entry.get('app_label'), entry.get('model'), entry['table']) != (
                'postgres', 'prism', 'AgentccRequestLog', 'agentcc_request_log'):
            return super().seed_reference_table(entry, rows)
        from scripts.update_rehearsal_shipping import REFERENCE_SEED
        secret = self.installation.env.get('AGENTCC_WEBHOOK_SECRET')
        p.need(bool(secret), 'reference log ingestion credential unavailable')
        namespace = self.witnesses.namespaces['source']
        request_id = 'qualification-reference-' + uuid.uuid4().hex
        # Explicit synthetic reference data through the shipping ingestion API;
        # no provider call, billed usage, direct fabricated ORM sample or schema bypass.
        result = self.witnesses._http('/agentcc/webhook/logs/',
            headers={'X-Webhook-Secret':secret}, payload={'logs':[dict(request_id=request_id,
                model='', resolved_model='', provider='', status_code=200,
                metadata={'org_id':namespace['headers']['X-Organization-Id'], 'qualification_fixture':True},
                request_body={'qualification_fixture':True}, response_body={'qualification_fixture':True})]})
        p.equal(result, {'ingested':1}, 'reference log was not ingested by the product API')
        self.app_python(REFERENCE_SEED, dict(entry, rows=rows, sample_request_id=request_id))
        self.corpus.setdefault('reference_seed_operations', []).append(dict(
            table=entry['table'], operation='agentcc/webhook/logs/', request_id=request_id))

    def observe_source(self):
        from deploy.update.standalone_state import StandaloneStateObserver
        from deploy.update.standalone_backup import capture_binding
        observer = StandaloneStateObserver(self.registration, timeout=180,
            stability_period_seconds=self.policy.stability_period_seconds)
        observed = observer.observe(self.installation, current_release=self.row.release,
            execution_deployment=self.execution, boundary='enrollment')
        self.source_observation = observed.runtime_observation
        self.source_migrations = ProductHarness.migration_inventory(self)
        p.equal((self.source_migrations['inventory_sha256'], self.source_migrations['applied_sha256']),
                (self.source_observation.migration_inventory_sha256, self.source_observation.applied_migrations_sha256),
                'independent source migration observations disagree')
        self.binding = capture_binding(self.installation,
            installation_fingerprint=self.registration.binding.installation_fingerprint,
            docker_context=self.context, daemon_id=self.daemon, project=self.project, profiles=(),
            verified_images={service: row['Id'] for service, row in self.image_inspections.items()},
            deployment_hashes=self.execution['files'], previous=getattr(self, 'binding', None))
        if not self._primary_sealed:
            self.corpus['source_observation_preimages'] = retain_source_preimages(
                self.installation, self.registration, self.row.release, observed)
            self.corpus['source_runtime_observation'] = wire(observed.runtime_observation)
            self.corpus['source_installation_binding'] = wire(self.registration.binding)
            self.corpus['source_image_inspections'] = copy.deepcopy(self.image_inspections)
            self._source_migrations = copy.deepcopy(self.source_migrations)
        return observed

    def loader_program(self):
        return p.target_table_scope_probe()

    def loader_input(self):
        return dict(applied=self.source_migrations['applied'], database_schemas=self.database_schemas)

    def plan_target(self):
        from scripts.update_rehearsal_shipping import DJANGO_PREFIX
        schemas = self.app_python(DJANGO_PREFIX + '''
from django.db import connection
with connection.cursor() as c:
    c.execute('SELECT current_schema()')
    schema=c.fetchone()[0]
print(json.dumps({'postgres':schema}))
''', {})
        ch = self.clickhouse('SELECT currentDatabase() AS database FORMAT JSONEachRow')
        p.need(len(ch) == 1, 'ClickHouse database observation unavailable')
        self.database_schemas = dict(schemas, clickhouse=ch[0]['database'])
        self.target_plan = self.image_plan()
        self.corpus['target_pending_plan'] = copy.deepcopy(self.target_plan['pending'])
        self.corpus['pending_table_scope_probe'] = p.pending_table_scope_record(self.target_plan,
            target_image_digest=self.target_digest, source_applied_sha256=self.source_migrations['applied_sha256'])
        self.corpus['pending_migration_tables'] = copy.deepcopy(self.target_plan['pending_migration_tables'])

    def prepare_target(self):
        # Recheck after quiescence; an empty pending plan is an observed result.
        TargetMilestone.prepare_target(self)
        p.equal(self.target_plan['pending'], self.corpus['target_pending_plan'], 'pending plan changed after snapshot')
        p.equal(self.target_plan['pending_migration_tables'], self.corpus['pending_migration_tables'],
                'table scope changed after snapshot')

    def take_snapshot(self):
        from deploy.update.runner_state import TargetIdentity
        if not self._primary_sealed:
            self.corpus['source_witnesses_rechecked'] = self.current_source_witnesses()
            p.equal(self.corpus['source_witnesses_rechecked'], self.corpus['source_witnesses'],
                    'source witnesses changed before snapshot')
            self.observe_source()
        identity = TargetIdentity(self.target_version, p.manifest_hash(self.target))
        with self.locks():
            state = self.store.read_state()
            if state.failed_target_latch is not None:
                p.equal(state.failed_target_latch, identity, 'another target is latched')
                # Separate deliberate producer fault cases, not scheduler acceptance.
                self.store.retry_target(identity)
            self.attempt = self.store.reserve_attempt(identity, 'always', datetime.now(timezone.utc))
        if self._primary_sealed:
            return SnapshotMilestone.take_snapshot(self)
        role_times = p.VolumeRoleTransfers()
        with self.transfer_roles('archive', role_times):
            _, self.backup_measurement, transfer = p.measure_transfer(
                lambda: SnapshotMilestone.take_snapshot(self), self.capture_snapshot)
        snapshot, members = self.capture_snapshot()
        self._primary_snapshot, self._primary_members = snapshot, members
        self.corpus.update(source_snapshot_inventory=snapshot, source_snapshot_members=members,
            source_backup_proof=json.loads((self.ledger.directory/'proof.json').read_bytes()),
            source_backup_seal=wire(self.artifact.seal), backup_transfer=transfer,
            volume_role_transfers={'backup': role_times.finish(snapshot, members)})
        self.corpus['large_volume_reference'] = dict(snapshot_generation=p.sha(p.canonical_json(snapshot)),
            registry_sha256=p.sha(p.read_bytes(p.ROOT/'deploy/update/high-volume-tables.json', p.MAX_EVIDENCE_BYTES)),
            reference_rows=p.REFERENCE_ROWS, tables=copy.deepcopy(self.sink.reference))
        self._primary_sealed = True

    @contextmanager
    def transfer_roles(self, purpose, timings):
        from deploy.update.standalone_restore import _StorageHelpers
        original = _StorageHelpers.execute
        def measured(helper, role, operation, *args, **kwargs):
            call = lambda: original(helper, role, operation, *args, **kwargs)
            return timings.measure(role, call) if operation == purpose else call()
        _StorageHelpers.execute = measured
        try:
            yield
        finally:
            _StorageHelpers.execute = original

    @contextmanager
    def start_measurement(self, runtime):
        if self._bootstrap_measurement is not None:
            yield
            return
        from scripts.update_rehearsal_resources import NormalBootstrapEnvelopeSampler
        original = runtime.start_and_wait
        tables = [dict(engine=row['engine'], table=row['table'], reference_rows=row['rows'],
                       reference_bytes=row['bytes']) for row in self.sink.reference]
        tables.sort(key=lambda row: (row['engine'], row['table']))
        scope = reference_scope(self.corpus['target_pending_plan'], self.corpus['pending_migration_tables'], tables)
        def measured(roster, *args, **kwargs):
            sampler = NormalBootstrapEnvelopeSampler(self.installation, binding=self.backup.b,
                roster=roster, target_image=self.target['components']['standalone'],
                snapshot_generation=p.sha(p.canonical_json(self._primary_snapshot)),
                pending_plan=self.corpus['target_pending_plan'], tables=tables, migration_tables=scope)
            result = sampler.measure(lambda: original(roster, *args, **kwargs))
            self._bootstrap_measurement = copy.deepcopy(result.migration_measurement)
            self.corpus['bootstrap_envelope'] = copy.deepcopy(result.private_evidence)
            return result.operation_result
        runtime.start_and_wait = measured
        try:
            yield
        finally:
            runtime.start_and_wait = original

    def target_ready(self, runtime, roster):
        # Fault injection follows the independently recorded readiness suites.
        from scripts.update_rehearsal_shipping import DJANGO_PREFIX
        self.runtime = runtime
        self._target_migrations = ProductHarness.migration_inventory(self)
        mapping = self.app_python(DJANGO_PREFIX + '''
from django.apps import apps
rows=sorted([{'label':app.label,'name':app.name,'path':app.path} for app in apps.get_app_configs()],key=lambda r:r['label'])
print(json.dumps(rows))
''', {})
        p.equal(p.manifest_hash(mapping), self.corpus['source_runtime_observation']['active_app_mapping_sha256'],
                'target active application mapping differs from source')

    def current_source_witnesses(self):
        rows = self.witnesses.read('source')
        rows.extend(dict(domain='postgres_app', origin='product_api', identity='project/'+row['id'],
            payload_sha256=p.manifest_hash(row)) for row in self.project_witnesses)
        return p.witness_inventory(rows)

    def verify_restored_source(self):
        observed = super().verify_restored_source()
        self.spool_witness.observe_bootstrap_delivery('source')
        rows = self.current_source_witnesses()
        p.equal(rows, self.corpus['source_witnesses'], 'restored namespace differs')
        self.corpus['restored_witnesses'] = rows
        return observed

    def domain_equal(self, observed, expected, domain):
        actual = [row for row in observed if row['domain'] == domain]
        p.need(bool(actual), 'unrun persistent-domain witness')
        p.equal(actual, [row for row in expected if row['domain'] == domain], 'persistent-domain witness differs')

    def check(self, suite, name, assertion):
        self.sink.check(suite, name, assertion)

    def assert_reference_inventory(self):
        measured = p.validate_reference_inventory(self.corpus['large_volume_reference'], registry=self.registry,
            pending_migration_tables=self.corpus['pending_migration_tables'], plan=self.corpus['target_pending_plan'])
        p.equal(measured, {(row['engine'], row['table']): row for row in self.sink.reference},
                'reference inventory differs from the prepared reference')

    def assert_restored_source_ready(self):
        observed = self.verify_restored_source().facts.runtime_observation
        p.need(observed.ready and observed.stable, 'restored source is not ready and stable')

    def source_suite(self):
        suite = 'source_snapshot'
        self.check(suite, 'source_identity', lambda: p.equal(
            self.corpus['source_runtime_observation']['manifest_sha256'], self.row.release.manifest_sha256,
            'source is not the authenticated predecessor'))
        self.check(suite, 'source_migrations_exact', lambda: p.need(
            not self._source_migrations['pending'] and self._source_migrations['applied'] == self._source_migrations['planned'],
            'source migration inventory is incomplete'))
        self.check(suite, 'snapshot_sealed', lambda: p.equal(self.ledger.backup_seal, self.artifact.seal, 'snapshot seal differs'))
        self.check(suite, 'persistent_domain_mapping', lambda: p.equal(
            set(self.artifact.proof.covered_domains), set(p.topology_profile('standalone', ()).backup_domains), 'snapshot domain coverage differs'))
        self.check(suite, 'witness_namespace_complete', lambda: p.equal(
            p.witness_inventory(self.corpus['source_witnesses_rechecked']), self.corpus['source_witnesses'],
            'source witness namespace changed'))
        self.check(suite, 'large_volume_reference', self.assert_reference_inventory)
        for domain in p.topology_profile('standalone', ()).backup_domains:
            self.check(suite, 'source_witness:'+domain, lambda d=domain: self.domain_equal(
                self.corpus['source_witnesses_rechecked'], self.corpus['source_witnesses'], d))

    def migration_evidence(self):
        source, target = self._source_migrations, self._target_migrations
        return dict(source_inventory_sha256=source['inventory_sha256'],
            target_inventory_sha256=target['inventory_sha256'], source_applied_set_sha256=source['applied_sha256'],
            target_applied_set_sha256=target['applied_sha256'],
            active_app_mapping_sha256=self.corpus['source_runtime_observation']['active_app_mapping_sha256'],
            target_pending_plan_sha256=p.pending_migration_plan_hash(self.corpus['target_pending_plan']),
            target_pending_plan_count=len(self.corpus['target_pending_plan']), migration_handling='standalone_bootstrap')

    def measurements(self):
        p.need(self._bootstrap_measurement is not None and hasattr(self, 'restore_measurement'),
               'complete executed measurements unavailable')
        return dict(migration_evidence=self.migration_evidence(),
            migration_measurements=copy.deepcopy(self._bootstrap_measurement),
            backup_measurement=copy.deepcopy(self.backup_measurement), restore_measurement=copy.deepcopy(self.restore_measurement),
            persistent_coverage=list(p.topology_profile('standalone', ()).backup_domains))

    def migration_suite(self):
        suite = 'migration_forward'
        fields = dict(self.row.binding(), migration_evidence=self.migration_evidence(),
                      migration_measurements=copy.deepcopy(self._bootstrap_measurement))
        self.check(suite, 'pending_plan_measured', lambda: p.validate_scope_probe(fields, self.corpus, self.target))
        pending = sorted([[row['app_label'], row['name']] for row in self.corpus['target_pending_plan']])
        applied = sorted(row for row in self._target_migrations['applied']
                         if row not in self._source_migrations['applied'])
        self.check(suite, 'pending_plan_applied_exact', lambda: p.equal(applied, pending,
            'normal bootstrap did not apply the exact planned migration set'))
        self.check(suite, 'target_migrations_exact', lambda: p.need(not self._target_migrations['pending'] and
            self._target_migrations['applied'] == self.target_plan['planned'] and
            self._target_migrations['inventory_sha256'] == self.target_plan['inventory_sha256'],
            'target recorder/inventory differs from the measured target plan'))
        references = {(row['engine'], row['table']): row for row in self.sink.reference}
        self.check(suite, 'migration_resource_measurements', lambda: p.equal(validate_bootstrap_envelope(
            fields, self.corpus, self.target, references), fields['migration_measurements'],
            'validated bootstrap measurement differs'))
        self.check(suite, 'large_volume_migrations', self.assert_reference_inventory)
        old = self.current_source_witnesses()
        for domain in p.topology_profile('standalone', ()).backup_domains:
            self.check(suite, 'new_reads_old:'+domain,
                lambda d=domain: self.domain_equal(old, self.corpus['source_witnesses'], d))
        self.spool_witness.observe_bootstrap_delivery('source')
        created = p.witness_inventory(self.witnesses.create('target'))
        readback = p.witness_inventory(self.witnesses.read('target'))
        self.corpus['target_witnesses'] = created
        for domain in p.topology_profile('standalone', ()).backup_domains:
            self.check(suite, 'target_create_read:'+domain,
                lambda d=domain: self.domain_equal(readback, created, d))

    @contextmanager
    def forward_budget(self):
        original = self.runner.deadline
        p.need(original-time.monotonic() > self.policy.required_recovery_budget_seconds,
               'protected recovery budget unavailable')
        self.runner.deadline = min(original, self.deadline-self.policy.required_recovery_budget_seconds)
        try:
            yield
        finally:
            self.runner.deadline = original

    def run_forward_and_failure(self):
        from deploy.update.apply import Interrupted, Updater
        from deploy.update.orchestrator import GuardRefused
        from deploy.update.automatic_runtime import RuntimeRefused
        from deploy.update.standalone_restore import StandaloneRestore
        from scripts.update_rehearsal_shipping import ShippingRehearsal
        with self.locks():
            try:
                with self.forward_budget(), self.running_target() as (runtime, roster, session):
                    p.need(type(self.engine) is Updater, 'shipping updater transaction unavailable')
                    self.shipping_executor_class = type(self.engine)
                    self.runtime = runtime
                    self.corpus['target_image_inspection'] = {key: self.target_inspection[key]
                        for key in ('Id', 'Os', 'Architecture', 'RepoDigests')}
                    self.corpus['target_container_inspections'] = {
                        service: runtime._exact(identity) for service, identity in roster.items()}
                    self.migration_suite()
                    for name, method in (
                        ('normal_compose_direct_start', ShippingRehearsal._normal_model),
                        ('health', ShippingRehearsal._target_health),
                        ('restart_count_stability', ShippingRehearsal._target_stability),
                        ('final_identities', ShippingRehearsal._target_identities),
                        ('redis_ephemeral', ShippingRehearsal._redis_ephemeral)):
                        self.check('target_readiness', name, lambda operation=method: operation(self))
                    session.check()
                    def fault():
                        p.equal(ProductHarness.migration_inventory(self), self._target_migrations,
                                'target changed before failure injection')
                        self.installation.docker_run('stop', '--time', '60', roster['app']['Id'])
                        try:
                            session.check()
                        except (GuardRefused, RuntimeRefused) as exc:
                            p.equal(exc.reason_code.value, 'readiness_failed', 'injected failure has another cause')
                            self.forward_failure = exc
                        else:
                            raise p.RehearsalError('stopped target was accepted as healthy')
                    self.check('failure_restore', 'failure_after_migration', fault)
            except (Exception, Interrupted, KeyboardInterrupt) as exc:
                # A failed assertion is still a real failed forward attempt.
                # Restore through the same shipping handler before keeping it red.
                self.record('unexpected-forward-failure.json', p.failure_diagnostics(exc, self))
                self.recover(exc)
                raise
            role_times = p.VolumeRoleTransfers()
            with self.transfer_roles('extract', role_times):
                _, self.restore_measurement, transfer = p.measure_transfer(
                    lambda: self.recover(self.forward_failure),
                    lambda: (self._primary_snapshot, self._primary_members))
            p.need(self.engine.automatic_outcome == 'restored', 'shipping restore did not complete')
            self.shipping_restore_class = StandaloneRestore
            self.corpus['restore_transfer'] = transfer
            self.corpus['volume_role_transfers']['restore'] = role_times.finish(
                self._primary_snapshot, self._primary_members)
            self.restore_assertions('failure_restore')
        self._remember_resources()

    def restore_assertions(self, suite):
        self.check(suite, 'same_volume_objects', lambda: p.equal(
            {key: json.loads(self.binding.raw)[key] for key in ('volumes', 'volume_root_ids')},
            {key: self._primary_snapshot['binding'][key] for key in ('volumes', 'volume_root_ids')},
            'restore replaced original volume objects or roots'))
        self.check(suite, 'shipping_restore', lambda: p.need(self.engine.automatic_outcome == 'restored' and
            self.ledger.phase == 'restored', 'shipping recovery did not reach restored'))
        self.check(suite, 'source_ready', self.assert_restored_source_ready)
        self.check(suite, 'source_migrations_exact', lambda: p.equal(
            ProductHarness.migration_inventory(self), self._source_migrations, 'source migrations not restored'))
        self.check(suite, 'candidate_only_absent', lambda: self.witnesses.assert_absent('target'))
        self.check(suite, 'restored_namespace_complete', lambda: p.equal(
            p.witness_inventory(self.current_source_witnesses()), self.corpus['source_witnesses'],
            'restored witness namespace changed'))
        for domain in p.topology_profile('standalone', ()).backup_domains:
            self.check(suite, 'restored_witness:'+domain, lambda d=domain: self.domain_equal(
                self.current_source_witnesses(), self.corpus['source_witnesses'], d))

    def interruption_reserve_seconds(self, remaining_count):
        # This job also owns source boot and the other five suites. Reserve a
        # complete cold transaction for the NEXT case, using actual remaining
        # time rather than summing hypothetical maxima of all future cases.
        # Every case still has protected recovery; any unrun case refuses the row.
        return self.policy.required_cold_budget_seconds

    def interruption_suite(self):
        self.output = self.evidence_root/'interruptions'
        self.output.mkdir(mode=0o700)
        self.check('interruption_restore', 'updater_process_terminated', self.interruption_restore)
        summary = json.loads((self.output/'interruption-summary.json').read_bytes())
        cases = summary['boundaries']
        self.corpus['interruption_results'] = copy.deepcopy(cases)
        self.check('interruption_restore', 'fresh_recovery_process', lambda: p.need(
            all(row['killed_pid'] != row['recovery_pid'] for row in cases), 'recovery reused killed process'))
        self.check('interruption_restore', 'durable_boundaries', lambda: validate_interruption_results(cases))
        self.verify_restored_source()
        with self.locks():
            self.restore_assertions('interruption_restore')

    def integrity_suite(self):
        # A new sealed attempt, never reset a previous-start barrier to test corruption.
        # Its projections must not overwrite the measured primary attempt.
        self.output = self.evidence_root/'integrity'
        self.output.mkdir(mode=0o700)
        self.scenario = 'qualification-integrity'
        self.take_snapshot()
        self.verify_snapshot()
        self.check('backup_integrity', 'full_extracted_trees', self.backup_integrity)
        self.check('backup_integrity', 'configuration_exact', lambda: p.equal(
            p.sha((self.root/'.env').read_bytes()), self.corpus['source_runtime_observation']['env_sha256'],
            'restored environment differs from the source snapshot'))
        for kind, case in (('tampered', 'tampered_archive'), ('truncated', 'truncated_archive'),
                           ('substituted', 'substituted_archive'), ('incomplete', 'truncated_inventory')):
            def refused(name=case):
                evidence = json.loads((self.output/('integrity-'+name+'.json')).read_bytes())
                p.need(evidence['case'] == name and evidence['before_first_clear'] is True and
                    evidence['checkpoint']['completed_volume_count'] == 0 and
                    not evidence['checkpoint']['operation_pending'], 'corrupt backup reached destructive restore')
            self.check('backup_integrity', 'refuses:'+kind, refused)
        fields = dict(self.row.binding(), **self.measurements())
        self.check('backup_integrity', 'backup_restore_throughput', lambda: p.validate_volume_role_transfers(fields, self.corpus))

    def execute(self):
        operations = (
            ('assemble_authenticated_source', self.assemble), ('install_and_boot_source', self.install_and_boot),
            ('seed_product_witnesses', self.seed), ('observe_authenticated_source', self.observe_source),
            ('prefetch_exact_target', self.prefetch_target), ('probe_pending_scope', self.plan_target),
            ('seed_registered_reference', lambda: self.sink.prepare_reference(self)),
            ('source_snapshot', self.take_snapshot), ('verify_cold_hold', self.verify_snapshot),
            ('recheck_cold_target_plan', self.prepare_target), ('source_assertions', self.source_suite),
            ('migration_readiness_and_failure_restore', self.run_forward_and_failure),
            ('interruption_restore', self.interruption_suite), ('backup_integrity', self.integrity_suite),
            ('capture_complete_evidence', lambda: self.sink.capture(self)),
        )
        for name, operation in operations:
            p.need(time.monotonic() < self.deadline, 'qualification deadline exceeded')
            self._phase(name, operation)
            p.need(time.monotonic() < self.deadline, 'qualification deadline exceeded')


def qualification_plan(args):
    from deploy.update.manifest import parse_image_ref
    source, target = parse_image_ref(args.source_ref), parse_image_ref(args.target_ref)
    p.need(source.digest is not None and target.digest is not None and args.platform == 'linux/amd64',
           'qualification requires an exact native amd64 pair')
    return label(milestone='qualification', source_image=args.source_ref, target_image=args.target_ref,
        platform=args.platform, qualification_suites={suite: 'planned' for suite in p.SUITES},
        source_boot_ceiling_seconds=args.boot_timeout, admission_bypass=False,
        measurement_basis='normal-bootstrap-envelope-v1')


def run_qualification(args):
    from deploy.update.apply import Interrupted
    output = Path(args.out).absolute()
    p.need(not output.exists() and not output.is_symlink(), 'qualification milestone output must be new')
    output.mkdir(mode=0o700, parents=True)
    record(output, 'job-plan.json', qualification_plan(args))
    if args.dry_run:
        record(output, 'milestone.json', label(milestone='qualification', status='planned',
            executed_stages=[], cleanup_verified=False))
        return 0
    driver = None
    failure = None
    previous = signal.getsignal(signal.SIGTERM)
    def terminated(signum, frame):
        raise Interrupted(signal.Signals(signum).name)
    signal.signal(signal.SIGTERM, terminated)
    try:
        from scripts.update_rehearsal_resources import _native_amd64_actions
        p._validate_context(args.docker_context, output)
        p.need(_native_amd64_actions() and output.resolve().is_relative_to(Path.home().resolve()),
               'isolated native Actions required')
        p.need(isinstance(args.qualification, p.QualificationSession) and args.qualification.row is args.row
               and args.qualification.target == args.target, 'qualification sink belongs to another pair')
        driver = QualificationRehearsal(args)
        driver.execute()
    except (Exception, Interrupted, KeyboardInterrupt) as exc:
        failure = p.failure_diagnostics(exc, driver)
        failure['stage'] = driver._diagnostic_data['active_phase'] if driver else 'qualification_setup'
    finally:
        if driver is not None:
            try:
                driver.cleanup()
                p.need(driver.cleanup_verified is True, 'qualification cleanup unverified')
            except (Exception, Interrupted, KeyboardInterrupt) as exc:
                if failure is None:
                    failure = dict(p.failure_diagnostics(exc, driver), stage='cleanup')
        signal.signal(signal.SIGTERM, previous)
    if failure is None:
        try:
            args.qualification.validate_finished()
        except (Exception, Interrupted, KeyboardInterrupt) as exc:
            failure = dict(p.failure_diagnostics(exc, driver), stage='validate_finished')
    result = label(milestone='qualification', status='failed' if failure else 'passed',
        executed_stages=[row['phase'] for row in driver._diagnostic_data['phase_timings']
                         if row['outcome'] == 'passed'] if driver else [],
        cleanup_verified=driver is not None and driver.cleanup_verified is True, failure=failure)
    record(output, 'milestone.json', result)
    if failure:
        record(output, 'failure.json', result)
        return 2
    return 0
