"""Exactness contract of one Observe graph response.

A graph response makes two independent claims: ``query_complete`` says the whole
requested window was answered, and ``query_exact`` says the values equal the
latest physical state. These tests pin the pairing between ``query_exact`` and
the ``query_provenance`` that produced the series, and pin the published
response contract to the same provenance vocabulary the read paths emit.

No database call is made; a read that touches ClickHouse fails the test.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tracer.serializers.filters import ObserveGraphDataResultSerializer
from tracer.services.clickhouse.graph_dispatch import (
    degraded_graph_response,
    fetch_system_metric_graph_ch,
)
from tracer.utils.graph_provenance import (
    BOUNDED_CANDIDATES,
    EMPTY_WINDOW,
    EXACT_SNAPSHOT,
    GRAPH_PROVENANCES,
    INEXACT_GRAPH_PROVENANCES,
    MATERIALIZED_ROLLUP,
    SERVER_READ_POLICY_UNAVAILABLE,
    graph_provenance_is_exact,
    graph_provenance_metadata,
)

PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
INSTANT = datetime(2026, 2, 2, 3, 4, 5, 654321)


class _NoQueryAnalytics:
    """Prove the empty-window branches answer without reading ClickHouse."""

    def __init__(self) -> None:
        self.calls = 0

    def execute_ch_query(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("an empty window must not query ClickHouse")


def _datetime_filter(operator: str, value=...) -> dict:
    config = {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "datetime",
        "filter_op": operator,
    }
    if value is not ...:
        config["filter_value"] = value
    return {"column_id": "created_at", "filter_config": config}


def _graph_result(**overrides) -> dict:
    payload = {
        "metric_name": "latency",
        "data": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }
    payload.update(overrides)
    return payload


@pytest.mark.unit
def test_unfiltered_zero_duration_window_is_exact_and_not_a_rollup_read():
    """A window with no duration reads nothing, so it is exactly empty."""

    analytics = _NoQueryAnalytics()
    instant = f"{INSTANT.isoformat()}Z"

    response = fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_datetime_filter("between", [instant, instant])],
        interval="day",
        metric_id="latency",
    )

    assert analytics.calls == 0
    assert response["query_complete"] is True
    assert response["query_status"] == "complete"
    assert response["query_count"] == 0
    assert response["query_provenance"] == EMPTY_WINDOW
    assert response["query_exact"] is True


@pytest.mark.unit
def test_filtered_zero_duration_window_does_not_claim_a_snapshot_it_never_read(
    monkeypatch,
):
    """The filtered path answered from the window itself, not from a snapshot."""

    from tracer.services.clickhouse import graph_dispatch

    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        lambda namespace, identity, **options: pytest.fail(
            "an empty window must not schedule an exact refresh"
        ),
    )
    analytics = _NoQueryAnalytics()

    response = fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_datetime_filter("is_null")],
        interval="day",
        metric_id="latency",
    )

    assert analytics.calls == 0
    assert response["query_complete"] is True
    assert response["query_count"] == 0
    assert response["query_provenance"] == EMPTY_WINDOW
    assert response["query_exact"] is True


@pytest.mark.unit
@pytest.mark.parametrize("provenance", sorted(INEXACT_GRAPH_PROVENANCES))
def test_live_read_provenances_never_declare_exactness(provenance):
    assert graph_provenance_is_exact(provenance) is False
    assert graph_provenance_metadata(provenance) == {
        "query_provenance": provenance,
        "query_exact": False,
    }


@pytest.mark.unit
def test_exact_snapshot_is_the_only_read_that_declares_exactness():
    assert graph_provenance_metadata(EXACT_SNAPSHOT) == {
        "query_provenance": EXACT_SNAPSHOT,
        "query_exact": True,
    }


@pytest.mark.unit
def test_unknown_provenance_is_rejected_instead_of_labelled_inexact():
    with pytest.raises(ValueError):
        graph_provenance_metadata("physical_latest_users")


@pytest.mark.unit
def test_response_contract_declares_every_provenance_the_reads_emit():
    field = ObserveGraphDataResultSerializer().fields["query_provenance"]

    assert set(field.choices) == set(GRAPH_PROVENANCES)
    assert SERVER_READ_POLICY_UNAVAILABLE in field.choices
    assert BOUNDED_CANDIDATES in field.choices


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provenance", "query_exact"),
    [
        (BOUNDED_CANDIDATES, True),
        (MATERIALIZED_ROLLUP, True),
        (EXACT_SNAPSHOT, False),
        (EMPTY_WINDOW, False),
    ],
)
def test_complete_graph_result_rejects_exactness_that_contradicts_provenance(
    provenance,
    query_exact,
):
    serializer = ObserveGraphDataResultSerializer(
        data=_graph_result(query_provenance=provenance, query_exact=query_exact)
    )

    assert serializer.is_valid() is False
    assert "query_exact" in serializer.errors


@pytest.mark.unit
@pytest.mark.parametrize("provenance", sorted(GRAPH_PROVENANCES))
def test_complete_graph_result_accepts_the_exactness_its_provenance_proves(
    provenance,
):
    serializer = ObserveGraphDataResultSerializer(
        data=_graph_result(**graph_provenance_metadata(provenance))
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.unit
def test_degraded_graph_read_publishes_nothing_and_claims_nothing():
    response = degraded_graph_response(
        "latency",
        ValueError("boom"),
        provenance=EXACT_SNAPSHOT,
    )

    assert response["data"] == []
    assert response["query_complete"] is False
    assert response["query_status"] == "degraded"
    assert response["query_exact"] is False
    assert ObserveGraphDataResultSerializer(data=response).is_valid()
