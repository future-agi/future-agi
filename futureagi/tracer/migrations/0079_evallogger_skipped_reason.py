"""TH-4910: add ``skipped_reason`` column to ``tracer_eval_logger``.

Set when ``_process_mapping`` raises a missing-attribute error so the
row is rendered as "Skipped" (not "Fail") and excluded from
failure-rate metrics. See ``tracer.utils.eval.EvalSkippedMissingAttribute``.

Nullable + non-indexed: read paths use ``skipped_reason IS NOT NULL``
as a coarse filter that piggy-backs on existing per-task / per-span
indexes upstream, so a dedicated index is unnecessary.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0078_backfill_skipped_eval_loggers"),
    ]

    operations = [
        migrations.AddField(
            model_name="evallogger",
            name="skipped_reason",
            field=models.TextField(blank=True, null=True),
        ),
    ]
