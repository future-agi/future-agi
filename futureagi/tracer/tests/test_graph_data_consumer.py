from unittest.mock import AsyncMock

import pytest

from tracer.socket import EVALUATION_QUERY_ROW_LIMIT, GraphDataConsumer


@pytest.mark.asyncio
async def test_send_evaluation_data_limits_grouped_query():
    consumer = GraphDataConsumer()
    consumer.project_id = "project-1"
    consumer.filters = []
    consumer.interval = "hour"
    consumer.property = ""
    consumer.graph = "charts"
    consumer.eval_id = ""
    consumer.fetch_raw_data = AsyncMock(return_value=[])
    consumer.send_json = AsyncMock()

    await consumer.send_evaluation_data()

    query, params = consumer.fetch_raw_data.call_args.args
    assert "bounded_eval_metrics" in query
    assert "LIMIT %s" in query
    assert params[-1] == EVALUATION_QUERY_ROW_LIMIT
    consumer.send_json.assert_called_once()
