"""Guard for the ISO 8601 'Z' formatting of session datetime fields."""

from datetime import UTC, datetime, timedelta, timezone

from tracer.utils.helper import (
    format_datetime_fields_to_iso,
    format_datetime_to_iso,
)


class TestFormatDatetimeToIso:
    def test_naive_datetime_formats_with_z_suffix(self):
        val = datetime(2024, 1, 1, 12, 30, 45, 123456)

        assert format_datetime_to_iso(val) == "2024-01-01T12:30:45.123456Z"

    def test_tz_aware_datetime_does_not_double_offset(self):
        val = datetime(2024, 1, 1, 12, 30, 45, 123456, tzinfo=UTC)

        result = format_datetime_to_iso(val)

        assert result == "2024-01-01T12:30:45.123456Z"
        assert "+00:00" not in result

    def test_non_utc_aware_datetime_still_single_suffix(self):
        val = datetime(
            2024, 1, 1, 12, 30, 45, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )

        result = format_datetime_to_iso(val)

        assert result.endswith("Z")
        assert "+05:30" not in result

    def test_falsy_input_returns_none(self):
        assert format_datetime_to_iso(None) is None
        assert format_datetime_to_iso("") is None


class TestFormatDatetimeFieldsToIso:
    def test_mutates_rows_in_place(self):
        rows = [
            {
                "session_id": "s-1",
                "start_time": datetime(2024, 1, 1, 0, 0, 0),
                "end_time": datetime(2024, 1, 1, 0, 5, 0),
            }
        ]

        assert format_datetime_fields_to_iso(rows, ["start_time", "end_time"]) is None
        assert rows[0]["start_time"] == "2024-01-01T00:00:00.000000Z"
        assert rows[0]["end_time"] == "2024-01-01T00:05:00.000000Z"
        assert rows[0]["session_id"] == "s-1"

    def test_none_fields_stay_none(self):
        rows = [
            {"start_time": datetime(2024, 1, 1, 0, 0, 0), "end_time": None},
            {"start_time": None, "end_time": None},
        ]

        format_datetime_fields_to_iso(rows, ["start_time", "end_time"])

        assert rows[0]["start_time"] == "2024-01-01T00:00:00.000000Z"
        assert rows[0]["end_time"] is None
        assert rows[1]["start_time"] is None
        assert rows[1]["end_time"] is None

    def test_missing_field_is_set_to_none(self):
        rows = [{"session_id": "s-1"}]

        format_datetime_fields_to_iso(rows, ["created_at"])

        assert rows[0]["created_at"] is None
