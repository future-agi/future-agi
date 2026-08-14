"""Voice filter aliases: the FE metrics picker sends these column_ids, but the
values are stored under different CH attribute keys. Each must resolve to the
stored key via VOICE_SYSTEM_METRIC_EXPRS, not fall through to a span-attr lookup
on the (non-existent) FE name."""

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder

# FE column_id (from /tracer/dashboard/metrics/) -> stored CH attr key it must read.
VOICE_ALIASES = {
    "talk_ratio": "call.talk_ratio",
    "agent_latency": "avg_agent_latency_ms",
    "ai_interruptions": "ai_interruption_count",
    "user_interruptions": "user_interruption_count",
    "stop_time_after_interruption": "avg_stop_time_after_interruption_ms",
    "llm_cost": "cost_breakdown.llm",
    "stt_cost": "cost_breakdown.stt",
    "tts_cost": "cost_breakdown.tts",
    "total_cost": "cost_breakdown.total",
    "customer_cost": "cost_breakdown.total",
    "llm_latency": "modelLatencyAverage",
    "stt_latency": "transcriberLatencyAverage",
    "tts_latency": "voiceLatencyAverage",
    "response_time": "turnLatencyAverage",
}


@pytest.mark.unit
@pytest.mark.parametrize("col_id,stored_key", list(VOICE_ALIASES.items()))
def test_voice_filter_alias_resolves_to_stored_key(col_id, stored_key):
    where, _ = ClickHouseFilterBuilder().translate(
        [
            {
                "column_id": col_id,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
    )
    assert stored_key in where, f"{col_id} must read '{stored_key}', got: {where[:200]}"
