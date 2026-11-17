"""A Langfuse connection's totals count each imported trace, span and score once.

Every sync after the first reads from five minutes before the previous sync
ended, so a trace in that overlap is read again and rewritten in place. The
connection's totals must not grow when that happens: a re-read stores no new
row. The traces and mocked servers are the ones in test_langfuse_newer_reads.py:
three traces with 8 observations and 5 scores, served by a Langfuse 3.x server
(the v1 reads) and by a Langfuse v4 server in events_only mode (the newer
reads). Each test syncs twice and states which traces each listing names.
"""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from integrations.models import SyncLog
from integrations.temporal.activities import sync_integration_connection
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace

from .test_langfuse_newer_reads import (
    RAG,
    RECENT,
    REQUEST,
    SUPPORT,
    TRACE_BY_ID,
    WINDOW_ROWS,
    Langfuse3,
    Langfuse4,
    _response,
    _v1_trace_summary,
)

ALL_THREE = (SUPPORT, RAG, RECENT)


class Langfuse3Listing(Langfuse3):
    """A Langfuse 3.x server whose trace list names only the given traces."""

    def __init__(self, trace_ids):
        super().__init__()
        summaries = [_v1_trace_summary(TRACE_BY_ID[trace_id]) for trace_id in trace_ids]
        self.listing = {
            "data": summaries,
            "meta": {
                "page": 1,
                "limit": 100,
                "totalItems": len(summaries),
                "totalPages": 1,
            },
        }

    def __call__(self, method, url, params=None, **kwargs):
        if urlsplit(url).path == "/api/public/traces":
            self.calls.append(("/api/public/traces", dict(params or {})))
            return _response(200, self.listing)
        return super().__call__(method, url, params=params, **kwargs)


def _langfuse_3(trace_ids):
    return Langfuse3Listing(trace_ids)


def _langfuse_4(trace_ids):
    rows = [row for row in WINDOW_ROWS if row["traceId"] in trace_ids]
    return Langfuse4(list_pages={None: {"data": rows, "meta": {}}})


SERVERS = pytest.mark.parametrize(
    "server", [_langfuse_3, _langfuse_4], ids=["langfuse-3", "langfuse-4-events-only"]
)


def _sync(connection, server):
    with patch(REQUEST, side_effect=server):
        sync_integration_connection(str(connection.id))


def _totals(connection):
    connection.refresh_from_db()
    return (
        connection.total_traces_synced,
        connection.total_spans_synced,
        connection.total_scores_synced,
    )


def _stored(project):
    """Traces, observations (not the synthetic roots) and scores stored."""
    spans = ObservationSpan.no_workspace_objects.filter(trace__project=project)
    return (
        Trace.no_workspace_objects.filter(project=project).count(),
        spans.exclude(id__startswith="root-").count(),
        EvalLogger.no_workspace_objects.filter(trace__project=project).count(),
    )


def _logs(connection):
    return list(SyncLog.objects.filter(connection=connection).order_by("started_at"))


@pytest.mark.django_db(transaction=True)
class TestImportTotalsCountEachTraceOnce:
    @SERVERS
    @patch("time.sleep")
    def test_an_overlapping_sync_that_reads_the_same_traces_adds_nothing(
        self, mock_sleep, server, integration_connection
    ):
        _sync(integration_connection, server(ALL_THREE))
        assert _totals(integration_connection) == (3, 8, 5)

        # The next window starts five minutes before the first one ended, and
        # the server lists the same three traces again.
        _sync(integration_connection, server(ALL_THREE))

        first, second = _logs(integration_connection)
        assert second.sync_from == first.sync_to - timedelta(minutes=5)
        # The log says what this sync did: three traces rewritten in place,
        # with their 8 spans and 5 scores.
        assert (second.traces_fetched, second.traces_created) == (3, 0)
        assert second.traces_updated == 3
        assert (second.spans_synced, second.scores_synced) == (8, 5)
        assert _stored(integration_connection.project) == (3, 8, 5)
        assert _totals(integration_connection) == (3, 8, 5)

    @SERVERS
    @patch("time.sleep")
    def test_a_sync_that_reads_one_new_trace_adds_exactly_one(
        self, mock_sleep, server, integration_connection
    ):
        _sync(integration_connection, server((SUPPORT, RAG)))
        assert _totals(integration_connection) == (2, 6, 4)

        # The next sync reads both traces again in the overlap and one new
        # trace, with 2 observations and 1 score.
        _sync(integration_connection, server(ALL_THREE))

        first, second = _logs(integration_connection)
        assert second.sync_from == first.sync_to - timedelta(minutes=5)
        assert (second.traces_fetched, second.traces_created) == (3, 1)
        assert second.traces_updated == 2
        assert _stored(integration_connection.project) == (3, 8, 5)
        assert _totals(integration_connection) == (3, 8, 5)
