"""Add denormalised project FK to EvalLogger + index + backfill.

Eliminates the CH-side INNER JOIN-to-spans previously required to scope
eval queries by project. Once this migration runs and the CDC stream
replicates the column to ClickHouse, ``ClickHouseFilterBuilder._build_has_
eval_condition`` can switch to ``WHERE project_id = %(project_id)s``
directly on ``tracer_eval_logger`` (TODO comment in filters.py).
"""

from django.db import migrations, models


def backfill_eval_logger_project(apps, schema_editor):
    """Populate EvalLogger.project_id from the related Trace.

    Runs in chunks to avoid long table-locks on a hot table. Idempotent —
    re-running only updates rows still NULL.
    """
    EvalLogger = apps.get_model("tracer", "EvalLogger")
    Trace = apps.get_model("tracer", "Trace")
    from django.db.models import OuterRef, Subquery

    BATCH = 10_000
    while True:
        ids = list(
            EvalLogger.objects.filter(project_id__isnull=True)
            .values_list("id", flat=True)[:BATCH]
        )
        if not ids:
            break
        EvalLogger.objects.filter(id__in=ids).update(
            project_id=Subquery(
                Trace.objects.filter(id=OuterRef("trace_id")).values("project_id")[:1]
            )
        )


def noop_reverse(apps, schema_editor):
    """Reverse is a no-op — dropping the column happens via the schema reversal."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0073_merge_20260428_1653"),
    ]

    operations = [
        migrations.AddField(
            model_name="evallogger",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="eval_logs",
                to="tracer.project",
            ),
        ),
        migrations.AddIndex(
            model_name="evallogger",
            index=models.Index(
                fields=["project", "created_at"],
                name="tracer_eval_project_created_idx",
            ),
        ),
        migrations.RunPython(
            backfill_eval_logger_project,
            reverse_code=noop_reverse,
        ),
    ]
