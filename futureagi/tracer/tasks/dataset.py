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


def _serialize_span_tree(span_id):
    """
    Serialize all descendants of a span into a nested JSON-safe structure.
    Uses a single query to fetch all spans in the same trace, then builds
    the tree in memory to avoid N+1 queries.
    """
    from tracer.models.observation_span import ObservationSpan

    # First, get the trace_id for this span
    try:
        root_span = ObservationSpan.objects.only("trace_id").get(id=span_id)
    except ObservationSpan.DoesNotExist:
        return []

    # Fetch ALL spans in the same trace in one query
    all_spans = list(
        ObservationSpan.objects.filter(trace_id=root_span.trace_id).only(
            "id", "parent_span_id", *_CHILD_SPAN_FIELDS
        )
    )

    # Build parent_id -> children lookup
    children_map = {}
    for span in all_spans:
        pid = span.parent_span_id
        if pid not in children_map:
            children_map[pid] = []
        children_map[pid].append(span)

    def _build_subtree(parent_id, depth=0):
        if depth > 20:  # Safety limit
            return []
        children = children_map.get(parent_id, [])
        result = []
        for child in children:
            child_data = {}
            for field_name in _CHILD_SPAN_FIELDS:
                value = getattr(child, field_name, None)
                if value is not None:
                    if hasattr(value, "isoformat"):
                        value = value.isoformat()
                    child_data[field_name] = value
            grandchildren = _build_subtree(child.id, depth + 1)
            if grandchildren:
                child_data["children"] = grandchildren
            result.append(child_data)
        return result

    return _build_subtree(span_id)


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
    from tracer.models.observation_span import ObservationSpan

    try:
        close_old_connections()

        observation_spans = ObservationSpan.objects.filter(
            id__in=span_ids
        ).prefetch_related("project")

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
                    else:
                        try:
                            value = getattr(observation_span, field_name, None)
                        except AttributeError:
                            value = None

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
