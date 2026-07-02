"""
Dataset Creation Tasks

Tasks for creating datasets from observation spans.
"""

import json
import uuid

import structlog
from django.db import close_old_connections, transaction

from tfc.temporal import temporal_activity

logger = structlog.get_logger(__name__)

CHUNK_SIZE = 500  # Process 500 spans at a time
CH_EXPORT_READ_BATCH_SIZE = 25
CH_CHILD_TREE_TRACE_BATCH_SIZE = 5

# Fields to include when serializing a child span
_CHILD_SPAN_FIELDS = [
    "id",
    "name",
    "observation_type",
    "operation_name",
    "status",
    "status_message",
    "model",
    "provider",
    "input",
    "output",
    "metadata",
    "span_attributes",
    "model_parameters",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "latency_ms",
    "cost",
    "tags",
    "span_events",
]


def _chspan_field_value(span, field_name):
    """Look up a span field by name, returning the same value as
    ``getattr(observation_span, field_name)`` would on the Django model
    where possible. Handles the columns that don't live as flat CH fields:

      • ``model_parameters`` / ``input_images`` / ``eval_input`` /
        ``eval_attributes`` round-trip into ``attributes_extra`` (per
        ``tracer/services/clickhouse/v2/adapter.py:330``) and are pulled
        back out here.
      • ``span_attributes`` is the merged typed-maps + non-roundtripped
        overflow dict — same shape ``ObservationSpanSerializer`` emits.
      • ``metadata`` / ``tags`` / ``span_events`` come back as JSON
        strings on CHSpan; decode them so callers see Python dicts/lists
        (matching the prior Django JSONField behavior).
    """
    if field_name in ("model_parameters", "input_images", "eval_input", "eval_attributes"):
        from tracer.services.clickhouse.v2.span_reader import CHSpanReader

        extra = CHSpanReader.attributes_extra_as_dict(span)
        if not isinstance(extra, dict):
            return None
        return extra.get(field_name)
    if field_name == "span_attributes":
        from tracer.services.clickhouse.v2.span_reader import CHSpanReader

        extra = CHSpanReader.attributes_extra_as_dict(span)
        if not isinstance(extra, dict):
            extra = {}
        merged: dict = {}
        merged.update(span.attrs_string or {})
        merged.update(span.attrs_number or {})
        merged.update(span.attrs_bool or {})
        for k, v in extra.items():
            if k not in ("model_parameters", "input_images", "eval_input", "eval_attributes"):
                merged[k] = v
        return merged
    if field_name == "metadata":
        try:
            return json.loads(span.metadata) if span.metadata else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    if field_name == "tags":
        try:
            return json.loads(span.tags) if span.tags else []
        except (json.JSONDecodeError, TypeError):
            return []
    if field_name == "span_events":
        try:
            return json.loads(span.span_events) if span.span_events else []
        except (json.JSONDecodeError, TypeError):
            return []
    if field_name == "input":
        try:
            return json.loads(span.input) if span.input else None
        except (json.JSONDecodeError, TypeError):
            return span.input
    if field_name == "output":
        try:
            return json.loads(span.output) if span.output else None
        except (json.JSONDecodeError, TypeError):
            return span.output
    return getattr(span, field_name, None)


def _json_value(raw, default=None):
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _export_span_field_value(span, field_name):
    if not isinstance(span, dict):
        return _chspan_field_value(span, field_name)

    if field_name == "id":
        return span.get("id")

    if field_name in (
        "model_parameters",
        "input_images",
        "eval_input",
        "eval_attributes",
    ):
        extra = _json_value(span.get("attributes_extra"), default={})
        if not isinstance(extra, dict):
            return None
        return extra.get(field_name)

    if field_name == "span_attributes":
        extra = _json_value(span.get("attributes_extra"), default={})
        if not isinstance(extra, dict):
            extra = {}
        merged = {}
        merged.update(span.get("attrs_string") or {})
        merged.update(span.get("attrs_number") or {})
        merged.update(span.get("attrs_bool") or {})
        for key, value in extra.items():
            if key not in (
                "model_parameters",
                "input_images",
                "eval_input",
                "eval_attributes",
            ):
                merged[key] = value
        return merged

    if field_name in ("metadata", "resource_attributes"):
        source_field = (
            "resource_attrs" if field_name == "resource_attributes" else field_name
        )
        return _json_value(span.get(source_field), default={})

    if field_name in ("tags", "span_events"):
        return _json_value(span.get(field_name), default=[])

    if field_name in ("input", "output"):
        parsed = _json_value(span.get(field_name), default=None)
        return span.get(field_name) if parsed is None else parsed

    if field_name == "project":
        return span.get("project_id")

    if field_name == "trace":
        return span.get("trace_id")

    return span.get(field_name)


