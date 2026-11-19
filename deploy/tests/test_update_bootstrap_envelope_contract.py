"""Synthetic envelope contract regressions, not release qualification evidence."""
import copy
import importlib.util
import unittest
from dataclasses import replace

from deploy.tests.test_update_compatibility import bind_proof, restore_release
from deploy.update.compatibility import (CompatibilityError, estimate_automatic_update, schema, shape,
                                         transition_measurements, validate_release_proof)
from deploy.update.contracts import (AUTOMATIC_VOLUMES, AutomationPolicy, BootstrapEnvelopeMeasurement,
                                     HostSizeObservation, HostTableSize, ReasonCode,
                                     pending_migration_plan_hash)


def fixture(count=1, tables=True):
    target, _, proof, _ = restore_release(count)
    row = proof['predecessors'][0]
    plan = [{'app_label': m['app_label'], 'name': m['name']} for m in row['migration_measurements']]
    row['migration_measurements'] = {
        'measurement_basis': 'normal-bootstrap-envelope-v1', 'pending_plan': plan,
        'migration_tables': [dict(item, tables=([{'engine': 'postgres', 'table': 'public.synthetic_table'}]
                                               if tables and index == 0 else []))
                             for index, item in enumerate(plan)],
        'tables': ([{'engine': 'postgres', 'table': 'public.synthetic_table',
                     'reference_rows': 100000, 'reference_bytes': 1000}] if tables else []),
        'duration_seconds': 5.5, 'peak_disk_growth_bytes': 1200,
        'wal_bytes': 300, 'temp_bytes': 100, 'peak_memory_bytes': 4000,
    }
    return target, proof, row


def host(rows=100, size=500, include=True):
    return HostSizeObservation((HostTableSize('postgres', 'public.synthetic_table', rows, size),) if include else (),
                               {name: 1000 for name in AUTOMATIC_VOLUMES}, 10**12, 10**12, False,
                               {name: 1000 for name in AUTOMATIC_VOLUMES}, transfer_members={role: 1 for role in AUTOMATIC_VOLUMES})


def estimate(row, observation=None, **kwargs):
    return estimate_automatic_update(transition_measurements(row), observation or host(),
        AutomationPolicy(mode='apply', qualification_policy='standalone-cold-restore-v1'),
        window_remaining_seconds=kwargs.get('window', 14400), tick_remaining_seconds=14400)


