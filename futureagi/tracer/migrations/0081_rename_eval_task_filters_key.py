"""Rename ``tracer_eval_task.filters['span_attributes_filters']`` → ``filters``.

The inner key historically held the per-item filter list (one entry per filter
chip). It was named ``span_attributes_filters`` back when only SPAN_ATTRIBUTE
chips were stored there. The list now carries items of every col_type
(SPAN_ATTRIBUTE, SYSTEM_METRIC, EVAL_METRIC, ANNOTATION, has_eval, ...) and
the eval-task dispatcher (``tracer/utils/eval_tasks.py::parsing_evaltask_filters``)
now mirrors the per-col_type dispatch pattern used by ``list_spans_observe``
(``tracer/views/observation_span.py:1755-1826``). The new canonical key is just
``filters`` — aligned with the rest of the API surface.

The BE dispatcher reads both keys during a transition window
(``parsing_evaltask_filters`` accepts ``filters`` or ``span_attributes_filters``
with a one-line ``logger.info`` on the legacy path), so this migration is
cleanup, not a hard cut. After this runs every row uses the canonical key
and the legacy-key branch goes quiet.

Scope: ``tracer_eval_task`` only. ``tracer_useralertmonitor`` and
``tracer_savedview`` carry the same shape but are intentionally out of scope
for this PR — they get the same treatment in a follow-up.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def _rename_in_eval_task_filters(apps, stats):
    EvalTask = apps.get_model("tracer", "EvalTask")
    for et in EvalTask.objects.iterator(chunk_size=500):
        try:
            f = et.filters
            if not isinstance(f, dict):
                continue
            if "span_attributes_filters" not in f:
                continue
            # Don't clobber a pre-existing canonical key — if both exist
            # (shouldn't happen in practice) keep ``filters`` as truth and
            # drop the legacy key.
            if "filters" not in f:
                f["filters"] = f.pop("span_attributes_filters")
            else:
                f.pop("span_attributes_filters")
            et.filters = f
            et.save(update_fields=["filters"])
            stats["renamed"] += 1
        except Exception as e:
            stats["failed"] += 1
            logger.exception(
                f"[rename_eval_task_filters_key] EvalTask id={et.pk} failed: {e}"
            )


def _restore_in_eval_task_filters(apps, stats):
    """Reverse: rename ``filters`` back to ``span_attributes_filters``."""
    EvalTask = apps.get_model("tracer", "EvalTask")
    for et in EvalTask.objects.iterator(chunk_size=500):
        try:
            f = et.filters
            if not isinstance(f, dict):
                continue
            if "filters" not in f or "span_attributes_filters" in f:
                continue
            f["span_attributes_filters"] = f.pop("filters")
            et.filters = f
            et.save(update_fields=["filters"])
            stats["renamed"] += 1
        except Exception as e:
            stats["failed"] += 1
            logger.exception(
                f"[rename_eval_task_filters_key] reverse EvalTask id={et.pk} "
                f"failed: {e}"
            )


def forwards(apps, schema_editor):
    stats = {"renamed": 0, "failed": 0}
    _rename_in_eval_task_filters(apps, stats)
    print(
        f"[rename_eval_task_filters_key] {stats['renamed']} eval tasks "
        f"renamed, {stats['failed']} rows skipped due to errors"
    )


def backwards(apps, schema_editor):
    stats = {"renamed": 0, "failed": 0}
    _restore_in_eval_task_filters(apps, stats)
    print(
        f"[rename_eval_task_filters_key] reverse: {stats['renamed']} "
        f"eval tasks restored, {stats['failed']} rows skipped"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0080_alter_tracescanconfig_sampling_rate"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
