import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from tracer.socket import GraphDataConsumer


def _ordered_series(data):
    return [
        (series["name"], [point["value"] for point in series["value"]])
        for series in data
    ]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "eval_id", ["", "config-1"], ids=["all-evaluations", "selected-evaluation"]
)
@pytest.mark.parametrize(
    ("first", "last", "name", "expected"),
    [
        (
            {"yes": {"score": 75}, "no": {"score": 25}},
            {"yes": {"score": 40}, "no": {"score": 60}},
            "Preference",
            [("yes", [75, 0, 40]), ("no", [25, 0, 60])],
        ),
        ({"score": 75}, {"score": 40}, "Quality", [("Quality", [75, 0, 40])]),
    ],
    ids=["choice", "numeric"],
)
async def test_evaluation_keeps_values_across_time_buckets(
    eval_id, first, last, name, expected
):
    consumer = GraphDataConsumer()
    consumer.project_id = "project-1"
    consumer.interval = "hour"
    consumer.graph = "charts"
    consumer.eval_id = eval_id
    consumer.property = ""
    consumer.filters = [
        {
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2026-01-01T10:00:00.000Z",
                    "2026-01-01T12:00:00.000Z",
                ],
            }
        }
    ]
    rows = [
        (
            datetime(2026, 1, 1, 10),
            "config-1",
            json.dumps({"metric_config-1": first}),
            name,
        ),
        (
            datetime(2026, 1, 1, 12),
            "config-1",
            json.dumps({"metric_config-1": last}),
            name,
        ),
    ]
    if eval_id:
        rows.insert(
            1,
            (
                datetime(2026, 1, 1, 11),
                "config-2",
                json.dumps({"metric_config-2": {"score": 50}}),
                "Other",
            ),
        )
    consumer.fetch_raw_data = AsyncMock(
        side_effect=[rows, [(name,)]] if eval_id else None,
        return_value=rows if not eval_id else None,
    )
    consumer.send_json = AsyncMock()

    await consumer.send_evaluation_data()

    result = consumer.send_json.await_args.args[0]["data"]
    assert list(result) == ["config-1"]
    assert _ordered_series(result["config-1"]["data"]) == expected


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ({"yes": {"score": 75}, "no": {"score": 25}}, [("yes", [75]), ("no", [25])]),
        ({"score": 75}, [("Quality", [75])]),
    ],
    ids=["single-choice-bucket", "numeric-bucket"],
)
@pytest.mark.parametrize(
    "eval_id", ["", "config-1"], ids=["all-evaluations", "selected-evaluation"]
)
async def test_evaluation_single_bucket_output(metric, expected, eval_id):
    consumer = GraphDataConsumer()
    consumer.project_id = "project-1"
    consumer.interval = "hour"
    consumer.graph = "charts"
    consumer.eval_id = eval_id
    consumer.property = ""
    consumer.filters = []
    rows = [
        (
            datetime(2026, 1, 1, 10),
            "config-1",
            json.dumps({"metric_config-1": metric}),
            "Quality",
        )
    ]
    consumer.fetch_raw_data = AsyncMock(
        side_effect=[rows, [("Quality",)]] if eval_id else None,
        return_value=rows if not eval_id else None,
    )
    consumer.send_json = AsyncMock()

    await consumer.send_evaluation_data()

    data = consumer.send_json.await_args.args[0]["data"]["config-1"]["data"]
    assert _ordered_series(data) == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_selected_evaluation_without_rows_keeps_empty_series():
    consumer = GraphDataConsumer()
    consumer.project_id = "project-1"
    consumer.interval = "hour"
    consumer.graph = "charts"
    consumer.eval_id = "config-1"
    consumer.property = ""
    consumer.filters = []
    consumer.fetch_raw_data = AsyncMock(side_effect=[[], [("Quality",)]])
    consumer.send_json = AsyncMock()

    await consumer.send_evaluation_data()

    assert consumer.send_json.await_args.args[0]["data"] == {
        "config-1": {"name": "", "data": [{"name": "Quality", "value": []}]}
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_selected_evaluation_without_a_stored_name_keeps_empty_series():
    consumer = GraphDataConsumer()
    consumer.project_id = "project-1"
    consumer.interval = "hour"
    consumer.graph = "charts"
    consumer.eval_id = "config-1"
    consumer.property = ""
    consumer.filters = []
    consumer.fetch_raw_data = AsyncMock(side_effect=[[], []])
    consumer.send_json = AsyncMock()

    await consumer.send_evaluation_data()

    assert consumer.send_json.await_args.args[0]["data"] == {
        "config-1": {"name": "", "data": [{"name": "", "value": []}]}
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_selected_evaluation_with_only_other_rows_keeps_empty_series():
    consumer = GraphDataConsumer()
    consumer.project_id = "project-1"
    consumer.interval = "hour"
    consumer.graph = "charts"
    consumer.eval_id = "config-1"
    consumer.property = ""
    consumer.filters = []
    other_rows = [
        (
            datetime(2026, 1, 1, 10),
            "config-2",
            json.dumps({"metric_config-2": {"score": 50}}),
            "Other",
        )
    ]
    consumer.fetch_raw_data = AsyncMock(side_effect=[other_rows, [("Quality",)]])
    consumer.send_json = AsyncMock()

    await consumer.send_evaluation_data()

    assert consumer.send_json.await_args.args[0]["data"] == {
        "config-1": {"name": "", "data": [{"name": "Quality", "value": []}]}
    }
