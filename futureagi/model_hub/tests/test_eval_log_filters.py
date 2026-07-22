from datetime import datetime, timezone

from model_hub.views.separate_evals import apply_filters


def _filter(column_id, filter_type, filter_op, filter_value=None):
    config = {
        "filter_type": filter_type,
        "filter_op": filter_op,
    }
    if filter_value is not None:
        config["filter_value"] = filter_value
    return {"column_id": column_id, "filter_config": config}


def _row(value):
    return {"metric": {"cell_value": value}}


def test_eval_log_text_in_and_not_in_use_list_values():
    rows = [_row("alpha"), _row("beta"), _row("gamma")]

    assert apply_filters(
        rows, [_filter("metric", "text", "in", ["alpha", "beta"])]
    ) == [
        rows[0],
        rows[1],
    ]
    assert apply_filters(rows, [_filter("metric", "text", "not_in", ["alpha"])]) == [
        rows[1],
        rows[2],
    ]


def test_eval_log_boolean_not_equals_and_null_filters():
    rows = [_row("passed"), _row("failed"), _row(None), {}]

    assert apply_filters(
        rows, [_filter("metric", "boolean", "not_equals", "passed")]
    ) == [rows[1]]
    assert apply_filters(rows, [_filter("metric", "boolean", "is_null")]) == [
        rows[2],
        rows[3],
    ]
    assert apply_filters(rows, [_filter("metric", "boolean", "is_not_null")]) == [
        rows[0],
        rows[1],
    ]


def test_eval_log_datetime_between_and_not_between():
    rows = [
        _row(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        _row("2026-01-15T00:00:00Z"),
        _row("2026-02-01T00:00:00Z"),
    ]

    assert apply_filters(
        rows,
        [
            _filter(
                "metric",
                "datetime",
                "between",
                ["2026-01-01T00:00:00Z", "2026-01-31T23:59:59Z"],
            )
        ],
    ) == [rows[0], rows[1]]
    assert apply_filters(
        rows,
        [
            _filter(
                "metric",
                "datetime",
                "not_between",
                ["2026-01-01T00:00:00Z", "2026-01-31T23:59:59Z"],
            )
        ],
    ) == [rows[2]]
