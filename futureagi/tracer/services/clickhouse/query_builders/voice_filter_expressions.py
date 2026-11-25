"""Canonical ClickHouse expressions for public voice-call system metrics.

These expressions match the normalized values returned by the voice-call list.
They deliberately use legacy span column tokens; the CH25 compiler rewrites
those tokens once at its schema boundary.
"""


def voice_conversation_root_expression(expression: str) -> str:
    """Restrict one public voice value to its rendered conversation root."""

    return (
        "if((parent_span_id IS NULL OR parent_span_id = '') "
        "AND observation_type = 'conversation', "
        f"({expression}), null)"
    )


def _raw_log_number(path: tuple[str, ...]) -> str:
    """Read one provider number from object, encoded-string, or Map raw_log."""

    quoted_path = ", ".join(f"'{part}'" for part in path)
    nested_path = f"'raw_log', {quoted_path}"
    encoded_raw_log = "JSONExtractString(span_attributes_raw, 'raw_log')"
    map_raw_log = "span_attr_str['raw_log']"
    return (
        "coalesce("
        f"if(JSONHas(span_attributes_raw, {nested_path}), "
        f"toFloat64OrNull(JSONExtractString(span_attributes_raw, {nested_path})), "
        "null), "
        f"if(JSONHas(span_attributes_raw, {nested_path}) AND "
        f"JSONType(span_attributes_raw, {nested_path}) IN "
        "('Int64', 'UInt64', 'Float64'), "
        f"JSONExtractFloat(span_attributes_raw, {nested_path}), null), "
        f"if(JSONHas({encoded_raw_log}, {quoted_path}), "
        f"toFloat64OrNull(JSONExtractString({encoded_raw_log}, {quoted_path})), "
        "null), "
        f"if(JSONHas({encoded_raw_log}, {quoted_path}) AND "
        f"JSONType({encoded_raw_log}, {quoted_path}) IN "
        "('Int64', 'UInt64', 'Float64'), "
        f"JSONExtractFloat({encoded_raw_log}, {quoted_path}), null), "
        f"if(JSONHas({map_raw_log}, {quoted_path}), "
        f"toFloat64OrNull(JSONExtractString({map_raw_log}, {quoted_path})), "
        "null), "
        f"if(JSONHas({map_raw_log}, {quoted_path}) AND "
        f"JSONType({map_raw_log}, {quoted_path}) IN "
        "('Int64', 'UInt64', 'Float64'), "
        f"JSONExtractFloat({map_raw_log}, {quoted_path}), null)"
        ")"
    )


def _raw_log_string(path: tuple[str, ...]) -> str:
    """Read one provider string from object or encoded-string raw_log."""

    quoted_path = ", ".join(f"'{part}'" for part in path)
    nested_path = f"'raw_log', {quoted_path}"
    encoded_raw_log = "JSONExtractString(span_attributes_raw, 'raw_log')"
    map_raw_log = "span_attr_str['raw_log']"
    return (
        "coalesce("
        f"nullIf(JSONExtractString(span_attributes_raw, {nested_path}), ''), "
        f"nullIf(JSONExtractString({encoded_raw_log}, {quoted_path}), ''), "
        f"nullIf(JSONExtractString({map_raw_log}, {quoted_path}), '')"
        ")"
    )


_RAW_RETELL_COST_CENTS = _raw_log_number(("call_cost", "combined_cost"))
_RAW_VAPI_COST_DOLLARS = _raw_log_number(("cost",))
_RAW_ELEVEN_LABS_COST_CENTS = _raw_log_number(("metadata", "cost"))
_RAW_PRICE_DOLLARS = _raw_log_number(("price",))
_VOICE_PROVIDER = "lowerUTF8(toString(provider))"
_VOICE_GEN_AI_SYSTEM = (
    "lowerUTF8(toString(if(mapContains(span_attr_str, 'gen_ai.system'), "
    "span_attr_str['gen_ai.system'], '')))"
)
_VOICE_RESOLVED_PROVIDER = (
    "multiIf("
    f"{_VOICE_PROVIDER} IN ('vapi', 'retell', 'eleven_labs', 'bland', 'twilio'), "
    f"{_VOICE_PROVIDER}, "
    f"{_VOICE_GEN_AI_SYSTEM} IN "
    "('vapi', 'retell', 'eleven_labs', 'bland', 'twilio'), "
    f"{_VOICE_GEN_AI_SYSTEM}, 'vapi')"
)

