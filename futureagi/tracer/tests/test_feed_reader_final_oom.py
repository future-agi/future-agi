"""Feed-path CH span reads must not OOM under FINAL.

The feed detail / list / trends / traces surface fans out into several
``FROM spans FINAL WHERE <trace_id | id | session> = X`` rollup reads whose only
prefilter is a bloom skip index — which CH 25.3 disables under FINAL by default.
Without ``use_skip_indexes_if_final`` each degrades to a full in-order merge over
every part (seconds each, run serially) and OOMs (code 241) on a tenant with
volume.

Mirrors ``test_span_reader_point_read_oom.py``: a real 9 GiB OOM can't run in
test-CH, so these pin the two levers that prevent the unpruned merge —
(A) the skip-index setting is on and the ``is_deleted = 0`` predicate (which,
alongside the setting, arms the ``is_deleted`` minmax resurrection bug; the 2-arg
ReplacingMergeTree drops deleted rows under FINAL anyway) is off; and
(B) where the caller knows the tenant, a ``project_id`` predicate is threaded so
the primary-key prefix prunes too. A revert of either fails here, not as a prod
OOM.
"""

from tracer.services.clickhouse.v2.span_reader import CHSpanReader


class _RecordingClient:
    """Captures the SQL + settings of the last query; returns no rows."""

    def __init__(self):
        self.sql = None
        self.params = None
        self.settings = None

    def query(self, sql, parameters=None, settings=None):
        self.sql = sql
        self.params = parameters or {}
        self.settings = settings or {}

        class _Result:
            result_rows = []

        return _Result()


def _reader_with(client) -> CHSpanReader:
    # Bypass __init__ (opens a real CH connection) and inject the fake.
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    return reader


def _assert_final_oom_safe(client, key_predicate):
    # Lever A: skip indexes re-enabled under FINAL (else full-table merge -> OOM)
    assert client.settings.get("use_skip_indexes_if_final") == 1
    # Lever A: the is_deleted=0 predicate is dropped (redundant under FINAL;
    # keeping it alongside the setting arms the is_deleted minmax resurrection bug)
    assert "is_deleted = 0" not in client.sql
    # still prunes on the stable bloom-indexed column
    assert key_predicate in client.sql


def test_distinct_end_users_is_final_oom_safe():
    client = _RecordingClient()
    _reader_with(client).distinct_end_users_by_trace_ids(["t1", "t2"])
    _assert_final_oom_safe(client, "trace_id IN %(tids)s")
    # Lever B not requested -> no project predicate
    assert "project_id IN" not in client.sql


def test_distinct_end_users_prunes_by_project():
    client = _RecordingClient()
    _reader_with(client).distinct_end_users_by_trace_ids(["t1"], ["p1"])
    _assert_final_oom_safe(client, "trace_id IN %(tids)s")
    assert "project_id IN %(pids)s" in client.sql
    assert client.params.get("pids") == ("p1",)


def test_trace_session_ids_is_final_oom_safe():
    client = _RecordingClient()
    _reader_with(client).trace_session_ids_by_trace_ids(["t1"])
    _assert_final_oom_safe(client, "trace_id IN %(trace_ids)s")
    assert "project_id IN" not in client.sql


def test_trace_session_ids_prunes_by_project():
    client = _RecordingClient()
    _reader_with(client).trace_session_ids_by_trace_ids(["t1"], ["p1"])
    _assert_final_oom_safe(client, "trace_id IN %(trace_ids)s")
    assert "project_id IN %(project_ids)s" in client.sql


def test_per_trace_root_span_start_times_is_final_oom_safe():
    client = _RecordingClient()
    _reader_with(client).per_trace_root_span_start_times(["t1"])
    _assert_final_oom_safe(client, "trace_id IN %(tids)s")
    assert "project_id IN" not in client.sql


