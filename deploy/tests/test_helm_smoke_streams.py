"""Daemon-free regressions for the *actual* kind-smoke.sh stream assertions.

Run with Python 3.9+; HELM_SMOKE_BASH selects a Bash binary (default: PATH).
Synthetic provider tests need only Bash and grep. The separately labelled real
kubectl tests skip if the client is missing, and use ONLY an owned loopback API,
/dev/null kubeconfig, private HOME/cache, and finite request/process timeouts.
Nothing sources/runs the full smoke script, accesses a cluster, or needs Docker.

The old grep -q SIGPIPE is a diagnostic, not a timing-dependent test expectation.
A source contract locks the narrow draining form so baseline rejection does not
rely on reproducing SIGPIPE; behavioral tests require successful draining and
fail-closed negative controls.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlsplit


SOURCE = Path(__file__).resolve().parents[1] / 'helm/futureagi/hack/kind-smoke.sh'
BASH = os.environ.get('HELM_SMOKE_BASH') or shutil.which('bash')
KUBECTL = shutil.which('kubectl')
ADMIN_MARKER = b'first admin smoke-admin@example.com already exists: left unchanged'
TEMPORAL_MARKER = b'outbox-cdc-drain'
FAILURES = {
    'bootstrap': 'the pre-upgrade bootstrap job did not leave the first admin as it was',
    'temporal': 'the outbox CDC drain schedule is not registered',
}
COMMANDS = {
    'bootstrap': ['-n', 'futureagi', 'logs', 'job/futureagi-bootstrap'],
    'temporal': ['-n', 'futureagi', 'exec', 'statefulset/futureagi-temporal', '--',
                 'temporal', 'schedule', 'list', '--address', '127.0.0.1:7233'],
}
# Much larger than a pipe buffer; the marker precedes all this output.
TAIL = b'unrelated successful output after the marker\n' * 32768


def extract_assertion(source, kind):
    """Locate the production assertion by its failure message, not line numbers."""
    lines = source.splitlines()
    target = '  fail "' + FAILURES[kind] + '"'
    ends = [i for i, line in enumerate(lines) if line == target]
    if len(ends) != 1:
        raise AssertionError('expected one smoke assertion: ' + kind)
    end = ends[0]
    start = end - 1
    while start > 0 and lines[start - 1].endswith('\\'):
        start -= 1
    assertion = '\n'.join(lines[start:end + 1]) + '\n'
    if not assertion.startswith('kubectl ') or not lines[end - 1].endswith('||'):
        raise AssertionError('unexpected smoke assertion shape: ' + kind)
    return assertion


def extract_prelude(source):
    # Reuse the real options, fail handler and admin identity. Never source the
    # script: that would create a cluster before reaching either assertion.
    options = re.findall(r'^set -euo pipefail$', source, re.M)
    fail = re.findall(r'^fail\(\) \{\n.*?^\}', source, re.M | re.S)
    admin = re.findall(r'^admin_email=smoke-admin@example\.com$', source, re.M)
    if len(options) != 1 or len(fail) != 1 or len(admin) != 1:
        raise AssertionError('smoke options, fail handler or admin identity changed')
    return '\n'.join(options + fail + admin) + '\nns=futureagi\nrelease=futureagi\n'


SYNTHETIC_PROVIDER = r'''
import json
import os
from pathlib import Path
import signal
import sys

path, split, status, diagnostic, expected = sys.argv[1:6]
if sys.argv[6:] != json.loads(expected):
    sys.exit('unexpected kubectl arguments')
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
if diagnostic:
    os.write(2, diagnostic.encode())
with Path(path).open('rb') as stream:
    chunk = stream.read(int(split))
    while chunk:
        pending = memoryview(chunk)
        while pending:
            pending = pending[os.write(1, pending):]
        chunk = stream.read(8192)
sys.exit(int(status))
'''


@contextmanager
def loopback_logs(chunks, http_error=False, truncated=False):
    """A synthetic GET-only Job/Pod/log API, not a Kubernetes cluster."""
    namespace = 'futureagi'
    name = 'futureagi-bootstrap'
    pod_name = name + '-stream-test'
    uid = '00000000-0000-0000-0000-000000000001'
    pod = {
        'apiVersion': 'v1', 'kind': 'Pod',
        'metadata': {'name': pod_name, 'namespace': namespace, 'uid': uid,
                     'labels': {'controller-uid': uid}},
        'spec': {'containers': [{'name': 'bootstrap', 'image': 'unused-synthetic'}]},
        'status': {'phase': 'Succeeded'},
    }
    job = {
        'apiVersion': 'batch/v1', 'kind': 'Job',
        'metadata': {'name': name, 'namespace': namespace, 'uid': uid},
        'spec': {'selector': {'matchLabels': {'controller-uid': uid}},
                 'template': {'spec': pod['spec']}},
        'status': {'succeeded': 1},
    }
    routes = {
        '/api': {'kind': 'APIVersions', 'apiVersion': 'v1', 'versions': ['v1']},
        '/apis': {'kind': 'APIGroupList', 'apiVersion': 'v1', 'groups': [
            {'name': 'batch', 'versions': [{'groupVersion': 'batch/v1', 'version': 'v1'}],
             'preferredVersion': {'groupVersion': 'batch/v1', 'version': 'v1'}}]},
        '/api/v1': {'kind': 'APIResourceList', 'apiVersion': 'v1', 'groupVersion': 'v1',
                    'resources': [
                        {'name': 'pods', 'singularName': 'pod', 'namespaced': True,
                         'kind': 'Pod', 'verbs': ['get', 'list']},
                        {'name': 'pods/log', 'singularName': '', 'namespaced': True,
                         'kind': 'Pod', 'verbs': ['get']}]},
        '/apis/batch/v1': {'kind': 'APIResourceList', 'apiVersion': 'v1',
                          'groupVersion': 'batch/v1', 'resources': [
                              {'name': 'jobs', 'singularName': 'job', 'namespaced': True,
                               'kind': 'Job', 'verbs': ['get', 'list']}]},
        '/apis/batch/v1/namespaces/' + namespace + '/jobs/' + name: job,
        '/api/v1/namespaces/' + namespace + '/pods': {
            'kind': 'PodList', 'apiVersion': 'v1',
            'metadata': {'resourceVersion': '1'}, 'items': [pod]},
        '/api/v1/namespaces/' + namespace + '/pods/' + pod_name: pod,
    }
    log_path = '/api/v1/namespaces/' + namespace + '/pods/' + pod_name + '/log'
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            requests.append({'method': self.command, 'path': path,
                             'authenticated': bool(self.headers.get('Authorization'))})
            status = 200
            content_type = 'application/json'
            missing = 0
            if path == log_path and not http_error:
                parts = chunks
                content_type = 'text/plain'
                missing = 100 if truncated else 0
            elif path == log_path:
                status = 500
                parts = [json.dumps({
                    'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure',
                    'message': 'synthetic upstream log failure',
                    'reason': 'InternalError', 'code': status,
                }).encode()]
            elif path in routes:
                parts = [json.dumps(routes[path]).encode()]
            else:
                status = 404
                parts = [b'{"kind":"Status","apiVersion":"v1","code":404}']
            self.connection.settimeout(5)
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(sum(map(len, parts)) + missing))
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                # Separate writes deliberately split the marker and trailing
                # output. No test depends on how TCP coalesces these writes.
                for part in parts:
                    self.wfile.write(part)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected in a non-gating diagnostic of the old reader.
            finally:
                self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01},
                              daemon=True)
    thread.start()
    try:
        yield ('http://127.0.0.1:' + str(server.server_port), requests,
               set(routes) | {log_path}, log_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class AssertionHarness(unittest.TestCase):
    def setUp(self):
        if not BASH:
            self.skipTest('Bash is required')
        self.source = SOURCE.read_text()
        self.prelude = extract_prelude(self.source)
        self.temp = tempfile.TemporaryDirectory(prefix='helm-smoke-streams-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)

    def run_assertion(self, kind, provider, arguments):
        assertion = extract_assertion(self.source, kind)
        # Drop inherited kube credentials, proxies, shell functions and BASH_ENV.
        environment = {'PATH': os.defpath, 'HOME': str(self.work), 'LC_ALL': 'C',
                       'TMPDIR': str(self.work), 'KUBECONFIG': os.devnull,
                       'NO_PROXY': '127.0.0.1'}
        status_file = self.work / 'producer-status'
        wrapper = r'''
status_file=$1
shift
provider=("$@")
kubectl() {
  local status
  if "${provider[@]}" "$@"; then status=0; else status=$?; fi
  printf '%s\n' "$status" >"$status_file"
  return "$status"
}
'''
        command = [BASH, '--noprofile', '--norc', '-c',
                   self.prelude + wrapper + assertion + "printf 'ASSERTION_OK\\n'\n",
                   'smoke-stream-test', str(status_file), provider] + arguments
        result = subprocess.run(command, env=environment, capture_output=True,
                                text=True, timeout=20)
        self.producer_exit = int(status_file.read_text())
        return result

    def check_result(self, result, kind, success, producer_exit, diagnostic=''):
        self.assertEqual(self.producer_exit, producer_exit, result.stderr)
        self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
        self.assertEqual(result.stdout, 'ASSERTION_OK\n' if success else '')
        expected = '' if success else 'FAIL: ' + FAILURES[kind] + '\n'
        self.assertEqual(result.stderr, diagnostic + expected)

    def synthetic(self, kind, payload, status=0, split=8192, diagnostic=''):
        fixture = self.work / 'stdout.txt'
        fixture.write_bytes(payload)
        return self.run_assertion(kind, sys.executable, [
            '-I', '-B', '-c', SYNTHETIC_PROVIDER, str(fixture), str(split),
            str(status), diagnostic, json.dumps(COMMANDS[kind]),
        ])


class SyntheticStreamCases:
    """Shared stream semantics, not a real kubectl exec/protocol simulation."""

    def test_draining_consumer_contract(self):
        # Deterministic baseline rejection, independent of pipe/TCP scheduling.
        # The behavior tests below still exercise the production assertion.
        assertion = extract_assertion(self.source, self.kind)
        self.assertIn('| ' + self.consumer + ' ||\n', assertion)

    def test_short_match(self):
        result = self.synthetic(self.kind, self.marker + b'\n')
        self.check_result(result, self.kind, True, 0)

    def test_marker_early_in_large_output_is_drained(self):
        result = self.synthetic(self.kind, self.marker + b'\n' + TAIL)
        self.check_result(result, self.kind, True, 0)

    def test_split_marker_and_large_trailing_output_are_drained(self):
        result = self.synthetic(self.kind, self.marker + b'\n' + TAIL, split=7)
        self.check_result(result, self.kind, True, 0)

    def test_absent_marker_has_exact_failure_message(self):
        result = self.synthetic(self.kind, b'unrelated successful output\n')
        self.check_result(result, self.kind, False, 0)

    def test_upstream_failure_is_not_hidden_by_marker(self):
        for status in (7, 23, 141):
            with self.subTest(producer_exit=status):
                diagnostic = 'synthetic upstream failure\n'
                result = self.synthetic(self.kind, self.marker + b'\n', status=status,
                                        diagnostic=diagnostic)
                self.check_result(result, self.kind, False, status, diagnostic)

    def test_late_upstream_failure_after_large_output_is_not_hidden(self):
        diagnostic = 'synthetic upstream failure\n'
        result = self.synthetic(self.kind, self.marker + b'\n' + TAIL, status=23,
                                diagnostic=diagnostic)
        self.check_result(result, self.kind, False, 23, diagnostic)

    def test_empty_stream_fails(self):
        result = self.synthetic(self.kind, b'')
        self.check_result(result, self.kind, False, 0)

    def test_marker_on_stderr_does_not_pass(self):
        diagnostic = self.marker.decode() + '\n'
        result = self.synthetic(self.kind, b'', diagnostic=diagnostic)
        self.check_result(result, self.kind, False, 0, diagnostic)

    def test_match_without_final_newline(self):
        result = self.synthetic(self.kind, self.marker)
        self.check_result(result, self.kind, True, 0)


class SyntheticBootstrapStreamTests(SyntheticStreamCases, AssertionHarness):
    kind = 'bootstrap'
    marker = ADMIN_MARKER
    consumer = ('grep -F "first admin $admin_email already exists: left unchanged" '
                '>/dev/null')

    def test_near_matches_do_not_weaken_exact_admin_invariant(self):
        for payload in (
            ADMIN_MARKER.replace(b'example.com', b'exampleXcom'),
            ADMIN_MARKER.replace(b'smoke-admin', b'another-admin'),
            ADMIN_MARKER.replace(b'left unchanged', b'password reset'),
            ADMIN_MARKER[:-1],
        ):
            with self.subTest(payload=payload):
                result = self.synthetic(self.kind, payload + b'\n')
                self.check_result(result, self.kind, False, 0)


class SyntheticTemporalStreamTests(SyntheticStreamCases, AssertionHarness):
    # Deliberately separate coverage: these are schedule-list stdout fixtures,
    # not evidence that real Temporal/kubectl exec was exercised.
    kind = 'temporal'
    marker = TEMPORAL_MARKER
    consumer = 'grep outbox-cdc-drain >/dev/null'


@unittest.skipUnless(KUBECTL, 'real kubectl loopback probes require the client binary')
class RealKubectlLoopbackBootstrapTests(AssertionHarness):
    def probe(self, chunks, success, http_error=False, truncated=False):
        with loopback_logs(chunks, http_error, truncated) as api:
            endpoint, requests, allowed, log_path = api
            result = self.run_assertion('bootstrap', KUBECTL, [
                '--server', endpoint, '--kubeconfig', os.devnull,
                '--cache-dir', str(self.work / 'cache'), '--request-timeout=5s',
            ])
        self.assertTrue(any(request['path'] == log_path for request in requests))
        self.assertTrue(all(request['method'] == 'GET' and request['path'] in allowed
                            and not request['authenticated'] for request in requests))
        if http_error or truncated:
            # kubectl diagnostic wording can differ by client version, but the
            # production failure line must be exact, once, and the final line.
            failure = 'FAIL: ' + FAILURES['bootstrap'] + '\n'
            self.assertTrue(result.stderr.endswith(failure), result.stderr)
            self.assertEqual(result.stderr.count('FAIL:'), 1)
            diagnostic = result.stderr[:-len(failure)]
            self.assertTrue(diagnostic, 'kubectl must report its transport failure')
            self.check_result(result, 'bootstrap', False, 1, diagnostic)
        else:
            self.check_result(result, 'bootstrap', success, 0)

    def test_short_marker(self):
        self.probe([b'[bootstrap] ' + ADMIN_MARKER + b'\n'], True)

    def test_marker_early_in_large_http_body(self):
        self.probe([ADMIN_MARKER + b'\n' + TAIL], True)

    def test_split_marker_and_trailing_http_writes(self):
        self.probe([ADMIN_MARKER[:17], ADMIN_MARKER[17:] + b'\n', TAIL], True)

    def test_absent_marker(self):
        self.probe([b'[bootstrap] ready\n'], False)

    def test_http_500_is_not_success(self):
        self.probe([], False, http_error=True)

    def test_truncated_http_body_after_marker_is_not_success(self):
        self.probe([ADMIN_MARKER + b'\n'], False, truncated=True)


if __name__ == '__main__':
    unittest.main()
