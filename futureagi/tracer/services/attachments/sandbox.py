"""Run media_worker.py confined by seccomp-bpf; never a permissive fallback.

Replaces the earlier bubblewrap runner, which fails closed on GCP VMs and in
default Docker/Kubernetes containers (no unprivileged network namespace:
RTM_NEWADDR EPERM). Seccomp filters need no namespace or capability and are
allowed under Docker's and Kubernetes' RuntimeDefault profiles. SANDBOX.md
documents the boundary and how readiness proves it is active.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from tracer.services.attachments.contract import AttachmentError

WORKER = Path(__file__).with_name("media_worker.py")
TIMEOUT_SECONDS = 60
STDOUT_LIMIT = 64 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024 + 44


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
        source = os.open(input_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        out_fd = output.fileno() if output is not None else -1
        command = [
            _interpreter(),
            "-I",
            "-S",
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
                pass_fds=tuple(fd for fd in (source, out_fd) if fd >= 0),
                close_fds=True,
                cwd="/",
                start_new_session=True,
                timeout=TIMEOUT_SECONDS,
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
