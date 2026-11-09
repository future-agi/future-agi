"""Repair session project stamps on installations that already applied 0117.

The released 0117 could discard successful CH session resolutions when an
unrelated PostgreSQL UI-overlay read failed. Editing 0117 fixed fresh installs,
but databases which already applied it still need this new migration.

Only NULL-project trace_session rows with a session id need this repair; trace
and observation_span rows are deliberately left untouched. Reuse 0117's corrected
batch processor for lean session resolution (include_overlay=False), the
same-org guard, and alias-bound writes without changing its module globals.
"""

import logging
from importlib import import_module

from django.db import DEFAULT_DB_ALIAS, migrations

from tfc.migration_database import executor_alias_scope

logger = logging.getLogger(__name__)
_original = import_module("model_hub.migrations.0117_backfill_queueitem_project")


def backfill_queueitem_project(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        alias = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        QueueItem = apps.get_model("model_hub", "QueueItem")  # noqa: N806
        Project = apps.get_model("tracer", "Project")  # noqa: N806

        base_qs = QueueItem.objects.using(alias).filter(
            project__isnull=True,
            source_type="trace_session",
            trace_session_id__isnull=False,
        ).only(
            "id",
            "source_type",
            "organization",
            "project",
            "trace_session",
        )
        stats = {"stamped": 0, "unresolvable": 0, "org_mismatch": 0}

        # Step past unresolved batches too, without holding an open write cursor.
        last_id = None
        while True:
            chunk_qs = base_qs.order_by("id")
            if last_id is not None:
                chunk_qs = chunk_qs.filter(id__gt=last_id)
            batch = list(chunk_qs[:_original._BATCH])
            if not batch:
                break
            last_id = batch[-1].id
            _original._process_batch(QueueItem, Project, batch, stats, alias)

        if any(stats.values()):
            logger.info(
                "queueitem_session_project_rebackfill done: stamped=%s unresolvable=%s "
                "org_mismatch=%s",
                stats["stamped"],
                stats["unresolvable"],
                stats["org_mismatch"],
            )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("model_hub", "0139_merge_dsx3f_c7_and_pgrid"),
    ]

    operations = [
        migrations.RunPython(
            backfill_queueitem_project,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
