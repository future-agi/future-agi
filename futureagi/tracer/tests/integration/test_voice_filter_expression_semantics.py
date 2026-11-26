"""Execute provider-normalized voice expressions against real ClickHouse JSON.

The unit tests assert query structure. These cases cover ClickHouse's runtime
typing behavior, especially provider prices serialized as JSON strings.
"""

import json

import pytest

from tracer.services.clickhouse.query_builders.voice_filter_expressions import (
    VOICE_CALL_STATUS_FILTER_EXPRESSION,
    VOICE_CALL_TYPE_FILTER_EXPRESSION,
    VOICE_COST_CENTS_FILTER_EXPRESSION,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _evaluate_expression(
    ch_client,
    expression: str,
    *,
    raw_log: dict,
    provider: str,
    gen_ai_system: str = "",
    encoding: str = "object",
):
    encoded_log = json.dumps(raw_log, separators=(",", ":"))
    if encoding == "object":
        attributes_raw = json.dumps({"raw_log": raw_log}, separators=(",", ":"))
        map_raw_log = ""
    elif encoding == "encoded":
        attributes_raw = json.dumps({"raw_log": encoded_log}, separators=(",", ":"))
        map_raw_log = ""
    elif encoding == "map":
        attributes_raw = "{}"
        map_raw_log = encoded_log
    else:  # pragma: no cover - test helper contract
        raise AssertionError(f"unsupported encoding: {encoding}")

    query = f"""
        SELECT {expression}
        FROM
        (
            SELECT
                {_sql_string(attributes_raw)} AS span_attributes_raw,
                map(
                    'raw_log', {_sql_string(map_raw_log)},
                    'gen_ai.system', {_sql_string(gen_ai_system)}
                ) AS span_attr_str,
                CAST(map(), 'Map(String, Float64)') AS span_attr_num,
                {_sql_string(provider)} AS provider
        )
    """
    return ch_client.command(query)


@pytest.mark.parametrize("encoding", ["object", "encoded", "map"])
def test_twilio_string_price_is_parsed_in_every_raw_log_encoding(ch_client, encoding):
    result = _evaluate_expression(
        ch_client,
        VOICE_COST_CENTS_FILTER_EXPRESSION,
        raw_log={"sid": "CA0123", "price": "-0.0085"},
        provider="twilio",
        encoding=encoding,
    )

    assert result == pytest.approx(0.85)


def test_cost_distinguishes_explicit_zero_from_missing(ch_client):
    explicit_zero = _evaluate_expression(
        ch_client,
        VOICE_COST_CENTS_FILTER_EXPRESSION,
        raw_log={"id": "call-1", "cost": 0},
        provider="vapi",
    )
    missing = _evaluate_expression(
        ch_client,
        VOICE_COST_CENTS_FILTER_EXPRESSION,
        raw_log={"id": "call-1"},
        provider="vapi",
    )

    assert explicit_zero == 0
    assert missing is None


@pytest.mark.parametrize(
    ("provider", "raw_log"),
    [
        ("vapi", {"id": "v-1", "cost": None}),
        ("retell", {"call_id": "r-1", "call_cost": {"combined_cost": None}}),
        (
            "eleven_labs",
            {"conversation_id": "e-1", "metadata": {"cost": None}},
        ),
        ("bland", {"call_id": "b-1", "price": None}),
        ("twilio", {"sid": "t-1", "price": None}),
        ("twilio", {"sid": "t-1", "price": {"wrong": "type"}}),
    ],
)
def test_null_or_wrong_typed_provider_cost_does_not_become_zero(
    ch_client, provider, raw_log
):
    result = _evaluate_expression(
        ch_client,
        VOICE_COST_CENTS_FILTER_EXPRESSION,
        raw_log=raw_log,
        provider=provider,
    )

    assert result is None


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("initiated", "in-progress"),
        ("future-provider-state", "in-progress"),
        ("  ERROR  ", "failed"),
        (None, None),
    ],
)
def test_status_expression_never_escapes_closed_vocabulary(
    ch_client, raw_status, expected
):
    raw_log = {"conversation_id": "el-1"}
    if raw_status is not None:
        raw_log["status"] = raw_status
    result = _evaluate_expression(
        ch_client,
        VOICE_CALL_STATUS_FILTER_EXPRESSION,
        raw_log=raw_log,
        provider="eleven_labs",
    )

    assert result == expected


@pytest.mark.parametrize(
    (
        "provider",
        "gen_ai_system",
        "raw_log",
        "expected_status",
        "expected_call_type",
    ),
    [
        (
            "vapi",
            "retell",
            {"call_status": "ended", "direction": "inbound"},
            "in-progress",
            "outbound",
        ),
        (
            "openai",
            "retell",
            {
                "call_status": "ended",
                "direction": "outbound",
                "status": "failed",
                "type": "inboundPhoneCall",
            },
            "completed",
            "outbound",
        ),
        (
            "openai",
            "unknown",
            {"call_status": "ended", "direction": "inbound"},
            "in-progress",
            "outbound",
        ),
    ],
)
def test_status_and_call_type_use_consumer_provider_precedence(
    ch_client,
    provider,
    gen_ai_system,
    raw_log,
    expected_status,
    expected_call_type,
):
    status = _evaluate_expression(
        ch_client,
        VOICE_CALL_STATUS_FILTER_EXPRESSION,
        raw_log=raw_log,
        provider=provider,
        gen_ai_system=gen_ai_system,
    )
    call_type = _evaluate_expression(
        ch_client,
        VOICE_CALL_TYPE_FILTER_EXPRESSION,
        raw_log=raw_log,
        provider=provider,
        gen_ai_system=gen_ai_system,
    )

    assert status == expected_status
    assert call_type == expected_call_type
