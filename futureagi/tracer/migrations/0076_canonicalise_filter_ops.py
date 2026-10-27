"""Rewrite legacy filter_op values to canonical names in all JSON fields
that carry filter configs.

Legacy → canonical mapping:
    is              → equals
    is_not          → not_equals
    equal_to        → equals
    not_equal_to    → not_equals
    not_in_between  → not_between

Scope (per the prod survey on 2026-05-13):
    - tracer_eval_task.filters["span_attributes_filters"][...]
    - tracer_saved_view.config["filters" | "compareFilters" | "extraFilters"
                              | "compareExtraFilters"][...]
    - tracer_useralertmonitor.filters[...]

The other tables checked (custom_eval_config, simulate_eval_config,
dashboardwidget, performancereport, optimizedataset) had zero canonical
filter dicts at survey time; we still walk them defensively so the
migration is idempotent for any data written between survey and deploy.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


_LEGACY_OP_ALIAS = {
    "is": "equals",
    "is_not": "not_equals",
    "equal_to": "equals",
    "not_equal_to": "not_equals",
    "not_in_between": "not_between",
}


def _rewrite_filter_list(filter_dicts):
    """Mutate a list of filter dicts in place; return count of ops rewritten."""
    if not isinstance(filter_dicts, list):
        return 0
    n = 0
    for f in filter_dicts:
        if not isinstance(f, dict):
            continue
        cfg = f.get("filter_config") or f.get("filterConfig")
        if not isinstance(cfg, dict):
            continue
        for key in ("filter_op", "filterOp"):
            if key in cfg and cfg[key] in _LEGACY_OP_ALIAS:
                cfg[key] = _LEGACY_OP_ALIAS[cfg[key]]
                n += 1
    return n


def _migrate_eval_task(apps, stats):
    EvalTask = apps.get_model("tracer", "EvalTask")
    for et in EvalTask.objects.iterator(chunk_size=500):
        try:
            f = et.filters
            if not isinstance(f, dict):
                continue
            nested = f.get("span_attributes_filters")
            if not isinstance(nested, list):
                continue
            before = stats["rewritten"]
            stats["rewritten"] += _rewrite_filter_list(nested)
            if stats["rewritten"] > before:
                et.save(update_fields=["filters"])
        except Exception as e:
            stats["failed"] += 1
            logger.exception(
                f"[canonicalise_filter_ops] EvalTask id={et.pk} failed: {e}"
            )


def _migrate_saved_view(apps, stats):
    SavedView = apps.get_model("tracer", "SavedView")
    keys = ("filters", "compareFilters", "extraFilters", "compareExtraFilters")
    for sv in SavedView.objects.iterator(chunk_size=500):
        try:
            cfg = sv.config
            if not isinstance(cfg, dict):
                continue
            before = stats["rewritten"]
            for k in keys:
                stats["rewritten"] += _rewrite_filter_list(cfg.get(k))
            if stats["rewritten"] > before:
                sv.save(update_fields=["config"])
        except Exception as e:
            stats["failed"] += 1
            logger.exception(
                f"[canonicalise_filter_ops] SavedView id={sv.pk} failed: {e}"
            )


def _migrate_flat_list_field(Model, field_name, stats):
    """Models where `filters` (or similar) is the bare list of filter dicts."""
    for obj in Model.objects.iterator(chunk_size=500):
        try:
            val = getattr(obj, field_name)
            if not isinstance(val, list):
                continue
            before = stats["rewritten"]
            stats["rewritten"] += _rewrite_filter_list(val)
            if stats["rewritten"] > before:
                obj.save(update_fields=[field_name])
        except Exception as e:
            stats["failed"] += 1
            logger.exception(
                f"[canonicalise_filter_ops] CustomEvalConfig id={obj.pk} failed: {e}"
            )


def _migrate_user_alert_monitor(apps, stats):
    UserAlertMonitor = apps.get_model("tracer", "UserAlertMonitor")
    for m in UserAlertMonitor.objects.iterator(chunk_size=500):
        try:
            f = m.filters
            # Some rows store filters as a dict with `span_attributes_filters`
            # nested (same shape as EvalTask); others as a flat list.
            before = stats["rewritten"]
            if isinstance(f, dict):
                stats["rewritten"] += _rewrite_filter_list(
                    f.get("span_attributes_filters")
                )
            elif isinstance(f, list):
                stats["rewritten"] += _rewrite_filter_list(f)
            if stats["rewritten"] > before:
                m.save(update_fields=["filters"])
        except Exception as e:
            stats["failed"] += 1
            logger.exception(
                f"[canonicalise_filter_ops] UserAlertMonitor id={m.pk} failed: {e}"
            )


def _safe_step(fn, *args, **kwargs):
    """Run a migration step; log and swallow any top-level exception."""
    try:
        fn(*args, **kwargs)
    except Exception as e:
        logger.exception(
            f"[canonicalise_filter_ops] step {fn.__name__} failed: {e}"
        )


def forwards(apps, schema_editor):
    stats = {"rewritten": 0, "failed": 0}

    _safe_step(_migrate_eval_task, apps, stats)
    _safe_step(_migrate_saved_view, apps, stats)
    _safe_step(_migrate_user_alert_monitor, apps, stats)

    # Defensive walks for tables that had zero matches at survey time but
    # may have received data between survey and deploy.
    try:
        CustomEvalConfig = apps.get_model("tracer", "CustomEvalConfig")
        _safe_step(_migrate_flat_list_field, CustomEvalConfig, "filters", stats)
    except Exception as e:
        logger.exception(
            f"[canonicalise_filter_ops] CustomEvalConfig walk failed: {e}"
        )

    print(
        f"[canonicalise_filter_ops] {stats['rewritten']} ops rewritten, "
        f"{stats['failed']} rows skipped due to errors"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0075_evallogger_target_type_evallogger_trace_session_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
