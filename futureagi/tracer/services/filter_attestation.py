"""Public evidence that a successful read applied its declared filter scope.

The digest deliberately contains no values beyond those the caller already
submitted.  It is a response-bound checksum over the exact non-window filter
list handed to the query builder/cache identity, plus the authorized project
and public surface.  Positive datetime bounds are proved separately by the
published query window; datetime complements remain in the digest because they
are executable filter leaves in addition to that base window.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

FILTER_ATTESTATION_VERSION = "canonical-json-sha256-v1"


def _canonical_value(value: Any) -> Any:
    """Return a JSON-safe value with stable integral-number representation."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("filter attestation cannot encode non-finite numbers")
        return int(value) if value.is_integer() else value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("filter attestation cannot encode non-finite numbers")
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
    )


def applied_filter_leaves(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the canonical executable leaves outside the positive base window."""

    from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

    leaves: list[dict[str, Any]] = []
    for item in filters or []:
        if not isinstance(item, dict):
            raise ValueError("filter attestation requires object leaves")
        column_id = item.get("column_id") or item.get("columnId")
        if column_id in {"created_at", "start_time"} and not (
            BaseQueryBuilder.is_datetime_complement_filter(item)
        ):
            continue
        leaves.append(_canonical_value(item))
    # Conjunction order does not change membership. Sorting makes equivalent
    # request/filter-builder orderings attest to the same executed predicate.
    return sorted(leaves, key=_canonical_json)


def applied_filter_attestation(
    *,
    project_id: Any,
    observe_type: str,
    filters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the public digest/count for one builder/cache filter identity."""

    normalized_project_id = str(project_id or "").strip()
    normalized_observe_type = str(observe_type or "").strip().lower()
    if not normalized_project_id or not normalized_observe_type:
        raise ValueError("filter attestation requires project and observe type")
    leaves = applied_filter_leaves(filters)
    digest_input = {
        "project_id": normalized_project_id,
        "observe_type": normalized_observe_type,
        "filters": leaves,
    }
    digest = hashlib.sha256(_canonical_json(digest_input).encode()).hexdigest()
    return {
        "query_applied_filter_version": FILTER_ATTESTATION_VERSION,
        "query_applied_filter_sha256": digest,
        "query_applied_filter_count": len(leaves),
    }


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")


def graph_execution_filters(
    filters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Freeze the builder's effective base window into one exact public leaf."""

    from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

    normalized_filters = list(filters or [])
    window_start, window_end = BaseQueryBuilder.parse_time_range(
        normalized_filters,
        strict=True,
    )
    if not isinstance(window_start, datetime) or not isinstance(window_end, datetime):
        raise ValueError("graph execution requires a bounded query window")
    retained = [
        item
        for item in normalized_filters
        if (item.get("column_id") or item.get("columnId"))
        not in {"created_at", "start_time"}
        or BaseQueryBuilder.is_datetime_complement_filter(item)
    ]
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [_utc_iso(window_start), _utc_iso(window_end)],
                "col_type": "SYSTEM_METRIC",
            },
        },
        *retained,
    ]


def graph_query_evidence(
    *,
    project_id: Any,
    observe_type: str,
    filters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return exact public graph-window and applied-filter evidence."""

    from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

    window_start, window_end = BaseQueryBuilder.parse_time_range(filters, strict=True)
    if not isinstance(window_start, datetime) or not isinstance(window_end, datetime):
        raise ValueError("graph evidence requires a bounded query window")
    return {
        **applied_filter_attestation(
            project_id=project_id,
            observe_type=observe_type,
            filters=filters,
        ),
        "query_window_start": _utc_iso(window_start),
        "query_window_end": _utc_iso(window_end),
    }


__all__ = [
    "FILTER_ATTESTATION_VERSION",
    "applied_filter_attestation",
    "applied_filter_leaves",
    "graph_execution_filters",
    "graph_query_evidence",
]
