"""Daemon-free failure projections; never affirmative product qualification."""
import ast
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
