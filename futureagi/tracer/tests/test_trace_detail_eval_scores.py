from tracer.services.eval_tiles import build_eval_rollup


def test_detail_rollup_is_latest_completed_per_span_but_root_aggregates_spans():
    rows = [
        {
            "span_id": "root",
            "span_name": "Root",
            "eval_config_id": "config-1",
            "created_at": "2026-01-01T00:00:00Z",
            "log_id": "first",
            "output_float": 0.25,
            "status": "completed",
            "target_type": "trace",
        },
        {
            "span_id": "root",
            "span_name": "Root",
            "eval_config_id": "config-1",
            "created_at": "2026-01-02T00:00:00Z",
            "log_id": "second",
            "output_float": 0.75,
            "status": "completed",
            "target_type": "trace",
            "eval_explanation": "latest root",
        },
        {
            "span_id": "child",
            "span_name": "Child",
            "eval_config_id": "config-1",
            "created_at": "2026-01-03T00:00:00Z",
            "log_id": "child",
            "output_float": 0.5,
            "status": None,
            "target_type": "span",
        },
    ]

    rollups = build_eval_rollup(
        rows,
        {"config-1": {"name": "Quality", "output_type": "score"}},
        root_span_id="root",
    )

    assert rollups["root"]["scope"] == "trace"
    assert rollups["root"]["evals"][0]["aggregate"] == 62.5
    assert [row["span_id"] for row in rollups["root"]["evals"][0]["spans"]] == [
        "root",
        "child",
    ]
    assert rollups["root"]["evals"][0]["spans"][0]["explanation"] == "latest root"
    assert rollups["child"]["evals"][0]["aggregate"] == 50.0
