"""The one place Observe reads a span's time to first token.

traceAI and the gateway send gen_ai.server.time_to_first_token in
milliseconds. A span that carries it in seconds is marked by traceAI with
traceai.timing.server_ttft.unit = "s", and is read times 1000. The dashboard,
the trace and span list filters and the graph filters read the value from
here, so a filter compares the value the dashboard shows.
"""

TTFT_ATTRIBUTE = "gen_ai.server.time_to_first_token"

# traceAI sets this string attribute to "s" on a span whose TTFT is in seconds.
# Unmarked spans (or any other value) carry milliseconds. Never guess from size.
TTFT_UNIT_ATTRIBUTE = "traceai.timing.server_ttft.unit"

_TTFT_STORED = f"span_attr_num['{TTFT_ATTRIBUTE}']"

# The value, in milliseconds.
TTFT_VALUE = (
    f"if(span_attr_str['{TTFT_UNIT_ATTRIBUTE}'] = 's', "
    f"{_TTFT_STORED} * 1000, {_TTFT_STORED})"
)

# The value a filter compares. A span that carries no time to first token has
# none, rather than the map's default 0, so "under 500 ms" doesn't match it.
TTFT_FILTER_EXPRESSION = (
    f"if(mapContains(span_attr_num, '{TTFT_ATTRIBUTE}'), {TTFT_VALUE}, null)"
)
