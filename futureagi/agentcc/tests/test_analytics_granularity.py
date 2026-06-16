"""Regression tests for analytics granularity parsing.

The gateway analytics charts (UsageCharts.computeGranularity) request
duration-style buckets such as "5m" / "1h" / "6h" / "1d". The backend buckets
by Django Trunc units, so these aliases must be normalized to a supported unit
instead of silently collapsing to "hour" (which squeezes a wide range into hour
buckets and renders the chart as a sliver).
"""

import pytest

from agentcc.services.analytics import (
    ALLOWED_GRANULARITIES,
    GRANULARITY_ALIASES,
    parse_time_range,
)


@pytest.mark.parametrize(
    "frontend_value, expected",
    [
        ("5m", "minute"),
        ("15m", "minute"),
        ("1h", "hour"),
        ("6h", "hour"),
        ("1d", "day"),
    ],
)
def test_frontend_duration_aliases_are_normalized(frontend_value, expected):
    _, _, granularity = parse_time_range({"granularity": frontend_value})
    assert granularity == expected


def test_every_frontend_value_resolves_to_a_supported_unit():
    # Mirrors UsageCharts.computeGranularity's full output set; none of these
    # should fall through to the "hour" default by accident.
    for frontend_value in ("5m", "15m", "1h", "6h", "1d"):
        _, _, granularity = parse_time_range({"granularity": frontend_value})
        assert granularity in ALLOWED_GRANULARITIES
    # "1d" in particular must keep day granularity, not degrade to hour.
    _, _, granularity = parse_time_range({"granularity": "1d"})
    assert granularity == "day"


def test_canonical_units_pass_through_unchanged():
    for unit in ALLOWED_GRANULARITIES:
        _, _, granularity = parse_time_range({"granularity": unit})
        assert granularity == unit


def test_unknown_granularity_still_falls_back_to_hour():
    _, _, granularity = parse_time_range({"granularity": "fortnight"})
    assert granularity == "hour"


def test_aliases_cover_the_frontend_contract():
    # Guard against the alias map drifting from the frontend's emitted values.
    assert set(GRANULARITY_ALIASES) == {"5m", "15m", "1h", "6h", "1d"}
    assert set(GRANULARITY_ALIASES.values()) <= ALLOWED_GRANULARITIES
