import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from tracer.socket import GraphDataConsumer


@pytest.mark.unit
@pytest.mark.asyncio
async def test_choice_evaluation_keeps_values_across_time_buckets():
    consumer = GraphDataConsumer()
    consumer.project_id = "project-1"
    consumer.interval = "hour"
    consumer.graph = "charts"
    consumer.eval_id = ""
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
    consumer.fetch_raw_data = AsyncMock(
        return_value=[
            (
                datetime(2026, 1, 1, 10),
                "config-1",
                json.dumps(
                    {"metric_config-1": {"yes": {"score": 75}, "no": {"score": 25}}}
                ),
                "Preference",
            ),
            (
                datetime(2026, 1, 1, 12),
                "config-1",
                json.dumps(
                    {"metric_config-1": {"yes": {"score": 40}, "no": {"score": 60}}}
                ),
                "Preference",
            ),
        ]
    )
    consumer.send_json = AsyncMock()

    await consumer.send_evaluation_data()

    data = consumer.send_json.await_args.args[0]["data"]["config-1"]["data"]
    assert {
        series["name"]: [point["value"] for point in series["value"]] for series in data
    } == {
        "yes": [75, 0, 40],
        "no": [25, 0, 60],
    }


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ({"yes": {"score": 75}, "no": {"score": 25}}, {"yes": [75], "no": [25]}),
        ({"score": 75}, {"Quality": [75]}),
    ],
    ids=["single-choice-bucket", "numeric-bucket"],
)
async def test_evaluation_single_bucket_output(metric, expected):
    consumer = GraphDataConsumer()
    consumer.project_id = "project-1"
    consumer.interval = "hour"
    consumer.graph = "charts"
    consumer.eval_id = ""
    consumer.property = ""
    consumer.filters = []
    consumer.fetch_raw_data = AsyncMock(
        return_value=[
            (
                datetime(2026, 1, 1, 10),
                "config-1",
                json.dumps({"metric_config-1": metric}),
                "Quality",
            )
        ]
    )
    consumer.send_json = AsyncMock()

    await consumer.send_evaluation_data()

    data = consumer.send_json.await_args.args[0]["data"]["config-1"]["data"]
    assert {
        series["name"]: [point["value"] for point in series["value"]] for series in data
    } == expected
