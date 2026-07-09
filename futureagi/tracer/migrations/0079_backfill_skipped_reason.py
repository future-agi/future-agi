import re

from django.db import migrations

ATTR_RE = re.compile(r"Required attribute '([^']+)'")
LEGACY_PREFIX = "Error during evaluation: Required attribute "


def backfill(apps, schema_editor):
    """Re-categorise rows that recorded a missing-attribute skip as an error.

    These rows were written by the eval pipeline when a mapped span attribute
    was absent (the eval never ran). They are recoded to the skip shape so
    existing dashboards stop showing them as failures without re-running evals.
    """
    EvalLogger = apps.get_model("tracer", "EvalLogger")
    rows = list(
        EvalLogger.objects.filter(
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
        EvalLogger.objects.bulk_update(
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
