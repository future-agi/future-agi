"""Normalize ``tracer_eval_task.filters`` items to the canonical snake_case
contract.

Legacy frontends persisted attribute filter items in camelCase
(``columnId`` / ``filterConfig.filterOp`` …), and API writers could still store
either casing or the legacy ``span_attributes_filters`` key. Current readers
are canonical-first: ``formatTaskFilters`` on the FE renders snake_case only,
and the reconciler's row resolver
(``tracer/selectors/eval_tasks/row_resolver.py``) reads only the ``filters``
key — so legacy rows display blank filters and, for legacy-key rows, run
unfiltered.

Rewrites each stored filter item to snake_case keys (``column_id`` /
``filter_config`` / ``filter_type`` / ``filter_op`` / ``filter_value`` /
``col_type``) and folds ``span_attributes_filters`` items into ``filters``.
Values are never touched, unknown keys are converted by the same camel→snake
rule, and re-running is a no-op. Rows are written with ``QuerySet.update`` so
``updated_at`` is preserved. Forward-only: a casing normalization has no
meaningful reverse.
"""

import logging
import re

from django.db import migrations

logger = logging.getLogger(__name__)

_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")


def _snake(key):
    return _CAMEL_RE.sub(r"\1_\2", key).lower()


def _normalize_dict(value):
    """Snake-case every key of ``value`` (one level), returning (dict, changed).

    When both casings of a key are present, the snake_case entry wins and the
    camelCase duplicate is dropped.
    """
    changed = False
    out = {}
    for key, val in value.items():
        new_key = _snake(key)
        if new_key != key:
            changed = True
            if new_key in value:
                continue
        out[new_key] = val
    return out, changed


def _normalize_item(item):
    if not isinstance(item, dict):
        return item, False
    out, changed = _normalize_dict(item)
    config = out.get("filter_config")
    if isinstance(config, dict):
        new_config, config_changed = _normalize_dict(config)
        if config_changed:
            out["filter_config"] = new_config
            changed = True
    return (out if changed else item), changed


def normalize_task_filters(filters):
    """Return (normalized_filters, changed) for one task's ``filters`` dict."""
    items = filters.get("filters")
    legacy = filters.get("span_attributes_filters")
    if not isinstance(items, list):
        items = []
    changed = False
    if isinstance(legacy, list) and legacy:
        items = items + legacy
        changed = True
    normalized = []
    for item in items:
        new_item, item_changed = _normalize_item(item)
        changed = changed or item_changed
        normalized.append(new_item)
    if not changed:
        return filters, False
    out = {k: v for k, v in filters.items() if k != "span_attributes_filters"}
    out["filters"] = normalized
    return out, True


def forwards(apps, schema_editor):
    EvalTask = apps.get_model("tracer", "EvalTask")
    stats = {"scanned": 0, "updated": 0, "failed": 0}
    # _base_manager: plain manager in both the historical and live registries —
    # covers soft-deleted rows and skips workspace-context filtering.
    for task in EvalTask._base_manager.exclude(filters__isnull=True).iterator(
        chunk_size=500
    ):
        stats["scanned"] += 1
        try:
            if not isinstance(task.filters, dict):
                continue
            new_filters, changed = normalize_task_filters(task.filters)
            if not changed:
                continue
            EvalTask._base_manager.filter(pk=task.pk).update(filters=new_filters)
            stats["updated"] += 1
        except Exception as e:
            stats["failed"] += 1
            logger.exception(
                f"[normalize_eval_task_filter_casing] EvalTask id={task.pk} "
                f"failed: {e}"
            )
    print(
        f"[normalize_eval_task_filter_casing] scanned {stats['scanned']}, "
        f"updated {stats['updated']}, {stats['failed']} rows skipped due to errors"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0093_register_eval_task_search_attributes"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
