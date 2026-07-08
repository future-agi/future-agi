"""The voice `agent_talk_percentage` filter must round to the same 2 decimals as
the displayed value, so filtering matches what the user sees instead of bucketing
to whole numbers."""

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


@pytest.mark.unit
def test_agent_talk_percentage_filter_rounds_to_two_decimals():
    # Display (trace.py get_voice_metrics) rounds to 2 decimals; the filter used
    # integer round(), so a call shown as 67.14 was bucketed to 67 and dropped by
    # a `> 67` filter. Filter precision must match the displayed value.
    where, params = ClickHouseFilterBuilder().translate(
        [
            {
                "column_id": "agent_talk_percentage",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 68,
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
    )
    assert "(span_attr_num['call.talk_ratio'] + 1) * 100, 2)" in where
    assert 68 in params.values()