def test_per_trace_root_span_start_times_prunes_by_project():
    client = _RecordingClient()
    _reader_with(client).per_trace_root_span_start_times(["t1"], ["p1"])
    _assert_final_oom_safe(client, "trace_id IN %(tids)s")
    assert "project_id IN %(pids)s" in client.sql


def test_per_trace_aggregate_is_final_oom_safe():
    client = _RecordingClient()
    _reader_with(client).per_trace_aggregate(["t1"])
    _assert_final_oom_safe(client, "trace_id IN %(tids)s")


def test_session_trace_ids_is_final_oom_safe():
    client = _RecordingClient()
    # session_trace_ids is already project-scoped (project_id = %(p)s); it just
    # missed the skip-index setting + carried the is_deleted predicate.
    _reader_with(client).session_trace_ids("p1", "s1")
    _assert_final_oom_safe(client, "spans.project_id = %(p)s")


def test_totals_by_trace_ids_prunes_by_project():
    # totals_by_trace_ids is NON-FINAL (analytics convention) so it keeps
    # is_deleted = 0 and gets no skip-index setting; the only lever here is
    # project-prefix pruning (Lever B) for the Traces-tab per-trace totals.
    client = _RecordingClient()
    _reader_with(client).totals_by_trace_ids(["t1"], ["p1"])
    assert "FINAL" not in client.sql
    # Enforce the exception, don't just comment it: this reader is NON-FINAL so
    # the engine won't drop deleted rows for it — the is_deleted=0 predicate MUST
    # stay and the skip-index setting MUST NOT be added. A "drop is_deleted
    # everywhere" cleanup would otherwise leak deleted spans into the per-trace
    # totals with every other test still green.
    assert "is_deleted = 0" in client.sql
    assert client.settings.get("use_skip_indexes_if_final") is None
    assert "project_id IN %(project_ids)s" in client.sql
    assert client.params.get("project_ids") == ("p1",)
    # unscoped call stays cross-project (backwards compatible)
    client2 = _RecordingClient()
    _reader_with(client2).totals_by_trace_ids(["t1"])
    assert "project_id IN" not in client2.sql


# ── Feed-caller wiring (Lever B delivery) ─────────────────────────────────────
# The reader tests above prove the SQL prunes when a project is passed; these
# prove the feed callers actually PASS it. Without them a revert of the feed.py
# threading (half the fix) leaves the reader tests green while the hot-path reads
# silently go unscoped again. One assertion per caller that owns a project_id.

from unittest.mock import MagicMock, call, patch  # noqa: E402

from tracer.queries import feed  # noqa: E402


def _reader_cm():
    """(context-manager, reader) pair for patching ``feed.get_reader``."""
    reader = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = reader
    cm.__exit__.return_value = False
    return cm, reader


def test_users_affected_in_window_threads_project():
    cm, reader = _reader_cm()
    reader.distinct_end_users_by_trace_ids.return_value = {}
    with patch.object(feed, "get_reader", return_value=cm):
        feed._users_affected_in_window(["t1"], "p1")
    reader.distinct_end_users_by_trace_ids.assert_called_once_with(["t1"], ["p1"])


def test_root_input_texts_threads_project():
    # Overview pattern-summary path (_insight_distinctive_topic): the failing and
    # passing corpora both belong to the cluster's project.
    with patch.object(feed, "_get_root_spans_batch", return_value={}) as roots:
        feed._root_input_texts(["t1", "t2"], "p1")
    roots.assert_called_once_with(["t1", "t2"], "p1")


def test_distribution_shift_threads_project_on_both_reads():
    # Overview pattern-summary path (_insight_distribution_shift): both the
    # failing totals read and the baseline totals read must pin the project.
    with patch.object(feed, "_get_trace_totals_batch", return_value={}) as totals:
        feed._insight_distribution_shift(["t1"], ["b1"], "latency", "p1")
    assert totals.call_args_list == [call(["t1"], "p1"), call(["b1"], "p1")]
