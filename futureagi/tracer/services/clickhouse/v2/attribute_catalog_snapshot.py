"""Explicit DEV-only frozen-window mode for public attribute pickers.

The catalog represents one immutable half-open handoff interval.  This helper
lets development deployments make that limitation visible and intentional by
pinning *fresh* picker cursors to the configured interval.  Continuations do
not consult these settings for their bounds; their signed cursor remains the
source of truth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from django.conf import settings

from tracer.services.clickhouse.list_cursor import (
    ListCursor,
    ListCursorError,
    decode_list_cursor,
)
from tracer.services.clickhouse.v2.attribute_catalog_cutover import (
    catalog_dev_read_enabled,
)

logger = structlog.get_logger(__name__)

CATALOG_SNAPSHOT_MODE = "frozen_snapshot"
CATALOG_SNAPSHOT_HEADER = "frozen-snapshot"


def decode_catalog_snapshot_list_cursor(
    token: str,
    *,
    resource: str,
    scope: dict[str, Any],
    query: dict[str, Any],
    page_size: int,
) -> tuple[ListCursor, str | None]:
    """Authenticate a cursor against the only two catalog window modes.

    Runtime flags govern fresh walks only. A continuation must instead recover
    whether its signed query identity was the baseline contract or the explicit
    frozen-snapshot contract. Trying only these two server-defined variants
    keeps that recovery authenticated without decoding or trusting cursor
    payload fields directly.
    """

    baseline_query = dict(query)
    supplied_mode = baseline_query.pop("query_window_mode", None)
    if supplied_mode not in (None, CATALOG_SNAPSHOT_MODE):
        raise ListCursorError(
            "invalid_cursor",
            "The continuation cursor is invalid.",
        )

    try:
        return (
            decode_list_cursor(
                token,
                resource=resource,
                scope=scope,
                query=baseline_query,
                page_size=page_size,
            ),
            None,
        )
    except ListCursorError as baseline_error:
        if baseline_error.code != "cursor_mismatch":
            raise
        baseline_mismatch = baseline_error

    snapshot_query = {
        **baseline_query,
        "query_window_mode": CATALOG_SNAPSHOT_MODE,
    }
    try:
        return (
            decode_list_cursor(
                token,
                resource=resource,
                scope=scope,
                query=snapshot_query,
                page_size=page_size,
            ),
            CATALOG_SNAPSHOT_MODE,
        )
    except ListCursorError as snapshot_error:
        if snapshot_error.code != "cursor_mismatch":
            raise
        raise baseline_mismatch from snapshot_error


def catalog_dev_snapshot_enabled() -> bool:
    """Return true only for the explicit, runtime-guarded DEV snapshot flag."""

    configured = getattr(
        settings,
        "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED",
        False,
    )
    return configured is True and catalog_dev_read_enabled()


def _snapshot_bound(setting_name: str) -> datetime:
    raw_value: Any = getattr(settings, setting_name, None)
    if isinstance(raw_value, datetime):
        value = raw_value
    elif isinstance(raw_value, str) and raw_value.strip():
        normalized = raw_value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        value = datetime.fromisoformat(normalized)
    else:
        raise ValueError(f"{setting_name} is not configured")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{setting_name} must use UTC")
    value = value.astimezone(UTC)
    if value.minute or value.second or value.microsecond:
        raise ValueError(f"{setting_name} must be aligned to a UTC hour")
    return value


def catalog_dev_snapshot_window() -> tuple[datetime, datetime] | None:
    """Return the validated configured ``[A, B)`` interval, or fail closed."""

    if not catalog_dev_snapshot_enabled():
        return None
    try:
        window_start = _snapshot_bound("SPAN_ATTRIBUTE_CATALOG_HANDOFF_START")
        window_end = _snapshot_bound("SPAN_ATTRIBUTE_CATALOG_HANDOFF_END")
        if window_start >= window_end:
            raise ValueError("catalog handoff start must precede end")
    except (TypeError, ValueError, OverflowError):
        # settings.py rejects this at startup.  Repeat the boundary at runtime
        # because tests and process-local settings overrides bypass startup.
        logger.error("span_attribute_catalog_dev_snapshot_config_invalid")
        return None
    return window_start, window_end


def catalog_snapshot_metadata(
    *,
    window_start: datetime,
    window_end: datetime,
    cursor_window_mode: str | None = None,
) -> dict[str, str]:
    """Label only responses whose public window is exactly the frozen snapshot."""

    if cursor_window_mode is not None:
        return (
            {"query_window_mode": CATALOG_SNAPSHOT_MODE}
            if cursor_window_mode == CATALOG_SNAPSHOT_MODE
            else {}
        )
    snapshot_window = catalog_dev_snapshot_window()
    if snapshot_window != (window_start, window_end):
        return {}
    return {"query_window_mode": CATALOG_SNAPSHOT_MODE}


def mark_catalog_snapshot_response(
    response,
    *,
    window_start: datetime,
    window_end: datetime,
    cursor_window_mode: str | None = None,
):
    """Attach an unmistakable transport marker to a frozen-snapshot response."""

    if catalog_snapshot_metadata(
        window_start=window_start,
        window_end=window_end,
        cursor_window_mode=cursor_window_mode,
    ):
        response["X-FutureAGI-Attribute-Catalog-Window"] = CATALOG_SNAPSHOT_HEADER
    return response


__all__ = [
    "CATALOG_SNAPSHOT_HEADER",
    "CATALOG_SNAPSHOT_MODE",
    "catalog_dev_snapshot_enabled",
    "catalog_dev_snapshot_window",
    "catalog_snapshot_metadata",
    "decode_catalog_snapshot_list_cursor",
    "mark_catalog_snapshot_response",
]
