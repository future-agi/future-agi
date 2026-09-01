"""Opaque, signed continuation cursors for ClickHouse list endpoints.

The cursor is transport state, not a client-readable contract.  It freezes the
request window and carries the complete last-row ordering tuple.  Every token
is bound to the authenticated tenant scope and the normalized query shape, so
it cannot be replayed for a different project, filter, sort, or page size.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.core import signing

# Version 3 deliberately removes ReplacingMergeTree version ceilings.  A
# ceiling is not an MVCC snapshot: after a background merge the older version
# may no longer exist, so a continuation could silently lose a row.  Cursors
# now freeze only immutable request bounds and keyset progress.  The salt bump
# makes every token carrying the former false-snapshot contract fail closed.
CURSOR_VERSION = 3
CURSOR_SALT = "tracer.clickhouse-list-cursor.v3"
DEFAULT_CURSOR_MAX_AGE_SECONDS = 24 * 60 * 60


class ListCursorError(ValueError):
    """A sanitized cursor validation error safe to expose at the API edge."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ListCursor:
    window_start: datetime
    window_end: datetime
    order: tuple[Any, ...]
    total_rows: int | None = None
    seen_rows: int = 0
    scan_slice_start: datetime | None = None
    scan_slice_end: datetime | None = None
    scan_before_start_time: datetime | None = None
    scan_before_id: Any = None


def _utc_datetime(value: datetime, field_name: str) -> datetime:
    """Normalize driver-produced naive UTC values before cursor validation."""

    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_utc_datetime(
    value: datetime | None,
    field_name: str,
) -> datetime | None:
    return None if value is None else _utc_datetime(value, field_name)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return {"$datetime": normalized.astimezone(UTC).isoformat()}
    if hasattr(value, "hex") and value.__class__.__name__ == "UUID":
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _restore_json_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$datetime"}:
        try:
            return datetime.fromisoformat(str(value["$datetime"]))
        except ValueError as exc:
            raise ListCursorError(
                "invalid_cursor", "The continuation cursor is invalid."
            ) from exc
    if isinstance(value, list):
        return tuple(_restore_json_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _restore_json_value(item) for key, item in value.items()}
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


_FILTER_PRESENTATION_KEYS = frozenset(
    {
        "display_name",
        "displayName",
    }
)


def _deduplicated_sorted_json_values(values: list[Any]) -> list[Any]:
    by_json = {_canonical_json(value): value for value in values}
    return [by_json[key] for key in sorted(by_json)]


def _normalized_filter(item: Any) -> Any:
    if not isinstance(item, dict):
        return _json_value(item)
    normalized = _json_value(item)
    for key in _FILTER_PRESENTATION_KEYS:
        normalized.pop(key, None)
    column_id = normalized.get("column_id", normalized.get("columnId"))
    config = normalized.get("filter_config") or normalized.get("filterConfig") or {}
    canonical_config: dict[str, Any] = {}
    for snake_key, camel_key in (
        ("filter_type", "filterType"),
        ("filter_op", "filterOp"),
        ("filter_value", "filterValue"),
        ("col_type", "colType"),
        ("attribute_value_types", "attributeValueTypes"),
    ):
        if snake_key in config:
            canonical_config[snake_key] = config[snake_key]
        elif camel_key in config:
            canonical_config[snake_key] = config[camel_key]
    config = canonical_config
    operator = config.get("filter_op") or config.get("filterOp")
    value_key = "filter_value"
    if operator in {"in", "not_in"} and isinstance(config.get(value_key), list):
        values = config[value_key]
        types_key = "attribute_value_types"
        value_types = config.get(types_key)
        if isinstance(value_types, list) and len(value_types) == len(values):
            # Storage provenance is positional. Sort and deduplicate pairs, not
            # values alone, or a mixed typed attribute filter changes meaning.
            pairs = _deduplicated_sorted_json_values(
                [
                    [value, value_type]
                    for value, value_type in zip(values, value_types, strict=True)
                ]
            )
            config[value_key] = [pair[0] for pair in pairs]
            config[types_key] = [pair[1] for pair in pairs]
        else:
            config[value_key] = _deduplicated_sorted_json_values(values)
    canonical_item = {"column_id": column_id, "filter_config": config}
    # These fields affect routing/type interpretation in dashboard and eval
    # query builders, so they are semantic even though they are outside
    # filter_config. Only the human-facing display label is discarded.
    if "source" in normalized:
        canonical_item["source"] = normalized["source"]
    if "output_type" in normalized:
        canonical_item["output_type"] = normalized["output_type"]
    elif "outputType" in normalized:
        canonical_item["output_type"] = normalized["outputType"]
    return canonical_item


