"""The conjunction gate is exact at the trace level, proven on real SQL.

A unit test can pin the statement's text; only the server can prove what the
text MEANS. This runs the short-text lane's gated seed statement, and the
unchanged classifier, against a local ClickHouse holding four traces built
to separate the correct trace-level gate from the wrong per-span one:

* ``split``   - the anchor value on the root, both presence keys on a tool
                child in a different key range. A per-span conjunction would
                drop it; the gate must keep it.
* ``partial`` - the anchor value, but only one of the two keys anywhere.
* ``no-anchor`` - both keys, but not the anchor value.
* ``late``    - the anchor value, both keys, but the child that carries them
                starts three hours after the root, outside the one-hour
                envelope. The classifier accepts it - its any-span leaves
                look at the trace's whole history - and so must the gate:
                a gate confined to the envelope dropped this trace, which is
                what made it broaden the shipped contract. The gate resolves
                ``split`` inside the envelope and ``late`` against history.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from django.test import override_settings

from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)


def _local_ch25_client():
    """Return an explicitly local native client or skip the live proof."""

    host = os.environ.get("CH25_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.skip("conjunction gate proof is restricted to local ClickHouse")
    try:
        from clickhouse_driver import Client

        client = Client(
            host=host,
            port=int(
                os.environ.get("CH25_NATIVE_PORT")
                or os.environ.get("CH25_TCP_PORT")
                or "19000"
            ),
            user=os.environ.get("CH25_USER", "default"),
            password=os.environ.get("CH25_PASSWORD", ""),
            database="default",
            connect_timeout=int(os.environ.get("CH25_CONNECT_TIMEOUT", "2")),
            send_receive_timeout=int(os.environ.get("CH25_READ_TIMEOUT", "10")),
        )
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse is unavailable for the gate proof: {exc!r}")
    return client


ANCHOR_KEY = "ended_reason"
ANCHOR_VALUE = "voicemail"
KEY_A = "call.status"
KEY_B = "conversation.transcript.13.message.role"

_SPANS_DDL = """
CREATE TABLE spans
(
    project_id UUID,
    observation_type LowCardinality(String),
    service_name LowCardinality(String),
    start_time DateTime64(6, 'UTC'),
    trace_id String,
    id String,
    parent_span_id Nullable(String),
    attrs_string Map(String, String),
    attrs_number Map(String, Float64),
    attrs_bool Map(String, UInt8),
    is_deleted UInt8,
    _version UInt64,
    project_version_id Nullable(UUID),
    trace_name String DEFAULT '',
    name String DEFAULT '',
    status String DEFAULT 'OK',
    end_time Nullable(DateTime64(6, 'UTC')),
    latency_ms Int64 DEFAULT 0,
    cost Float64 DEFAULT 0,
    total_tokens Int64 DEFAULT 0,
    prompt_tokens Int64 DEFAULT 0,
    completion_tokens Int64 DEFAULT 0,
    model String DEFAULT '',
    provider String DEFAULT '',
    trace_session_id Nullable(String),
    INDEX idx_attrs_str_keys mapKeys(attrs_string) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_trace_id trace_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toDate(start_time)
ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)
"""


def _time_filter(start: datetime, end: datetime) -> dict:
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "date",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
            "col_type": "TRACE",
        },
    }


def _leaf(key: str, operation: str, value=None) -> dict:
    config = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "text",
        "filter_op": operation,
    }
    if value is not None:
        config["filter_value"] = value
    return {"column_id": key, "filter_config": config}


def _rows(project_id: str, root_at: datetime):
    def span(trace, span_id, kind, service, at, parent, attrs):
        return (
            project_id,
            kind,
            service,
            at,
            trace,
            span_id,
            parent,
            attrs,
            {},
            {},
            0,
            1,
            None,
        )

    child_at = root_at + timedelta(minutes=2)
    late_at = root_at + timedelta(hours=3)
    return [
        span(
            "split",
            "split-root",
            "conversation",
            "voice",
            root_at,
            None,
            {ANCHOR_KEY: ANCHOR_VALUE},
        ),
        span(
            "split",
            "split-tool",
            "tool",
            "agent",
            child_at,
            "split-root",
            {KEY_A: "ended", KEY_B: "user"},
        ),
        span(
            "partial",
            "partial-root",
            "conversation",
            "voice",
            root_at,
            None,
            {ANCHOR_KEY: ANCHOR_VALUE},
        ),
        span(
            "partial",
            "partial-tool",
            "tool",
            "agent",
            child_at,
            "partial-root",
            {KEY_A: "ended"},
        ),
        span(
            "no-anchor",
            "na-root",
            "conversation",
            "voice",
            root_at,
            None,
            {ANCHOR_KEY: "customer-ended-call"},
        ),
        span(
            "no-anchor",
            "na-tool",
            "tool",
            "agent",
            child_at,
            "na-root",
            {KEY_A: "ended", KEY_B: "user"},
        ),
        span(
            "late",
            "late-root",
            "conversation",
            "voice",
            root_at,
            None,
            {ANCHOR_KEY: ANCHOR_VALUE},
        ),
        span(
            "late",
            "late-tool",
            "tool",
            "agent",
            late_at,
            "late-root",
            {KEY_A: "ended", KEY_B: "user"},
        ),
    ]


@pytest.mark.integration
def test_ch25_conjunction_gate_keeps_split_and_late_witnesses() -> None:
    admin = _local_ch25_client()
    database = f"test_conjunction_seed_gate_{uuid4().hex}"
    project_id = str(uuid4())
    root_at = datetime(2026, 8, 8, 10, 5)
    window = (root_at - timedelta(days=1), root_at + timedelta(hours=1))
    slice_ = (root_at.replace(minute=0), root_at.replace(minute=0) + timedelta(hours=1))
    filters = [
        _time_filter(*window),
        _leaf(ANCHOR_KEY, "equals", ANCHOR_VALUE),
        _leaf(KEY_A, "is_not_null"),
        _leaf(KEY_B, "is_not_null"),
    ]

    try:
        admin.execute(f"CREATE DATABASE {database}")
        admin.execute(f"USE {database}")
        admin.execute(_SPANS_DDL)
        admin.execute("SYSTEM STOP MERGES spans")
        for row in _rows(project_id, root_at):
            admin.execute(
                "INSERT INTO spans (project_id, observation_type, service_name, "
                "start_time, trace_id, id, parent_span_id, attrs_string, "
                "attrs_number, attrs_bool, is_deleted, _version, "
                "project_version_id) VALUES",
                [row],
            )
        assert admin.execute("SELECT count() FROM spans") == [(8,)]

        builder = TraceListQueryBuilderV2(
            project_id=project_id, filters=filters, page_size=25
        )
        assert builder._uses_short_text_candidate_seed()

        def candidates(slack: int) -> set[str]:
            with override_settings(FILTER_SELECTOR_TEXT_SEED_WITNESS_SLACK_HOURS=slack):
                query, params = builder.build_filter_candidate_seed_page(
                    slice_start=slice_[0], slice_end=slice_[1], limit=200
                )
            assert ("GROUP BY trace_id" in query) is bool(slack)
            assert ("unconfirmed_scalar_trace_ids" in query) is bool(slack)
            return {row[0] for row in admin.execute(query, params)}

        # The gate keeps the trace whose witnesses sit on two different rows
        # in two different key ranges, keeps the trace whose presence witness
        # starts three hours after its root - outside the envelope, inside the
        # history the classifier reads - and rejects the partial and anchorless
        # traces before any classifier runs. Candidates are exactly what the
        # classifier publishes; the shipped contract is unchanged.
        assert candidates(slack=1) == {"split", "late"}
        # The legacy contract seeds from the anchor alone and is unchanged.
        assert candidates(slack=0) == {"split", "partial", "late"}

        # The gate only ever narrows candidacy: the unchanged classifier, asked
        # about every trace, accepts exactly the traces that satisfy the whole
        # conjunction in latest state - and that is the gated set.
        query, params = builder.build_filter_match_query(
            ["split", "partial", "no-anchor", "late"]
        )
        rows, columns = admin.execute(query, params, with_column_types=True)
        trace_column = [name for name, _type in columns].index("trace_id")
        assert {row[trace_column] for row in rows} == {"split", "late"}
    finally:
        admin.execute("USE default")
        admin.execute(f"DROP DATABASE IF EXISTS {database}")
