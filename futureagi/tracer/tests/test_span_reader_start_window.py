"""``list_by_ids`` trace + start-window narrowing (the attachment binder's read).

Under FINAL the window must filter on expressions every version of a row
shares: the partition key ``toDate(start_time)`` and the sorting-key bucket
``toStartOfHour(start_time)``. A raw ``start_time`` range would run before the
merge and could drop the latest version while keeping an older one.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tracer.services.clickhouse.v2.span_reader import CHSpanReader


class _RecordingClient:
    def __init__(self):
        self.sql = self.parameters = self.settings = None

    def query(self, sql, parameters=None, settings=None):
        self.sql, self.parameters, self.settings = sql, parameters, settings

        class _Result:
            result_rows = []

        return _Result()


def _read(**kwargs):
    client = _RecordingClient()
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    reader.list_by_ids(["s1"], project_id="p1", columns=["id"], **kwargs)
    return client


def test_default_read_is_unchanged():
    client = _read()
    assert "trace_id IN" not in client.sql and "start_time" not in client.sql
    assert set(client.parameters) == {"ids", "pid"}


def test_trace_and_window_filter_on_shared_key_expressions():
    low = datetime(2026, 11, 12, 9, 15, 30, 123456, tzinfo=UTC)
    high = low + timedelta(hours=49)
    client = _read(trace_ids=["t2", "t1"], start_window=(low, high))
    where = client.sql.split(" WHERE ", 1)[1]
    assert "trace_id IN %(tids)s" in where
    assert "toDate(start_time) BETWEEN" in where
    assert "toStartOfHour(start_time)" in where
    # Never a raw start_time comparison: only the two key expressions.
    assert where.count("start_time") == 2
    assert client.parameters["tids"] == ("t2", "t1")
    assert client.parameters["start_lo"] == "2026-11-12 09:15:30.123456"
    assert client.parameters["start_hi"] == "2026-11-14 10:15:30.123456"
    assert " FROM spans FINAL " in client.sql
    assert client.settings == {"use_skip_indexes_if_final": 1}


def test_window_bounds_are_converted_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    low = datetime(2026, 11, 12, 9, 0, tzinfo=ist)
    client = _read(start_window=(low, low))
    assert client.parameters["start_lo"] == "2026-11-12 03:30:00.000000"


def test_naive_window_is_refused():
    with pytest.raises(ValueError):
        _read(start_window=(datetime(2026, 1, 1), datetime(2026, 1, 2)))
