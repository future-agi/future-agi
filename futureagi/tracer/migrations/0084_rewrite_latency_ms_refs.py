"""Rewrite the legacy `latency_ms` ("Duration") reference to `latency` in
persisted SavedView / EvalTask / DashboardWidget configs.

`latency_ms` ("Duration") was removed from the metrics/filter catalog; `latency`
is the surviving equivalent (same underlying spans `latency_ms` column). Only the
known shapes are touched (NOT a blind recursive walk — a span view's `columns`
legitimately keeps `latency_ms`, so we must not rewrite it):

  * SavedView.config        — filter lists under filters / compare_filters /
                              extra_filters / compare_extra_filters.
  * EvalTask.filters        — a filter list, or a wrapper dict whose filter items
                              live under `span_attributes_filters`.
  * DashboardWidget.query_config — top-level `filters`, each metric's `filters`,
                              and metric refs (`metrics[].id|name`).

A filter ref = a filter item whose `column_id` (legacy `columnId`) == latency_ms.
A metric ref = a `metrics[]` entry whose `id`/`name` == latency_ms.

Defensive: every model load, query, and per-row save is wrapped — failures are
logged and skipped. This migration must never raise.
"""
import logging

from django.db import migrations

logger = logging.getLogger(__name__)

TARGET = "latency_ms"
REPLACEMENT = "latency"
COL_KEYS = ("column_id", "columnId")
SAVED_VIEW_FILTER_KEYS = (
    "filters", "compare_filters", "extra_filters", "compare_extra_filters",
)


def _swap_filter_list(items):
    """Rewrite filter items in a list whose column_id == latency_ms. Returns changed."""
    if not isinstance(items, list):
        return False
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        for ck in COL_KEYS:
            if item.get(ck) == TARGET:
                item[ck] = REPLACEMENT
                if item.get("display_name") == "Duration":
                    item["display_name"] = "Latency"
                changed = True
    return changed


def _saved_view_config(cfg):
    if not isinstance(cfg, dict):
        return False
    changed = False
    for key in SAVED_VIEW_FILTER_KEYS:
        if _swap_filter_list(cfg.get(key)):
            changed = True
    return changed


def _eval_task_filters(filters):
    # Either a bare filter list, or a wrapper dict whose item list lives under
    # the canonical `filters` key (post-0081) or the legacy
    # `span_attributes_filters` key (un-migrated rows). Swap both.
    if isinstance(filters, list):
        return _swap_filter_list(filters)
    if isinstance(filters, dict):
        changed = _swap_filter_list(filters.get("filters"))
        if _swap_filter_list(filters.get("span_attributes_filters")):
            changed = True
        return changed
    return False


def _widget_query_config(qc):
    if not isinstance(qc, dict):
        return False
    changed = _swap_filter_list(qc.get("filters"))
    metrics = qc.get("metrics")
    if isinstance(metrics, list):
        for m in metrics:
            if not isinstance(m, dict):
                continue
            for k in ("id", "name"):
                if m.get(k) == TARGET:
                    m[k] = REPLACEMENT
                    if m.get("display_name") == "Duration":
                        m["display_name"] = "Latency"
                    changed = True
            if _swap_filter_list(m.get("filters")):
                changed = True
    return changed


def _migrate_model(apps, model_name, field, transform, stats):
    try:
        Model = apps.get_model("tracer", model_name)
    except Exception as exc:  # model missing in historical state — skip
        logger.warning("[rewrite_latency_ms] load %s failed: %s", model_name, exc)
        return
    try:
        rows = Model.objects.all().iterator(chunk_size=500)
    except Exception as exc:  # querying failed — skip the whole model
        logger.warning("[rewrite_latency_ms] query %s failed: %s", model_name, exc)
        return
    while True:
        try:
            obj = next(rows)
        except StopIteration:
            break
        except Exception as exc:  # fetch error mid-iteration — stop, don't fail
            logger.warning("[rewrite_latency_ms] iterate %s failed: %s", model_name, exc)
            break
        try:
            blob = getattr(obj, field, None)
            if not blob:
                continue
            if transform(blob):
                setattr(obj, field, blob)
                obj.save(update_fields=[field])
                stats["updated"] += 1
        except Exception as exc:  # per-row failure — log + skip, never abort
            stats["failed"] += 1
            logger.warning(
                "[rewrite_latency_ms] %s id=%s skipped: %s",
                model_name, getattr(obj, "pk", "?"), exc,
            )


def forwards(apps, schema_editor):
    stats = {"updated": 0, "failed": 0}
    try:
        _migrate_model(apps, "SavedView", "config", _saved_view_config, stats)
        _migrate_model(apps, "EvalTask", "filters", _eval_task_filters, stats)
        _migrate_model(apps, "DashboardWidget", "query_config", _widget_query_config, stats)
    except Exception as exc: 
        logger.warning("[rewrite_latency_ms] aborted early (ignored): %s", exc)
    print(
        f"[rewrite_latency_ms] {stats['updated']} rows updated, "
        f"{stats['failed']} rows skipped"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0083_merge_20260610_1220"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
