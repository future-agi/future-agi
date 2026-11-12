"""Host version observations with completed Compose bootstrap jobs, without Docker."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.tests.test_update_public_status import NOW, PublisherCase  # noqa: E402
from deploy.update.contracts import ReasonCode  # noqa: E402
from deploy.update.install import Installation  # noqa: E402
from deploy.update.status import observe_status  # noqa: E402


JOBS = ('postgres-schema-bootstrap', 'clickhouse-native-bootstrap', 'clickhouse-cdc-bootstrap')


class ObservedInstallation:
    """Resolved Compose model and inspected images; use the real product-service adapter."""

    futureagi_services = Installation.futureagi_services

    def __init__(self, *, production=False, version='v1.53.0'):
        self.production = production
        self.root = Path('/installation')
        self.env_file = self.root / ('.env.production' if production else '.env')
        backend_image = f'futureagi/future-agi:{version}'
        self.services = {
            'backend': {'image': backend_image, 'restart': 'unless-stopped',
                        'depends_on': {JOBS[-1]: {'condition': 'service_completed_successfully'}}},
            'frontend': {'image': f'futureagi/frontend:{version}', 'restart': 'unless-stopped',
                         'depends_on': {'backend': {'condition': 'service_started'}}},
        }
        for index, job in enumerate(JOBS):
            self.services[job] = {'image': backend_image, 'restart': 'no'}
            if index:
                self.services[job]['depends_on'] = {
                    JOBS[index - 1]: {'condition': 'service_completed_successfully'}}
        self.records = [dict(service=name, image_id=name,
                             status='exited' if name in JOBS else 'running', exit_code=0)
                        for name in self.services]
        self.images = {name: {'version': version} for name in self.services}

    def fresh(self):
        return self

    def check_ownership(self):
        pass

    def config(self, *, refresh=False):
        return {'services': self.services}

    def containers(self):
        return self.records

    def image(self, image_id):
        return self.images.get(image_id)

    def identity(self):
        return {'mode': 'distributed', 'production': self.production,
                'env_file': str(self.env_file), 'project': 'installation'}

    def container(self, service):
        return next(record for record in self.records if record['service'] == service)


def empty_state():
    state = mock.Mock()
    state.read_journal.return_value = None
    state.latest_receipt.return_value = None
    return state


class BootstrapStatusTests(unittest.TestCase):
    def setUp(self):
        self.installation = ObservedInstallation()
        self.state = empty_state()

    def observe(self):
        return observe_status(self.installation, self.state, now=NOW)

    def assert_unknown(self):
        observation = self.observe()
        self.assertIsNotNone(observation.versions['observed_at'])
        self.assertEqual(observation.versions['runtime_state'], 'unknown')
        self.assertIsNone(observation.versions['running_release'])
        self.assertIn(ReasonCode.RUNNING_VERSIONS_UNKNOWN, observation.reason_codes)
        return observation

    def test_completed_bootstrap_jobs_preserve_running_release(self):
        for production in (False, True):
            with self.subTest(production=production):
                self.installation = ObservedInstallation(production=production)
                observation = self.observe()
                self.assertEqual(observation.versions['runtime_state'], 'consistent')
                self.assertEqual(observation.versions['running_release'], 'v1.53.0')
                self.assertEqual(observation.versions['selected_release'], 'v1.53.0')
                self.assertFalse(observation.pending_transaction)
                self.assertEqual(observation.reason_codes, ())
                for component in observation.versions['components']:
                    self.assertEqual(component['observation'], 'known')
                    self.assertEqual(component['observed_versions'], ['v1.53.0'])

    def test_all_running_services_remain_consistent(self):
        for record in self.installation.records:
            record['status'] = 'running'
        self.assertEqual(self.observe().versions['running_release'], 'v1.53.0')

    def test_completed_bootstrap_version_is_not_ignored(self):
        self.installation.images[JOBS[0]]['version'] = 'v1.52.0'
        observation = self.observe()
        self.assertEqual(observation.versions['runtime_state'], 'mixed')
        self.assertIsNone(observation.versions['running_release'])
        self.assertEqual(observation.reason_codes, (ReasonCode.MIXED_RUNNING_VERSIONS,))
        backend = next(c for c in observation.versions['components'] if c['component'] == 'backend')
        self.assertEqual(backend['observed_versions'], ['v1.52.0', 'v1.53.0'])

    def test_missing_or_unreadable_bootstrap_version_is_unknown(self):
        for info in (None, {}, {'version': 'latest'}, {'version': ' v1.53.0'}):
            with self.subTest(info=info):
                self.installation.images[JOBS[0]] = info
                self.assert_unknown()

    def test_failed_or_unfinished_bootstrap_is_unknown(self):
        for status, code in (('exited', 1), ('exited', None), ('created', 0),
                             ('restarting', 0), ('paused', 0), ('dead', 0)):
            with self.subTest(status=status, exit_code=code):
                self.installation.container(JOBS[0]).update(status=status, exit_code=code)
                self.assert_unknown()

    def test_missing_service_is_unknown(self):
        for name in self.installation.services:
            with self.subTest(service=name):
                records = self.installation.records
                self.installation.records = [c for c in records if c['service'] != name]
                self.assert_unknown()
                self.installation.records = records

    def test_stopped_long_lived_replica_is_unknown_even_with_exit_zero(self):
        for restart in ('unless-stopped', 'no'):
            with self.subTest(restart=restart):
                self.installation = ObservedInstallation()
                self.installation.services['backend']['restart'] = restart
                self.installation.records.append(dict(self.installation.container('backend'), status='exited'))
                self.assert_unknown()

    def test_completion_dependency_is_required(self):
        for condition in ('service_started', 'service_healthy', None):
            with self.subTest(condition=condition):
                self.installation.services[JOBS[1]]['depends_on'] = (
                    {JOBS[0]: {'condition': condition}} if condition else {})
                self.assert_unknown()

    def test_restartable_service_is_not_a_completed_job(self):
        for restart in ('always', 'unless-stopped', 'on-failure'):
            with self.subTest(restart=restart):
                self.installation.services[JOBS[0]]['restart'] = restart
                self.assert_unknown()

    def test_omitted_restart_uses_compose_no_restart_default(self):
        del self.installation.services[JOBS[0]]['restart']
        self.assertEqual(self.observe().versions['running_release'], 'v1.53.0')

    def test_completed_jobs_alone_do_not_establish_a_running_release(self):
        # A non-product consumer declares completion of the last product job.
        self.installation.services = {
            **{name: self.installation.services[name] for name in JOBS},
            'consumer': {'image': 'busybox:1.36',
                         'depends_on': {JOBS[-1]: {'condition': 'service_completed_successfully'}}},
        }
        self.installation.records = [c for c in self.installation.records if c['service'] in JOBS]
        observation = self.assert_unknown()
        self.assertEqual(observation.versions['components'], [
            {'component': 'backend', 'selected_version': 'v1.53.0',
             'observed_versions': ['v1.53.0'], 'observation': 'known'}])

    def test_compose_run_containers_do_not_poison_service_observations(self):
        self.installation.records.append(dict(service='backend', image_id='unreadable',
                                              status='exited', exit_code=1, oneoff=True))
        self.assertEqual(self.observe().versions['running_release'], 'v1.53.0')

    def test_host_json_status_succeeds_with_completed_bootstrap_jobs(self):
        from deploy.update.__main__ import _host_status

        for production in (False, True):
            with self.subTest(production=production):
                installation = ObservedInstallation(production=production)
                output = io.StringIO()
                with mock.patch('sys.stdout', output):
                    result = _host_status(installation, self.state)
                payload = json.loads(output.getvalue())
                self.assertEqual(result, 0)
                self.assertEqual(payload['production'], production)
                self.assertEqual(payload['versions']['running_release'], 'v1.53.0')
                self.assertEqual(payload['reason_codes'], [])


class BootstrapProjectionTests(PublisherCase):
    def test_installed_verified_release_is_current_after_bootstrap_completion(self):
        installation = ObservedInstallation(version=self.release.version)
        self.observation = observe_status(installation, empty_state(), now=NOW)
        snapshot = self.snapshot()
        self.assertEqual(snapshot['versions']['running_release'], self.release.version)
        self.assertEqual(snapshot['discovery']['verification'], 'verified')
        self.assertEqual(snapshot['discovery']['state'], 'current')


if __name__ == '__main__':
    unittest.main()