def normalize_filter_conjunction(filters: list[Any] | tuple[Any, ...]) -> list[Any]:
    """Canonicalize one AND-conjunction without presentation-only metadata."""

    normalized = [_normalized_filter(item) for item in (filters or [])]
    # Repeated identical leaves are idempotent under conjunction. Removing them
    # keeps cursor/cache identities stable and bounds redundant query predicates.
    by_json = {_canonical_json(item): item for item in normalized}
    return [by_json[key] for key in sorted(by_json)]


def normalize_cursor_query(query: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize semantically equivalent list query payloads."""

    normalized: dict[str, Any] = {}
    for key, value in query.items():
        if key in {
            "allow_sampled",
            "cursor",
            "cursor_mode",
            "page",
            "page_number",
        }:
            continue
        if key == "filters":
            normalized[key] = normalize_filter_conjunction(value or [])
        elif key in {"project_ids"} and isinstance(value, (list, tuple)):
            normalized[key] = sorted(str(item) for item in value)
        elif key == "search" and isinstance(value, str):
            normalized[key] = value.strip()
        else:
            normalized[key] = _json_value(value)
    return normalized


def exact_total_explicitly_required(
    request: Any,
    validated_data: dict[str, Any],
    *,
    allow_exact_cursor_lower_bound: bool = False,
) -> bool:
    """Return whether the client explicitly rejected a bounded total.

    ``allow_sampled`` was added after the list APIs were already deployed, so
    older clients omit it.  A complete bounded page is safe to return to those
    clients as long as its lower-bound total is labelled truthfully.  Clients
    that explicitly send ``allow_sampled=false`` retain the strict exact-total
    contract. Trace cursor reads may opt into a lower-bound total because every
    returned row is still exact and ordered and the signed continuation proves
    where the unscanned suffix begins. Other resources may opt in only after
    implementing and exposing the same signed exact-continuation contract.
    """

    query_params = getattr(request, "query_params", None)
    return (
        query_params is not None
        and "allow_sampled" in query_params
        and validated_data.get("allow_sampled") is False
        and not (
            allow_exact_cursor_lower_bound
            and (validated_data.get("cursor_mode") or validated_data.get("cursor"))
        )
    )


def cursor_scope_for_request(
    request: Any,
    *,
    project_ids: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Return stable authentication + tenant identifiers without token secrets."""

    user = getattr(request, "user", None)
    organization = getattr(request, "organization", None) or getattr(
        user, "organization", None
    )
    workspace = getattr(request, "workspace", None)
    auth = getattr(request, "auth", None)
    auth_id = None
    for attr in ("pk", "id", "key_prefix"):
        candidate = getattr(auth, attr, None)
        if candidate not in (None, ""):
            auth_id = str(candidate)
            break
    return {
        "principal_id": str(getattr(user, "pk", None) or getattr(user, "id", "")),
        "auth_type": auth.__class__.__name__ if auth is not None else "session",
        "auth_id": auth_id,
        "organization_id": str(getattr(organization, "pk", "") or ""),
        "workspace_id": str(
            getattr(workspace, "pk", None)
            or getattr(request, "workspace_id", None)
            or getattr(user, "default_workspace_id", None)
            or ""
        ),
        "project_ids": sorted(str(project_id) for project_id in project_ids),
    }


def _max_age_seconds() -> int:
    value = int(
        getattr(
            settings,
            "TRACER_LIST_CURSOR_MAX_AGE_SECONDS",
            DEFAULT_CURSOR_MAX_AGE_SECONDS,
        )
    )
    return max(1, value)


def encode_list_cursor(
    *,
    resource: str,
    scope: dict[str, Any],
    query: dict[str, Any],
    page_size: int,
    window_start: datetime,
    window_end: datetime,
    order: tuple[Any, ...] | list[Any],
    seen_rows: int,
    total_rows: int | None = None,
    scan_slice_start: datetime | None = None,
    scan_slice_end: datetime | None = None,
    scan_before_start_time: datetime | None = None,
    scan_before_id: Any = None,
) -> str:
    window_start = _utc_datetime(window_start, "window_start")
    window_end = _utc_datetime(window_end, "window_end")
    scan_slice_start = _optional_utc_datetime(scan_slice_start, "scan_slice_start")
    scan_slice_end = _optional_utc_datetime(scan_slice_end, "scan_slice_end")
    scan_before_start_time = _optional_utc_datetime(
        scan_before_start_time,
        "scan_before_start_time",
    )
    if (
        not resource
        or page_size <= 0
        or window_start >= window_end
        or not order
        or seen_rows < 0
    ):
        raise ValueError("invalid list cursor state")
    if (scan_before_start_time is None) != (scan_before_id is None):
        raise ValueError("invalid list scan checkpoint")
    if scan_slice_end is not None and not (window_start < scan_slice_end <= window_end):
        raise ValueError("invalid list scan checkpoint")
    if scan_slice_start is not None and (
        scan_slice_end is None or not window_start <= scan_slice_start < scan_slice_end
    ):
        raise ValueError("invalid list scan checkpoint")
    if scan_before_start_time is not None and not (
        (scan_slice_start or window_start)
        <= scan_before_start_time
        < (scan_slice_end or window_end)
    ):
        raise ValueError("invalid list scan checkpoint")
    payload = {
        "v": CURSOR_VERSION,
        "resource": resource,
        "scope": _digest(scope),
        "query": _digest(normalize_cursor_query(query)),
        "page_size": int(page_size),
        "window_start": _json_value(window_start),
        "window_end": _json_value(window_end),
        "order": _json_value(list(order)),
        "total_rows": int(total_rows) if total_rows is not None else None,
        "seen_rows": int(seen_rows),
        "scan_slice_start": _json_value(scan_slice_start),
        "scan_slice_end": _json_value(scan_slice_end),
        "scan_before_start_time": _json_value(scan_before_start_time),
        "scan_before_id": _json_value(scan_before_id),
    }
    return signing.dumps(
        payload, key=settings.SECRET_KEY, salt=CURSOR_SALT, compress=True
    )


def list_cursor_boundary_fingerprint(token: str | None) -> str | None:
    """Return a stable, explicit identity for one opaque cursor boundary.

    ``TimestampSigner`` deliberately changes the transport token when the same
    cursor payload is signed at a different time.  Clients must never parse that
    private wire format to decide whether an evicted page replay is consistent,
    so list responses publish this digest of the verified payload separately.
    """

    if token is None:
        return None
    try:
        payload = signing.loads(
            token,
            key=settings.SECRET_KEY,
            salt=CURSOR_SALT,
        )
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise RuntimeError("generated list cursor could not be verified") from exc
    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise RuntimeError("generated list cursor payload is invalid")
    return _digest(payload)


def decode_list_cursor(
    token: str,
    *,
    resource: str,
    scope: dict[str, Any],
    query: dict[str, Any],
    page_size: int,
) -> ListCursor:
    try:
        payload = signing.loads(
            token,
            key=settings.SECRET_KEY,
            salt=CURSOR_SALT,
            max_age=_max_age_seconds(),
        )
    except signing.SignatureExpired as exc:
        raise ListCursorError(
            "cursor_expired", "The continuation cursor has expired."
        ) from exc
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise ListCursorError(
            "invalid_cursor", "The continuation cursor is invalid."
        ) from exc

    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    expected = (
        payload.get("resource") == resource
        and payload.get("scope") == _digest(scope)
        and payload.get("query") == _digest(normalize_cursor_query(query))
        and payload.get("page_size") == int(page_size)
    )
    if not expected:
        raise ListCursorError(
            "cursor_mismatch",
            "The continuation cursor does not match this request.",
        )
    try:
        window_start = _restore_json_value(payload["window_start"])
        window_end = _restore_json_value(payload["window_end"])
        order = _restore_json_value(payload["order"])
    except (KeyError, TypeError) as exc:
        raise ListCursorError(
            "invalid_cursor", "The continuation cursor is invalid."
        ) from exc
    if (
        not isinstance(window_start, datetime)
        or not isinstance(window_end, datetime)
        or window_start >= window_end
        or not isinstance(order, tuple)
        or not order
        or not isinstance(payload.get("seen_rows"), int)
        or payload["seen_rows"] < 0
    ):
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    # v3 cursors issued before scan_slice_start was added remain valid. A
    # missing lower boundary falls back to the frozen request start when the
    # selector resumes an in-slice keyset, which may rescan but cannot skip.
    scan_slice_start = _restore_json_value(payload.get("scan_slice_start"))
    scan_slice_end = _restore_json_value(payload.get("scan_slice_end"))
    scan_before_start_time = _restore_json_value(payload.get("scan_before_start_time"))
    scan_before_id = _restore_json_value(payload.get("scan_before_id"))
    if scan_slice_end is not None and (
        not isinstance(scan_slice_end, datetime)
        or not window_start < scan_slice_end <= window_end
    ):
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    if scan_slice_start is not None and (
        not isinstance(scan_slice_start, datetime)
        or scan_slice_end is None
        or not window_start <= scan_slice_start < scan_slice_end
    ):
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    if (scan_before_start_time is None) != (scan_before_id is None):
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    if scan_before_start_time is not None and (
        not isinstance(scan_before_start_time, datetime)
        or not (scan_slice_start or window_start)
        <= scan_before_start_time
        < (scan_slice_end or window_end)
    ):
        raise ListCursorError("invalid_cursor", "The continuation cursor is invalid.")
    return ListCursor(
        window_start=window_start,
        window_end=window_end,
        order=order,
        total_rows=(
            int(payload["total_rows"])
            if payload.get("total_rows") is not None
            else None
        ),
        seen_rows=payload["seen_rows"],
        scan_slice_start=scan_slice_start,
        scan_slice_end=scan_slice_end,
        scan_before_start_time=scan_before_start_time,
        scan_before_id=scan_before_id,
    )


def snapshot_cursor_supported(filters: list[dict[str, Any]], *, resource: str) -> bool:
    """Whether the bounded compiler supports keyset continuation for filters."""

    from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
        partition_span_filter_plans,
        partition_trace_filter_plans,
    )

    partition = {
        "observe_traces": partition_trace_filter_plans,
        "observe_spans": partition_span_filter_plans,
    }.get(resource)
    if partition is None:
        raise ValueError("unsupported cursor resource")
    try:
        partition(filters)
    except (TypeError, ValueError):
        return False
    return True


