"""Run media_worker.py confined by seccomp-bpf; never a permissive fallback.

Replaces the earlier bubblewrap runner, which fails closed on GCP VMs and in
default Docker/Kubernetes containers (no unprivileged network namespace:
RTM_NEWADDR EPERM). Seccomp filters need no namespace or capability and are
allowed under Docker's and Kubernetes' RuntimeDefault profiles. SANDBOX.md
documents the boundary and how readiness proves it is active.
"""

import fcntl
import hashlib
import json
import os
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from tracer.services.attachments.contract import AttachmentError

WORKER = Path(__file__).with_name("media_worker.py")
TIMEOUT_SECONDS = 60
STDOUT_LIMIT = 64 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
BUFFER_BYTES = 256 * 1024


def _interpreter():
    # The worker runs with -I -S, so the venv's site-packages are never loaded.
    return sys.executable or "/usr/bin/python3"


def run(operation, input_path, *args, output=None, output_limit=0):
    """Return the worker's JSON result. ``output`` is an open binary file.

    The child receives only the read-only input descriptor and, if given, the
    output descriptor. It has an empty environment, no stdin and no stderr.
    """
    if output is not None and not 0 < output_limit <= MAX_OUTPUT_BYTES:
        raise AttachmentError("validation_unavailable", 503)
    source = None
    try:
        source = _input_fd(input_path)
        out_fd = output.fileno() if output is not None else -1
        command = [
            _interpreter(),
            "-I",
            *([] if operation == "source" and args else ["-S"]),
            "-B",
            str(WORKER),
            operation,
            str(source),
            str(out_fd),
            # RLIMIT_FSIZE also covers the stdout file, so it is at least the
            # stdout cap; the parent re-checks the exact output size anyway.
            str(max(output_limit, STDOUT_LIMIT + 1)),
            *(str(value) for value in args),
        ]
        with tempfile.TemporaryFile() as stdout:
            completed = subprocess.run(
                command,
                env={},
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=subprocess.DEVNULL,
                pass_fds=tuple(
                    fd for fd in (source, out_fd, *_ACTIVE_IMPORTS.values()) if fd >= 0
                ),
                close_fds=True,
                cwd="/",
                start_new_session=True,
                timeout=600 if operation == "source" and args else TIMEOUT_SECONDS,
                check=False,
            )
            code = completed.returncode
            if code == 2 or code == -signal.SIGSYS:
                raise AttachmentError("validation_rejected", 422)
            if code != 0:
                raise AttachmentError("validation_unavailable", 503)
            stdout.seek(0)
            raw = stdout.read(STDOUT_LIMIT + 1)
            if len(raw) > STDOUT_LIMIT:
                raise AttachmentError("validation_rejected", 422)
            if output is not None and os.fstat(output.fileno()).st_size > output_limit:
                raise AttachmentError("validation_rejected", 422)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise AttachmentError("validation_rejected", 422)
            return result
    except AttachmentError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        # Never surface paths, interpreter output or exception text.
        raise AttachmentError("validation_unavailable", 503) from None
    finally:
        if source is not None:
            os.close(source)


