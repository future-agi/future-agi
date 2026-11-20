"""CI regression checks for missing tests, skips, and wrong-checkout receipts."""

import hashlib
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

    def test_missing_discovery_only_blocks_skills(self):
        self.assertIn('SKILLS', coverage['pattern']({'suites': []}, 'ordinary'))
        with self.assertRaisesRegex(ValueError, 'Absent'):
            coverage['pattern']({'suites': []}, 'native')

    def test_registered_wheel_identity(self):
        commit, sdk = 'a' * 40, 'b' * 40
        pair, wheel, application = 'c' * 64, 'd' * 64, 'e' * 64
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for case in ['056', '057', '058', '059', '063', 'j3', 'j4']:
                journey = case in ('j3', 'j4')
                names = ['cold', 'configured'] if case == 'j4' else ['configured'] if journey else ['guest' if case == '063' else 'campaign']
                records = [dict(name=n, status='completed' if journey else 'registered', product_head=commit, sdk_commit=sdk,
                                package_hashes=[wheel, application] if case == '063' else [wheel]) for n in names]
                path = folder / ('skills-gcp-' + case) / (case + '.json')
                path.parent.mkdir()
                path.write_text(json.dumps(dict(case=case, sha=commit, passed=True, count=1, environment='gcp',
                                                project='fagi-omega-staging', pair_sha256=pair, records=records)))
            def check():
                return coverage['registered_passes'](folder, commit, sdk, pair, wheel, application, coverage['required_runs']())
            self.assertEqual(len(check()), 7)
            for case in ('j3', 'j4'):
                path = folder / f'skills-gcp-{case}/{case}.json'
                original = path.read_text()
                data = json.loads(original)
                if case == 'j3':
                    data['records'][0].update(name='fixture', status='fixture')
                else:
                    data['records'].pop()
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'Invalid or incomplete'):
                    check()
                path.write_text(original)
            path = folder / 'skills-gcp-063/063.json'
            data = json.loads(path.read_text())
            for bad in (['f' * 64, application], [wheel, 'f' * 64]):
                data['records'][0]['package_hashes'] = bad
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'Wrong wheel'):
                    check()
            data['records'][0]['package_hashes'] = [wheel, application]
            data['pair_sha256'] = 'f' * 64
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'Invalid or incomplete'):
                check()

    def test_aggregate_rejects_old_schema_or_missing_provenance_before_counting_passes(self):
        commit, sdk, traceai = ('a' * 40, 'b' * 40, 'c' * 40)
        sources = {'product': commit, 'sdk': sdk, 'traceai': traceai}
        with tempfile.TemporaryDirectory() as tmp:
            pair = Path(tmp)
            for document, error in [
                ({'schema': 1}, 'source revisions'),
                ({'schema': 2}, 'contract provenance'),
                ({'schema': 2, 'contract': {'sync_mode': 'full', 'source': {'backend_commit': sdk}}},
                 'contract provenance'),
                ({'schema': 2, 'sources': {**sources, 'traceai': sdk}}, 'source revisions'),
            ]:
                with self.subTest(document=document):
                    raw = json.dumps({'sources': sources, 'branches': {'sdk': 'dev', 'traceai': 'dev'},
                                      **document}).encode()
                    (pair / 'pair.json').write_bytes(raw)
                    with self.assertRaisesRegex(ValueError, error):
                        coverage['reconcile_verified'](pair / 'passes', pair / 'registered', pair, commit, sdk,
                                                       traceai, hashlib.sha256(raw).hexdigest(), 'd' * 64, 'e' * 64)

    def test_missing_lane_and_wrong_checkout_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            commit = 'a' * 40
            for cohort, entries in json.loads(coverage['MANIFEST'].read_text()).items():
                (folder / f'{cohort}.json').write_text(json.dumps(dict(cohort=cohort, passed=entries, commit=commit)))
            coverage['reconcile'](folder, commit)
            with self.assertRaisesRegex(ValueError, 'Wrong checkout'):
                coverage['reconcile'](folder, 'b' * 40)
            # Host and native must each prove 021; a single receipt cannot satisfy both.
            for cohort in ('native', 'host'):
                path = folder / f'{cohort}.json'
                original = path.read_text()
                receipt = json.loads(original)
                receipt['passed'].remove('SKILLS-E2E-021')
                path.write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError, rf"missing: .*'{cohort}', 'SKILLS-E2E-021'"):
                    coverage['reconcile'](folder, commit)
                path.write_text(original)
            (folder / 'native.json').unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                coverage['reconcile'](folder, commit)


if __name__ == '__main__':
    unittest.main()
