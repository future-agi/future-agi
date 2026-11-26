"""Opaque, context-bound keyset positions; never authorization credentials."""

import base64
import binascii

from tracer.services.derived_metrics.errors import MetricInputError

MAX_GROUP_KEY_BYTES = 2048


def encode_result_cursor(result_id: str, rollup: str, key: str) -> str:
    raw = key.encode()
    if len(raw) > MAX_GROUP_KEY_BYTES:
        raise MetricInputError("Cohort key exceeds the supported result cursor size.")
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"v1:{result_id}:{rollup}:{encoded}"


def decode_result_cursor(cursor: str, result_id: str, rollup: str) -> tuple[bool, str]:
    if not cursor:
        return False, ""
    try:
        version, cursor_result, cursor_rollup, encoded = cursor.split(":", 3)
        if (version, cursor_result, cursor_rollup) != ("v1", result_id, rollup):
            raise ValueError("cursor scope changed")
        raw = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
        if len(raw) > MAX_GROUP_KEY_BYTES:
            raise ValueError("cursor key is too long")
        return True, raw.decode()
    except (ValueError, binascii.Error) as exc:
        raise MetricInputError(
            "Result cursor is invalid or belongs to another result/rollup. Start from the first page."
        ) from exc


def newest_first_page(queryset, before, limit: int):
    """One newest-first page over ``(created_at, id)`` and the next ``before`` id.

    Random UUID order is neither chronological nor stable, so pages are keyed on
    creation time with the id only as a tie-break. ``before`` stays the id of the
    last row of the previous page (the published cursor contract); its position is
    resolved from the same scoped queryset, so a cursor can neither skip or repeat
    rows that were created while paging nor reveal a row outside the caller's
    scope. A row newer than the cursor never changes later pages.
    """
    from django.db.models import Q

    ordered = queryset.order_by("-created_at", "-id")
    if before:
        created_at = (
            queryset.prefetch_related(None)
            .filter(pk=before)
            .values_list("created_at", flat=True)
            .first()
        )
        if created_at is None:
            raise MetricInputError(
                {
                    "before": "Cursor is unknown in this scope. Start from the first page."
                }
            )
        ordered = ordered.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=before)
        )
    rows = list(ordered[: limit + 1])
    page = rows[:limit]
    return page, (str(page[-1].id) if len(rows) > limit else None)