def _mapped_span_fields(column_span_mapping_data):
    virtual_fields = {"child_spans", "eval_metrics", "annotation_metrics"}
    fields = set()
    for mapping in column_span_mapping_data:
        field_name = mapping.get("span_field") or mapping.get("column_name")
        if field_name and field_name not in virtual_fields:
            fields.add(field_name)
    return fields


def _export_span_rows(reader, span_ids, mapped_span_fields, project_id=None, org_id=None):
    if not span_ids:
        return {}

    try:
        return reader.export_fields_by_ids(
            [str(s) for s in span_ids],
            mapped_span_fields,
            project_id=str(project_id) if project_id else None,
            org_id=str(org_id) if org_id else None,
        )
    except Exception:
        if len(span_ids) == 1:
            raise
        midpoint = len(span_ids) // 2
        left = _export_span_rows(
            reader,
            span_ids[:midpoint],
            mapped_span_fields,
            project_id=project_id,
            org_id=org_id,
        )
        right = _export_span_rows(
            reader,
            span_ids[midpoint:],
            mapped_span_fields,
            project_id=project_id,
            org_id=org_id,
        )
        left.update(right)
        return left


def _child_tree_spans_by_trace_ids(reader, trace_ids, project_id=None):
    if not trace_ids:
        return {}

    try:
        return reader.child_tree_spans_by_trace_ids(
            [str(trace_id) for trace_id in trace_ids],
            project_id=str(project_id) if project_id else None,
        )
    except Exception:
        if len(trace_ids) == 1:
            raise
        midpoint = len(trace_ids) // 2
        left = _child_tree_spans_by_trace_ids(
            reader,
            trace_ids[:midpoint],
            project_id=project_id,
        )
        right = _child_tree_spans_by_trace_ids(
            reader,
            trace_ids[midpoint:],
            project_id=project_id,
        )
        left.update(right)
        return left


def _serialize_span_trees_batch(span_ids, project_id=None) -> dict[str, list]:
    """Batch-serialize child trees for multiple span IDs in minimal CH queries.

    Replaces per-span _serialize_span_tree which did N × (reader.get + list_by_trace)
    with FINAL — causing OOM on large traces. This version:
      1. Resolves trace_ids for all span_ids in one lightweight query
      2. Deduplicates traces (multiple span_ids may share a trace)
      3. Fetches all spans for those traces in one argMax query (no FINAL)

    Returns {span_id: [child_dicts]} matching the old function's output shape.
    """
    from tracer.services.clickhouse.v2 import get_reader

    if not span_ids:
        return {}

    str_ids = [str(s) for s in span_ids]

    with get_reader() as reader:
        trace_map = reader.trace_ids_for_span_ids(
            str_ids, project_id=str(project_id) if project_id else None
        )
        unique_trace_ids = list(dict.fromkeys(trace_map.values()))

        all_trace_spans = {}
        for start in range(0, len(unique_trace_ids), CH_CHILD_TREE_TRACE_BATCH_SIZE):
            trace_chunk = unique_trace_ids[
                start : start + CH_CHILD_TREE_TRACE_BATCH_SIZE
            ]
            all_trace_spans.update(
                _child_tree_spans_by_trace_ids(
                    reader,
                    trace_chunk,
                    project_id=project_id,
                )
            )

    # Build children_map once per trace (not per span_id)
    trace_children_maps: dict[str, dict] = {}
    for trace_id, trace_spans in all_trace_spans.items():
        children_map: dict = {}
        for span in trace_spans:
            pid = span.get("parent_span_id") or None
            children_map.setdefault(pid, []).append(span)
        trace_children_maps[trace_id] = children_map

    result = {}
    for span_id in span_ids:
        str_span_id = str(span_id)
        trace_id = trace_map.get(str_span_id)
        if not trace_id:
            result[str_span_id] = []
            continue

        children_map = trace_children_maps.get(trace_id, {})
        result[str_span_id] = _build_subtree_from_dicts(children_map, str_span_id)

    return result


