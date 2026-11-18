"""Voice metric filters must round to the same precision as the displayed value,
so filtering matches what the user sees instead of bucketing to the wrong grain."""

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


def _translate(col_id: str, filter_op: str, filter_value):
    return ClickHouseFilterBuilder().translate(
        [
            {
                "column_id": col_id,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": filter_op,
                    "filter_value": filter_value,
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
    )


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


@pytest.mark.unit
def test_talk_ratio_filter_rounds_to_integer_percentage():
    # TalkRatioCell derives an integer bot percentage via Math.round; the filter
    # must match that integer grain, not a 2-decimal percentage.
    where, params = _translate("talk_ratio", "equals", 62)
    assert "(span_attr_num['call.talk_ratio'] + 1) * 100)" in where
    assert "* 100, 2)" not in where
    assert 62 in params.values()


@pytest.mark.unit
def test_duration_filter_truncates_to_integer_seconds():
    # trace.py returns int()-truncated duration_seconds; the filter must
    # truncate too, else equals 62 never matches a 62.7s call.
    where, params = _translate("duration", "equals", 62)
    assert "toInt64(span_attr_num['call.duration'])" in where
    assert 62 in params.values()
