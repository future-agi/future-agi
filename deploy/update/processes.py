"""Bounded, noninteractive children for registered scheduled runs (stdlib only).

Install this runner on Installation AND on Updater's backup hook. It is not the
manual CLI's runner. Captured output is private, bounded memory, never a public
error. A timeout means external mutation state is unknown even after local child
cleanup: a killed Docker client cannot retract an operation sent to the daemon.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
import selectors
import signal
import subprocess
import time
from typing import Mapping, Optional, Sequence

from .contracts import AutomationPolicy, ReasonCode

_BASE_ENV = frozenset(('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'DOCKER_CONFIG'))
_FORBIDDEN_ENV = frozenset(('DOCKER_HOST', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH', 'DOCKER_CONTEXT',
                            'PYTHONPATH', 'PYTHONHOME', 'BASH_ENV', 'ENV', 'CDPATH', 'GIT_CONFIG',
                            'GIT_CONFIG_COUNT', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'))


class ProcessError(RuntimeError):
    def __init__(self, reason_code: ReasonCode, *, uncertain: bool = False):
        self.reason_code = ReasonCode(reason_code)
        self.uncertain = uncertain
        super().__init__(self.reason_code.value)


@dataclass(frozen=True, repr=False)
class StreamResult:
    """Private stdout identity; stderr is bounded and must never be projected."""

    returncode: int
    size: int
    sha256: str
    stderr: bytes


class ProcessSupervisor:
    """subprocess.run-compatible subset; one live tick owns this instance.

    environment is the registered launcher environment, not ambient os.environ.
    Per-call env is explicit (Compose overlays or the backup hook protocol).
    Callers must not copy ambient exports into that mapping. Injections that can
    redirect Docker or inject interpreter startup code are stripped regardless.
    """

    def __init__(self, policy: AutomationPolicy, *, docker_context: str,
                 environment: Mapping[str, str], output_limit: int = 1024 * 1024,
                 grace_seconds: float = 10.0):
        if not docker_context or not 0 < grace_seconds <= 10 or output_limit < 1:
            raise ValueError('invalid process supervision policy')
        self.policy = policy
        self.docker_context = docker_context
        self.environment = {k: v for k, v in environment.items() if k in _BASE_ENV}
        self.environment.setdefault('PATH', os.defpath)
        self.environment.update(DOCKER_CONTEXT=docker_context, GIT_TERMINAL_PROMPT='0',
                                GCM_INTERACTIVE='Never', CI='true', COMPOSE_INTERACTIVE_NO_CLI='1')
        self.output_limit, self.grace_seconds = output_limit, grace_seconds
        self.deadline = time.monotonic() + policy.tick_timeout_seconds
        self.uncertain = False

    def _environment(self, values):
        result = dict(self.environment)
        for key, value in (values or {}).items():
            if (key not in _FORBIDDEN_ENV and not key.startswith(('LD_', 'DYLD_', 'GIT_CONFIG_'))
                    and key not in _BASE_ENV):
                result[key] = value
        return result

    def _command(self, args):
        args = list(args)
        if not args or not all(isinstance(a, str) and '\0' not in a for a in args):
            raise ProcessError(ReasonCode.PROCESS_FAILED)
        if os.path.basename(args[0]) == 'docker':
            # Context/host/config routing is never accepted from the command.
            if any(a in ('-H', '--host', '-c', '--context', '--config')
                   or a.startswith(('--host=', '--context=', '--config=')) for a in args[1:]):
                # Installation's registered explicit context is the sole exception.
                if args[1:3] != ['--context', self.docker_context] or any(
                        a in ('-H', '--host', '-c', '--context', '--config') or
                        a.startswith(('--host=', '--context=', '--config=')) for a in args[3:]):
                    raise ProcessError(ReasonCode.OWNERSHIP_UNCONFIRMED)
            else:
                args[1:1] = ['--context', self.docker_context]
            if 'context' in args[3:] and args[3:] != ['context', 'inspect', self.docker_context]:
                # The ONE context subcommand allowed is the read-only inspection of the registered context itself
                # (host_facts.daemon_space_available needs it to qualify a native local daemon): exactly this
                # argv, no other name, flag or extra word. Never use/create/update/rm/ls/export/import.
                raise ProcessError(ReasonCode.OWNERSHIP_UNCONFIRMED)
        return args

    def _budget(self, args, timeout):
        from .install import split_command
        program, command = split_command(args)
        verb = command[0] if command else ''
        if program == ['docker'] and verb == 'start' and any(
                flag in ('-a', '--attach', '-ai', '-ia') for flag in command[1:]):
            # This client remains attached for the helper's entire operation,
            # not just its startup. Storage callers supply the remaining backup
            # or restore budget, which can exceed the ordinary startup budget.
            # Without an explicit budget retain the short default; the outer
            # tick/recovery deadline below always wins.
            limit = timeout if timeout is not None else self.policy.probe_timeout_seconds
        elif program in (['docker'], ['docker', 'compose'], ['docker-compose']):
            limit = (self.policy.pull_timeout_seconds if verb == 'pull' else
                     self.policy.wait_timeout_seconds if verb in ('up', 'run', 'exec') else
                     self.policy.probe_timeout_seconds)
        else:
            limit = timeout if timeout is not None else self.policy.probe_timeout_seconds
        return min(limit, timeout if timeout is not None else limit, self.deadline - time.monotonic())

    def _stop(self, process):
        # Even a successful leader may leave descendants. Always clean its owned
        # process group, never a name match or a host-wide process scan.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=2)
            return
        except PermissionError:
            # Darwin can also deny the first TERM as the group exits. Do not
            # mask a stream/capacity failure or infer cleanup: still try the
            # final KILL and reap the owned leader below.
            pass
        deadline = time.monotonic() + self.grace_seconds
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                # Darwin can deny signal-zero for an exited group. Do not
                # infer that descendants are gone: still send the final KILL.
                break
            time.sleep(0.02)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # An exiting Darwin group may return EPERM rather than ESRCH.
            # Reap the owned leader below, as the manual hook supervisor does.
            pass
        # apply._reap handles an interrupt inside Popen's wait lock on Python
        # 3.15 as well as 3.9. Never release the engine lock with a live child.
        from .apply import _reap
        _reap(process)

    def __call__(self, args: Sequence[str], *, cwd=None, env: Optional[Mapping[str, str]] = None,
                 check=False, timeout=None, capture_output=False, text=False, **kwargs):
        from .apply import signals_as_interrupt
        with signals_as_interrupt():
            return self._run(args, cwd=cwd, env=env, check=check, timeout=timeout,
                             capture_output=capture_output, text=text, **kwargs)

    def stream_to(self, args: Sequence[str], output_fd: int, *, max_bytes: int,
                  reserve_bytes: int = 64 * 1024 * 1024, cwd=None, env=None,
                  timeout=None) -> StreamResult:
        """Stream stdout to an already exclusive private empty regular file.

        Uses the SAME command, context, environment, deadline and group cleanup
        as ordinary calls. At most 64 KiB of stdout is resident; stderr retains
        the ordinary output cap. Checks destination capacity before every write.
        The caller owns the fd, fsync, archive verification and incomplete-file
        retention. A local write/capacity failure also leaves child state unknown.
        """
        from .apply import signals_as_interrupt
        if (type(max_bytes) is not int or max_bytes < 1
                or type(reserve_bytes) is not int or reserve_bytes < 0):
            raise ProcessError(ReasonCode.PROCESS_FAILED)
        info = os.fstat(output_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size or os.lseek(output_fd, 0, os.SEEK_CUR)):
            raise ProcessError(ReasonCode.OWNERSHIP_UNCONFIRMED)
        stream = dict(fd=output_fd, maximum=max_bytes, reserve=reserve_bytes,
                      size=0, digest=hashlib.sha256())
        with signals_as_interrupt():
            result = self._run(args, cwd=cwd, env=env, timeout=timeout, _stream=stream)
        return StreamResult(result.returncode, stream['size'], stream['digest'].hexdigest(), result.stderr)

    def stream_from(self, args: Sequence[str], input_fd: int, *, max_bytes: int,
                    cwd=None, env=None, timeout=None, expected_identity=None):
        """Bounded nonblocking stdin from a prequalified, immutable archive FD.

        Never buffers the archive; at most 64 KiB is in flight. This does not
        authenticate the archive. Caller verifies the sealed hash first and
        independently verifies the extracted tree after the helper exits.
        """
        from .apply import signals_as_interrupt
        info = os.fstat(input_fd)
        if (type(max_bytes) is not int or max_bytes < 1 or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1 or not 0 < info.st_size <= max_bytes):
            raise ProcessError(ReasonCode.OWNERSHIP_UNCONFIRMED)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if expected_identity is not None and identity(info) != expected_identity:
            raise ProcessError(ReasonCode.OWNERSHIP_UNCONFIRMED)
        os.lseek(input_fd, 0, os.SEEK_SET)
        source = dict(fd=input_fd, size=info.st_size, sent=0, pending=b'')
        with signals_as_interrupt():
            result = self._run(args, cwd=cwd, env=env, timeout=timeout, _input=source)
        if identity(info) != identity(os.fstat(input_fd)) or source['sent'] != info.st_size:
            self.uncertain = True
            raise ProcessError(ReasonCode.PROCESS_FAILED, uncertain=True)
        return result

    def _run(self, args, *, cwd=None, env=None, check=False, timeout=None,
             capture_output=False, text=False, _stream=None, _input=None, input=None, **kwargs):
        if isinstance(input, str) and text:
            input = input.encode('utf-8')
        if kwargs or input is not None and (_input is not None or not isinstance(input, bytes)
                                            or len(input) > self.output_limit):
            raise ProcessError(ReasonCode.PROCESS_FAILED)
        if input is not None:
            _input = dict(data=input, size=len(input), sent=0, pending=b'')
        args = self._command(args)
        budget = self._budget(args, timeout)
        if budget <= 0:
            raise ProcessError(ReasonCode.OPERATION_TIMEOUT)
        try:
            child = subprocess.Popen(args, cwd=cwd, env=self._environment(env),
                                     stdin=subprocess.PIPE if _input is not None else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                     close_fds=True)
        except OSError:
            raise ProcessError(ReasonCode.PROCESS_FAILED) from None
        deadline = time.monotonic() + budget
        buffers = {child.stdout: bytearray(), child.stderr: bytearray()}

        def consume(pipe, chunk):
            if _stream is not None and pipe is child.stdout:
                space = os.fstatvfs(_stream['fd'])
                if (_stream['size'] + len(chunk) > _stream['maximum']
                        or space.f_bavail * space.f_frsize < _stream['reserve'] + len(chunk)):
                    raise ProcessError(ReasonCode.PROCESS_FAILED, uncertain=True)
                remaining = memoryview(chunk)
                while remaining:
                    written = os.write(_stream['fd'], remaining)
                    if written <= 0:
                        raise ProcessError(ReasonCode.PROCESS_FAILED, uncertain=True)
                    remaining = remaining[written:]
                _stream['size'] += len(chunk)
                _stream['digest'].update(chunk)
            else:
                buffers[pipe].extend(chunk)
                if sum(map(len, buffers.values())) > self.output_limit:
                    raise ProcessError(ReasonCode.PROCESS_FAILED, uncertain=True)

        try:
            with selectors.DefaultSelector() as selector:
                for pipe in buffers:
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ)
                if _input is not None:
                    os.set_blocking(child.stdin.fileno(), False)
                    selector.register(child.stdin, selectors.EVENT_WRITE)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        self.uncertain = True
                        raise ProcessError(ReasonCode.OPERATION_TIMEOUT, uncertain=True)
                    for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                        if key.fileobj is child.stdin:
                            if not _input['pending'] and _input['sent'] < _input['size']:
                                if 'fd' in _input:
                                    chunk = os.read(_input['fd'], min(65536, _input['size'] - _input['sent']))
                                    if not chunk:
                                        raise ProcessError(ReasonCode.PROCESS_FAILED, uncertain=True)
                                else:
                                    chunk = _input['data'][_input['sent']:_input['sent'] + 65536]
                                _input['pending'] = chunk
                            if _input['pending']:
                                try:
                                    count = os.write(child.stdin.fileno(), _input['pending'])
                                except BlockingIOError:
                                    continue
                                except BrokenPipeError:
                                    raise ProcessError(ReasonCode.PROCESS_FAILED, uncertain=True) from None
                                if count <= 0:
                                    raise ProcessError(ReasonCode.PROCESS_FAILED, uncertain=True)
                                _input['sent'] += count
                                _input['pending'] = _input['pending'][count:]
                            if _input['sent'] == _input['size']:
                                selector.unregister(child.stdin)
                                child.stdin.close()
                            continue
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        consume(key.fileobj, chunk)
                    if child.poll() is not None:
                        # Drain what the leader wrote without waiting for a
                        # background descendant to close inherited pipes.
                        for pipe in list(buffers):
                            while True:
                                try:
                                    chunk = os.read(pipe.fileno(), 65536)
                                except BlockingIOError:
                                    break
                                if not chunk:
                                    break
                                if time.monotonic() >= deadline:
                                    raise ProcessError(ReasonCode.OPERATION_TIMEOUT, uncertain=True)
                                consume(pipe, chunk)
                        break
                remaining = max(0.001, deadline - time.monotonic())
                try:
                    child.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    self.uncertain = True
                    raise ProcessError(ReasonCode.OPERATION_TIMEOUT, uncertain=True) from None
        except BaseException:
            self.uncertain = True
            raise
        finally:
            # A second terminal/service-manager signal must not interrupt child
            # cleanup and release update.lock while descendants are still live.
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM, signal.SIGHUP})
            try:
                self._stop(child)
            finally:
                for pipe in buffers:
                    pipe.close()
                if child.stdin is not None:
                    child.stdin.close()
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        if _input is not None and _input['sent'] != _input['size']:
            self.uncertain = True
            raise ProcessError(ReasonCode.PROCESS_FAILED, uncertain=True)
        stdout, stderr = (bytes(buffers[pipe]) for pipe in (child.stdout, child.stderr))
        if text:
            stdout, stderr = stdout.decode('utf-8', 'replace'), stderr.decode('utf-8', 'replace')
        if check and child.returncode:
            raise ProcessError(ReasonCode.PROCESS_FAILED)
        return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
