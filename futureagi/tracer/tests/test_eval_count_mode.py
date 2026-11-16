from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder


def test_observe_span_count_mode_keeps_tuple_identity_and_zero_values():
    rows = [
        {
            "trace_id": "trace-1",
            "observation_span_id": "span-1",
            "eval_config_id": "numeric",
            "success_count": 1,
            "error_count": 0,
            "avg_score": 0.0,
        },
        {
            "trace_id": "trace-1",
            "observation_span_id": "span-1",
            "eval_config_id": "choices",
            "success_count": 2,
            "error_count": 0,
            "str_lists": [["declared", "unknown", "unknown"], ["unknown"]],
        },
    ]

    cells = SpanListQueryBuilder.pivot_eval_results(
        rows,
        key_by_trace=True,
        count_mode=True,
        output_types={"numeric": "score", "choices": "choices"},
        declared_choices={"choices": ["declared", "empty"]},
    )

    assert cells[("trace-1", "span-1")]["numeric"] == 0.0
    assert cells[("trace-1", "span-1")]["choices"] == {
        "declared": 1,
        "empty": 0,
        "unknown": 2,
    }


def test_observe_span_count_mode_preserves_error_and_lifecycle_markers():
    rows = [
        {
            "trace_id": "trace-1",
            "observation_span_id": "span-1",
            "eval_config_id": "errored",
            "success_count": 0,
            "error_count": 1,
        },
        {
            "trace_id": "trace-1",
            "observation_span_id": "span-1",
            "eval_config_id": "queued",
            "success_count": 0,
            "error_count": 0,
            "pending_count": 1,
        },
    ]

    cells = SpanListQueryBuilder.pivot_eval_results(
        rows,
        key_by_trace=True,
        count_mode=True,
        output_types={"errored": "score", "queued": "score"},
    )

    assert cells[("trace-1", "span-1")]["errored"] == {"error": True}
    assert cells[("trace-1", "span-1")]["queued"] == {"status": "pending"}
