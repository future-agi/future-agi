"""
Pure unit tests for ai_tools/formatting.py.

No database or Django needed — these are fast, isolated tests.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    format_number,
    format_status,
    format_uuid,
    key_value_block,
    markdown_table,
    section,
    truncate,
)


class TestMarkdownTable:
    def test_basic_table(self):
        result = markdown_table(["Name", "Age"], [["Alice", "30"], ["Bob", "25"]])
        assert "| Name | Age |" in result
        assert "| --- | --- |" in result
        assert "| Alice | 30 |" in result
        assert "| Bob | 25 |" in result

    def test_empty_rows(self):
        result = markdown_table(["Name"], [])
        assert result == "_No data found._"

    def test_none_values(self):
        result = markdown_table(["A", "B"], [[None, "val"]])
        assert "—" in result
        assert "val" in result

    def test_short_row_padded(self):
        result = markdown_table(["A", "B", "C"], [["x"]])
        # Short row should be padded with —
        assert result.count("—") >= 2

    def test_long_row_truncated(self):
        result = markdown_table(["A"], [["x", "extra", "more"]])
        # Only first column should appear in data row
        lines = result.strip().split("\n")
        data_line = lines[2]  # header, separator, then data
        assert "extra" not in data_line


class TestDashboardLink:
    def test_known_entity(self):
        link = dashboard_link("dataset", "abc-123")
        assert "[" in link
        assert "dashboard/develop/abc-123" in link

    def test_custom_label(self):
        link = dashboard_link("evaluation", "id-1", label="My Eval")
        assert "[My Eval]" in link
        assert "evaluations/id-1" in link

    def test_unknown_entity_uses_type_as_path(self):
        link = dashboard_link("custom_thing", "id-2")
        assert "dashboard/get-started" in link

    def test_default_label_shows_short_id(self):
        link = dashboard_link("dataset", "abcdefgh-1234")
        assert "abcdefgh" in link


class TestFormatDatetime:
    def test_none_returns_dash(self):
        assert format_datetime(None) == "—"

    def test_just_now(self):
        dt = datetime.now(timezone.utc) - timedelta(seconds=10)
        assert format_datetime(dt) == "just now"

    def test_minutes_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert "5m ago" == format_datetime(dt)

    def test_hours_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=3)
        assert "3h ago" == format_datetime(dt)

    def test_days_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(days=2)
        assert "2d ago" == format_datetime(dt)

    def test_old_date_shows_absolute(self):
        dt = datetime(2020, 1, 15, 10, 30, tzinfo=timezone.utc)
        result = format_datetime(dt)
        assert "2020-01-15" in result

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime.now() - timedelta(seconds=30)
        result = format_datetime(dt)
        assert result == "just now"

    def test_future_date_shows_absolute(self):
        dt = datetime.now(timezone.utc) + timedelta(days=10)
        result = format_datetime(dt)
        assert "UTC" in result


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello") == "hello"

    def test_none_returns_dash(self):
        assert truncate(None) == "—"

    def test_long_text_truncated(self):
        text = "x" * 600
        result = truncate(text, max_len=100)
        assert len(result) < 600
        assert "truncated" in result
        assert "600 chars" in result

    def test_exact_boundary(self):
        text = "x" * 500
        assert truncate(text, max_len=500) == text

    def test_non_string_converted(self):
        assert truncate(42) == "42"
        assert truncate({"key": "val"}) == "{'key': 'val'}"


class TestFormatUuid:
    def test_valid_uuid(self):
        result = format_uuid("abcdef12-3456-7890-abcd-ef1234567890")
        assert "`abcdef12-3456-7890-abcd-ef1234567890`" == result

    def test_none_returns_dash(self):
        assert format_uuid(None) == "—"


class TestFormatNumber:
    def test_integer(self):
        assert format_number(42) == "42.00"

    def test_float(self):
        assert format_number(3.14159, decimals=3) == "3.142"

    def test_none_returns_dash(self):
        assert format_number(None) == "—"

    def test_invalid_returns_string(self):
        assert format_number("not_a_number") == "not_a_number"

    def test_zero(self):
        assert format_number(0) == "0.00"


class TestFormatStatus:
    def test_known_statuses(self):
        assert format_status("completed") == "completed"
        assert format_status("failed") == "FAILED"
        assert format_status("pending") == "pending"
        assert format_status("processing") == "processing..."

    def test_case_insensitive(self):
        assert format_status("COMPLETED") == "completed"
        assert format_status("Failed") == "FAILED"

    def test_unknown_status_passthrough(self):
        assert format_status("custom_status") == "custom_status"

    def test_none_returns_dash(self):
        assert format_status(None) == "—"


class TestSection:
    def test_basic_section(self):
        result = section("Title", "Body content")
        assert result == "## Title\n\nBody content"


class TestKeyValueBlock:
    def test_basic_pairs(self):
        result = key_value_block([("Name", "Alice"), ("Age", "30")])
        assert "**Name:** Alice" in result
        assert "**Age:** 30" in result

    def test_none_values_skipped(self):
        result = key_value_block([("Name", "Alice"), ("Missing", None)])
        assert "Name" in result
        assert "Missing" not in result

    def test_empty_pairs(self):
        result = key_value_block([])
        assert result == ""