class BootstrapEnvelopeTests(unittest.TestCase):
    def test_full_proof_admission_and_immutable_conversion(self):
        target, proof, row = fixture()
        raw = bind_proof(target, proof)
        validate_release_proof(raw, target, repository='future-agi/future-agi')
        result = transition_measurements(row).migration_measurements
        self.assertIsInstance(result, BootstrapEnvelopeMeasurement)
        row['migration_measurements']['pending_plan'][0]['name'] = 'changed'
        self.assertNotEqual(result.pending_plan[0]['name'], 'changed')
        with self.assertRaises(TypeError):
            result.pending_plan[0]['name'] = 'changed'
        row['migration_measurements']['migration_tables'][0]['tables'][0]['table'] = 'public.changed'
        self.assertEqual(result.migration_tables[0]['tables'][0]['table'], 'public.synthetic_table')
        with self.assertRaises(TypeError):
            result.migration_tables[0]['tables'][0]['table'] = 'public.changed'

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'jsonschema not installed')
    def test_schema_runtime_shape_parity(self):
        from jsonschema import Draft202012Validator
        spec = schema('compatibility-proof.schema.json')
        Draft202012Validator.check_schema(spec)
        validator = Draft202012Validator(spec)
        _, proof, _ = fixture()
        self.assertTrue(validator.is_valid(proof))
        shape(proof, spec)
        base = proof['predecessors'][0]['migration_measurements']
        variations = []
        for key, value in [('measurement_basis', 'unknown'), ('duration_seconds', 0),
                           ('duration_seconds', True), ('duration_seconds', 1e19),
                           ('peak_disk_growth_bytes', -1), ('wal_bytes', 1.5),
                           ('temp_bytes', True), ('peak_memory_bytes', -1),
                           ('pending_plan', [{}]), ('tables', [{'engine': 'postgres'}]),
                           ('migration_tables', [{}]),
                           ('migration_tables', [{'app_label': 'app', 'name': '0001', 'tables': [{}]}]),
                           ('migration_tables', [{'app_label': 'app', 'name': '0001', 'tables': [
                               {'engine': 'postgres', 'table': 'public.t'},
                               {'engine': 'postgres', 'table': 'public.t'}]}]),
                           ('extra', 1)]:
            value_copy = copy.deepcopy(base)
            value_copy[key] = value
            variations.append(value_copy)
        for key in base:
            missing = copy.deepcopy(base)
            del missing[key]
            variations.append(missing)
        variations += [[copy.deepcopy(base)], [copy.deepcopy(base), copy.deepcopy(base)],
                       [copy.deepcopy(base), {'app_label': 'a'}], None]
        for value in variations:
            bad = copy.deepcopy(proof)
            bad['predecessors'][0]['migration_measurements'] = value
            with self.subTest(value=value):
                self.assertFalse(validator.is_valid(bad))
                with self.assertRaises(CompatibilityError):
                    shape(bad, spec)

    def test_plan_and_table_semantics_fail_closed(self):
        for mutation in ('hash', 'count', 'order', 'duplicate_plan', 'duplicate_table', 'empty_with_table'):
            _, _, row = fixture(2 if mutation in ('order', 'duplicate_plan') else 1)
            env = row['migration_measurements']
            if mutation == 'hash':
                row['migration_evidence']['target_pending_plan_sha256'] = 'f' * 64
            elif mutation == 'count':
                row['migration_evidence']['target_pending_plan_count'] += 1
            elif mutation == 'order':
                env['pending_plan'].reverse()
            elif mutation == 'duplicate_plan':
                env['pending_plan'][1] = copy.deepcopy(env['pending_plan'][0])
            elif mutation == 'duplicate_table':
                duplicate = copy.deepcopy(env['tables'][0])
                duplicate['reference_rows'] += 1
                env['tables'].append(duplicate)
            else:
                env['pending_plan'] = []
            with self.subTest(mutation=mutation), self.assertRaises(CompatibilityError):
                transition_measurements(row)

    def test_schema_shape_cannot_enforce_cross_field_scope_semantics(self):
        for mutation in ('reordered', 'union'):
            target, proof, row = fixture(2)
            if mutation == 'reordered':
                row['migration_measurements']['migration_tables'].reverse()
            else:
                row['migration_measurements']['migration_tables'][0]['tables'] = []
            raw = bind_proof(target, proof)
            shape(proof, schema('compatibility-proof.schema.json'))
            with self.subTest(mutation=mutation), self.assertRaises(CompatibilityError):
                validate_release_proof(raw, target, repository='future-agi/future-agi')

    def test_signed_shape_without_matching_plan_is_not_admitted(self):
        target, proof, row = fixture()
        row['migration_evidence']['target_pending_plan_sha256'] = 'f' * 64
        raw = bind_proof(target, proof)
        shape(proof, schema('compatibility-proof.schema.json'))
        with self.assertRaises(CompatibilityError):
            validate_release_proof(raw, target, repository='future-agi/future-agi')

    def test_nonfinite_and_out_of_range_values_refused(self):
        for field, value in [('duration_seconds', float('nan')), ('duration_seconds', float('inf')),
                             ('peak_memory_bytes', 9007199254740992), ('wal_bytes', False),
                             ('temp_bytes', -1), ('peak_disk_growth_bytes', 1.5)]:
            _, _, row = fixture()
            row['migration_measurements'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(CompatibilityError):
                transition_measurements(row)

    def test_envelope_charged_in_full_and_no_reference_scaling(self):
        _, _, row = fixture()
        for observation in (host(), host(rows=0, size=0)):
            result = estimate(row, observation)
            self.assertTrue(result.allowed, result.reason_codes)
            self.assertEqual(result.estimate.migration_seconds, 11)
            self.assertEqual(result.estimate.extra_disk_bytes, 3200)
        for observation in (host(rows=100001), host(size=1001), host(include=False)):
            result = estimate(row, observation)
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
        constrained = replace(host(), docker_free_bytes=2 * 3000 + 1073741824 + 3199)
        self.assertIn(ReasonCode.INSUFFICIENT_DISK_FOR_MIGRATION, estimate(row, constrained).reason_codes)
        total = estimate(row).estimate.total_seconds
        self.assertIn(ReasonCode.MIGRATION_EXCEEDS_WINDOW, estimate(row, window=total - 1).reason_codes)

    def test_zero_plan_still_charges_envelope_exact_empty_remains_valid(self):
        _, _, row = fixture(0, tables=False)
        result = estimate(row, host(include=False))
        self.assertTrue(result.allowed, result.reason_codes)
        self.assertEqual((result.estimate.migration_seconds, result.estimate.extra_disk_bytes), (11, 3200))
        row['migration_measurements'] = []
        result = estimate(row, host(include=False))
        self.assertTrue(result.allowed)
        self.assertEqual((result.estimate.migration_seconds, result.estimate.extra_disk_bytes), (0, 0))

    def test_scopes_are_ordered_complete_and_exact_union(self):
        for mutation in ('missing_scope', 'reordered', 'wrong_identity', 'missing_table', 'extra_table',
                         'duplicate_in_scope', 'malformed', 'missing_tables'):
            _, _, row = fixture(2)
            scopes = row['migration_measurements']['migration_tables']
            if mutation == 'missing_scope':
                scopes.pop()
            elif mutation == 'reordered':
                scopes.reverse()
            elif mutation == 'wrong_identity':
                scopes[0]['name'] = 'other'
            elif mutation == 'missing_table':
                scopes[0]['tables'] = []
            elif mutation == 'extra_table':
                scopes[1]['tables'].append({'engine': 'postgres', 'table': 'public.extra'})
            elif mutation == 'duplicate_in_scope':
                scopes[0]['tables'].append(copy.deepcopy(scopes[0]['tables'][0]))
            elif mutation == 'malformed':
                scopes[0]['tables'][0]['unexpected'] = True
            else:
                del scopes[0]['tables']
            with self.subTest(mutation=mutation), self.assertRaises(CompatibilityError):
                transition_measurements(row)

    def test_repeated_scopes_share_one_reference_and_charge_whole_envelope_once(self):
        for count in (2, 3):
            with self.subTest(count=count):
                target, proof, row = fixture(count)
                env = row['migration_measurements']
                baseline = estimate(row)
                for scope in env['migration_tables'][1:]:
                    scope['tables'] = copy.deepcopy(env['migration_tables'][0]['tables'])
                validate_release_proof(bind_proof(target, proof), target, repository='future-agi/future-agi')
                measurements = transition_measurements(row).migration_measurements
                self.assertEqual(len(measurements.tables), 1)
                self.assertEqual(len(measurements.migration_tables), count)
                result = estimate(row, host(rows=100000, size=1000))
                self.assertTrue(result.allowed, result.reason_codes)
                self.assertEqual(result.estimate, baseline.estimate)
                self.assertEqual((result.estimate.migration_seconds, result.estimate.extra_disk_bytes), (11, 3200))

    def test_repeated_scopes_do_not_enlarge_reference_bounds_or_supply_missing_observations(self):
        _, _, row = fixture(2)
        env = row['migration_measurements']
        env['migration_tables'][1]['tables'] = copy.deepcopy(env['migration_tables'][0]['tables'])
        for observation in (host(rows=100001), host(size=1001), host(include=False)):
            with self.subTest(observation=observation):
                result = estimate(row, observation)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))

    def test_repeated_scopes_still_refuse_conflicting_or_missing_reference_evidence(self):
        for mutation in ('duplicate', 'rows', 'bytes', 'missing', 'engine'):
            target, proof, row = fixture(2)
            env = row['migration_measurements']
            env['migration_tables'][1]['tables'] = copy.deepcopy(env['migration_tables'][0]['tables'])
            if mutation in ('duplicate', 'rows', 'bytes'):
                duplicate = copy.deepcopy(env['tables'][0])
                if mutation != 'duplicate':
                    duplicate['reference_' + mutation] += 1
                env['tables'].append(duplicate)
            elif mutation == 'missing':
                env['tables'] = []
            else:
                env['tables'][0]['engine'] = 'clickhouse'
            with self.subTest(mutation=mutation):
                with self.assertRaises(CompatibilityError):
                    transition_measurements(row)
                with self.assertRaises(CompatibilityError):
                    validate_release_proof(bind_proof(target, proof), target, repository='future-agi/future-agi')

    def test_repeated_scopes_require_capacity_for_combined_envelope_growth(self):
        _, _, row = fixture(2)
        env = row['migration_measurements']
        env['migration_tables'][1]['tables'] = copy.deepcopy(env['migration_tables'][0]['tables'])
        full = estimate(row).estimate
        # Disk growth, WAL and temp each fit alone, but their conservative sum
        # (including the safety factor) is required even with a shared table.
        constrained = replace(host(), docker_free_bytes=full.docker_required_free_bytes - 1)
        result = estimate(row, constrained)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_codes, (ReasonCode.INSUFFICIENT_DISK_FOR_MIGRATION,))
        self.assertTrue(estimate(row, replace(constrained, docker_free_bytes=full.docker_required_free_bytes)).allowed)
        self.assertIn(ReasonCode.MIGRATION_EXCEEDS_WINDOW,
                      estimate(row, window=full.total_seconds - 1).reason_codes)

    def test_producer_shared_eval_task_scope_is_accepted_by_consumer(self):
        from scripts.update_rehearsal_qualification import reference_scope
        from scripts.update_rehearsal_resources import reduce_envelope

        target, proof, row = fixture(2)
        plan = [{'app_label': 'tracer', 'name': name}
                for name in ('0117_cohort_sampling', '0118_instant_eval_answers')]
        tables = [{'engine': 'postgres', 'table': 'public.tracer_eval_task',
                   'reference_rows': 100000, 'reference_bytes': 4096000}]
        scoped_table = {'engine': 'postgres', 'table': 'public.tracer_eval_task'}
        scopes = [dict(identity, tables=[dict(scoped_table)]) for identity in plan]
        scopes[0]['tables'].append({'engine': 'postgres', 'table': 'public.tracer_project'})
        scope = reference_scope(plan, scopes, tables)
        samples = [dict(monotonic=stamp, boundary=boundary, disk_bytes=disk,
                        wal_bytes=300, temp_bytes=100, memory_bytes=4000)
                   for stamp, boundary, disk in ((10., 'before_start', 1000),
                                                (11., 'during', 2200), (12., 'after_end', 2000))]
        row['migration_measurements'] = reduce_envelope(plan, tables, scope, samples, 1.5,
            started_monotonic=10.25, completed_monotonic=11.75)
        row['migration_evidence']['target_pending_plan_sha256'] = pending_migration_plan_hash(plan)
        validate_release_proof(bind_proof(target, proof), target, repository='future-agi/future-agi')
        self.assertEqual(row['migration_measurements']['tables'], tables)
        self.assertEqual([item['tables'] for item in scope], [[scoped_table], [scoped_table]])
        observation = replace(host(), table_sizes=(HostTableSize('postgres', 'public.tracer_eval_task',
                                                                100000, 4096000),))
        result = estimate(row, observation)
        self.assertTrue(result.allowed, result.reason_codes)
        self.assertEqual((result.estimate.migration_seconds, result.estimate.extra_disk_bytes), (3, 3200))

    def test_disjoint_and_empty_scopes_allowed_with_exact_reference_union(self):
        _, _, row = fixture(2)
        env = row['migration_measurements']
        self.assertTrue(estimate(row).allowed)  # Second intersection is explicitly empty.
        env['migration_tables'][1]['tables'] = [{'engine': 'clickhouse', 'table': 'default.other'}]
        env['tables'].append({'engine': 'clickhouse', 'table': 'default.other',
                              'reference_rows': 10, 'reference_bytes': 20})
        observation = replace(host(), table_sizes=host().table_sizes +
                              (HostTableSize('clickhouse', 'default.other', 10, 20),))
        self.assertTrue(estimate(row, observation).allowed)
        self.assertEqual(estimate(row, observation).estimate.migration_seconds, 11)
        for missing in (0, 1):
            with self.subTest(missing=missing):
                remaining = tuple(table for index, table in enumerate(observation.table_sizes) if index != missing)
                result = estimate(row, replace(observation, table_sizes=remaining))
                self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_CAPACITY_UNKNOWN,))
        env['tables'] = []
        for scope in env['migration_tables']:
            scope['tables'] = []
        self.assertTrue(estimate(row, host(include=False)).allowed)


if __name__ == '__main__':
    unittest.main()