def _build_subtree_from_dicts(children_map, parent_id, depth=0) -> list[dict]:
    if depth > 20:
        return []
    children = children_map.get(parent_id, [])
    result = []
    for child in children:
        child_data = {}
        for field_name in _CHILD_SPAN_FIELDS:
            value = _export_span_field_value(child, field_name)
            if value is not None:
                if hasattr(value, "isoformat"):
                    value = value.isoformat()
                child_data[field_name] = value
        grandchildren = _build_subtree_from_dicts(
            children_map, str(child.get("id", "")), depth + 1
        )
        if grandchildren:
            child_data["children"] = grandchildren
        result.append(child_data)
    return result


def _serialize_cell_value(value):
    """Serialize a value for storage in a Cell's TextField."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


@temporal_activity(
    max_retries=3,
    time_limit=1800,
    queue="tasks_l",
)
def process_spans_chunk_task(
    span_ids,
    dataset_id,
    column_span_mapping_data,
    project_id=None,
    org_id=None,
):
    """
    Process a chunk of observation spans and create rows + cells.

    This task processes spans in bulk to optimize database operations.
    Each task is independent and creates its batch of rows and cells.

    Args:
        span_ids: List of ObservationSpan IDs to process
        dataset_id: Target dataset ID
        column_span_mapping_data: List of column mapping dicts with keys:
            - column_id: UUID of the column
            - span_field: Field name on ObservationSpan
            - column_name: Column name (fallback if span_field not provided)

    Returns:
        Dict with rows_created and cells_created counts
    """
    from model_hub.models.develop_dataset import Cell, Row
    from tracer.services.clickhouse.v2 import get_reader

    try:
        close_old_connections()

        # Span data comes from ClickHouse. Fetch only columns that are mapped to
        # dataset cells; hydrating full CHSpan rows reads fat payload columns
        # and can exceed CH memory limits on large exports.
        mapped_span_fields = _mapped_span_fields(column_span_mapping_data)
        with get_reader() as reader:
            by_id = {}
            for start in range(0, len(span_ids), CH_EXPORT_READ_BATCH_SIZE):
                chunk = span_ids[start : start + CH_EXPORT_READ_BATCH_SIZE]
                by_id.update(
                    _export_span_rows(
                        reader,
                        chunk,
                        mapped_span_fields,
                        project_id=project_id,
                        org_id=org_id,
                    )
                )
        observation_spans = [by_id[str(s)] for s in span_ids if str(s) in by_id]

        # Codex consolidated review P0 (2026-05-26): silently dropping
        # missing-CH spans is data loss. Caller derived span_ids from a
        # PG queryset — the legacy ORM path would have built dataset rows
        # for ALL of them. We FAIL FAST so Celery retry can absorb
        # transient CH lag; sustained misses surface as a real divergence
        # the operator must triage (rebackfill or rerun once CH catches up).
        if len(observation_spans) != len(span_ids):
            missing = [str(s) for s in span_ids if str(s) not in by_id]
            logger.error(
                "dataset_chunk_ch_spans_missing",
                requested=len(span_ids),
                found=len(observation_spans),
                missing_count=len(missing),
                missing_sample=missing[:10],
                dataset_id=str(dataset_id),
            )
            raise RuntimeError(
                f"CH missing {len(missing)} of {len(span_ids)} requested spans "
                f"for dataset {dataset_id}; refusing to silently drop rows. "
                f"Sample missing ids: {missing[:5]}. "
                f"Action: let Celery retry, OR re-run after rebackfill if "
                f"misses are sustained beyond the dual-write window."
            )

        rows_to_create = []
        cells_to_create = []

        rows_count = Row.objects.filter(dataset_id=dataset_id, deleted=False).count()

        # Check if any mapping needs child_spans
        needs_child_spans = any(
            (m.get("span_field") or m.get("column_name")) == "child_spans"
            for m in column_span_mapping_data
        )

        # Check if any mapping needs virtual eval/annotation fields
        needs_eval_metrics = any(
            (m.get("span_field") or m.get("column_name")) == "eval_metrics"
            for m in column_span_mapping_data
        )
        needs_annotation_metrics = any(
            (m.get("span_field") or m.get("column_name")) == "annotation_metrics"
            for m in column_span_mapping_data
        )

        # Pre-fetch child span trees if needed (batch to avoid N+1 CH queries)
        child_spans_cache = {}
        if needs_child_spans:
            child_spans_cache = _serialize_span_trees_batch(
                span_ids, project_id=project_id
            )

        # Pre-fetch eval metrics (EvalLogger) if needed — single bulk query, no N+1
        from collections import defaultdict

        eval_metrics_cache = defaultdict(dict)
        if needs_eval_metrics:
            from tracer.models.observation_span import EvalLogger

            for log in EvalLogger.objects.filter(
                observation_span_id__in=span_ids
            ).order_by(
                "created_at"
            ):  # ascending → most recent wins per key
                key = log.eval_type_id or str(log.id)
                if log.output_float is not None:
                    score = log.output_float
                elif log.output_bool is not None:
                    score = log.output_bool
                elif log.output_str:
                    score = log.output_str
                else:
                    score = log.output_str_list
                eval_metrics_cache[log.observation_span_id][key] = {
                    "score": score,
                    "explanation": log.results_explanation,
                    "error": log.error,
                    "error_message": log.error_message if log.error else None,
                }

        # Pre-fetch annotation metrics (Score) if needed — single bulk query, no N+1
        annotation_metrics_cache = defaultdict(dict)
        if needs_annotation_metrics:
            from model_hub.models.score import Score

            for score in (
                Score.objects.filter(
                    observation_span_id__in=span_ids,
                    deleted=False,
                )
                .select_related("label")
                .order_by("created_at")
            ):  # ascending → most recent wins per label
                annotation_metrics_cache[score.observation_span_id][
                    score.label.name
                ] = score.value

        # Create all Row objects for this chunk (in memory - no DB operation)
        for i, observation_span in enumerate(observation_spans):
            row = Row(id=uuid.uuid4(), dataset_id=dataset_id, order=rows_count + i)
            rows_to_create.append(row)

        # Create all Rows and Cells in a single transaction for atomicity
        # If either fails, both roll back - prevents orphaned rows without cells
        with transaction.atomic():
            # Bulk create rows (DB operation)
            created_rows = Row.objects.bulk_create(rows_to_create)

            # Create all Cell objects (in memory - no DB operation yet)
            for observation_span, row in zip(observation_spans, created_rows):
                for column_mapping in column_span_mapping_data:
                    column_id = column_mapping["column_id"]
                    span_field = column_mapping["span_field"]
                    column_name = column_mapping["column_name"]

                    # Use span_field if provided, otherwise fall back to column_name
                    field_name = span_field or column_name

                    observation_span_id = str(observation_span["id"])
                    if field_name == "child_spans":
                        # Virtual field: recursively collected child span data
                        value = child_spans_cache.get(observation_span_id, [])
                    elif field_name == "eval_metrics":
                        value = dict(eval_metrics_cache.get(observation_span_id, {}))
                    elif field_name == "annotation_metrics":
                        value = dict(
                            annotation_metrics_cache.get(observation_span_id, {})
                        )
                    else:
                        value = _export_span_field_value(observation_span, field_name)

                    # Use ForeignKey _id fields directly - no need to fetch objects!
                    cell = Cell(
                        id=uuid.uuid4(),
                        dataset_id=dataset_id,
                        column_id=column_id,
                        row=row,  # Uses row with ID from bulk_create
                        value=_serialize_cell_value(value),
                    )
                    cells_to_create.append(cell)

            # Bulk create all cells (DB operation)
            Cell.objects.bulk_create(cells_to_create, batch_size=1000)

        logger.info(
            f"dataset_chunk_processed: chunk_size={len(span_ids)}, "
            f"rows_created={len(created_rows)}, cells_created={len(cells_to_create)}, "
            f"dataset_id={dataset_id}"
        )

        # Emit storage usage event for dataset row creation
        try:
            from model_hub.models.develop_dataset import Database

            try:
                from ee.usage.schemas.events import UsageEvent
            except ImportError:
                UsageEvent = None
            try:
                from ee.usage.services.emitter import emit
            except ImportError:
                emit = None

            dataset = Database.objects.only("organization_id").get(id=dataset_id)
            data_size = sum(len(str(c.value or "").encode()) for c in cells_to_create)
            emit(
                UsageEvent(
                    org_id=str(dataset.organization_id),
                    event_type="dataset_row_from_spans",
                    amount=data_size,
                    properties={
                        "source": "dataset_from_spans",
                        "source_id": str(dataset_id),
                        "rows_created": len(created_rows),
                    },
                )
            )
        except Exception:
            logger.debug(
                "emit_dataset_storage_event_failed", dataset_id=str(dataset_id)
            )

        return {
            "rows_created": len(created_rows),
            "cells_created": len(cells_to_create),
        }

    except Exception as exc:
        logger.exception(
            f"dataset_chunk_processing_failed: span_ids={span_ids[:5]}, "
            f"dataset_id={dataset_id}, error={str(exc)}"
        )
        raise  # Re-raise for Temporal to handle retry
    finally:
        close_old_connections()
