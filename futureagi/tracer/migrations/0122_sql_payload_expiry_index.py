"""Index only retained payloads; repair legacy zero-byte accounting in batches."""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.expressions import RawSQL


def repair_payload_bytes(apps, schema_editor):
    executions = apps.get_model("tracer", "SQLExecution").objects.using(
        schema_editor.connection.alias
    )
    last_id = None
    while True:
        candidates = executions.filter(output_bytes=0).exclude(result={})
        if last_id is not None:
            candidates = candidates.filter(id__gt=last_id)
        ids = list(candidates.order_by("id").values_list("id", flat=True)[:1000])
        if not ids:
            return
        # Actual stored JSON byte count, not a guessed historical measurement.
        executions.filter(id__in=ids, output_bytes=0).exclude(result={}).update(
            output_bytes=RawSQL("octet_length(result::text)", ())
        )
        last_id = ids[-1]


UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:tracer_sqlexecution"],
    "mitigation": (
        "Non-atomic migration: two partial indexes built CONCURRENTLY, then "
        "RunPython repairs only legacy rows with output_bytes=0 and a non-empty "
        "result, in id-ordered batches of 1000 with an id cursor, setting "
        "output_bytes to octet_length(result::text). Touches only "
        "tracer_sqlexecution (new in this release, so small on upgrade); "
        "idempotent and safe to rerun; reverse is a no-op."
    ),
    "reviewer": "nik13",
    "review": "https://github.com/future-agi/future-agi/pull/3577#discussion_r4537374137",
    "large_volume_rehearsal": True,
}


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("tracer", "0121_merge_sql_execution_and_alert_high_water")]
    operations = [
        AddIndexConcurrently(
            model_name="sqlexecution",
            index=models.Index(
                fields=["deadline"],
                name="sql_active_deadline_idx",
                condition=models.Q(state__in=("queued", "running"))
                | models.Q(state="cancelled", finished_at__isnull=True),
            ),
        ),
        AddIndexConcurrently(
            model_name="sqlexecution",
            index=models.Index(
                fields=["expires_at"],
                name="sql_payload_expiry_idx",
                condition=models.Q(
                    output_bytes__gt=0, state__in=("succeeded", "failed", "cancelled")
                ),
            ),
        ),
        migrations.RunPython(repair_payload_bytes, migrations.RunPython.noop),
    ]