_VOICE_STORED_STATUS = (
    "if(mapContains(span_attr_str, 'call.status'), "
    "nullIf(span_attr_str['call.status'], ''), null)"
)
_VOICE_HAS_NONEMPTY_RAW_LOG = (
    "((JSONHas(span_attributes_raw, 'raw_log') AND "
    "JSONExtractRaw(span_attributes_raw, 'raw_log') NOT IN "
    "('', '{}', 'null', '\"\"', '\"{}\"', '\"null\"')) OR "
    "(mapContains(span_attr_str, 'raw_log') AND "
    "span_attr_str['raw_log'] NOT IN ('', '{}', 'null')))"
)


def _canonical_voice_status(expression: str) -> str:
    """Map one list-consumer status source to the public five-value vocabulary."""

    normalized = f"lowerUTF8(trimBoth(toString({expression})))"
    return (
        "multiIf("
        f"{normalized} IN "
        "('ended', 'done', 'complete', 'completed', 'success', 'succeeded', 'ok'), "
        "'completed', "
        f"{normalized} IN "
        "('in-progress', 'in_progress', 'ongoing', 'started', 'initiated', "
        "'processing', 'scheduled', 'created', 'dialing', 'connecting', "
        "'ringing', 'queued', 'pending'), 'in-progress', "
        f"{normalized} IN "
        "('failed', 'failure', 'error', 'errored'), 'failed', "
        f"{normalized} IN "
        "('dropped', 'cancelled', 'canceled', 'aborted', 'hung-up', 'hung_up'), "
        "'dropped', "
        f"{normalized} IN "
        "('not-connected', 'not_connected', 'no-answer', 'no_answer', "
        "'unanswered', 'busy'), 'not-connected', "
        f"if(isNull({expression}) OR {normalized} = '', "
        "CAST(NULL AS Nullable(String)), 'in-progress'))"
    )


