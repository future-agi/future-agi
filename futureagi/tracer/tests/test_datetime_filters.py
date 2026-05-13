"""
Tests for datetime filtering functionality.

Tests the apply_created_at_filters function that handles datetime filters
at the database level for querysets.
"""

from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from tracer.utils.filters import apply_created_at_filters
try:
    from ee.usage.models.usage import APICallLog, APICallStatusChoices
except ImportError:
    APICallLog = None
    APICallStatusChoices = None


def _make_datetime_filter(filter_op, filter_value, column_id="created_at"):
    """Helper to build a datetime filter payload."""
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


@pytest.fixture
def api_call_logs(db, organization):
    """Create API call logs with different dates for testing."""
    now = timezone.now()
    logs = []

    # Create logs for different days
    for i in range(5):
        log = APICallLog.objects.create(
            organization=organization,
            source="test",
            source_id="test-source",
            status=APICallStatusChoices.SUCCESS.value,
            config="{}",
            cost=0.001,
        )
        # Update created_at after creation since it's auto_now_add
        APICallLog.objects.filter(pk=log.pk).update(created_at=now - timedelta(days=i))
        log.refresh_from_db()
        logs.append(log)

    return logs


# ---------------------------------------------------------------------------
# Unit tests: filter application and remaining filters
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyCreatedAtFiltersUnit:
    """Unit tests for the apply_created_at_filters function."""

    def test_empty_filters_returns_original_queryset(self, db, organization):
        """Empty filters list should return original queryset unchanged."""
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, [])

        assert remaining == []
        # Queryset should be unchanged (same SQL)
        assert str(filtered_qs.query) == str(qs.query)

    def test_non_datetime_filters_returned_as_remaining(self, db, organization):
        """Non-datetime filters should be returned in remaining list."""
        filters = [
            {
                "column_id": "status",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "success",
                },
            }
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        assert remaining == filters
        assert str(filtered_qs.query) == str(qs.query)

    def test_datetime_filter_removed_from_remaining(self, db, organization):
        """Datetime filters should be applied and removed from remaining."""
        now = timezone.now()
        filters = [
            _make_datetime_filter("equals", now.isoformat()),
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        assert remaining == []

    def test_mixed_filters_separates_correctly(self, db, organization):
        """Mixed filter types should be separated correctly."""
        now = timezone.now()
        text_filter = {
            "column_id": "status",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "success",
            },
        }
        datetime_filter = _make_datetime_filter("equals", now.isoformat())

        filters = [text_filter, datetime_filter]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        assert remaining == [text_filter]

    def test_handles_camel_case_filter_params(self, db, organization):
        """Should handle camelCase parameter names."""
        now = timezone.now()
        filters = [
            {
                "columnId": "created_at",
                "filterConfig": {
                    "filter_type": "datetime",
                    "filter_op": "equals",
                    "filter_value": now.isoformat(),
                },
            }
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        assert remaining == []

    def test_handles_none_filter_config(self, db, organization):
        """Should handle None filter_config gracefully."""
        filters = [
            {
                "column_id": "created_at",
                "filter_config": None,
            }
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        # Filter with None config should pass through as remaining
        assert len(remaining) == 1

    def test_handles_missing_filter_value(self, db, organization):
        """Should handle missing filter_value gracefully."""
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "equals",
                    # filter_value is missing
                },
            }
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        # Filter without value should pass through
        assert len(remaining) == 1

    def test_handles_invalid_datetime_format(self, db, organization):
        """Should handle invalid datetime format gracefully."""
        filters = [
            _make_datetime_filter("equals", "not-a-date"),
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        # Invalid format should pass through as remaining
        assert len(remaining) == 1

    def test_datetime_filter_applied_regardless_of_column_id(self, db, organization):
        """Datetime filters are applied to created_at regardless of column_id."""
        now = timezone.now()
        filters = [
            _make_datetime_filter("equals", now.isoformat(), column_id="column7"),
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        # Should be applied (datetime filters always target created_at at DB level)
        assert remaining == []


# ---------------------------------------------------------------------------
# Integration tests: actual DB filtering
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApplyCreatedAtFiltersIntegration:
    """Integration tests: actual database filtering."""

    def test_equals_filter_matches_same_day(self, api_call_logs, organization):
        """Equals filter should match logs from the same day."""
        target_log = api_call_logs[0]  # Today's log
        filters = [
            _make_datetime_filter("equals", target_log.created_at.isoformat()),
        ]

        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, _ = apply_created_at_filters(qs, filters)

        assert target_log in filtered_qs
        # Should match by date, so all logs from today
        assert (
            filtered_qs.filter(created_at__date=target_log.created_at.date()).count()
            == filtered_qs.count()
        )

    def test_not_equals_filter_excludes_day(self, api_call_logs, organization):
        """Not equals filter should exclude logs from the specified day."""
        target_log = api_call_logs[0]  # Today's log
        filters = [
            _make_datetime_filter("not_equals", target_log.created_at.isoformat()),
        ]

        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, _ = apply_created_at_filters(qs, filters)

        assert target_log not in filtered_qs

    def test_greater_than_filter(self, api_call_logs, organization):
        """Greater than filter should return logs after the specified time."""
        # Get a log from 2 days ago
        target_time = api_call_logs[2].created_at
        filters = [
            _make_datetime_filter("greater_than", target_time.isoformat()),
        ]

        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, _ = apply_created_at_filters(qs, filters)

        # Should include logs from today and yesterday (indices 0 and 1)
        assert api_call_logs[0] in filtered_qs
        assert api_call_logs[1] in filtered_qs
        # Should not include the target or older logs
        assert api_call_logs[2] not in filtered_qs
        assert api_call_logs[3] not in filtered_qs

    def test_less_than_filter(self, api_call_logs, organization):
        """Less than filter should return logs before the specified time."""
        # Get a log from 2 days ago
        target_time = api_call_logs[2].created_at
        filters = [
            _make_datetime_filter("less_than", target_time.isoformat()),
        ]

        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, _ = apply_created_at_filters(qs, filters)

        # Should include logs older than target (indices 3 and 4)
        assert api_call_logs[3] in filtered_qs
        assert api_call_logs[4] in filtered_qs
        # Should not include newer logs
        assert api_call_logs[0] not in filtered_qs
        assert api_call_logs[1] not in filtered_qs

    def test_greater_than_or_equal_filter(self, api_call_logs, organization):
        """Greater than or equal filter should include the boundary."""
        target_time = api_call_logs[2].created_at
        filters = [
            _make_datetime_filter("greater_than_or_equal", target_time.isoformat()),
        ]

        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, _ = apply_created_at_filters(qs, filters)

        # Should include the target log and newer ones
        assert api_call_logs[0] in filtered_qs
        assert api_call_logs[1] in filtered_qs
        assert api_call_logs[2] in filtered_qs
        # Should not include older logs
        assert api_call_logs[3] not in filtered_qs

    def test_less_than_or_equal_filter(self, api_call_logs, organization):
        """Less than or equal filter should include the boundary."""
        target_time = api_call_logs[2].created_at
        filters = [
            _make_datetime_filter("less_than_or_equal", target_time.isoformat()),
        ]

        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, _ = apply_created_at_filters(qs, filters)

        # Should include the target log and older ones
        assert api_call_logs[2] in filtered_qs
        assert api_call_logs[3] in filtered_qs
        assert api_call_logs[4] in filtered_qs
        # Should not include newer logs
        assert api_call_logs[0] not in filtered_qs

    def test_between_filter(self, api_call_logs, organization):
        """Between filter should return logs within the range."""
        start_time = api_call_logs[3].created_at  # 3 days ago
        end_time = api_call_logs[1].created_at  # Yesterday
        filters = [
            _make_datetime_filter(
                "between", [start_time.isoformat(), end_time.isoformat()]
            ),
        ]

        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, _ = apply_created_at_filters(qs, filters)

        # Should include logs within range (indices 1, 2, 3)
        assert api_call_logs[1] in filtered_qs
        assert api_call_logs[2] in filtered_qs
        assert api_call_logs[3] in filtered_qs
        # Should not include logs outside range
        assert api_call_logs[0] not in filtered_qs
        assert api_call_logs[4] not in filtered_qs

    def test_not_between_filter(self, api_call_logs, organization):
        """Not between filter should return logs outside the range."""
        start_time = api_call_logs[3].created_at  # 3 days ago
        end_time = api_call_logs[1].created_at  # Yesterday
        filters = [
            _make_datetime_filter(
                "not_between", [start_time.isoformat(), end_time.isoformat()]
            ),
        ]

        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, _ = apply_created_at_filters(qs, filters)

        # Should include logs outside range (indices 0 and 4)
        assert api_call_logs[0] in filtered_qs
        assert api_call_logs[4] in filtered_qs
        # Should not include logs within range
        assert api_call_logs[1] not in filtered_qs
        assert api_call_logs[2] not in filtered_qs
        assert api_call_logs[3] not in filtered_qs

    def test_multiple_datetime_filters_combined(self, api_call_logs, organization):
        """Multiple datetime filters should be combined (AND logic)."""
        # Greater than 3 days ago AND less than today
        filters = [
            _make_datetime_filter(
                "greater_than", api_call_logs[3].created_at.isoformat()
            ),
            _make_datetime_filter("less_than", api_call_logs[0].created_at.isoformat()),
        ]

        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, _ = apply_created_at_filters(qs, filters)

        # Should only include logs from days 1 and 2
        assert api_call_logs[1] in filtered_qs
        assert api_call_logs[2] in filtered_qs
        # Should not include boundary logs
        assert api_call_logs[0] not in filtered_qs
        assert api_call_logs[3] not in filtered_qs


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyCreatedAtFiltersEdgeCases:
    """Edge case tests for datetime filtering."""

    def test_handles_iso_format_with_timezone(self, db, organization):
        """Should handle ISO format with timezone info."""
        now = timezone.now()
        filters = [
            _make_datetime_filter("equals", now.isoformat()),
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        assert remaining == []

    def test_handles_date_only_format(self, db, organization):
        """Should handle date-only format (YYYY-MM-DD)."""
        today = timezone.now().date().isoformat()
        filters = [
            _make_datetime_filter("equals", today),
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        # Should be applied (parsing via extract_date fallback)
        assert remaining == []

    def test_handles_space_separated_datetime(self, db, organization):
        """Should handle space-separated datetime format."""
        filters = [
            _make_datetime_filter("equals", "2026-02-05 00:00:00"),
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        # Should be applied via extract_date fallback
        assert remaining == []

    def test_between_with_invalid_range_length(self, db, organization):
        """Between filter with wrong number of values should pass through."""
        filters = [
            _make_datetime_filter("between", ["2026-01-01"]),  # Only 1 value
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        # Invalid range should pass through (handled by lambda check)
        assert str(filtered_qs.query) == str(qs.query)

    def test_unknown_operator_passes_through(self, db, organization):
        """Unknown operator should pass through as remaining."""
        filters = [
            _make_datetime_filter("unknown_op", "2026-01-01"),
        ]
        qs = APICallLog.objects.filter(organization=organization)
        filtered_qs, remaining = apply_created_at_filters(qs, filters)

        # Unknown op should pass through
        assert len(remaining) == 1
