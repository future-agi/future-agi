from datetime import UTC, datetime

from tracer.services.filter_attestation import (
    FILTER_ATTESTATION_VERSION,
    applied_filter_attestation,
    graph_execution_filters,
    graph_query_evidence,
)


def _leaf(column_id, value, *, filter_type="number", op="equals"):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": op,
            "filter_value": value,
        },
    }


def _window(start, end):
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start, end],
        },
    }


def test_filter_attestation_is_order_independent_and_normalizes_integral_numbers():
    first = _leaf("first", 1)
    second = _leaf("second", [2, 3.5], op="in")

    left = applied_filter_attestation(
        project_id="project-1",
        observe_type="trace",
        filters=[first, second],
    )
    right = applied_filter_attestation(
        project_id="project-1",
        observe_type="trace",
        filters=[_leaf("second", [2.0, 3.5], op="in"), _leaf("first", 1.0)],
    )

    assert left == right
    assert left["query_applied_filter_version"] == FILTER_ATTESTATION_VERSION
    assert left["query_applied_filter_count"] == 2
    assert len(left["query_applied_filter_sha256"]) == 64


def test_positive_window_is_published_separately_while_complement_is_attested():
    start = "2026-01-01T00:00:00Z"
    end = "2026-02-01T00:00:00Z"
    complement = _leaf(
        "created_at",
        "2026-01-15T00:00:00Z",
        filter_type="datetime",
        op="not_equals",
    )

    evidence = graph_query_evidence(
        project_id="project-1",
        observe_type="span",
        filters=[_window(start, end), complement],
    )

    assert evidence["query_window_start"] == start
    assert evidence["query_window_end"] == end
    assert evidence["query_applied_filter_count"] == 1


def test_graph_window_is_normalized_to_exact_utc_instants():
    execution_filters = graph_execution_filters(
        [
            _window(
                "2026-01-01T02:00:00+02:00",
                "2026-02-01T02:00:00+02:00",
            )
        ]
    )
    evidence = graph_query_evidence(
        project_id="project-1",
        observe_type="session",
        filters=execution_filters,
    )

    assert evidence["query_window_start"] == datetime(
        2026, 1, 1, tzinfo=UTC
    ).isoformat().replace("+00:00", "Z")
    assert evidence["query_window_end"] == datetime(
        2026, 2, 1, tzinfo=UTC
    ).isoformat().replace("+00:00", "Z")
    assert evidence["query_applied_filter_count"] == 0
    assert execution_filters[0]["filter_config"]["filter_value"] == [
        evidence["query_window_start"],
        evidence["query_window_end"],
    ]
