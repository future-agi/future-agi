"""A span is priced at its own start, from the dated schedule, as the gateway prices it.

Gemini 3.7 Flash bills 0.75 in / 3.75 out per 1M tokens through 31 Dec 2026 and
1.5 / 7.5 from 1 Jan 2027 (UTC). A span that starts before midnight is billed at the
2026 rate whatever time it ends or is ingested. No database.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from tracer.utils.otel import _convert_single_span, calculate_cost_from_tokens

MODEL = "gemini-3.7-flash"
LAST_2026 = datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
FIRST_2027 = datetime(2027, 1, 1, tzinfo=UTC)
COST_2026 = 2.25  # 2M in at 0.75 and 200k out at 3.75
COST_2027 = 4.5


def _ns(moment):
    return int(moment.timestamp()) * 1_000_000_000 + moment.microsecond * 1000


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        pytest.param(LAST_2026, COST_2026, id="last-microsecond-of-2026"),
        pytest.param(FIRST_2027, COST_2027, id="midnight-utc-2027"),
    ],
)
def test_a_dated_model_is_priced_at_the_span_start(at, expected):
    cost = calculate_cost_from_tokens(2_000_000, 200_000, MODEL, at=at)

    assert cost == pytest.approx(expected, abs=0.001)


def test_a_model_the_schedule_does_not_list_keeps_the_litellm_price():
    listed = {"not-in-schedule": {"input_cost_per_token": 1}}
    with (
        patch("tracer.utils.otel.cost_per_token", return_value=(0.25, 0.5)) as litellm_price,
        patch.dict("tracer.utils.otel.model_cost", listed),
    ):
        cost = calculate_cost_from_tokens(10, 10, "not-in-schedule", at=LAST_2026)

    litellm_price.assert_called_once()
    assert cost == 0.75


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        pytest.param(LAST_2026, COST_2026, id="starts-before-2027"),
        pytest.param(FIRST_2027, COST_2027, id="starts-after-the-change"),
    ],
)
def test_a_converted_span_is_priced_at_the_instant_it_started(start, expected):
    span = {
        "span_id": "01" * 8,
        "trace_id": "02" * 16,
        "project_name": "test",
        "project_type": "observe",
        # The span ends two seconds after the change either way.
        "start_time": _ns(start),
        "end_time": _ns(datetime(2027, 1, 1, 0, 0, 2, tzinfo=UTC)),
        "attributes": {
            "llm.model_name": MODEL,
            "llm.token_count.prompt": 2_000_000,
            "llm.token_count.completion": 200_000,
        },
    }
    project = type("P", (), {"id": "project"})()

    result = _convert_single_span(span, {("test", "org", "observe"): project}, {}, "org")

    assert result["observation_span"]["cost"] == pytest.approx(expected, abs=0.001)
