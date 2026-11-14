"""Daemon/network-free release authentication and native credential boundaries."""
from email.message import Message
from http.client import HTTPSConnection, IncompleteRead
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.update_e2e import acceptance, real_automatic

# Deliberately fictitious; never read a developer's credential or call GitHub.
TOKEN = 'ghs_release_metadata_unit_sentinel'
TOKEN_ENV = 'FAGI_UPDATE_RELEASE_TOKEN'
BASE = 'https://api.github.com/repos/future-agi/future-agi/releases/'
LIMIT = 8*1024*1024


class TrackedBody(io.BytesIO):
    def __init__(self, raw):
        super().__init__(raw)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def response(url, body, code=200, location=None):
    headers = Message()
    if location is not None:
        headers['Location'] = location
    result = addinfourl(body, headers, url, code)
    result.msg = 'fixture response'
    return result


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {TOKEN_ENV: TOKEN}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_only_latest_and_stable_tag_metadata_receive_bearer_auth(self):
        for endpoint in ('latest', 'tags/v1.53.0', 'tags/v1.54.0', 'tags/v1.55.0', 'tags/v2.0.1'):
            with self.subTest(endpoint=endpoint):
                url = BASE+endpoint
                body = TrackedBody(b'{"tag_name":"v1.55.0"}')
                with patch.object(HTTPSHandler, 'https_open', return_value=response(url, body)) as send, \
                     patch.object(acceptance, 'build_opener', wraps=build_opener) as factory:
                    self.assertEqual(acceptance.get_json(url), {'tag_name': 'v1.55.0'})
                request = send.call_args.args[0]
                self.assertEqual(request.full_url, url)
                self.assertEqual(request.get_method(), 'GET')
                self.assertIsNone(request.data)
                self.assertEqual(request.get_header('Accept'), 'application/json')
                self.assertEqual(request.get_header('Authorization'), 'Bearer '+TOKEN)
                self.assertNotIn('Authorization', request.headers)
                self.assertEqual(request.unredirected_hdrs['Authorization'], 'Bearer '+TOKEN)
                self.assertEqual(request.timeout, 60)
                self.assertEqual(factory.call_args.args[0].proxies, {})
                self.assertEqual(body.read_sizes, [LIMIT+1])
                self.assertTrue(body.closed)

    def test_raw_origin_path_and_stable_version_allowlist_before_any_request(self):
        invalid = [None, b'https://api.github.com', '',
            BASE.replace('https:', 'http:')+'latest',
            BASE.replace('https:', 'HTTPS:')+'latest',
            BASE.replace('api.github.com', 'api.github.com.evil.invalid')+'latest',
            BASE.replace('api.github.com', 'api.github.com@evil.invalid')+'latest',
            BASE.replace('api.github.com', 'user:password@api.github.com')+'latest',
            BASE.replace('api.github.com', 'api.github.com:443')+'latest',
            BASE.replace('api.github.com', 'api.github.com:80')+'latest',
            BASE.replace('api.github.com', 'api.github.com.')+'latest',
            BASE.replace('api.github.com', 'API.GITHUB.COM')+'latest',
            BASE.replace('api.github.com', 'api%2egithub.com')+'latest',
            BASE.replace('api.github.com', '127.0.0.1')+'latest',
            BASE.replace('future-agi/future-agi', 'other/repository')+'latest',
            '//api.github.com/repos/future-agi/future-agi/releases/latest',
            ' '+BASE+'latest', '\n'+BASE+'latest', BASE+'latest\n', BASE+'latest\x00',
            BASE.replace('github', 'git\thub')+'latest', BASE.replace('/repos/', '\\repos/')+'latest']
        invalid += [BASE+suffix for suffix in ('', 'latest/', 'latest?', 'latest?token='+TOKEN,
            'latest#', 'latest#fragment', '../releases/latest', '/latest', 'LATEST',
            '123', 'assets/123', 'download/v1.55.0/manifest.json', 'tags/', 'tags/latest',
            'tags/v1.55.0/', 'tags/v1.55.0/other', 'tags/v1.55.0?x=1', 'tags/v1.55.0#x',
            'tags/v1.55.0%2f..', 'tags/%761.55.0', 'tags/../latest', 'tags/v01.55.0',
            'tags/v1.55', 'tags/v1.55.0-rc.1', 'tags/v1.55.0-beta.1', 'tags/ v1.55.0',
            'tags/v1.55.0\n', 'tags/v１.55.0', 'tags/v'+'9'*19+'.0.0')]
        with patch.object(acceptance, 'build_opener') as factory:
            for url in invalid:
                with self.subTest(url=url), self.assertRaisesRegex(ValueError, '^unsupported release metadata URL$'):
                    acceptance.get_json(url)
            factory.assert_not_called()

    def test_absent_or_empty_scoped_token_is_anonymous_without_ambient_fallback(self):
        for value in (None, ''):
            with self.subTest(value=value), patch.dict(os.environ, {'GITHUB_TOKEN': 'not-for-this-request',
                    'GH_TOKEN': 'also-not-for-this-request'}):
                if value is None:
                    os.environ.pop(TOKEN_ENV, None)
                else:
                    os.environ[TOKEN_ENV] = value
                with patch.object(HTTPSHandler, 'https_open', return_value=response(BASE+'latest', io.BytesIO(b'{}'))) as send:
                    self.assertEqual(acceptance.get_json(BASE+'latest'), {})
                self.assertIsNone(send.call_args.args[0].get_header('Authorization'))

    def test_malformed_credential_is_refused_without_echo_or_network(self):
        for value in (TOKEN+'\r\nX-Header: injected', TOKEN+' ', TOKEN+'é', 'x'*4097):
            with self.subTest(length=len(value)), patch.dict(os.environ, {TOKEN_ENV: value}), \
                 patch.object(acceptance, 'build_opener') as factory:
                with self.assertRaisesRegex(ValueError, '^invalid release metadata credential$'):
                    acceptance.get_json(BASE+'latest')
                factory.assert_not_called()

    def test_all_redirects_refused_even_same_origin_without_forwarding_auth(self):
        locations = ['https://evil.invalid/'+TOKEN, '//evil.invalid/path',
            'http://api.github.com/repos/future-agi/future-agi/releases/latest',
            BASE+'tags/v1.55.0', '/repos/future-agi/future-agi/releases/tags/v1.55.0',
            'https://api.github.com/repos/other/repo/releases/latest', 'file:///tmp/metadata', None]
        for code in (301, 302, 303, 307, 308):
            for location in locations:
                with self.subTest(code=code, location=location):
                    body = TrackedBody(TOKEN.encode())
                    with patch.object(HTTPSHandler, 'https_open', return_value=response(BASE+'latest', body, code, location)) as send:
                        with self.assertRaisesRegex(RuntimeError, 'status '+str(code)) as raised:
                            acceptance.get_json(BASE+'latest')
                    self.assertEqual(send.call_count, 1)
                    self.assertEqual(body.read_sizes, [])
                    self.assertTrue(body.closed)
                    self.assertNotIn(TOKEN, str(raised.exception))

    def test_proxy_environment_is_ignored_and_TLS_defaults_verify_peer_and_hostname(self):
        def inspect_connection(connection_type, request, **kwargs):
            self.assertIs(connection_type, HTTPSConnection)
            connection = connection_type(request.host, **kwargs)
            self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(connection._context.check_hostname)
            self.assertEqual(request.host, 'api.github.com')
            self.assertFalse(request.has_proxy())
            raise URLError(ssl.SSLCertVerificationError('untrusted certificate '+TOKEN))
        with patch.dict(os.environ, {key: 'http://proxy.invalid:1234' for key in
                ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy')}), \
             patch.object(HTTPSHandler, 'do_open', side_effect=inspect_connection) as send:
            with self.assertRaisesRegex(RuntimeError, '^release metadata request failed$'):
                acceptance.get_json(BASE+'latest')
            send.assert_called_once()

    def test_exact_8MiB_response_allowed_but_one_more_byte_refused_and_closed(self):
        for size in (LIMIT, LIMIT+1):
            with self.subTest(size=size):
                body = TrackedBody(b'{}'+b' '*(size-2))
                with patch.object(HTTPSHandler, 'https_open', return_value=response(BASE+'latest', body)):
                    if size == LIMIT:
                        self.assertEqual(acceptance.get_json(BASE+'latest'), {})
                    else:
                        with self.assertRaisesRegex(ValueError, 'response exceeds bound'):
                            acceptance.get_json(BASE+'latest')
                self.assertEqual(body.read_sizes, [LIMIT+1])
                self.assertTrue(body.closed)

    def test_invalid_JSON_and_encoding_never_echo_response(self):
        for raw in (TOKEN.encode(), b'\xff'+TOKEN.encode(), b'', b'['*1100):
            with self.subTest(length=len(raw)):
                body = TrackedBody(raw)
                with patch.object(HTTPSHandler, 'https_open', return_value=response(BASE+'latest', body)):
                    with self.assertRaisesRegex(ValueError, '^invalid release metadata JSON$'):
                        acceptance.get_json(BASE+'latest')
                self.assertTrue(body.closed)

    def test_network_and_HTTP_failures_have_only_fixed_public_messages(self):
        body = TrackedBody(TOKEN.encode())
        errors = [URLError(TOKEN), TimeoutError(TOKEN), IncompleteRead(TOKEN.encode()),
            ValueError(TOKEN), HTTPError(BASE+TOKEN, 403, TOKEN, Message(), body)]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.object(HTTPSHandler, 'https_open', side_effect=error):
                try:
                    acceptance.get_json(BASE+'latest')
                except RuntimeError as refused:
                    shown = ''.join(traceback.format_exception(type(refused), refused, refused.__traceback__))
                    self.assertNotIn(TOKEN, shown)
                    self.assertTrue(refused.__suppress_context__)
                else:
                    self.fail('network failure did not fail closed')
        self.assertTrue(body.closed)
        self.assertEqual(body.read_sizes, [])


class ReleaseCredentialIsolationTests(unittest.TestCase):
    def assert_no_token_files(self, root):
        for path in root.rglob('*'):
            if path.is_file():
                self.assertNotIn(TOKEN.encode(), path.read_bytes(), str(path))

    def test_real_native_command_child_has_no_token_but_keeps_required_environment(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp, \
             patch.dict(os.environ, {TOKEN_ENV: TOKEN, 'FAGI_UPDATE_E2E_BUILDER': 'builder-unit', 'GITHUB_RUN_ID': '123'}):
            driver = acceptance.NativeAcceptance(Path(temp))
            code = ('import json, os; print(json.dumps({k: os.environ.get(k) for k in '
                    +repr((TOKEN_ENV, 'PATH', 'FAGI_UPDATE_E2E_BUILDER', 'GITHUB_RUN_ID'))+'}))')
            data = json.loads(driver.command([sys.executable, '-I', '-c', code]))
            self.assertIsNone(data[TOKEN_ENV])
            self.assertEqual(data['PATH'], os.environ['PATH'])
            self.assertEqual(data['FAGI_UPDATE_E2E_BUILDER'], 'builder-unit')
            self.assertEqual(data['GITHUB_RUN_ID'], '123')
            self.assertEqual(os.environ[TOKEN_ENV], TOKEN, 'parent must still authenticate later metadata reads')
            self.assert_no_token_files(Path(temp))

    def test_unittest_and_qualification_subtree_never_inherits_release_token(self):
        real_run = subprocess.run
        # Execute a real child at the unittest boundary; no Docker/product test.
        code = ('import os; assert '+repr(TOKEN_ENV)+' not in os.environ; '
                'assert os.environ["FUTURE_AGI_UPDATE_E2E"] == "1"; '
                'assert os.environ["GITHUB_RUN_ID"] == "123"; '
                'assert os.path.isfile(os.environ["FAGI_UPDATE_ACCEPTANCE_INPUTS"]); '
                'print("Ran 1 test in 0.01s\\n\\nOK")')
        for mode in ('a-b-c', 'upgrade'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=Path.home()) as temp, \
                 patch.dict(os.environ, {TOKEN_ENV: TOKEN, 'GITHUB_RUN_ID': '123'}):
                driver = acceptance.NativeAcceptance(Path(temp))
                def execute(argv, **kwargs):
                    self.assertIn('test_update_e2e_automatic.py', argv)
                    self.assertNotIn(TOKEN, ' '.join(map(str, argv)))
                    self.assertNotIn(TOKEN_ENV, kwargs['env'])
                    return real_run([sys.executable, '-I', '-c', code], **kwargs)
                with patch.object(acceptance, 'get_json', return_value={'tag_name': 'v1.55.0'}), \
                     patch.object(driver, 'published', return_value=({'version': 'v1.55.0'}, Path(temp))), \
                     patch.object(driver, 'build_pr', return_value={'version': 'v1.55.1'}), \
                     patch.object(acceptance.subprocess, 'run', side_effect=execute) as run:
                    self.assertEqual(driver.run_tests(mode, None), 0)
                    run.assert_called_once()
                self.assert_no_token_files(Path(temp))

    def test_actual_app_env_writer_and_container_command_environment_exclude_token(self):
        from deploy.tests.update_e2e.test_real_automatic_harness import model
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp, patch.dict(os.environ, {TOKEN_ENV: TOKEN}):
            h = real_automatic.ProductHarness(Path(temp), ROOT, 'fagi-upd-e2e-real-token-unit')
            h.root.mkdir()
            (h.root/'docker-compose.yml').write_text('fixture, never executed')
            self.assertNotIn(TOKEN_ENV, h.env)
            with patch.object(h, 'install_updater'), \
                 patch.object(h, 'docker', return_value=SimpleNamespace(stdout=b'')), \
                 patch.object(h, 'dc', return_value=SimpleNamespace(stdout=json.dumps(model(h.project)).encode())):
                h.prepare('sha256:'+'a'*64)
            self.assertNotIn(TOKEN_ENV, (h.root/'.env').read_text())
            self.assert_no_token_files(Path(temp))

    def test_main_failure_artifact_and_console_do_not_retain_reflected_HTTP_credentials(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp, patch.dict(os.environ, {TOKEN_ENV: TOKEN}), \
             patch.object(acceptance, 'native_ci', return_value=True), \
             patch.object(acceptance.NativeAcceptance, 'command', return_value=b'amd64'), \
             patch.object(acceptance.NativeAcceptance, 'cron_environment_probe'), \
             patch.object(acceptance, 'OCIRegistry'), \
             patch.object(acceptance.NativeAcceptance, 'run_tests', side_effect=lambda *args: acceptance.get_json(BASE+'latest')), \
             patch.object(HTTPSHandler, 'https_open', side_effect=HTTPError(BASE+TOKEN, 403, TOKEN, Message(), io.BytesIO(TOKEN.encode()))), \
             patch.object(sys, 'stdout', io.StringIO()) as stdout, patch.object(sys, 'stderr', io.StringIO()) as stderr:
            out = Path(temp)/'out'
            old_umask = os.umask(0o077)
            try:
                self.assertEqual(acceptance.main(['--mode', 'upgrade', '--out', str(out)]), 1)
            finally:
                os.umask(old_umask)
            self.assertEqual(json.loads((out/'failure.json').read_bytes())['reason'],
                             'release metadata HTTP request failed (status 403)')
            self.assertNotIn(TOKEN, stdout.getvalue()+stderr.getvalue())
            self.assert_no_token_files(out)

    def test_success_response_validation_cannot_echo_credential_into_failure_artifact(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp, patch.dict(os.environ, {TOKEN_ENV: TOKEN}), \
             patch.object(acceptance, 'native_ci', return_value=True), \
             patch.object(acceptance.NativeAcceptance, 'command', return_value=b'amd64'), \
             patch.object(acceptance.NativeAcceptance, 'cron_environment_probe'), \
             patch.object(acceptance, 'OCIRegistry'), \
             patch.object(HTTPSHandler, 'https_open', return_value=response(BASE+'latest',
                 io.BytesIO(json.dumps({'tag_name': TOKEN}).encode()))):
            out = Path(temp)/'out'
            old_umask = os.umask(0o077)
            try:
                self.assertEqual(acceptance.main(['--mode', 'upgrade', '--out', str(out)]), 1)
            finally:
                os.umask(old_umask)
            self.assert_no_token_files(out)
            self.assertIn('[REDACTED]', json.loads((out/'failure.json').read_bytes())['reason'])

    def test_scoped_token_never_appears_in_plan_output(self):
        for mode in ('upgrade', 'a-b-c'):
            with self.subTest(mode=mode), patch.dict(os.environ, {TOKEN_ENV: TOKEN}), \
                 patch.object(sys, 'stdout', io.StringIO()) as stdout, patch.object(acceptance, 'get_json') as get:
                self.assertEqual(acceptance.main(['--mode', mode, '--plan']), 0)
                self.assertNotIn(TOKEN, stdout.getvalue())
                get.assert_not_called()

    def test_workflow_token_is_only_on_two_acceptance_steps_with_unchanged_read_permissions(self):
        from deploy.tests.test_update_release_static import load
        workflow = load(ROOT/'.github/workflows/update-ci.yml')
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertNotIn(TOKEN_ENV, workflow.get('env', {}))
        found = []
        for name, job in workflow['jobs'].items():
            self.assertNotIn(TOKEN_ENV, job.get('env', {}))
            for step in job['steps']:
                if TOKEN_ENV in step.get('env', {}):
                    found.append(name)
                    self.assertEqual(step['env'][TOKEN_ENV], '${{ github.token }}')
                    self.assertIn('python -I deploy/tests/update_e2e/acceptance.py --mode ', step['run'])
                    self.assertNotIn(TOKEN_ENV, step['run'])
                    self.assertNotIn('github.token', step['run'])
                    self.assertNotIn('GITHUB_ENV', step['run'])
                    self.assertEqual(job['permissions'], {'contents': 'read'})
                if step.get('uses', '').startswith('actions/upload-artifact@'):
                    self.assertNotIn(TOKEN_ENV, json.dumps(step))
        self.assertEqual(sorted(found), ['automatic-a-b-c', 'automatic-upgrade'])
        unit = '\n'.join(step.get('run', '') for step in workflow['jobs']['unit']['steps'])
        self.assertIn("-p 'test_release_metadata.py' -v", unit)


if __name__ == '__main__':
    unittest.main()
