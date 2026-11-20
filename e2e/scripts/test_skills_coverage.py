"""CI regression checks for missing tests, skips, and wrong-checkout receipts."""

import json
import runpy
import tempfile
import unittest
from pathlib import Path

coverage = runpy.run_path(str(Path(__file__).with_name('skills-coverage.py')))


def report(*statuses, expected='passed'):
    return {'suites': [{'suites': [{'specs': [{'file': 'flows/skills/j5-campaign-executor.spec.ts',
        'title': 'SKILLS-E2E-051: execute forty targets', 'tests': [{'expectedStatus': expected,
        'status': 'expected', 'results': [{'retry': i, 'status': s} for i, s in enumerate(statuses)]}]}]}]}]}


class RequiredCoverage(unittest.TestCase):
    def test_requires_a_single_real_pass(self):
        passed = coverage['passed']
        self.assertEqual(passed(report('passed'), 'campaign-executor'), ['SKILLS-E2E-051'])
        for bad in ({'suites': []}, report(), report('skipped'), report('failed', expected='failed'),
                    report('failed', 'passed'), report('passed', expected='skipped')):
            with self.assertRaises(ValueError):
                passed(bad, 'campaign-executor')

    def test_missing_lane_and_wrong_checkout_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            commit = 'a' * 40
            for cohort, entries in json.loads(coverage['MANIFEST'].read_text()).items():
                (folder / f'{cohort}.json').write_text(json.dumps(dict(cohort=cohort, passed=entries, commit=commit)))
            coverage['reconcile'](folder, commit)
            with self.assertRaisesRegex(ValueError, 'Wrong checkout'):
                coverage['reconcile'](folder, 'b' * 40)
            (folder / 'native.json').unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                coverage['reconcile'](folder, commit)


if __name__ == '__main__':
    unittest.main()
