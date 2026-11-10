"""Performance -> Tag Distribution date window (future-agi#1483).

Good Tags / Bad Tags (get_top_tags_distribution) must count the same
EventDateTime window as All Tags (get_all_tags_distribution) for the date
ranges the Performance page sends, so "Yesterday" counts yesterday only.
"""

import re
from datetime import datetime

import pytest

from model_hub.utils import performance_ch

pytestmark = pytest.mark.unit

_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_WINDOW_CLAUSE = re.compile(r"EventDateTime BETWEEN '([^']+)' AND '([^']+)'")

_ORG_ID = "org-1483"
_MODEL_ID = "model-1483"
_DATASET = {"version": "v1", "environment": "Production", "metric_id": "metric-1483"}

# Ranges the Performance page sends (Performance.jsx handleDataOptionChange
# presets, plus an inclusive-local custom range from DatePicker.jsx).
YESTERDAY = ("2026-11-09 00:00:00", "2026-11-10 00:00:00")
TODAY = ("2026-11-10 00:00:00", "2026-11-11 00:00:00")
SEVEN_DAYS = ("2026-11-03 09:30:00", "2026-11-11 00:00:00")
CUSTOM = ("2026-11-01 00:00:00", "2026-11-05 00:00:00")


@pytest.fixture
def captured_queries(monkeypatch):
    queries = []

    class _RecordingClickHouseClient:
        def execute_read(self, query, *args, **kwargs):
            queries.append(query)
            return []

    monkeypatch.setattr(
        performance_ch, "ClickHouseClientSingleton", _RecordingClickHouseClient
    )
    return queries


def _all_tags_query(queries, start_date, end_date, tag_type):
    performance_ch.get_all_tags_distribution(
        _ORG_ID,
        _MODEL_ID,
        dict(_DATASET),
        [],
        "daily",
        start_date,
        end_date,
        tag_type,
    )
    return queries[-1]


def _top_tags_query(queries, start_date, end_date, tag_type):
    performance_ch.get_top_tags_distribution(
        _ORG_ID,
        _MODEL_ID,
        dict(_DATASET),
        [],
        start_date,
        end_date,
        tag_type,
    )
    return queries[-1]


def _effective_window(query):
    """The EventDateTime range a tag query counts.

    Every EventDateTime BETWEEN clause in these queries is AND-ed into the
    one WHERE over events, so the rows counted fall in their intersection.
    """
    clauses = _WINDOW_CLAUSE.findall(query)
    assert clauses, "tag query has no EventDateTime window"
    starts = [datetime.strptime(start, _DATE_FORMAT) for start, _ in clauses]
    ends = [datetime.strptime(end, _DATE_FORMAT) for _, end in clauses]
    return max(starts), min(ends)


def _counts_event_at(query, event_time):
    window_start, window_end = _effective_window(query)
    return window_start <= event_time <= window_end


@pytest.mark.parametrize("tag_type", ["good", "bad"])
def test_yesterday_top_tags_count_yesterday_only(captured_queries, tag_type):
    start_date, end_date = YESTERDAY
    top_query = _top_tags_query(captured_queries, start_date, end_date, tag_type)

    assert _counts_event_at(top_query, datetime(2026, 11, 9, 12, 0, 0))
    assert not _counts_event_at(top_query, datetime(2026, 11, 10, 12, 0, 0))


@pytest.mark.parametrize(
    "window",
    [YESTERDAY, TODAY, SEVEN_DAYS, CUSTOM],
    ids=["yesterday", "today", "7d", "custom"],
)
@pytest.mark.parametrize("tag_type", ["good", "bad"])
def test_top_tags_window_matches_all_tags(captured_queries, window, tag_type):
    start_date, end_date = window
    all_query = _all_tags_query(captured_queries, start_date, end_date, tag_type)
    top_query = _top_tags_query(captured_queries, start_date, end_date, tag_type)

    selected = (
        datetime.strptime(start_date, _DATE_FORMAT),
        datetime.strptime(end_date, _DATE_FORMAT),
    )
    assert _effective_window(all_query) == selected
    assert _effective_window(top_query) == _effective_window(all_query)
