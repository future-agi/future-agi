"""Ground-truth few-shot retrieval over the ClickHouse vector store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from agentic_eval.core.embeddings.embedding_manager import (
    GROUND_TRUTH_TABLE_NAME,
    EmbeddingManager,
)

logger = structlog.get_logger(__name__)


DEFAULT_MAX_RESULTS = 3


@dataclass(frozen=True)
class GroundTruthFewShotRequest:
    """Typed request to :func:`retrieve_ground_truth_fewshots`."""

    eval_id: str
    inputs: dict[str, Any]
    input_cols: dict[str, str]
    organization_id: str
    workspace_id: str | None = None
    max_results: int = DEFAULT_MAX_RESULTS


@dataclass(frozen=True)
class GroundTruthMatch:
    """One retrieved GT row, projected back into the original shape.

    The CH writer splits a single GT row into one CH row per mapped
    input column, all sharing an ``item_id``. We collapse them here so
    callers get the source-row shape they uploaded (plus the mapped
    columns appear once, not N times).
    """

    item_id: str
    row: dict[str, Any]
    per_column_input_types: dict[str, str]


def retrieve_ground_truth_fewshots(
    request: GroundTruthFewShotRequest,
    *,
    embedding_manager: EmbeddingManager | None = None,
) -> list[GroundTruthMatch]:
    """Retrieve ground-truth rows similar to the runtime input.

    The runtime ``inputs`` dict is split per template variable and each
    value is similarity-searched against its corresponding GT column.
    Rows that match across **all** input columns survive the
    intersection; ties below ``similarity_threshold`` drop the whole row.

    Returns an empty list when:
        * the request has no usable inputs (every value is empty),
        * the underlying CH search returns no intersection.

    Raises whatever ``EmbeddingManager.retrieve_avg_rag_based_examples``
    raises on a real failure (CH unreachable, embedding service down,
    etc.). The caller decides how to render those.
    """
    template_vars = _ordered_present_keys(request.inputs)
    if not template_vars:
        logger.info(
            "ground_truth_fewshots_skipped_empty_inputs",
            eval_id=request.eval_id,
            organization_id=request.organization_id,
        )
        return []

    parallel_input_values: list[Any] = []
    parallel_column_names: list[str] = []
    for var in template_vars:
        column = request.input_cols.get(var)
        if not column:
            # Unmapped variables can't index against the writer's CH
            # rows. Skip them rather than poison the query.
            continue
        parallel_input_values.append(request.inputs[var])
        parallel_column_names.append(column)

    if not parallel_input_values:
        logger.info(
            "ground_truth_fewshots_skipped_no_mapped_columns",
            eval_id=request.eval_id,
            organization_id=request.organization_id,
            template_vars=template_vars,
            input_cols=request.input_cols,
        )
        return []

    manager = embedding_manager or EmbeddingManager()
    raw = manager.retrieve_avg_rag_based_examples(
        eval_id=request.eval_id,
        inputs=parallel_input_values,
        input_cols=parallel_column_names,
        table_name=GROUND_TRUTH_TABLE_NAME,
        organization_id=request.organization_id,
        workspace_id=request.workspace_id,
        top_k=request.max_results,
    )

    matches = [_project_match(group) for group in raw if group]

    logger.info(
        "ground_truth_fewshots_retrieved",
        eval_id=request.eval_id,
        organization_id=request.organization_id,
        workspace_id=request.workspace_id,
        queried_columns=parallel_column_names,
        matches_returned=len(matches),
    )
    return matches


def _ordered_present_keys(inputs: dict[str, Any]) -> list[str]:
    """Drop empty/None values; preserve insertion order for stable queries."""
    keys: list[str] = []
    for var, value in inputs.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, set, dict)) and len(value) == 0:
            continue
        keys.append(var)
    return keys


def _project_match(group: list[dict[str, Any]]) -> GroundTruthMatch:
    """Collapse one CH item group back into a single source-row shape.

    Each CH row in ``group`` corresponds to one mapped input column of
    the same source GT row (same ``item_id``). They share all
    non-column metadata. We deduplicate by taking the first row's
    metadata as the canonical row and recording the per-column
    input types separately.
    """
    canonical = dict(group[0])
    item_id = str(canonical.pop("item_id", ""))
    per_column_input_types: dict[str, str] = {}
    for entry in group:
        column = entry.get("index_column")
        input_type = entry.get("input_type")
        if column is not None and input_type is not None:
            per_column_input_types[str(column)] = str(input_type)
    # Strip storage-only keys before handing back to the caller.
    for storage_only_key in ("index_column", "input_type"):
        canonical.pop(storage_only_key, None)
    return GroundTruthMatch(
        item_id=item_id,
        row=canonical,
        per_column_input_types=per_column_input_types,
    )
