"""Operator wall-clock budget and value-free migration progress.

Schema defaults to 900 seconds: the measured empty install took 204.56 seconds,
so the old 180-second process wrapper was too short. Override with
``hosted_gateway_migrate schema --schema-deadline-seconds SECONDS`` (1..86400).
This dedicated Unix/main-thread operator uses a wall-clock alarm, not PostgreSQL
settings. Lock/statement timeouts remain 2/30 seconds. An interrupted phase can
have committed earlier migrations: preserve history, stay quiesced and rerun the
same forward command after investigating; never fake/reverse to recover.
"""

import argparse
import signal
import threading
import time
from contextlib import contextmanager

from django.core.management.base import CommandError

DEFAULT_SCHEMA_DEADLINE_SECONDS = 900


def schema_deadline_seconds(value):
    if len(str(value)) > 5 or not str(value).isascii() or not str(value).isdigit():
        raise argparse.ArgumentTypeError(
            "schema deadline must be an integer in 1..86400 seconds"
        )
    seconds = int(value)
    if not 1 <= seconds <= 86400:
        raise argparse.ArgumentTypeError(
            "schema deadline must be an integer in 1..86400 seconds"
        )
    return seconds


def add_schema_deadline_argument(parser):
    parser.add_argument(
        "--schema-deadline-seconds",
        type=schema_deadline_seconds,
        default=DEFAULT_SCHEMA_DEADLINE_SECONDS,
        help="Schema wall-clock deadline, 1..86400 seconds (default: 900; measured empty install: 204.56s). SQL lock/statement limits stay 2/30s.",
    )


class _SchemaPhaseExpired(BaseException):
    """Cancellation must unwind even historical helpers that catch Exception."""


@contextmanager
def schema_phase_deadline(seconds):
    if seconds is None:
        yield
        return
    # The dedicated operator process owns this alarm. Never replace a caller's
    # timer or silently lose enforcement in a non-main thread/non-Unix process.
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "setitimer")
        or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)
    ):
        raise CommandError(
            "Schema deadline requires a dedicated Unix main-thread operator without an existing alarm"
        )

    def expired(signum, frame):
        raise _SchemaPhaseExpired()

    prior = signal.signal(signal.SIGALRM, expired)
    started = time.monotonic()
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
        if time.monotonic() - started >= seconds:
            expired(None, None)
    except _SchemaPhaseExpired:
        # Convert only after transaction/schema-editor contexts have unwound;
        # an ordinary Exception inside RunPython can be swallowed and recorded.
        raise CommandError(
            f"Schema phase exceeded {seconds}s deadline; preserve applied-state metadata, remain quiesced and rehearse recovery"
        ) from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prior)


class MigrationProgress:
    def __init__(self, stdout):
        self.stdout = stdout
        self.started = time.monotonic()
        self.migrations = {}

    def __call__(self, action, migration=None, fake=False):
        if action not in ("apply_start", "apply_success"):
            return
        name = f"{migration.app_label}.{migration.name}"
        now = time.monotonic()
        if action == "apply_start":
            self.migrations[name] = now
        elapsed = now - self.migrations.get(name, now)
        status = "started" if action == "apply_start" else "applied"
        self.stdout.write(
            f"migration={name} status={status} elapsed_seconds={elapsed:.3f} phase_elapsed_seconds={now - self.started:.3f}"
        )
        self.stdout.flush()