_VOICE_CANONICAL_STORED_STATUS = _canonical_voice_status(_VOICE_STORED_STATUS)
_VOICE_VAPI_RAW_STATUS = _raw_log_string(("status",))
_VOICE_RETELL_RAW_STATUS = _raw_log_string(("call_status",))
_VOICE_OTHER_RAW_STATUS = _canonical_voice_status(_raw_log_string(("status",)))
_VOICE_STORED_COST_CENTS = (
    "coalesce("
    "if(mapContains(span_attr_num, 'combined_cost'), "
    "span_attr_num['combined_cost'], null), "
    "if(mapContains(span_attr_num, 'cost_breakdown.total'), "
    "span_attr_num['cost_breakdown.total'] * 100, null), "
    "CAST(NULL AS Nullable(Float64)))"
)
VOICE_COST_CENTS_FILTER_EXPRESSION = (
    "multiIf("
    f"NOT {_VOICE_HAS_NONEMPTY_RAW_LOG}, {_VOICE_STORED_COST_CENTS}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'retell', {_RAW_RETELL_COST_CENTS}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'eleven_labs', "
    f"{_RAW_ELEVEN_LABS_COST_CENTS}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'bland', {_RAW_PRICE_DOLLARS} * 100, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'twilio', "
    f"abs({_RAW_PRICE_DOLLARS}) * 100, "
    f"{_RAW_VAPI_COST_DOLLARS} * 100)"
)
_VOICE_OTLP_CALL_ID = (
    "nullIf(JSONExtractString(span_attributes_raw, 'metadata', "
    "'call_execution_id'), '')"
)
VOICE_CALL_ID_FILTER_EXPRESSION = (
    "multiIf("
    f"NOT {_VOICE_HAS_NONEMPTY_RAW_LOG}, {_VOICE_OTLP_CALL_ID}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'retell', {_raw_log_string(('call_id',))}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'eleven_labs', "
    f"{_raw_log_string(('conversation_id',))}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'bland', {_raw_log_string(('call_id',))}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'twilio', {_raw_log_string(('sid',))}, "
    f"{_raw_log_string(('id',))})"
)
_VOICE_STORED_CALL_TYPE = (
    "if(mapContains(span_attr_str, 'call_type'), "
    "nullIf(span_attr_str['call_type'], ''), null)"
)
_VOICE_VAPI_RAW_CALL_TYPE = _raw_log_string(("type",))
VOICE_CALL_TYPE_FILTER_EXPRESSION = (
    "multiIf("
    f"NOT {_VOICE_HAS_NONEMPTY_RAW_LOG}, {_VOICE_STORED_CALL_TYPE}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'retell', "
    f"{_raw_log_string(('direction',))}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'vapi', "
    f"if({_VOICE_VAPI_RAW_CALL_TYPE} = 'inboundPhoneCall', "
    "'inbound', 'outbound'), "
    "CAST(NULL AS Nullable(String)))"
)
VOICE_CALL_STATUS_FILTER_EXPRESSION = (
    "multiIf("
    f"NOT {_VOICE_HAS_NONEMPTY_RAW_LOG}, "
    f"coalesce({_VOICE_CANONICAL_STORED_STATUS}, 'completed'), "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'retell', "
    f"if({_VOICE_RETELL_RAW_STATUS} = 'ended', 'completed', 'in-progress'), "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'vapi', "
    f"if({_VOICE_VAPI_RAW_STATUS} = 'ended', 'completed', 'in-progress'), "
    f"{_VOICE_OTHER_RAW_STATUS})"
)
_VOICE_STORED_ENDED_REASON = (
    "if(mapContains(span_attr_str, 'ended_reason'), "
    "nullIf(span_attr_str['ended_reason'], ''), null)"
)
_VOICE_VAPI_RAW_ENDED_REASON = _raw_log_string(("endedReason",))
_VOICE_RETELL_RAW_ENDED_REASON = _raw_log_string(("disconnection_reason",))
VOICE_ENDED_REASON_FILTER_EXPRESSION = (
    "multiIf("
    f"NOT {_VOICE_HAS_NONEMPTY_RAW_LOG}, {_VOICE_STORED_ENDED_REASON}, "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'retell', "
    f"coalesce({_VOICE_RETELL_RAW_ENDED_REASON}, {_VOICE_STORED_ENDED_REASON}), "
    f"({_VOICE_RESOLVED_PROVIDER}) = 'vapi', "
    f"coalesce({_VOICE_VAPI_RAW_ENDED_REASON}, {_VOICE_STORED_ENDED_REASON}), "
    f"{_VOICE_STORED_ENDED_REASON})"
)


VOICE_NORMALIZED_SYSTEM_METRIC_EXPRS = {
    "call_status": VOICE_CALL_STATUS_FILTER_EXPRESSION,
    "cost_cents": VOICE_COST_CENTS_FILTER_EXPRESSION,
    "call_id": VOICE_CALL_ID_FILTER_EXPRESSION,
    "call_type": VOICE_CALL_TYPE_FILTER_EXPRESSION,
    "ended_reason": VOICE_ENDED_REASON_FILTER_EXPRESSION,
}

# The public expressions above intentionally describe only value normalization:
# the voice list and value picker already constrain their source rows. Generic
# trace/span/graph compilers must carry the same row-domain constraint so an
# ordinary root or child span cannot inherit voice defaults such as completed.
VOICE_NORMALIZED_ROOT_SYSTEM_METRIC_EXPRS = {
    key: voice_conversation_root_expression(expression)
    for key, expression in VOICE_NORMALIZED_SYSTEM_METRIC_EXPRS.items()
}


__all__ = [
    "VOICE_CALL_ID_FILTER_EXPRESSION",
    "VOICE_CALL_STATUS_FILTER_EXPRESSION",
    "VOICE_CALL_TYPE_FILTER_EXPRESSION",
    "VOICE_COST_CENTS_FILTER_EXPRESSION",
    "VOICE_ENDED_REASON_FILTER_EXPRESSION",
    "VOICE_NORMALIZED_ROOT_SYSTEM_METRIC_EXPRS",
    "VOICE_NORMALIZED_SYSTEM_METRIC_EXPRS",
    "voice_conversation_root_expression",
]
