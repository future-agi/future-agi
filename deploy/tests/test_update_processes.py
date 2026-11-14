"""Real child-process tests; no Docker and no inherited terminal/environment."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ProcessTests(unittest.TestCase):
    def supervisor(self, **kwargs):
        from deploy.update.processes import ProcessSupervisor
        from deploy.update.contracts import AutomationPolicy
        return ProcessSupervisor(AutomationPolicy(), docker_context='test-context',
                                 environment={'PATH': os.defpath, 'HOME': str(Path.home()),
                                              'DOCKER_HOST': 'evil', 'PYTHONPATH': '/evil',
                                              'SECRET_TOKEN': 'not inherited'},
                                 grace_seconds=0.1, **kwargs)

    def test_noninteractive_clean_environment_and_registered_context(self):
        runner = self.supervisor()
        result = runner([sys.executable, '-I', '-c',
                         'import os,sys,json; print(json.dumps([sys.stdin.read(),os.isatty(0),dict(os.environ)]))'],
                        capture_output=True, text=True)
        stdin, tty, env = json.loads(result.stdout)
        self.assertEqual(stdin, '')
        self.assertFalse(tty)
        self.assertEqual(env['DOCKER_CONTEXT'], 'test-context')
        for key in ('SECRET_TOKEN', 'DOCKER_HOST', 'PYTHONPATH'):
            self.assertNotIn(key, env)

    def test_bounded_output_and_sanitized_exception(self):
        from deploy.update.processes import ProcessError
        from deploy.update.contracts import ReasonCode
        runner = self.supervisor(output_limit=1024)
        with self.assertRaises(ProcessError) as caught:
            runner([sys.executable, '-I', '-c', 'print("secret"*100000)'])
        self.assertEqual(caught.exception.reason_code, ReasonCode.PROCESS_FAILED)
        self.assertNotIn('secret', str(caught.exception))

    def test_timeout_kills_group_including_sigterm_ignoring_descendant(self):
        from deploy.update.processes import ProcessError
        from deploy.update.contracts import ReasonCode
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'survived'
            code = ('import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                    'pid=os.fork(); time.sleep(0.6); pathlib.Path(' + repr(str(marker)) + ').write_text("alive"); '
                    'time.sleep(10)')
            runner = self.supervisor()
            started = time.monotonic()
            with self.assertRaises(ProcessError) as caught:
                runner([sys.executable, '-I', '-c', code], timeout=0.15)
            self.assertEqual(caught.exception.reason_code, ReasonCode.OPERATION_TIMEOUT)
            self.assertLess(time.monotonic() - started, 2)
            time.sleep(0.7)
            self.assertFalse(marker.exists())
            self.assertTrue(runner.uncertain)

    def test_successful_leader_cannot_leave_background_children(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'survived'
            code = ('import os,time,pathlib; pid=os.fork(); '
                    'os._exit(0) if pid else None; time.sleep(0.6); '
                    'pathlib.Path(' + repr(str(marker)) + ').write_text("alive")')
            runner = self.supervisor()
            runner([sys.executable, '-I', '-c', code], timeout=2)
            time.sleep(0.7)
            self.assertFalse(marker.exists())

    def test_sigterm_cleans_owned_descendants_before_propagating(self):
        import signal
        import threading
        from deploy.update.apply import Interrupted
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'survived'
            code = ('import os,time,pathlib; os.fork(); time.sleep(0.6); '
                    'pathlib.Path(' + repr(str(marker)) + ').write_text("alive"); time.sleep(10)')
            timer = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM))
            previous = signal.getsignal(signal.SIGTERM)
            runner = self.supervisor()
            timer.start()
            try:
                with self.assertRaises(Interrupted):
                    runner([sys.executable, '-I', '-c', code], timeout=3)
            finally:
                timer.cancel()
                timer.join()
            self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
            self.assertTrue(runner.uncertain)
            time.sleep(0.7)
            self.assertFalse(marker.exists())

    def test_probe_pull_wait_and_backup_budgets_come_from_policy(self):
        runner = self.supervisor()
        self.assertEqual(runner._budget(['docker', '--context', 'test-context', 'compose', 'pull'], None),
                         runner.policy.pull_timeout_seconds)
        self.assertEqual(runner._budget(['docker', 'compose', 'config'], None),
                         runner.policy.probe_timeout_seconds)
        self.assertEqual(runner._budget(['docker', 'compose', 'up', '-d'], None),
                         runner.policy.wait_timeout_seconds)
        self.assertEqual(runner._budget(['/usr/bin/backup'], runner.policy.backup_timeout_seconds),
                         runner.policy.backup_timeout_seconds)
        self.assertEqual(runner._budget(['docker', 'compose', 'pull'], 0.1), 0.1)

    def test_attached_storage_start_uses_explicit_budget_not_probe_or_startup_cap(self):
        from dataclasses import replace
        runner = self.supervisor()
        runner.policy = replace(runner.policy, probe_timeout_seconds=90, wait_timeout_seconds=600,
                                backup_timeout_seconds=3600, restore_timeout_seconds=86400, tick_timeout_seconds=172800)
        runner.deadline = time.monotonic() + runner.policy.tick_timeout_seconds
        for flag in ('-a', '--attach', '-ai', '-ia'):
            args = ['docker', '--context', 'test-context', 'start', flag, 'a' * 64]
            for budget in (runner.policy.backup_timeout_seconds, runner.policy.restore_timeout_seconds):
                with self.subTest(flag=flag, budget=budget):
                    self.assertEqual(runner._budget(args, budget), budget)
            self.assertEqual(runner._budget(args, None), runner.policy.probe_timeout_seconds)
            self.assertEqual(runner._budget(args, 1), 1)
            with mock.patch('deploy.update.processes.time.monotonic', return_value=100):
                runner.deadline = 107.25
                self.assertEqual(runner._budget(args, 3600), 7.25)
            runner.deadline = time.monotonic() + runner.policy.tick_timeout_seconds
        self.assertEqual(runner._budget(['docker', 'start', 'a' * 64], 3600), 90)
        self.assertEqual(runner._budget(['docker', 'inspect', 'a' * 64], 3600), 90)
        self.assertEqual(runner._budget(['docker', 'compose', 'start', '-a', 'app'], 3600), 90)

    def test_real_attached_helper_outlives_probe_for_capture_archive_and_extraction(self):
        from dataclasses import replace
        import shlex
        with tempfile.TemporaryDirectory() as temporary:
            docker = Path(temporary) / 'docker'
            code = 'import sys,time; time.sleep(6); sys.stdout.buffer.write(sys.stdin.buffer.read() or b"archive")'
            docker.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -I -c ' + shlex.quote(code) + '\n')
            docker.chmod(0o700)
            for mode in ('capture', 'archive', 'extract'):
                with self.subTest(mode=mode):
                    runner = self.supervisor()
                    runner.policy = replace(runner.policy, probe_timeout_seconds=5, wait_timeout_seconds=60)
                    args = [str(docker), 'start', '-a', 'a' * 64]
                    with tempfile.NamedTemporaryFile() as stream:
                        if mode == 'archive':
                            result = runner.stream_to(args, stream.fileno(), max_bytes=1024, reserve_bytes=1, timeout=30)
                            self.assertEqual(result.sha256, hashlib.sha256(b'archive').hexdigest())
                        elif mode == 'extract':
                            stream.write(b'verified-archive')
                            stream.flush()
                            result = runner.stream_from(args, stream.fileno(), max_bytes=1024, timeout=30)
                            self.assertEqual(result.stdout, b'verified-archive')
                        else:
                            result = runner(args, timeout=30)
                            self.assertEqual(result.stdout, b'archive')
                        self.assertEqual(result.returncode, 0)
                        self.assertFalse(runner.uncertain)

    def test_total_budget_and_context_override_fail_closed(self):
        from deploy.update.processes import ProcessError
        runner = self.supervisor()
        with self.assertRaises(ProcessError):
            runner(['docker', '--context', 'foreign', 'ps'])
        runner.deadline = time.monotonic() - 1
        with self.assertRaises(ProcessError):
            runner([sys.executable, '-I', '-c', 'raise AssertionError()'])


class StreamingTests(unittest.TestCase):
    """Same validation/deadlines, bounded stdout-to-private-fd instead of RAM."""

    supervisor = ProcessTests.supervisor

    def test_large_stream_is_hashed_without_retaining_stdout(self):
        import hashlib
        runner = self.supervisor(output_limit=128)
        with tempfile.NamedTemporaryFile() as output:
            result = runner.stream_to([sys.executable, '-I', '-c',
                                       'import os; [os.write(1,b"x"*65536) for _ in range(128)]'],
                                      output.fileno(), max_bytes=8 * 1024 * 1024, reserve_bytes=1)
            self.assertEqual(result.size, 8 * 1024 * 1024)
            self.assertEqual(result.sha256, hashlib.sha256(b'x' * result.size).hexdigest())
            self.assertEqual(os.fstat(output.fileno()).st_size, result.size)
            self.assertEqual(result.stderr, b'')
            self.assertFalse(hasattr(result, 'stdout'))

    def test_stream_size_capacity_and_stderr_failures_keep_partial_files_unproven(self):
        from deploy.update.processes import ProcessError
        for mode in ('size', 'capacity', 'stderr'):
            runner = self.supervisor(output_limit=64)
            code = 'import os; os.write(1,b"x"*65536)'
            if mode == 'stderr':
                code += '; os.write(2,b"private"*1024)'
            with self.subTest(mode=mode), tempfile.NamedTemporaryFile() as output:
                with self.assertRaises(ProcessError):
                    runner.stream_to([sys.executable, '-I', '-c', code], output.fileno(),
                                     max_bytes=1024 if mode == 'size' else 100000,
                                     reserve_bytes=2 ** 62 if mode == 'capacity' else 1)
                self.assertTrue(runner.uncertain)
                self.assertLessEqual(os.fstat(output.fileno()).st_size, 65536)

    def test_initial_term_denial_still_attempts_kill_and_reaps_owned_child(self):
        import signal
        import subprocess
        runner = self.supervisor()
        child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(30)'], start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        real = os.killpg
        sent = []

        def killpg(pid, sig):
            sent.append(sig)
            if sig == signal.SIGTERM:
                raise PermissionError
            return real(pid, sig)

        with mock.patch('deploy.update.processes.os.killpg', killpg):
            runner._stop(child)
        self.assertEqual(sent[0], signal.SIGTERM)
        self.assertIn(signal.SIGKILL, sent)
        self.assertIsNotNone(child.poll())

    def test_stream_timeout_and_context_mismatch_preserve_existing_safety(self):
        from deploy.update.processes import ProcessError
        runner = self.supervisor()
        with tempfile.NamedTemporaryFile() as output:
            with self.assertRaises(ProcessError):
                runner.stream_to(['docker', '--context', 'other', 'ps'], output.fileno(), max_bytes=100)
            self.assertEqual(os.fstat(output.fileno()).st_size, 0)
            with self.assertRaises(ProcessError):
                runner.stream_to([sys.executable, '-I', '-c', 'import time; time.sleep(10)'],
                                 output.fileno(), max_bytes=100, timeout=0.05)
            self.assertTrue(runner.uncertain)

    def test_nonempty_public_or_hardlinked_output_is_not_adopted(self):
        from deploy.update.processes import ProcessError
        runner = self.supervisor()
        with tempfile.NamedTemporaryFile() as output:
            os.write(output.fileno(), b'existing')
            with self.assertRaises(ProcessError):
                runner.stream_to([sys.executable, '-I', '-c', 'print("new")'], output.fileno(), max_bytes=100)
            self.assertEqual(Path(output.name).read_bytes(), b'existing')
            os.ftruncate(output.fileno(), 0)
            os.lseek(output.fileno(), 0, os.SEEK_SET)
            os.fchmod(output.fileno(), 0o644)
            with self.assertRaises(ProcessError):
                runner.stream_to([sys.executable, '-I', '-c', 'print("new")'], output.fileno(), max_bytes=100)
            os.fchmod(output.fileno(), 0o600)
            alias = output.name + '-link'
            os.link(output.name, alias)
            try:
                with self.assertRaises(ProcessError):
                    runner.stream_to([sys.executable, '-I', '-c', 'print("new")'], output.fileno(), max_bytes=100)
            finally:
                os.unlink(alias)


class InputStreamingTests(unittest.TestCase):
    supervisor = ProcessTests.supervisor

    def test_large_archive_stdin_is_streamed_with_short_pipe_writes_and_hash_matches(self):
        import hashlib
        runner = self.supervisor(output_limit=256)
        payload = b'archive-fixture-' * (512 * 1024)
        with tempfile.NamedTemporaryFile() as source:
            source.write(payload)
            source.flush()
            real_write = os.write
            with mock.patch('deploy.update.processes.os.write', lambda fd, data: real_write(fd, data[:137])):
                result = runner.stream_from([sys.executable, '-I', '-c',
                    'import sys,hashlib; h=hashlib.sha256(); '
                    '[h.update(c) for c in iter(lambda:sys.stdin.buffer.read(65536),b"")]; print(h.hexdigest())'],
                    source.fileno(), max_bytes=len(payload), timeout=20)
            self.assertEqual(result.stdout.decode().strip(), hashlib.sha256(payload).hexdigest())
            self.assertFalse(runner.uncertain)

    def test_fixed_input_bytes_and_text_are_bounded_and_do_not_inherit_stdin(self):
        from deploy.update.processes import ProcessError
        runner = self.supervisor(output_limit=64)
        cmd = [sys.executable, '-I', '-c', 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())']
        self.assertEqual(runner(cmd, input=b'fixed model').stdout, b'fixed model')
        self.assertEqual(runner(cmd, input='fixed model', text=True).stdout, 'fixed model')
        for value in (b'x' * 65, 'not bytes without text', 1):
            with self.assertRaises(ProcessError):
                runner(cmd, input=value)

    def test_stream_from_rejects_nonprivate_nonregular_and_oversize_inputs(self):
        from deploy.update.processes import ProcessError
        runner = self.supervisor()
        with tempfile.NamedTemporaryFile() as source:
            source.write(b'x' * 1024)
            source.flush()
            with self.assertRaises(ProcessError):
                runner.stream_from(['true'], source.fileno(), max_bytes=1023)
            os.fchmod(source.fileno(), 0o644)
            with self.assertRaises(ProcessError):
                runner.stream_from(['true'], source.fileno(), max_bytes=1024)
            os.fchmod(source.fileno(), 0o600)
            alias = source.name + '.link'
            os.link(source.name, alias)
            try:
                with self.assertRaises(ProcessError):
                    runner.stream_from(['true'], source.fileno(), max_bytes=1024)
            finally:
                os.unlink(alias)

    def test_slow_reader_deadline_early_close_and_excess_output_are_uncertain(self):
        from deploy.update.processes import ProcessError
        for code in ('import time; time.sleep(30)', 'import os; os.close(0)',
                     'import os,time; os.write(2,b"x"*4096); time.sleep(30)'):
            runner = self.supervisor(output_limit=64)
            with tempfile.NamedTemporaryFile() as source:
                source.write(b'x' * 1024 * 1024)
                source.flush()
                with self.assertRaises(ProcessError):
                    runner.stream_from([sys.executable, '-I', '-c', code], source.fileno(), max_bytes=1024*1024, timeout=0.15)
                self.assertTrue(runner.uncertain)

    def test_archive_mutation_after_read_is_not_accepted(self):
        from deploy.update.processes import ProcessError
        import threading
        runner = self.supervisor()
        with tempfile.NamedTemporaryFile() as source:
            source.write(b'x' * 4096)
            source.flush()
            timer = threading.Timer(0.15, lambda: os.pwrite(source.fileno(), b'y', 0))
            timer.start()
            try:
                with self.assertRaises(ProcessError):
                    runner.stream_from([sys.executable, '-I', '-c', 'import sys,time; sys.stdin.buffer.read(); time.sleep(.4)'],
                                       source.fileno(), max_bytes=4096)
            finally:
                timer.cancel()
                timer.join()
            self.assertTrue(runner.uncertain)


class RegisteredContextProbeTests(unittest.TestCase):
    """Review round 2: the real supervisor lets the real capacity adapter read the registered context, and no more.

    The child is a real executable (a fake `docker` that logs its argv); neither the supervisor's command
    validation nor the adapter's own validation is stubbed. Only the platform and the daemon socket (there is
    no dockerd peer here) are simulated, and only AFTER the probe has run.
    """

    CONTEXT = 'native-test'

    def setUp(self):
        from deploy.update.contracts import AutomationPolicy
        from deploy.update.install import Installation
        from deploy.update.processes import ProcessSupervisor
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name).resolve()
        self.log = self.dir / 'docker.log'
        self.socket = str(self.dir / 'missing-dockerd.sock')
        bin_dir = self.dir / 'bin'
        bin_dir.mkdir()
        self.docker = bin_dir / 'docker'
        self.docker.write_text(
            '#!/bin/sh\n'
            'for word in "$@"; do printf "%s\\n" "$word" >> "' + str(self.log) + '"; done\n'
            'printf -- "--\\n" >> "' + str(self.log) + '"\n'
            'if [ "$*" = "--context ' + self.CONTEXT + ' context inspect ' + self.CONTEXT + '" ]; then\n'
            '  printf \'[{"Name":"' + self.CONTEXT + '","Endpoints":{"docker":{"Host":"unix://' + self.socket + '"}}}]\'\n'
            '  exit 0\n'
            'fi\n'
            'exit 91\n')
        self.docker.chmod(0o755)
        (self.dir / '.env').write_text('FUTURE_AGI_VERSION=v1.0.0\n')
        self.supervisor = ProcessSupervisor(AutomationPolicy(mode='download'), docker_context=self.CONTEXT,
                                            environment={'PATH': os.defpath}, grace_seconds=0.1)
        self.installation = Installation(self.dir, environ=self.supervisor.environment, runner=self.supervisor,
                                         docker=[str(self.docker), '--context', self.CONTEXT],
                                         compose=['docker', 'compose'])

    def calls(self):
        if not self.log.exists():
            return []
        return [call.split('\n') for call in self.log.read_text().split('--\n') if call]

    def test_the_real_capacity_adapter_runs_its_readonly_probe_through_the_real_supervisor(self):
        from deploy.update import host_facts
        connections = []

        class Peer:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def settimeout(self, seconds):
                pass

            def connect(self, path):
                connections.append(path)
                raise ConnectionRefusedError  # no dockerd peer in a unit test: capacity stays unknown

        binding = SimpleNamespace(docker_context=self.CONTEXT, daemon_identity='daemon-id')
        with mock.patch.object(host_facts, 'sys', SimpleNamespace(platform='linux')), \
                mock.patch.object(host_facts.socket, 'socket', Peer):
            capacity = host_facts.daemon_space_available(self.installation, binding)
        self.assertIsNone(capacity)
        # The probe really ran as a child with exactly this argv, and its answer was used to find the socket.
        self.assertEqual(self.calls(), [['--context', self.CONTEXT, 'context', 'inspect', self.CONTEXT, '']])
        self.assertEqual(connections, [self.socket])

    def test_native_capacity_succeeds_through_real_supervisor_and_adapter_validation(self):
        import socket
        import struct
        from deploy.update import host_facts
        root = self.dir / 'daemon-root'
        (root / 'overlay2').mkdir(parents=True)
        # Both responses come from the fake executable, not a mocked docker_run or command validator.
        script = self.docker.read_text()
        info = json.dumps({'ID': 'daemon-id', 'DockerRootDir': str(root), 'Driver': 'overlay2'})
        self.docker.write_text(script.replace('exit 91\n',
            'if [ "$*" = "--context ' + self.CONTEXT + ' info --format {{json .}}" ]; then\n'
            '  printf \'%s\' \'' + info + '\'\n  exit 0\nfi\nexit 91\n'))
        peer = mock.MagicMock()
        peer.getsockopt.return_value = struct.pack('3i', 12345, 0, 0)
        peer.__enter__.return_value = peer
        fake_socket = SimpleNamespace(AF_UNIX=socket.AF_UNIX, SOCK_STREAM=socket.SOCK_STREAM,
                                      SOL_SOCKET=socket.SOL_SOCKET, SO_PEERCRED=17,
                                      socket=mock.Mock(return_value=peer))
        read_text = Path.read_text

        def proc_text(path, *args, **kwargs):
            return 'dockerd\n' if str(path) == '/proc/12345/comm' else read_text(path, *args, **kwargs)

        fake_os = SimpleNamespace(geteuid=os.geteuid, readlink=mock.Mock(return_value='mnt:[1]'),
                                  stat=lambda path: os.stat('/') if str(path) == '/proc/12345/root' else os.stat(path),
                                  statvfs=mock.Mock(return_value=SimpleNamespace(f_bavail=123, f_frsize=4096)))
        binding = SimpleNamespace(docker_context=self.CONTEXT, daemon_identity='daemon-id')
        with mock.patch.object(host_facts, 'sys', SimpleNamespace(platform='linux')), \
                mock.patch.object(host_facts, 'socket', fake_socket), \
                mock.patch.object(host_facts, 'os', fake_os), \
                mock.patch.object(Path, 'read_text', proc_text):
            self.assertEqual(host_facts.daemon_space_available(self.installation, binding), 123 * 4096)
        self.assertEqual(self.calls(), [['--context', self.CONTEXT, 'context', 'inspect', self.CONTEXT, ''],
                                       ['--context', self.CONTEXT, 'info', '--format', '{{json .}}', '']])
        peer.connect.assert_called_once_with(self.socket)
        fake_os.statvfs.assert_called_once_with(root / 'overlay2')

    def test_other_platforms_never_spawn_the_probe(self):
        from deploy.update import host_facts
        binding = SimpleNamespace(docker_context=self.CONTEXT, daemon_identity='daemon-id')
        with mock.patch.object(host_facts, 'sys', SimpleNamespace(platform='darwin')):
            self.assertIsNone(host_facts.daemon_space_available(self.installation, binding))
        self.assertEqual(self.calls(), [])

    def test_exactly_the_registered_inspect_argv_is_accepted_with_or_without_the_leading_context(self):
        explicit = [str(self.docker), '--context', self.CONTEXT, 'context', 'inspect', self.CONTEXT]
        self.assertEqual(self.supervisor._command(explicit), explicit)
        self.assertEqual(self.supervisor._command([str(self.docker), 'context', 'inspect', self.CONTEXT]), explicit)
        self.assertEqual(self.supervisor._command(['docker', 'context', 'inspect', self.CONTEXT]),
                         ['docker', '--context', self.CONTEXT, 'context', 'inspect', self.CONTEXT])
        # Ordinary registered-context commands are unchanged.
        self.assertEqual(self.supervisor._command(['docker', 'info']), ['docker', '--context', self.CONTEXT, 'info'])

    def test_every_other_context_subcommand_and_shape_is_refused_without_a_child(self):
        from deploy.update.contracts import ReasonCode
        from deploy.update.processes import ProcessError
        c = self.CONTEXT
        rejected = [('context', 'use', c), ('context', 'create', 'x'), ('context', 'update', c), ('context', 'rm', c),
                    ('context', 'rm', '-f', c), ('context', 'ls'), ('context', 'list'), ('context', 'export', c),
                    ('context', 'import', 'x', '/tmp/f'), ('context', 'show'), ('context',), ('context', 'inspect'),
                    ('context', 'inspect', 'other'), ('context', 'inspect', c, 'extra'), ('context', 'inspect', c, c),
                    ('context', 'inspect', '--format', '{{.Name}}', c), ('context', 'inspect', c, '--format', 'x'),
                    ('context', 'inspect', c, '--context', 'other'), ('context', 'inspect', 'default'),
                    ('info', 'context'), ('run', '--rm', 'img', 'context', 'inspect', c)]
        for words in rejected:
            with self.subTest(words=words), self.assertRaises(ProcessError) as caught:
                self.installation.docker_run(*words)
            self.assertEqual(caught.exception.reason_code, ReasonCode.OWNERSHIP_UNCONFIRMED)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.supervisor.uncertain)

    def test_routing_overrides_stay_refused_even_with_the_exact_probe_words(self):
        from deploy.update.contracts import ReasonCode
        from deploy.update.processes import ProcessError
        c, tail = self.CONTEXT, ['context', 'inspect', self.CONTEXT]
        docker = str(self.docker)
        rejected = [[docker, '--context', 'other', 'context', 'inspect', 'other'],
                    [docker, '-c', c, *tail], [docker, '-H', 'tcp://127.0.0.1:2375', *tail],
                    [docker, '--host', 'tcp://127.0.0.1:2375', *tail], [docker, '--host=tcp://x', *tail],
                    [docker, '--config', '/somewhere', *tail], [docker, '--config=/somewhere', *tail],
                    [docker, '--context=' + c, *tail], [docker, '--context', c, '--context', 'other', *tail],
                    [docker, '--context', c, '-H', 'tcp://x', *tail],
                    [docker, '--context', c, *tail, '--context', 'other'],
                    [docker, '--context', c, *tail, '--host=tcp://x'],
                    [docker, '--context', c, '--config', '/somewhere', *tail]]
        for argv in rejected:
            with self.subTest(argv=argv), self.assertRaises(ProcessError) as caught:
                self.supervisor(argv, capture_output=True, text=True)
            self.assertEqual(caught.exception.reason_code, ReasonCode.OWNERSHIP_UNCONFIRMED)
        self.assertEqual(self.calls(), [])

    def test_a_context_named_like_the_subcommand_still_only_allows_its_own_inspection(self):
        from deploy.update.contracts import AutomationPolicy
        from deploy.update.processes import ProcessError, ProcessSupervisor
        supervisor = ProcessSupervisor(AutomationPolicy(mode='download'), docker_context='context', environment={})
        self.assertEqual(supervisor._command(['docker', 'context', 'inspect', 'context']),
                         ['docker', '--context', 'context', 'context', 'inspect', 'context'])
        for words in (['docker', 'context', 'inspect', 'inspect'], ['docker', 'context', 'use', 'context']):
            with self.subTest(words=words), self.assertRaises(ProcessError):
                supervisor._command(words)


class SupervisorEdgeTests(unittest.TestCase):
    """Review round 2 (coverage): refusals and edge paths of the supervisor, with real children."""

    def supervisor(self, **kwargs):
        from deploy.update.contracts import AutomationPolicy
        from deploy.update.processes import ProcessSupervisor
        kwargs.setdefault('grace_seconds', 0.1)
        return ProcessSupervisor(AutomationPolicy(), docker_context='test-context', environment={'PATH': os.defpath},
                                 **kwargs)

    def test_invalid_supervision_policy_is_rejected_at_construction(self):
        from deploy.update.contracts import AutomationPolicy
        from deploy.update.processes import ProcessSupervisor
        for kwargs in ({'docker_context': ''}, {'docker_context': 'c', 'grace_seconds': 0},
                       {'docker_context': 'c', 'grace_seconds': 11}, {'docker_context': 'c', 'output_limit': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ProcessSupervisor(AutomationPolicy(), environment={}, **kwargs)

    def test_malformed_argv_unsupported_keywords_missing_programs_and_failures_are_typed(self):
        from deploy.update.contracts import ReasonCode
        from deploy.update.processes import ProcessError
        runner = self.supervisor()
        for call in (lambda: runner([]), lambda: runner(['true', 3]), lambda: runner(['tr\0ue']),
                     lambda: runner(['true'], shell=True), lambda: runner(['/nonexistent-fake/program']),
                     lambda: runner([sys.executable, '-I', '-c', 'raise SystemExit(3)'], check=True)):
            with self.assertRaises(ProcessError) as caught:
                call()
            self.assertEqual(caught.exception.reason_code, ReasonCode.PROCESS_FAILED)
        result = runner([sys.executable, '-I', '-c', 'raise SystemExit(3)'])
        self.assertEqual(result.returncode, 3)

    def test_a_child_that_closes_its_pipes_but_keeps_running_times_out_as_uncertain(self):
        from deploy.update.contracts import ReasonCode
        from deploy.update.processes import ProcessError
        runner = self.supervisor()
        started = time.monotonic()
        with self.assertRaises(ProcessError) as caught:
            runner([sys.executable, '-I', '-c', 'import os,time; os.close(1); os.close(2); time.sleep(30)'], timeout=0.4)
        self.assertEqual(caught.exception.reason_code, ReasonCode.OPERATION_TIMEOUT)
        self.assertTrue(caught.exception.uncertain)
        self.assertTrue(runner.uncertain)
        self.assertLess(time.monotonic() - started, 5)

    def delayed_first_select(self):
        """A selector whose first wait reports nothing until the leader has exited: the final drain reads the pipes."""
        import selectors

        class Delayed(selectors.DefaultSelector):
            first = True

            def select(self, timeout=None):
                if Delayed.first:
                    Delayed.first = False
                    time.sleep(0.6)  # long enough for the child to write and exit
                    return []
                return super().select(timeout)

        return Delayed

    def test_output_written_before_the_leader_exits_is_drained_and_returned(self):
        runner = self.supervisor()
        with mock.patch('deploy.update.processes.selectors.DefaultSelector', self.delayed_first_select()):
            result = runner([sys.executable, '-I', '-c', 'import sys; print("out" * 5); print("err" * 5, file=sys.stderr)'],
                            capture_output=True, text=True)
        self.assertEqual((result.stdout, result.stderr), ('outoutoutoutout\n', 'errerrerrerrerr\n'))

    def test_output_over_the_limit_is_refused_while_draining(self):
        from deploy.update.contracts import ReasonCode
        from deploy.update.processes import ProcessError
        runner = self.supervisor(output_limit=64)
        with mock.patch('deploy.update.processes.selectors.DefaultSelector', self.delayed_first_select()):
            with self.assertRaises(ProcessError) as caught:
                runner([sys.executable, '-I', '-c', 'print("x" * 500)'], capture_output=True)
        self.assertEqual(caught.exception.reason_code, ReasonCode.PROCESS_FAILED)
        self.assertNotIn('xxxx', str(caught.exception))

    def test_stop_still_sends_the_final_kill_when_signal_zero_is_denied(self):
        import signal
        import subprocess
        runner = self.supervisor()
        child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(30)'], start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        real = os.killpg
        signals = []

        def killpg(pid, sig):
            signals.append(sig)
            if sig == 0:
                raise PermissionError  # Darwin can deny signal-zero for a group it is tearing down
            return real(pid, sig)

        with mock.patch('deploy.update.processes.os.killpg', killpg):
            runner._stop(child)
        self.assertEqual(signals[:3], [signal.SIGTERM, 0, signal.SIGKILL])
        self.assertIsNotNone(child.poll())
