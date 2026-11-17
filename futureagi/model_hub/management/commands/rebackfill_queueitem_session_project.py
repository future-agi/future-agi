"""Run migration 0140's session project repair again, without recording it.

0140 stamps the project of trace session queue items that have none, from
ClickHouse. Where its ClickHouse lookup failed while 0140 ran (for example,
ClickHouse was unreachable), those items were not stamped, yet the migration
was recorded, so ``migrate`` never runs it again. The
earlier note in 0117 that such rows are picked up by a later run no longer
holds once 0140 is recorded; this command is that run:

    manage.py rebackfill_queueitem_session_project

It runs 0140's own step on the migration's historical models. It selects
only trace session queue items whose project is empty, so running it again
changes nothing already stamped. It prints counts only, and exits with an
error when a ClickHouse lookup fails. Do not migrate model_hub back to 0139 to
run 0140 again.
"""

import contextlib
import importlib
import io

from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.loader import MigrationLoader

MIGRATION = "model_hub.migrations.0140_rebackfill_queueitem_project_upgraded"
STATE = ("model_hub", "0140_rebackfill_queueitem_project_upgraded")


def historical_apps():
    """The models as 0140 sees them when migrate runs it."""
    loader = MigrationLoader(connections[DEFAULT_DB_ALIAS], ignore_no_migrations=True)
    return loader.project_state(STATE).apps


@contextlib.contextmanager
def recorded_step(migration):
    """0140's counts, and the batches whose ClickHouse session lookup failed.

    0117's batch step keeps going when ClickHouse fails (its rows stay empty)
    and prints a line; the line is held back so the output is counts only.
    """
    from tracer.services.clickhouse.v2 import (
        trace_session_dict_reader as session_reader,
    )

    original = migration._original
    real_batch = original._process_batch
    real_resolve = session_reader.resolve_session_fields
    seen = {"stats": None, "failed_batches": 0}

    def process_batch(queue_item, project, batch, stats, alias):
        seen["stats"] = stats
        return real_batch(queue_item, project, batch, stats, alias)

    def resolve_session_fields(*args, **kwargs):
        try:
            return real_resolve(*args, **kwargs)
        except Exception:
            seen["failed_batches"] += 1
            raise

    original._process_batch = process_batch
    session_reader.resolve_session_fields = resolve_session_fields
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield seen
    finally:
        original._process_batch = real_batch
        session_reader.resolve_session_fields = real_resolve


class Command(BaseCommand):
    help = (
        "Stamp the project of trace session queue items that migration 0140 "
        "left empty because a ClickHouse lookup failed (for example, ClickHouse "
        "was unreachable). 0140 is recorded even then, so migrate does not run "
        "it again, and 0117's note that a later run picks such rows up is no "
        "longer true: this command is that run. Safe to run again; prints "
        "counts only; exits with an error when a ClickHouse lookup fails."
    )

    def handle(self, *args, **options):
        migration = importlib.import_module(MIGRATION)
        apps = historical_apps()
        with recorded_step(migration) as seen:
            migration.backfill_queueitem_project(apps, None)
        stats = seen["stats"] or {"stamped": 0, "unresolvable": 0, "org_mismatch": 0}
        self.stdout.write(
            f"stamped={stats['stamped']} unresolvable={stats['unresolvable']} "
            f"org_mismatch={stats['org_mismatch']} "
            f"failed_batches={seen['failed_batches']}"
        )
        failed = seen["failed_batches"]
        if failed:
            raise CommandError(
                f"The ClickHouse lookup failed for {failed} "
                f"{'batch' if failed == 1 else 'batches'} of trace sessions, so "
                "their queue items still have no project. Run this command "
                "again; it stamps only the items that still have none."
            )
