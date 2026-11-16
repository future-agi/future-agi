"""Daemon-free failure projections; never affirmative product qualification."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_qualification as q
from scripts import update_rehearsal_resources as resources
from scripts import update_rehearsal_spool as spool


class QualificationDiagnosticsTests(unittest.TestCase):
    def test_pre_sink_failure_keeps_stage_and_fixed_detail_through_outer_projection(self):
        errors = (
            (p.RehearsalError('source is not digest-pinned'), 'suite_failed', 'source is not digest-pinned'),
            (resources.ResourceObservationError('resource_identity_ambiguous'), 'resource_identity_ambiguous', None),
            (spool.ObservedSpoolWitnessError('unsupported observed-catalog table layout'),
             'witness_operation_unavailable', 'unsupported observed-catalog table layout'),
            (RuntimeError('PRIVATE PASSWORD'), 'suite_failed', None),
        )
        for error, code, detail in errors:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
                args = SimpleNamespace(out=Path(temporary)/'out', dry_run=False,
                    platform='linux/amd64', boot_timeout=4200, docker_context='colima-fagi-update',
                    source_ref='futureagi/standalone:v1.54.0@sha256:'+'a'*64,
                    target_ref='futureagi/standalone:v1.55.0@sha256:'+'b'*64, row=object(), target={})
                session = object.__new__(p.QualificationSession)
                session.row, session.target = args.row, args.target
                session.prepare_reference = mock.Mock()
                session.validate_finished = mock.Mock()
                args.qualification = session
                driver = object.__new__(q.QualificationRehearsal)
                driver.sink, driver.deadline = session, time.monotonic()+30
                driver._diagnostic_data = dict(active_phase='not_started', phase_timings=[])
                driver.assemble = mock.Mock()
                driver.install_and_boot = mock.Mock(side_effect=error)
                driver.cleanup_verified = False
                driver.cleanup = mock.Mock(side_effect=lambda: setattr(driver, 'cleanup_verified', True))
                with mock.patch.object(q, 'QualificationRehearsal', return_value=driver), \
                     mock.patch.object(p, '_validate_context'), \
                     mock.patch.object(resources, '_native_amd64_actions', return_value=True):
                    self.assertEqual(q.run_qualification(args), 2)
                outcome = p._milestone_outcome(args.out)
                self.assertEqual(outcome['executed_stages'], ['assemble_authenticated_source'])
                self.assertEqual(outcome['failure']['stage'], 'install_and_boot_source')
                self.assertEqual(outcome['failure']['refusal_code'], code)
                self.assertEqual(outcome['failure'].get('assertion_detail'), detail)
                self.assertTrue(outcome['cleanup_verified'])
                self.assertNotIn('PRIVATE', json.dumps(outcome))
                session.prepare_reference.assert_not_called()
                session.validate_finished.assert_not_called()
                result = json.loads((args.out/'milestone.json').read_bytes())
                self.assertFalse(result['eligible'])
                self.assertFalse(result['qualification_evidence'])
                self.assertEqual(set(result['failure']['suites'].values()), {'unrun'})

    def test_live_gap_survives_qualification_projection_without_assigning_bootstrap_evidence(self):
        from deploy.tests.test_update_rehearsal_resources import ControlledSampler, PLAN, TABLES, MIGRATION_TABLES
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            args = SimpleNamespace(out=Path(temporary)/'milestone', dry_run=False,
                platform='linux/amd64', boot_timeout=4200, docker_context='colima-fagi-update',
                source_ref='futureagi/standalone:v1.54.0@sha256:'+'a'*64,
                target_ref='futureagi/standalone:v1.55.0@sha256:'+'b'*64, row=object(),
                target={'components': {'standalone': {}}})
            session = object.__new__(p.QualificationSession)
            session.row, session.target = args.row, args.target
            session.reference = [dict(engine=row['engine'], table=row['table'], rows=row['reference_rows'],
                                      bytes=row['reference_bytes']) for row in TABLES]
            session.validate_finished = mock.Mock()
            args.qualification = session
            sampler = ControlledSampler()
            runtime = SimpleNamespace(start_and_wait=mock.Mock(side_effect=lambda roster: sampler.operation()))
            original_start = runtime.start_and_wait
            driver = object.__new__(q.QualificationRehearsal)
            driver.sink, driver.deadline = session, time.monotonic()+30
            driver.installation, driver.backup, driver.target = object(), SimpleNamespace(b={}), args.target
            driver._primary_snapshot = {'private': '/PRIVATE/PATH'}
            driver._bootstrap_measurement = None
            driver.corpus = dict(target_pending_plan=copy.deepcopy(PLAN),
                                 pending_migration_tables=copy.deepcopy(MIGRATION_TABLES))
            driver._diagnostic_data = dict(active_phase='migration_readiness_and_failure_restore', phase_timings=[])
            def forward():
                with driver.start_measurement(runtime):
                    runtime.start_and_wait({})
            driver.execute = mock.Mock(side_effect=forward)
            driver.cleanup_verified = False
            driver.cleanup = mock.Mock(side_effect=lambda: setattr(driver, 'cleanup_verified', True))
            with mock.patch.object(q, 'QualificationRehearsal', return_value=driver), \
                    mock.patch.object(p, '_validate_context'), \
                    mock.patch.object(resources, '_native_amd64_actions', return_value=True), \
                    mock.patch.object(resources, 'NormalBootstrapEnvelopeSampler', return_value=sampler):
                self.assertEqual(q.run_qualification(args), 2)
            self.assertIs(runtime.start_and_wait, original_start)
            self.assertIsNone(driver._bootstrap_measurement)
            self.assertNotIn('bootstrap_envelope', driver.corpus)
            session.validate_finished.assert_not_called()
            driver.cleanup.assert_called_once_with()
            record = json.loads((args.out/'milestone.json').read_bytes())
            self.assertEqual(record['status'], 'failed')
            self.assertFalse(record['eligible'])
            self.assertFalse(record['qualification_evidence'])
            self.assertTrue(record['cleanup_verified'])
            self.assertEqual(set(record['failure']['suites'].values()), {'unrun'})
            self.assertEqual(record['failure']['refusal_code'], 'resource_sampling_gap')
            self.assertEqual(record['failure']['stage'], 'migration_readiness_and_failure_restore')
            summary = record['failure']['resource_sampling_failure']
            self.assertFalse(summary['qualification_evidence'])
            self.assertEqual(summary['sample_count'], 3)
            self.assertEqual(len(summary['reads']), 2)
            self.assertAlmostEqual(summary['first_offending_pair']['delta_seconds'], 2.01)
            outcome = p._milestone_outcome(args.out)
            self.assertEqual(outcome['failure']['resource_sampling_failure'], summary)
            outer = Path(temporary)/'outer'
            outer.mkdir()
            p.retain_diagnostics(outer, failure=dict(eligible=False, milestone_outcome=outcome))
            diagnostic = json.loads((outer/'diagnostics.json').read_bytes())
            self.assertFalse(diagnostic['qualification_evidence'])
            self.assertFalse(diagnostic['failure']['eligible'])
            self.assertEqual(diagnostic['failure']['milestone_outcome']['failure']['resource_sampling_failure'], summary)
            self.assertNotIn('PRIVATE', json.dumps(diagnostic))
            self.assertNotIn(str(Path.home()), json.dumps(diagnostic))
            self.assertLess(len(json.dumps(summary)), 4096)

    def test_sampling_summary_projection_is_numeric_allowlisted_and_bounded(self):
        from deploy.tests.test_update_rehearsal_resources import SAMPLES
        samples = copy.deepcopy(SAMPLES)
        samples[1]['monotonic'], samples[2]['monotonic'] = 12.01, 14.0
        with self.assertRaises(resources.ResourceObservationError) as caught:
            resources.validate_samples(samples, 3.5, started_monotonic=10.25, completed_monotonic=13.75)
        error = caught.exception
        safe = copy.deepcopy(error.sampling_failure)
        injected = copy.deepcopy(safe)
        injected['command'] = '/PRIVATE/COMMAND'
        injected['started_monotonic'] = 'PRIVATE ENV'
        injected['completed_monotonic'] = float('inf')
        injected['first_monotonic'] = 10**1000
        injected['first_offending_pair']['environment'] = 'PRIVATE KEY'
        injected['reads'] = [dict(sample_index=0, started_monotonic=9.75, completed_monotonic=10.0,
            docker_seconds=0.125, native_reader_seconds=0.125, privileged_fallback=False,
            scheduling_lateness_seconds=None, output='PRIVATE OUTPUT')]
        error.sampling_failure = injected  # The exception is rechecked, not blindly copied.
        projected = p.failure_diagnostics(error)
        summary = projected['resource_sampling_failure']
        self.assertFalse(projected['eligible'])
        self.assertEqual(projected['refusal_code'], 'resource_sampling_gap')
        self.assertEqual(set(summary), set(safe))
        self.assertIsNone(summary['started_monotonic'])
        self.assertIsNone(summary['completed_monotonic'])
        self.assertIsNone(summary['first_monotonic'])
        self.assertEqual(len(summary['reads']), 1)
        self.assertNotIn('PRIVATE', json.dumps(projected, allow_nan=False))
        self.assertEqual(resources.sampling_failure_diagnostics(summary), summary)
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            directory = Path(temporary)
            (directory/'milestone.json').write_text(json.dumps(dict(status='failed', failure=dict(
                refusal_code='resource_sampling_gap', resource_sampling_failure=dict(injected,
                    completed_monotonic='PRIVATE ENV'))), allow_nan=False))
            self.assertEqual(p._milestone_outcome(directory)['failure']['resource_sampling_failure'], summary)
        for key, value in (('sample_count', resources.MAX_SAMPLE_COUNT+1), ('sample_count', True),
                           ('schema_version', True), ('qualification_evidence', True)):
            with self.subTest(key=key, value=value):
                self.assertIsNone(resources.sampling_failure_diagnostics(dict(safe, **{key: value})))
        for rows in ([injected['reads'][0]] * 3, [dict(injected['reads'][0], native_reader_seconds='PRIVATE')],
                     [dict(injected['reads'][0], sample_index=resources.MAX_SAMPLE_COUNT)]):
            with self.subTest(rows=rows):
                self.assertEqual(resources.sampling_failure_diagnostics(dict(safe, reads=rows))['reads'], [])
        self.assertIsNone(resources.ResourceObservationError('resource_reader_failed', sampling_failure=safe).sampling_failure)

    def test_resource_refusal_retains_only_its_fixed_source_line_not_locals_or_paths(self):
        try:
            resources.validate_plan_and_tables([], [{'PRIVATE TOKEN': 'never publish'}], [])
        except resources.ResourceObservationError as error:
            data = p.failure_diagnostics(error)
        else:
            self.fail('invalid resource input was accepted')
        self.assertRegex(data['assertion_detail'], r'^update_rehearsal_resources\.py:[1-9][0-9]*$')
        self.assertNotIn('PRIVATE', json.dumps(data))
        self.assertNotIn(str(Path.home()), json.dumps(data))
        self.assertEqual(data['refusal_code'], 'resource_input_invalid')

    def test_package_refusal_code_phase_and_check_line_survive_the_subprocess_boundary(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            from deploy.tests.test_update_automatic_release import default_v2
            from deploy.update.manifest import canonical_json
            (root/'target.json').write_bytes(canonical_json(default_v2()))
            rows = root/'qualify'; rows.mkdir()
            (rows/'proof-row.json').write_bytes(canonical_json(dict(eligible=False, rows_directory='rows', run_id=7)))
            result = subprocess.run([sys.executable, '-I', str(p.ROOT/'scripts/rehearse_update_compatibility.py'),
                'package', '--rows', str(rows), '--target', str(root/'target.json'), '--ephemeral-key',
                '--out', str(root/'out'), '--run-id', '7'], capture_output=True, cwd=str(p.ROOT), timeout=120)
        self.assertEqual(result.returncode, 2)
        error = p.CommandError('python', result.returncode, result.stderr)
        self.assertEqual((error.refusal_code, error.phase), ('suite_failed', 'read_qualification_row'))
        self.assertRegex(error.location, r'^rehearse_update_compatibility\.py:[1-9][0-9]*$')
        self.assertIn('phase read_qualification_row at rehearse_update_compatibility.py:', str(error))
        data = p.failure_diagnostics(error)
        self.assertEqual(data['command']['phase'], 'read_qualification_row')
        self.assertNotIn(str(Path.home()), json.dumps(data) + str(error))
        # Only the closed shapes are parsed; arbitrary stderr cannot inject fields.
        forged = p.CommandError('python', 2, b'local-test package refused: Bad Code (x y)\n'
                                b'local-test package check: /etc/passwd:1\nPRIVATE TOKEN\n')
        self.assertEqual((forged.refusal_code, forged.phase, forged.location), ('suite_failed', None, None))
        self.assertNotIn('PRIVATE', str(forged))

    def test_spool_error_vocabulary_is_closed_and_covers_all_fixed_refusals(self):
        tree = ast.parse(Path(spool.__file__).read_text())
        messages = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_fail':
                self.assertIsInstance(node.args[0], ast.Constant)
                messages.add(node.args[0].value)
        self.assertEqual(messages, spool.WITNESS_DIAGNOSTIC_DETAILS)
        for message in messages:
            self.assertEqual(p.failure_diagnostics(spool.ObservedSpoolWitnessError(message))['assertion_detail'], message)
        error = spool.ObservedSpoolWitnessError('PRIVATE TOKEN: data from a product response')
        self.assertNotIn('PRIVATE', str(error))
        self.assertNotIn('PRIVATE', json.dumps(p.failure_diagnostics(error)))


if __name__ == '__main__':
    unittest.main()