def cursor_page_metadata(
    *,
    enabled: bool,
    has_more: bool,
    seen_rows: int,
    next_cursor: str | None,
    unseen_row_proven: bool = False,
) -> dict[str, Any]:
    """Build cursor totals, or no cursor contract for a legacy fallback page."""

    if not enabled:
        return {}
    if seen_rows < 0:
        raise ValueError("seen_rows must be non-negative")
    if has_more and not next_cursor:
        raise RuntimeError("cursor page with more rows requires a continuation token")
    return {
        # A scan checkpoint means more search space, not necessarily another
        # matching row. Add the sentinel only when the selector has already
        # classified an extra match beyond the published page.
        "total_rows": seen_rows + (1 if has_more and unseen_row_proven else 0),
        "total_rows_exact": None if has_more else seen_rows,
        "total_rows_is_lower_bound": has_more,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "next_cursor_fingerprint": list_cursor_boundary_fingerprint(next_cursor),
    }


def frozen_window_filter(cursor: ListCursor) -> dict[str, Any]:
    """Return the immutable time bound carried by a live keyset cursor."""

    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [cursor.window_start, cursor.window_end],
        },
    }


__all__ = [
    "ListCursor",
    "ListCursorError",
    "cursor_page_metadata",
    "cursor_scope_for_request",
    "decode_list_cursor",
    "encode_list_cursor",
    "list_cursor_boundary_fingerprint",
    "frozen_window_filter",
    "normalize_filter_conjunction",
    "normalize_cursor_query",
    "snapshot_cursor_supported",
]
