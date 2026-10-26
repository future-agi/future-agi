"""Bounded, noninteractive children for registered scheduled runs (stdlib only).

Install this runner on Installation AND on Updater's backup hook. It is not the
manual CLI's runner. Captured output is private, bounded memory, never a public
error. A timeout means external mutation state is unknown even after local child
cleanup: a killed Docker client cannot retract an operation sent to the daemon.
"""
from __future__ import annotations

import os
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
        if program in (['docker'], ['docker', 'compose'], ['docker-compose']):
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

    def _run(self, args, *, cwd=None, env=None, check=False, timeout=None,
             capture_output=False, text=False, **kwargs):
        if kwargs:
            raise ProcessError(ReasonCode.PROCESS_FAILED)
        args = self._command(args)
        budget = self._budget(args, timeout)
        if budget <= 0:
            raise ProcessError(ReasonCode.OPERATION_TIMEOUT)
        try:
            child = subprocess.Popen(args, cwd=cwd, env=self._environment(env), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                     close_fds=True)
        except OSError:
            raise ProcessError(ReasonCode.PROCESS_FAILED) from None
        deadline = time.monotonic() + budget
        buffers = {child.stdout: bytearray(), child.stderr: bytearray()}
        try:
            with selectors.DefaultSelector() as selector:
                for pipe in buffers:
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        self.uncertain = True
                        raise ProcessError(ReasonCode.OPERATION_TIMEOUT, uncertain=True)
                    for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        buffers[key.fileobj].extend(chunk)
                        if sum(map(len, buffers.values())) > self.output_limit:
                            self.uncertain = True
                            raise ProcessError(ReasonCode.PROCESS_FAILED, uncertain=True)
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
                                buffers[pipe].extend(chunk)
                                if sum(map(len, buffers.values())) > self.output_limit:
                                    raise ProcessError(ReasonCode.PROCESS_FAILED)
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
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        stdout, stderr = (bytes(buffers[pipe]) for pipe in (child.stdout, child.stderr))
        if text:
            stdout, stderr = stdout.decode('utf-8', 'replace'), stderr.decode('utf-8', 'replace')
        if check and child.returncode:
            raise ProcessError(ReasonCode.PROCESS_FAILED)
        return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
