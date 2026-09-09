"""Application graph completion must not fail a diagnostic publication wall."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graph
from tracer.services.clickhouse.application_read_policy import (
    application_read_context,
    is_application_read,
)


@pytest.mark.parametrize("days", [7, 30, 365])
def test_completed_application_graph_survives_slow_query_and_publication(
    monkeypatch, days
):
    clock = [0.0]
    monkeypatch.setattr(graph, "monotonic", lambda: clock[0])
    calls = []

    def execute(query, params, **options):
        assert is_application_read()
        calls.append(options)
        clock[0] = graph.EXACT_GRAPH_QUERY_TIMEOUT_MS / 1000 + 60
        return SimpleNamespace(data=[], columns=[])

    end = datetime(2026, 9, 5)
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (end - timedelta(days=days)).isoformat(),
                    end.isoformat(),
                ],
            },
        }
    ]
    with application_read_context():
        result = graph.read_exact_session_system_graph(
            analytics=SimpleNamespace(execute_ch_query=execute),
            project_id="00000000-0000-4000-8000-000000000001",
            filters=filters,
            interval="day",
            metric_id="traffic",
        )
        assert graph._remaining_exact_graph_timeout_ms(0, 1234) == 1234
        with pytest.raises(ValueError):
            graph._remaining_exact_graph_timeout_ms(0, 0)
    assert not is_application_read()
    assert len(calls) == 1
    assert result["query_complete"] is True
    assert result["query_sampled"] is False
    assert result["query_elapsed_ms"] > graph.EXACT_GRAPH_QUERY_TIMEOUT_MS
    # The same elapsed time still invalidates a bounded diagnostic read.
    with pytest.raises(graph.ExactGraphReadError):
        graph._remaining_exact_graph_timeout_ms(0)
    with pytest.raises(graph.ExactGraphReadError):
        graph._metadata(started=0, query_count=1, rows_returned=0)
