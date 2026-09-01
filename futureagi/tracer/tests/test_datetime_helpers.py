from datetime import UTC, datetime, timedelta, timezone

from tracer.utils.helper import format_datetime_to_iso


def test_format_datetime_to_iso_serializes_non_null_datetime():
    value = datetime(2026, 8, 30, 17, 45, 12, 345678, tzinfo=UTC)

    assert format_datetime_to_iso(value) == "2026-08-30T17:45:12.345678Z"


def test_format_datetime_to_iso_converts_offset_to_utc():
    value = datetime(
        2026, 8, 30, 17, 45, 12, 345678, tzinfo=timezone(timedelta(hours=2))
    )

    assert format_datetime_to_iso(value) == "2026-08-30T15:45:12.345678Z"
