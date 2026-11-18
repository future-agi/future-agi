"""Gemini 3.6/3.7/3.8 Flash are billed at the price in force when the usage happened.

The 2026 rates apply through 23:59:59.999999 UTC on 31 Dec 2026 and the 2027 rates
from 00:00:00 UTC on 1 Jan 2027, in a worker that has run across midnight too.
No database: the catalogue and the dated prices are module data.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.run_prompt import model_pricing
from agentic_eval.core_evals.run_prompt.model_pricing import (
    get_model_info,
    get_model_pricing,
)

T26 = datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
T27 = datetime(2027, 1, 1, tzinfo=UTC)
RATES_2026 = {
    "input_per_1M_tokens": 0.75,
    "cached_input_per_1M_tokens": 0.075,
    "output_per_1M_tokens": 3.75,
}
RATES_2027 = {
    "input_per_1M_tokens": 1.5,
    "cached_input_per_1M_tokens": 0.15,
    "output_per_1M_tokens": 7.5,
}
# 1M prompt tokens, 800k of them cached, and 100k completion tokens.
USAGE = {
    "prompt_tokens": 1_000_000,
    "cached_input_tokens": 800_000,
    "completion_tokens": 100_000,
}
COST_2026 = 0.585
COST_2027 = 1.17


def _rates(pricing):
    return {name: pricing.get(name) for name in RATES_2026}


@pytest.mark.parametrize(
    "model_name",
    [
        "gemini/gemini-3.6-flash",
        "gemini/gemini-3.7-flash",
        "gemini/gemini-3.8-flash",
        "gemini-3.7-flash",
        "vertex_ai/gemini-3.8-flash",
        # Not a key of the dated file: the row is chosen on the entry the
        # name resolves to (gemini/gemini-3.7-flash), not on the name itself.
        "GEMINI/gemini-3.7-flash",
    ],
)
@pytest.mark.parametrize(
    ("at", "expected"),
    [
        pytest.param(T26, RATES_2026, id="last-microsecond-of-2026"),
        pytest.param(T27, RATES_2027, id="midnight-utc-2027"),
        pytest.param(
            datetime(
                2027, 1, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
            ),
            RATES_2027,
            id="midnight-utc-seen-from-india",
        ),
        pytest.param(
            datetime(2026, 12, 31, 23, tzinfo=timezone(timedelta(hours=-5))),
            RATES_2027,
            id="new-year-in-utc-still-2026-in-new-york",
        ),
        # For gemini/gemini-3.6-flash this is the 3.6 fix: 0.75 / 3.75 until 31 Dec.
        pytest.param(datetime(2026, 11, 11, 12, tzinfo=UTC), RATES_2026, id="today"),
    ],
)
def test_the_row_in_force_at_the_usage_time_is_billed(model_name, at, expected):
    pricing = get_model_pricing(model_name, at=at)

    assert _rates(pricing) == pytest.approx(expected, rel=0, abs=1e-12)


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "model_name", ["vertex_ai/gemini-3.6-flash", "vertex_ai/gemini-3.7-flash"]
)
def test_the_enterprise_vertex_rows_switch_at_midnight(model_name):
    before = get_model_pricing(model_name, at=T26)
    after = get_model_pricing(model_name, at=T27)

    assert _rates(before) == pytest.approx(RATES_2026, rel=0, abs=1e-12)
    assert _rates(after) == pytest.approx(RATES_2027, rel=0, abs=1e-12)


def test_a_long_running_worker_switches_at_midnight(monkeypatch):
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: T26)
    assert get_model_pricing("gemini-3.7-flash")["input_per_1M_tokens"] == 0.75
    assert calculate_total_cost("gemini-3.7-flash", USAGE)["total_cost"] == (
        pytest.approx(COST_2026)
    )

    # Same process, no cache clear: only the clock moved.
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: T27)
    assert get_model_pricing("gemini-3.7-flash")["input_per_1M_tokens"] == 1.5
    assert calculate_total_cost("gemini-3.7-flash", USAGE)["total_cost"] == (
        pytest.approx(COST_2027)
    )


def test_the_cached_catalogue_pricing_is_never_changed():
    before = copy.deepcopy(model_pricing._catalogue_pricing("gemini/gemini-3.7-flash"))

    pricing = get_model_pricing("gemini/gemini-3.7-flash", at=T27)
    pricing["input_per_1M_tokens"] = 99

    cached = model_pricing._catalogue_pricing("gemini/gemini-3.7-flash")
    assert cached == before
    assert cached[1]["input_per_1M_tokens"] == 0.75
    assert get_model_pricing("gemini/gemini-3.7-flash", at=T27)[
        "input_per_1M_tokens"
    ] == pytest.approx(1.5)
    assert get_model_pricing("gemini/gemini-3.7-flash", at=T26)[
        "input_per_1M_tokens"
    ] == pytest.approx(0.75)


def test_a_naive_usage_time_is_refused():
    with pytest.raises(ValueError, match="no time zone"):
        get_model_pricing("gemini-3.7-flash", at=datetime(2027, 1, 1))


@pytest.mark.parametrize(("at", "cost"), [(T26, COST_2026), (T27, COST_2027)])
def test_calculate_total_cost_prices_at_the_usage_time(at, cost):
    result = calculate_total_cost("gemini-3.7-flash", USAGE, at=at)

    assert result["total_cost"] == pytest.approx(cost)
    assert result["pricing_source"] == "available_models"


@pytest.mark.parametrize("model_name", ["gpt-4o", "gemini/gemini-3.5-flash"])
def test_models_without_dated_rows_are_unchanged(model_name):
    static = get_model_info(model_name)["pricing"]

    assert get_model_pricing(model_name, at=T27) == static
    assert get_model_pricing(model_name, at=T26) == static
