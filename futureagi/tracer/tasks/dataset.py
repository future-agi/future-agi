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


def _serialize_span_tree(span_id):
    """
    Serialize all descendants of a span into a nested JSON-safe structure.
    Uses a single ClickHouse query to fetch all spans in the same trace,
    then builds the tree in memory to avoid N+1 queries.
    """
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        root_span = reader.get(str(span_id))
        if root_span is None:
            return []
        all_spans = reader.list_by_trace(root_span.trace_id)

    # Preserve the legacy ObservationSpan model's default ordering
    # (`Meta.ordering = ["-start_time"]`) so sibling child JSON order is
    # unchanged. list_by_trace returns ascending start_time, so reverse.
    all_spans = list(reversed(all_spans))

    # Build parent_id -> children lookup
    children_map: dict = {}
    for span in all_spans:
        # CHSpan stores empty parent ids as "" (CH default), not None.
        pid = span.parent_span_id or None
        children_map.setdefault(pid, []).append(span)

    def _build_subtree(parent_id, depth=0):
        if depth > 20:  # Safety limit
            return []
        children = children_map.get(parent_id, [])
        result = []
        for child in children:
            child_data = {}
            for field_name in _CHILD_SPAN_FIELDS:
                value = _chspan_field_value(child, field_name)
                if value is not None:
                    if hasattr(value, "isoformat"):
                        value = value.isoformat()
                    child_data[field_name] = value
            grandchildren = _build_subtree(child.id, depth + 1)
            if grandchildren:
                child_data["children"] = grandchildren
            result.append(child_data)
        return result

    return _build_subtree(str(span_id))


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
def process_spans_chunk_task(span_ids, dataset_id, column_span_mapping_data):
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

        # Span data comes from ClickHouse. The legacy ORM call also did
        # `.prefetch_related("project")` but no code below actually reads
        # ``.project``, so the prefetch was dead weight — dropping it is
        # safe. Result order from list_by_ids is by id (deterministic but
        # not matching span_ids); we reorder below to preserve the input
        # order so the resulting Row.order numbers are stable.
        with get_reader() as reader:
            fetched = reader.list_by_ids([str(s) for s in span_ids])
        by_id = {s.id: s for s in fetched}
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

        # Pre-fetch child span trees if needed
        child_spans_cache = {}
        if needs_child_spans:
            for span_id in span_ids:
                child_spans_cache[span_id] = _serialize_span_tree(span_id)

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

                    if field_name == "child_spans":
                        # Virtual field: recursively collected child span data
                        value = child_spans_cache.get(observation_span.id, [])
                    elif field_name == "eval_metrics":
                        value = dict(eval_metrics_cache.get(observation_span.id, {}))
                    elif field_name == "annotation_metrics":
                        value = dict(
                            annotation_metrics_cache.get(observation_span.id, {})
                        )
                    else:
                        value = _chspan_field_value(observation_span, field_name)

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
