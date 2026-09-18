"""Pin the CH-row → scanner SpanData contract (no DB).

Both fields under test were silently wrong in production for four months, and
both failed the same way: the fetch layer described the span in a vocabulary the
stored row never uses, and the fail-open default hid it.

``observation_type`` is written lowercase by both ingest paths (Python
``get_observation_type`` and the Go collector's ``resolveObservationType``, each
validating against ``ObservationType``). The lookup map was keyed UPPERCASE, so
it never matched and every span reached the scanner as ``CHAIN`` — which in turn
left ``turn_count``, ``tools_called`` and ``tools_available`` empty and made the
``tool_failures`` / ``no_tool_calls`` / ``llm_only_trace`` prefilter signals
unreachable.

``status`` was derived from the free-text ``status_message`` while the
authoritative ``status`` column was ignored, so a failed span whose message did
not happen to contain the substring "error" was reported to the judge as ``Ok``.

Fixtures below are therefore CH-shaped on purpose — lowercase ``observation_type``,
populated ``status``. A fixture written in the map's own vocabulary would pass
against the broken code forever, which is how this survived.
"""

from datetime import datetime

import pytest

from tracer.queries.trace_scanner import _ch_span_to_span
from tracer.services.clickhouse.v2.span_reader import CHSpan


def _span(**overrides) -> CHSpan:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "project_id": "22222222-2222-2222-2222-222222222222",
        "trace_id": "33333333-3333-3333-3333-333333333333",
        "parent_span_id": "",
        "name": "lookup_payment",
        "observation_type": "tool",
        "operation_name": "chat",
        "start_time": datetime(2026, 8, 17, 1, 2, 3),
        "end_time": datetime(2026, 8, 17, 1, 2, 4),
        "latency_ms": 1000,
        "model": "",
        "provider": "openai",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "status": "OK",
        "status_message": "",
        "org_id": None,
        "project_version_id": None,
        "end_user_id": None,
        "trace_session_id": None,
        "prompt_version_id": None,
        "prompt_label_id": None,
        "custom_eval_config_id": None,
        "input": "hi",
        "output": "hello",
        "tags": "[]",
        "span_events": "",
        "metadata": "{}",
        "resource_attrs": "{}",
        "attributes_extra": "{}",
        "trace_name": "my-trace",
    }
    base.update(overrides)
    return CHSpan(**base)


def _kind(obs_type: str) -> str:
    return _ch_span_to_span(_span(observation_type=obs_type)).span_attributes[
        "span.kind"
    ]


@pytest.mark.parametrize(
    ("obs_type", "expected"),
    [
        # The lowercase canon actually stored in ClickHouse. Values are the
        # scanner's own vocabulary: it matches {"Tool","TOOL"}, {"LLM","llm"},
        # {"Retriever","RETRIEVER"} — so these spellings are load-bearing.
        ("llm", "LLM"),
        ("tool", "Tool"),
        ("retriever", "Retriever"),
        ("agent", "AGENT"),
        ("chain", "CHAIN"),
    ],
)
def test_lowercase_canon_maps_to_scanner_vocabulary(obs_type, expected):
    assert _kind(obs_type) == expected


@pytest.mark.parametrize(
    ("obs_type", "expected"),
    [
        ("GENERATION", "LLM"),  # Langfuse-style, predates the lowercase canon
        ("generation", "LLM"),
        ("SPAN", "CHAIN"),
        ("TOOL", "Tool"),
        ("LLM", "LLM"),
    ],
)
def test_legacy_and_uppercase_spellings_still_resolve(obs_type, expected):
    assert _kind(obs_type) == expected


@pytest.mark.parametrize("obs_type", ["unknown", "", "guardrail", "embedding"])
def test_unrecognised_type_falls_open_to_chain(obs_type):
    """Fail-open is intended — but it must be the exception, not every row."""
    assert _kind(obs_type) == "CHAIN"


def test_no_canonical_type_silently_degrades_to_chain():
    """Guards the actual regression: a real trace must not come back all-CHAIN.

    This is the assertion the old UPPERCASE map failed on every production row.
    """
    kinds = {_kind(t) for t in ("llm", "tool", "retriever", "chain")}
    assert kinds == {"LLM", "Tool", "Retriever", "CHAIN"}


def _status(**overrides) -> str:
    return _ch_span_to_span(_span(**overrides)).status_code


def test_error_column_wins_over_message_text():
    """The regression: "tool call failed" contains no "error" substring.

    Under the old message-only derivation this returned "Ok" for a span that had
    genuinely failed, so the judge was told every error span succeeded.
    """
    assert _status(status="ERROR", status_message="tool call failed") == "Error"


def test_error_column_without_any_message():
    assert _status(status="ERROR", status_message="") == "Error"


def test_ok_column_maps_to_ok():
    assert _status(status="OK", status_message="") == "Ok"


def test_message_fallback_survives_when_status_column_is_blank():
    """Rows predating the status column still get a best-effort read."""
    assert _status(status="", status_message="upstream error 504") == "Error"
    assert _status(status="", status_message="completed fine") == "Ok"


def test_no_status_and_no_message_is_unset():
    assert _status(status="", status_message="") == "Unset"