def self_test():
    """Readiness: prove confinement is active in THIS runtime, or fail closed.

    Each negative control must be killed by SIGSYS. A worker that can open a
    file, create a socket or exec means the boundary is absent.
    """
    try:
        for operation in ("deny-socket", "deny-open", "deny-exec"):
            completed = subprocess.run(
                [
                    _interpreter(),
                    "-I",
                    "-S",
                    "-B",
                    str(WORKER),
                    operation,
                    "-1",
                    "-1",
                    "1",
                ],
                env={},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                cwd="/",
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
            if completed.returncode != -signal.SIGSYS:
                raise AttachmentError("sandbox_unavailable", 503)
    except AttachmentError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise AttachmentError("sandbox_unavailable", 503) from None


def probe(path):
    result = run("probe", path)
    if set(result) != {
        "mime_type",
        "sample_rate",
        "channels",
        "frame_count",
    } or result["mime_type"] != ("audio/wav"):
        raise AttachmentError("validation_rejected", 422)
    return result


# One per-epoch guard is retained, even after the data files have been removed.
# It is a termination proof on the configured durable worker filesystem, not a
# new DB ledger or an assumption that a lease timeout killed a paused process.
_ACTIVE_IMPORTS = {}


def _scratch_root():
    from django.conf import settings

    value = getattr(settings, "FI_ATTACHMENTS_WORK_DIR", "")
    if not value or not os.path.isabs(value):
        raise AttachmentError("validation_unavailable", 503)
    root = os.open(value, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    return root


def _directory(parent, name, *, create):
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    if os.fstat(fd).st_mode & 0o077:
        os.close(fd)
        raise AttachmentError("validation_unavailable", 503)
    return fd


@contextmanager
def _epoch(job_id, epoch, *, create=False):
    if (
        not isinstance(job_id, uuid.UUID)
        or type(epoch) is not int
        or not 1 <= epoch <= 3
    ):
        raise AttachmentError("conflict", 409)
    descriptors = []
    try:
        root = _scratch_root()
        descriptors.append(root)
        for name in ("import", str(job_id), str(epoch)):
            root = _directory(root, name, create=create)
            descriptors.append(root)
        yield root
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def prepare_import_scratch(job_id, epoch):
    with _epoch(job_id, epoch, create=True) as directory:
        fd = os.open(
            "guard", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        os.close(fd)


@contextmanager
def import_execution(job_id, epoch):
    with _epoch(job_id, epoch) as directory:
        guard = os.open("guard", os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory)
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _ACTIVE_IMPORTS[(job_id, epoch)] = guard
            yield directory
        except BlockingIOError:
            raise AttachmentError("conflict", 409) from None
        finally:
            _ACTIVE_IMPORTS.pop((job_id, epoch), None)
            os.close(guard)


def require_import_execution(job_id, epoch):
    if (job_id, epoch) not in _ACTIVE_IMPORTS:
        raise AttachmentError("validation_unavailable", 503)


@contextmanager
def scratch_file(directory, name):
    if name not in {"source", "segment"}:
        raise AttachmentError("validation_unavailable", 503)
    fd = os.open(
        name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    try:
        # /proc/self/fd is a server-owned descriptor, never a caller pathname.
        with os.fdopen(fd, "w+b") as stream:
            yield _NamedScratch(stream, f"/proc/self/fd/{fd}")
    finally:
        os.unlink(name, dir_fd=directory)


class _NamedScratch:
    def __init__(self, stream, name):
        self.stream, self.name = stream, name

    def __getattr__(self, name):
        return getattr(self.stream, name)


def cleanup_import_scratch(job_id, epochs):
    if not 0 <= epochs <= 3:
        return False
    guards = []
    try:
        # Hold EVERY epoch guard until the scan is complete; a paused A can
        # never have its source removed or its capacity released by B.
        for epoch in range(1, epochs + 1):
            with _epoch(job_id, epoch) as directory:
                guard = os.open("guard", os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory)
                guards.append(guard)
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                names = os.listdir(directory)
                if len(names) > 3 or set(names) - {"guard", "source", "segment"}:
                    return False
                for name in names:
                    if name != "guard":
                        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            return False
                        os.unlink(name, dir_fd=directory)
        return True
    except (OSError, AttachmentError):
        return False
    finally:
        for guard in guards:
            os.close(guard)


def hash_file(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(BUFFER_BYTES), b""):
        digest.update(block)
    stream.seek(0)
    return digest.hexdigest()


def _input_fd(input_path):
    # Only an exact descriptor from our own scratch wrapper bypasses NOFOLLOW.
    prefix = "/proc/self/fd/"
    if str(input_path).startswith(prefix) and str(input_path)[len(prefix) :].isdigit():
        return os.dup(int(str(input_path)[len(prefix) :]))
    return os.open(input_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)


@contextmanager
def pcm_stream(input_path, mime_type, facts, *, deadline):
    source = _input_fd(input_path)
    process = None
    try:
        timeout = min(600, (deadline - datetime.now(UTC)).total_seconds())
        if timeout <= 0:
            raise AttachmentError("expired", 410)
        command = [
            _interpreter(),
            "-I",
            "-B",
            str(WORKER),
            "pcm",
            str(source),
            "1",
            str(1024**3),
            mime_type,
            str(facts["sample_rate"]),
            str(facts["channels"]),
            str(facts["frame_count"]),
        ]
        process = subprocess.Popen(
            command,
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=(source, *_ACTIVE_IMPORTS.values()),
            close_fds=True,
            cwd="/",
            start_new_session=True,
            bufsize=0,
        )
        reader = _PCMReader(process.stdout, time.monotonic() + timeout)
        yield reader
        if reader.read(1):
            raise AttachmentError("validation_rejected", 422)
        code = process.wait(timeout=max(0.01, reader.deadline - time.monotonic()))
        if code != 0:
            raise AttachmentError(
                "validation_rejected" if code == 2 else "validation_unavailable",
                422 if code == 2 else 503,
            )
    except (OSError, subprocess.SubprocessError):
        raise AttachmentError("validation_unavailable", 503) from None
    finally:
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            if process.stdout is not None:  # Popen was given stdout=PIPE.
                process.stdout.close()
        os.close(source)


class _PCMReader:
    def __init__(self, pipe, deadline):
        self.pipe, self.deadline = pipe, deadline

    def read(self, size):
        import select

        if not 0 < size <= BUFFER_BYTES:
            raise AttachmentError("validation_rejected", 422)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or not select.select([self.pipe], [], [], remaining)[0]:
            raise AttachmentError("validation_unavailable", 503)
        return os.read(self.pipe.fileno(), size)


def write_wav_segment(pcm, output, plan, facts):
    channels, rate = facts["channels"], facts["sample_rate"]
    size = plan.frame_count * channels * 2
    if (
        size + 44 != plan.byte_length
        or plan.byte_length > MAX_OUTPUT_BYTES
        or plan.frame_count > 30 * rate
    ):
        raise AttachmentError("too_large", 413)
    output.write(
        struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            size + 36,
            b"WAVE",
            b"fmt ",
            16,
            1,
            channels,
            rate,
            rate * channels * 2,
            channels * 2,
            16,
            b"data",
            size,
        )
    )
    remaining = size
    while remaining:
        block = pcm.read(min(BUFFER_BYTES, remaining))
        if not block or len(block) > remaining:
            raise AttachmentError("validation_rejected", 422)
        output.write(block)
        remaining -= len(block)
    output.flush()
