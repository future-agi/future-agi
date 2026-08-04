"""Round-trip regression tests for the end-user analytics rollup tasks.

These pin the *number* of external round-trips the two rollup tasks make, which
is the property that regressed: the ClickHouse reader, the session COUNT and the
row write were all inside the per-user loop, so each scaled linearly with the
number of users.

The ORM and the ClickHouse reader are both stubbed, so nothing here needs a
database, a ClickHouse instance, or the Docker test stack. That is deliberate —
a query-count assertion only needs to observe the calls, not execute them.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

MODULE = "tracer.tasks.session"


class _FakeUser:
    """Stands in for an EndUser row."""

    def __init__(self, idx):
        self.id = f"user-{idx}"
        self.project_id = "project-1"
        self.first_seen = None
        self.last_seen = None
        self.total_sessions = None
        self.total_traces = None
        self.total_tokens_used = None
        self.total_cost = None
        self.saved = 0

    def save(self, update_fields=None):
        self.saved += 1


def _fake_queryset(users):
    """A queryset stub that iterates `users` and supports .values("pk")."""
    qs = MagicMock()
    qs.__iter__ = lambda self: iter(users)
    qs.select_related.return_value = qs
    qs.values.return_value = qs
    return qs


def _agg(trace_count=3):
    return {
        "trace_count": trace_count,
        "total_tokens": 100,
        "cost": 1.5,
        "first_seen": None,
        "last_seen": None,
    }


@pytest.fixture
def reader():
    """A ClickHouse reader stub usable as a context manager."""
    r = MagicMock()
    r.aggregate_by_end_user.return_value = _agg()
    r.__enter__ = MagicMock(return_value=r)
    r.__exit__ = MagicMock(return_value=False)
    return r


def _session_counts_queryset(mapping):
    """Stub for the grouped-COUNT query; dict() over it yields `mapping`."""
    qs = MagicMock()
    qs.values_list.return_value = qs
    qs.annotate.return_value = list(mapping.items())
    return qs


class TestUpdateEndUserAnalyticsRoundTrips:
    """update_end_user_analytics_task()"""

    def _run(self, users, reader, ch_stats=None):
        from tracer.tasks.session import update_end_user_analytics_task

        end_user_model = MagicMock()
        end_user_model.objects.filter.return_value = _fake_queryset(users)

        trace_session_model = MagicMock()
        trace_session_model.objects.filter.return_value = _session_counts_queryset(
            {u.id: 7 for u in users}
        )

        with patch.dict(
            "sys.modules",
            {
                "tracer.models.observation_span": MagicMock(EndUser=end_user_model),
                "tracer.models.trace_session": MagicMock(
                    TraceSession=trace_session_model
                ),
                "tracer.services.clickhouse.v2": MagicMock(
                    get_reader=MagicMock(return_value=reader)
                ),
            },
        ) as mods:
            get_reader = mods["tracer.services.clickhouse.v2"].get_reader
            with patch(f"{MODULE}._get_user_stats_from_ch", return_value=ch_stats):
                with patch(f"{MODULE}.close_old_connections"):
                    result = update_end_user_analytics_task()
        return result, end_user_model, trace_session_model, get_reader

    @pytest.mark.parametrize("n_users", [1, 5, 50])
    def test_clickhouse_reader_acquired_once_regardless_of_user_count(
        self, n_users, reader
    ):
        """The regression: get_reader() was called once per user."""
        users = [_FakeUser(i) for i in range(n_users)]
        result, _, _, get_reader = self._run(users, reader)

        assert get_reader.call_count == 1
        # The per-user aggregate itself still runs once per user.
        assert reader.aggregate_by_end_user.call_count == n_users
        assert result["updated_users"] == n_users

    @pytest.mark.parametrize("n_users", [1, 5, 50])
    def test_session_counts_fetched_in_one_query(self, n_users, reader):
        """The per-user TraceSession COUNT collapses to a single grouped query."""
        users = [_FakeUser(i) for i in range(n_users)]
        _, _, trace_session_model, _ = self._run(users, reader)

        assert trace_session_model.objects.filter.call_count == 1

    @pytest.mark.parametrize("n_users", [1, 5, 50])
    def test_rows_written_via_bulk_update_not_per_user_save(self, n_users, reader):
        """Writes are batched; no per-user save() survives on this path."""
        users = [_FakeUser(i) for i in range(n_users)]
        _, end_user_model, _, _ = self._run(users, reader)

        assert all(u.saved == 0 for u in users)
        assert end_user_model.objects.bulk_update.called
        # 50 users at a batch size of 500 is still a single write.
        assert end_user_model.objects.bulk_update.call_count == 1

    def test_bulk_update_writes_the_same_columns_the_saves_did(self, reader):
        """The column list must match the old save(update_fields=...) exactly.

        `updated_at` is auto_now on BaseModel; it was not in update_fields
        before, so it must not appear here either.
        """
        from tracer.tasks.session import _END_USER_ANALYTICS_FIELDS

        users = [_FakeUser(0)]
        _, end_user_model, _, _ = self._run(users, reader)

        _, fields = end_user_model.objects.bulk_update.call_args[0]
        assert fields == _END_USER_ANALYTICS_FIELDS
        assert set(fields) == {
            "total_sessions",
            "total_traces",
            "total_tokens_used",
            "total_cost",
            "first_seen",
            "last_seen",
        }
        assert "updated_at" not in fields

    def test_batches_are_flushed_at_the_configured_size(self, reader):
        """More users than the batch size produces more than one write."""
        from tracer.tasks.session import _ANALYTICS_UPDATE_BATCH_SIZE

        n = _ANALYTICS_UPDATE_BATCH_SIZE + 1
        users = [_FakeUser(i) for i in range(n)]
        _, end_user_model, _, _ = self._run(users, reader)

        assert end_user_model.objects.bulk_update.call_count == 2

    def test_session_count_falls_back_to_zero_for_users_with_no_sessions(self, reader):
        """Absent from the grouped mapping must mean 0, as .count() returned."""
        from tracer.tasks.session import update_end_user_analytics_task

        users = [_FakeUser(0)]
        end_user_model = MagicMock()
        end_user_model.objects.filter.return_value = _fake_queryset(users)
        trace_session_model = MagicMock()
        # Empty mapping — this user has no sessions at all.
        trace_session_model.objects.filter.return_value = _session_counts_queryset({})

        with patch.dict(
            "sys.modules",
            {
                "tracer.models.observation_span": MagicMock(EndUser=end_user_model),
                "tracer.models.trace_session": MagicMock(
                    TraceSession=trace_session_model
                ),
                "tracer.services.clickhouse.v2": MagicMock(
                    get_reader=MagicMock(return_value=reader)
                ),
            },
        ):
            with patch(f"{MODULE}._get_user_stats_from_ch", return_value=None):
                with patch(f"{MODULE}.close_old_connections"):
                    update_end_user_analytics_task()

        assert users[0].total_sessions == 0

    def test_a_failing_user_does_not_abort_the_run(self, reader):
        """Per-user error isolation must survive the refactor."""
        users = [_FakeUser(i) for i in range(3)]
        reader.aggregate_by_end_user.side_effect = [
            _agg(),
            RuntimeError("CH blew up for this user"),
            _agg(),
        ]
        result, _, _, _ = self._run(users, reader)

        assert result["updated_users"] == 2

    def test_short_circuit_path_uses_ch_stats_session_count(self, reader):
        """When the analytics service answers, its session_count wins."""
        users = [_FakeUser(0)]
        ch_stats = {
            "session_count": 42,
            "total_tokens": 10,
            "total_cost": 2,
            "first_seen": None,
            "last_seen": None,
        }
        self._run(users, reader, ch_stats=ch_stats)

        assert users[0].total_sessions == 42
        assert users[0].total_cost == Decimal("2")


class TestRecalculateProjectUserAnalyticsRoundTrips:
    """recalculate_project_user_analytics_task()"""

    def _run(self, users, reader):
        from tracer.tasks.session import recalculate_project_user_analytics_task

        end_user_model = MagicMock()
        end_user_model.objects.filter.return_value = _fake_queryset(users)
        trace_session_model = MagicMock()
        trace_session_model.objects.filter.return_value = _session_counts_queryset(
            {u.id: 3 for u in users}
        )

        with patch.dict(
            "sys.modules",
            {
                "tracer.models.observation_span": MagicMock(EndUser=end_user_model),
                "tracer.models.trace_session": MagicMock(
                    TraceSession=trace_session_model
                ),
                "tracer.services.clickhouse.v2": MagicMock(
                    get_reader=MagicMock(return_value=reader)
                ),
            },
        ) as mods:
            get_reader = mods["tracer.services.clickhouse.v2"].get_reader
            with patch(f"{MODULE}._get_user_stats_from_ch", return_value=None):
                with patch(f"{MODULE}.close_old_connections"):
                    with patch(f"{MODULE}.transaction.atomic"):
                        result = recalculate_project_user_analytics_task("project-1")
        return result, trace_session_model, get_reader

    @pytest.mark.parametrize("n_users", [1, 5, 50])
    def test_clickhouse_reader_acquired_once(self, n_users, reader):
        users = [_FakeUser(i) for i in range(n_users)]
        result, _, get_reader = self._run(users, reader)

        assert get_reader.call_count == 1
        assert result["updated_users"] == n_users

    @pytest.mark.parametrize("n_users", [1, 5, 50])
    def test_session_counts_fetched_in_one_query(self, n_users, reader):
        users = [_FakeUser(i) for i in range(n_users)]
        _, trace_session_model, _ = self._run(users, reader)

        assert trace_session_model.objects.filter.call_count == 1

    def test_per_user_save_is_retained(self, reader):
        """This task's save() has no update_fields, so it also writes
        updated_at (auto_now). It is deliberately NOT converted to
        bulk_update, which would silently stop maintaining that column.
        """
        users = [_FakeUser(i) for i in range(4)]
        self._run(users, reader)

        assert all(u.saved == 1 for u in users)
