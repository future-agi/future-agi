"""Run the Time to First Token filter expression against real ClickHouse.

The unit tests assert which key and expression the filters use. These cases
cover the runtime behaviour: values are stored ms, a span that traceAI marks
with traceai.timing.server_ttft.unit = "s" carries seconds and is read times
1000, and a span without the key has no value rather than the Map's default 0.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

STORED_KEY = "gen_ai.server.time_to_first_token"
UNIT_KEY = "traceai.timing.server_ttft.unit"


def _ttft_of(ch_client, comparison, stored=None, unit=None):
    from tracer.services.clickhouse.query_builders.ttft import (
        TTFT_FILTER_EXPRESSION,
    )

    if stored is None:
        numbers = "CAST(map(), 'Map(String, Float64)')"
    else:
        numbers = f"CAST(map('{STORED_KEY}', {float(stored)}), 'Map(String, Float64)')"
    if unit is None:
        strings = "CAST(map(), 'Map(String, String)')"
    else:
        strings = f"CAST(map('{UNIT_KEY}', '{unit}'), 'Map(String, String)')"
    return ch_client.command(
        f"SELECT ({TTFT_FILTER_EXPRESSION}) {comparison} "
        f"FROM (SELECT {numbers} AS span_attr_num, {strings} AS span_attr_str)"
    )


@pytest.mark.parametrize(
    ("stored_ms", "listed"),
    [(800, 1), (200, 0), (500, 0), (500.5, 1)],
)
def test_over_500_ms_lists_a_span_by_its_stored_milliseconds(
    ch_client, stored_ms, listed
):
    assert _ttft_of(ch_client, "> 500", stored_ms) == listed


@pytest.mark.parametrize("comparison", ["> 500", "< 500", "= 0"])
def test_a_span_without_time_to_first_token_matches_no_comparison(
    ch_client, comparison
):
    assert _ttft_of(ch_client, comparison) is None


def test_a_measured_zero_is_kept(ch_client):
    assert _ttft_of(ch_client, "= 0", stored=0) == 1


@pytest.mark.parametrize(
    ("stored_seconds", "listed"),
    [(0.8, 1), (0.2, 0), (0.5, 0)],
)
def test_over_500_ms_reads_a_span_traceai_marks_as_seconds_times_1000(
    ch_client, stored_seconds, listed
):
    assert _ttft_of(ch_client, "> 500", stored_seconds, unit="s") == listed


@pytest.mark.parametrize("unit", ["S", "ms", " s", ""])
def test_any_other_unit_value_is_read_as_stored_milliseconds(ch_client, unit):
    assert _ttft_of(ch_client, "= 800", 800, unit=unit) == 1
