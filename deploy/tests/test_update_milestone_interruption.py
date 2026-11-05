"""Daemon-free child-to-parent diagnostics; never product qualification evidence."""
from contextlib import nullcontext
import inspect
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from deploy.update.contracts import ReasonCode
from deploy.update.orchestrator import GuardRefused
from deploy.update.processes import ProcessSupervisor
from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_milestone as m


PRIVATE = 'PRIVATE-password-SQL-rows-env-attempt-witness'


class InterruptionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root/'output'
        self.output.mkdir()
        self.driver = m.RestoreMilestone(self.output, context='unit-no-daemon', boot_timeout=4200)
        self.driver.deadline += m.INTERRUPTION_TOTAL_SECONDS
        ledger_dir = self.root/'ledger'
        ledger_dir.mkdir()
        (ledger_dir/'backup-seal.json').write_text(PRIVATE)
        self.ledger = SimpleNamespace(directory=ledger_dir, phase='previous_verifying',
            direction='restore_decided', backup_seal={'private': PRIVATE},
            snapshot={'blocked_reason': PRIVATE, 'pending': None})
        driver = self.driver
        driver.ledger = self.ledger
        driver.expected = SimpleNamespace(attempt_id=PRIVATE)
        driver.store = SimpleNamespace(path=self.root)
        (self.root/'attempts').mkdir()
        driver.engine = SimpleNamespace(_open_automatic_ledger=lambda ledger: ledger)
        driver.fresh_process_objects = mock.Mock()
        driver.observe_source = mock.Mock()
        driver.forward_operation_counts = lambda: dict(archive=len(p.VOLUME_ROLES),
            compose_create=1, env_commit=1, start=1)
        driver.locks = nullcontext
        driver._remember_resources = mock.Mock()
        driver._collect_diagnostics = lambda: driver._diagnostic_data.update(child_private=PRIVATE)
        driver.retain_authority = lambda: (driver.output/'authority-inventory.json').write_text(PRIVATE)
        # Forward/storage are synthetic. The recovery closure and fork/file/parent
        # handoff are real, so in-memory-only diagnostic changes cannot pass.
        def child(operation, *, killed=False, result_name='child-result.json'):
            if killed:
                return 1
            pid = m.RestoreMilestone.child_process(driver, operation, result_name=result_name)
            if self.tamper is not None:
                self.tamper(driver.output/'fresh-recovery-refusal.json')
            return pid
        self.tamper = None
        driver.child_process = child
        self.popen = mock.patch('deploy.update.processes.subprocess.Popen',
            side_effect=AssertionError('unit tests must not launch commands')).start()
        self.addCleanup(mock.patch.stopall)

    def fail_recovery(self, boundary, kind):
        driver = self.driver
        def recover():
            if driver.output.name != 'interruption-'+boundary:
                return
            try:
                if kind == 'deadline':
                    runner = ProcessSupervisor(m.restore_policy(), docker_context='unit-no-daemon', environment={})
                    runner.deadline = 0
                    runner._run(['unit-command-must-not-run'])
                elif kind == 'observation_deadline':
                    driver.registration = SimpleNamespace()
                    driver.descriptor = {}
                    driver.backup = SimpleNamespace()
                    (self.ledger.directory/'proof.json').write_text('{}')
                    driver.lifecycle()._verification_window(SimpleNamespace(runner=SimpleNamespace(deadline=0)))
                else:
                    driver.source_migrations = {}
                    driver.witnesses = SimpleNamespace(read=lambda _: [dict(domain='postgres_app', identity=PRIVATE)],
                        namespaces={'source': {'namespace': PRIVATE, 'headers': {
                            'X-Organization-Id': PRIVATE, 'X-Workspace-Id': PRIVATE}}})
                    driver.app_python = mock.Mock(return_value=[])
                    driver.project_witnesses = []
                    driver.corpus['source_witnesses'] = []
                    with mock.patch.object(m.ProductHarness, 'migration_inventory', return_value={}):
                        driver.verify_restored_source()
            except Exception as cause:
                raise GuardRefused(ReasonCode.RECOVERY_REQUIRED) from cause
            raise AssertionError('fixture did not produce the intended recovery refusal')
        driver.recover = recover

    def run_failed(self):
        with self.assertRaisesRegex(p.RehearsalError, '^interruption_recovery_required$') as caught:
            self.driver._phase('interruption_restore', self.driver.interruption_restore)
        failure = p.failure_diagnostics(caught.exception, self.driver)
        p.retain_diagnostics(self.output, self.driver, failure=failure)
        exported = json.loads((self.output/'diagnostics.json').read_bytes())
        self.assertFalse(exported['qualification_evidence'])
        self.assertFalse(exported['failure']['eligible'])
        self.assertEqual(exported['product']['phase_timings'][-1]['outcome'], 'failed')
        self.assertNotIn(PRIVATE, json.dumps(exported))
        self.assertNotIn(str(self.root), json.dumps(exported))
        self.assertNotIn('child_private', exported['product'])
        self.assertEqual(self.driver.output, self.output)
        summary = json.loads((self.output/'interruption-summary.json').read_bytes())
        self.assertEqual(summary['boundaries'][-1]['outcome'], 'recovery_required')
        return exported['product']['interruption_recovery']

    def check_location(self, function, check):
        lines, start = inspect.getsourcelines(function)
        number = start + next(index for index, line in enumerate(lines) if check in line)
        return Path(inspect.getsourcefile(function)).name+':'+str(number)

    def test_deadline_cause_crosses_real_recovery_fork_at_actual_boundary(self):
        boundary = 'target_started'
        self.fail_recovery(boundary, 'deadline')
        rows = self.run_failed()
        self.assertEqual([row['boundary'] for row in rows], list(m.INTERRUPTION_BOUNDARIES[:3]))
        row = rows[-1]
        self.assertEqual(row['outcome'], 'recovery_required')
        self.assertEqual(row['phase'], 'previous_verifying')
        self.assertEqual(row['direction'], 'restore_decided')
        self.assertTrue(row['retained_evidence'])
        self.assertEqual(row['collection_status'], 'collected')
        self.assertEqual([cause['exception_type'] for cause in row['causes']], ['GuardRefused', 'ProcessError'])
        self.assertEqual([cause['reason'] for cause in row['causes']], ['recovery_required', 'operation_timeout'])
        self.assertEqual(row['causes'][-1]['location'], self.check_location(ProcessSupervisor._run,
            'raise ProcessError(ReasonCode.OPERATION_TIMEOUT)'))
        self.popen.assert_not_called()

    def test_witness_assertion_and_observation_deadline_keep_fixed_check_not_message(self):
        for kind, detail in (('witness', 'restored A witnesses differ'),
                             ('observation_deadline', 'restore observation deadline exceeded')):
            with self.subTest(kind=kind):
                # Each run has its own boundary directories.
                if kind == 'observation_deadline':
                    self.driver.output = self.output/'second-run'
                    self.driver.output.mkdir()
                    self.output = self.driver.output
                self.fail_recovery('backup_sealed', kind)
                rows = self.run_failed()
                cause = rows[-1]['causes'][-1]
                self.assertEqual(cause['exception_type'], 'RehearsalError')
                self.assertEqual(cause['reason'], 'suite_failed')
                function, check = ((m.RestoreMilestone.verify_restored_source, 'p.equal(sorted(rows')
                    if kind == 'witness' else (m.RestoreMilestone.lifecycle, 'p.need(time.monotonic()'))
                self.assertEqual(cause['location'], self.check_location(function, check))
                self.assertNotIn(detail, json.dumps(rows))
                private = json.loads((self.output/'interruption-backup_sealed/fresh-recovery-refusal.json').read_bytes())
                self.assertEqual(private['causes'][-1]['detail'], detail)

    def test_missing_malformed_and_oversized_causes_preserve_original_failure(self):
        mutations = (
            ('missing', lambda path: path.unlink()),
            ('malformed', lambda path: path.write_text('{'+PRIVATE)),
            ('oversized', lambda path: path.write_text(PRIVATE*m.INTERRUPTION_DIAGNOSTIC_BYTES)),
        )
        for status, tamper in mutations:
            with self.subTest(status=status):
                self.driver.output = self.output/status
                self.driver.output.mkdir()
                self.output = self.driver.output
                self.fail_recovery('backup_sealed', 'deadline')
                self.tamper = tamper
                row = self.run_failed()[-1]
                self.assertEqual(row['collection_status'], status)
                self.assertEqual(row['causes'], [])
                self.assertEqual(row['boundary'], 'backup_sealed')
                self.assertTrue(row['retained_evidence'])

    def projection(self, document):
        (self.output/'fresh-recovery-refusal.json').write_text(json.dumps(document))
        return m.interruption_diagnostic(self.output,
            dict(boundary='extract_intent', outcome='recovery_required', retained_evidence=True), self.ledger)

    def test_private_and_malicious_fields_are_closed_and_eight_causes_are_maximum(self):
        self.ledger.phase = PRIVATE
        self.ledger.direction = PRIVATE
        cause = dict(exception_type=PRIVATE, reason=PRIVATE, detail=PRIVATE,
            location='/private/'+PRIVATE+'/processes.py:123', env=PRIVATE, rows=[PRIVATE])
        row = self.projection(dict(causes=[cause]*12, boundary=PRIVATE, attempt_id=PRIVATE, witnesses=[PRIVATE]))
        self.assertEqual(row['collection_status'], 'truncated')
        self.assertEqual(row['boundary'], 'extract_intent')
        self.assertEqual(row['causes'], [dict(exception_type='OtherError', reason='suite_failed')]*8)
        self.assertNotIn('phase', row)
        self.assertNotIn('direction', row)
        self.assertNotIn(PRIVATE, json.dumps(row))
        for field in ('exception_type', 'reason', 'location'):
            for value in ([PRIVATE], {PRIVATE: PRIVATE}, PRIVATE*200):
                with self.subTest(field=field, value_type=type(value).__name__):
                    row = self.projection(dict(causes=[dict(cause, **{field: value})]))
                    self.assertEqual(row['collection_status'], 'filtered')
                    self.assertNotIn(PRIVATE, json.dumps(row))
                    self.assertEqual(row['causes'], [dict(exception_type='OtherError', reason='suite_failed')])
        row = self.projection(dict(causes=[dict(exception_type='ManualRelease', reason='suite_timed_out',
            location='processes.py:123', detail=PRIVATE)]))
        self.assertEqual(row['causes'], [dict(exception_type='ManualRelease', reason='suite_timed_out',
            location='processes.py:123')])

    def test_malformed_shapes_nonregular_files_and_io_errors_are_bounded(self):
        for document in (None, [], {}, {'causes': []}, {'causes': {}}, {'causes': [None]},
                         {'causes': [dict(exception_type='ProcessError'), None]}):
            with self.subTest(document=document):
                row = self.projection(document)
                self.assertEqual(row['collection_status'], 'malformed')
                self.assertEqual(row['causes'], [])
        path = self.output/'fresh-recovery-refusal.json'
        path.write_bytes(b'['*2000+b']'*2000)
        result = dict(boundary='env_selected', outcome='recovery_required', retained_evidence=False)
        self.assertEqual(m.interruption_diagnostic(self.output, result, self.ledger)['collection_status'], 'malformed')
        path.unlink()
        path.symlink_to(self.ledger.directory/'backup-seal.json')
        self.assertEqual(m.interruption_diagnostic(self.output, result, self.ledger)['collection_status'], 'unavailable')
        path.unlink()
        path.mkdir()
        self.assertEqual(m.interruption_diagnostic(self.output, result, self.ledger)['collection_status'], 'unavailable')
        with mock.patch.object(m.os, 'open', side_effect=PermissionError(PRIVATE)):
            self.assertEqual(m.interruption_diagnostic(self.output, result, self.ledger)['collection_status'], 'unavailable')

    def test_arbitrary_traceback_filename_is_not_a_safe_basename(self):
        namespace = {}
        try:
            exec(compile('raise AssertionError("private")', '/private/processes.py', 'exec'), namespace)
        except AssertionError as exc:
            self.assertIsNone(m.interruption_cause_location(exc))
        for location in ('processes.py:0', 'processes.py:9999999', 'private.py:1', 'processes.py:1\n'+PRIVATE):
            row = self.projection(dict(causes=[dict(location=location)]))
            self.assertNotIn('location', row['causes'][0])

    def test_complete_restored_matrix_is_unchanged_and_missing_retention_still_fails(self):
        self.driver.recover = lambda: None
        self.driver._phase('interruption_restore', self.driver.interruption_restore)
        rows = self.driver.diagnostics()['interruption_recovery']
        self.assertEqual([row['boundary'] for row in rows], list(m.INTERRUPTION_BOUNDARIES))
        self.assertTrue(all(row['outcome'] == 'restored' and row['collection_status'] == 'not_required' for row in rows))
        self.assertEqual(self.driver.diagnostics()['phase_timings'][-1]['outcome'], 'passed')
        with self.assertRaisesRegex(p.RehearsalError, 'interruption_recovery_evidence_missing'):
            m.validate_interruption_results([dict(boundary='extract_intent', outcome='recovery_required',
                retained_evidence=False)])


if __name__ == '__main__':
    unittest.main()
