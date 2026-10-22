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
