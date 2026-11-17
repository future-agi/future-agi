import re

from django.db import migrations

ATTR_RE = re.compile(r"Required attribute '([^']+)'")
LEGACY_PREFIX = "Error during evaluation: Required attribute "

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:fi_cdc_outbox",
        "postgres:tracer_eval_logger",
    ],
    "mitigation": (
        "Recodes tracer_eval_logger rows that recorded a missing mapped attribute as "
        "an error (error = true and error_message starting with 'Error during "
        "evaluation: Required attribute') to the skip shape: skipped_reason "
        "'missing_required_attribute: <attribute>', error = false, error_message and "
        "output_str cleared. Loads every matching row into memory in one query (no "
        "paging) and writes them with bulk_update in batches of 500. The migration is "
        "atomic: every updated row stays locked until the step commits, and a failed "
        "run rolls back as a whole. Row scale: one row per eval result that recorded "
        "this error, soft-deleted rows included; tracer_eval_logger is a registered "
        "high-volume table. On a Standalone install in the default outbox CDC mode, "
        "the statement-level capture triggers on tracer_eval_logger also insert one "
        "fi_cdc_outbox row per updated row in the same transaction; fi_cdc_outbox is "
        "a registered high-volume table. The step itself runs no statement on "
        "fi_cdc_outbox and sends nothing to ClickHouse. Second run: a recoded row has "
        "error = false and no message, so it is not selected again; a re-run would "
        "still recode rows written since with the same legacy error. No schema "
        "change; the reverse is a no-op."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def backfill(apps, schema_editor):
    """Re-categorise rows that recorded a missing-attribute skip as an error.

    These rows were written by the eval pipeline when a mapped span attribute
    was absent (the eval never ran). They are recoded to the skip shape so
    existing dashboards stop showing them as failures without re-running evals.
    """
    EvalLogger = apps.get_model("tracer", "EvalLogger")
    rows = list(
        EvalLogger.objects.using(schema_editor.connection.alias).filter(
            error=True, error_message__startswith=LEGACY_PREFIX
        )
    )
    for row in rows:
        match = ATTR_RE.search(row.error_message or "")
        attribute = match.group(1) if match else "unknown"
        row.skipped_reason = f"missing_required_attribute: {attribute}"
        row.error = False
        row.error_message = None
        row.output_str = None
    if rows:
        EvalLogger.objects.using(schema_editor.connection.alias).bulk_update(
            rows,
            ["skipped_reason", "error", "error_message", "output_str"],
            batch_size=500,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0078_evallogger_skipped_reason"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
