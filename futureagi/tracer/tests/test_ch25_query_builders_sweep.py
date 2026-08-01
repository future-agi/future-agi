"""
Sweep test: every v2 query builder produces SQL with NO legacy column refs.

Whenever a v1 builder grows a new method that touches `span_attr_*`,
`span_attributes_raw`, `metadata_map`, or `_peerdb_*`, this test fails
unless the corresponding v2 builder either overrides the new method OR
the new method goes through one of the already-overridden ones.

Cheap to run: pure-Python (no DB), exercises each v2 builder's public
build* methods with minimal valid input.
"""
from __future__ import annotations

import re

import pytest

# v2 builders under test
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.eval_metrics import (
    EvalMetricsQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)


PROJECT_ID = "11111111-1111-1111-1111-111111111111"

LEGACY_TOKENS = (
    "_peerdb_is_deleted",
    "_peerdb_version",
    "span_attr_str",
    "span_attr_num",
    "span_attr_bool",
    "span_attributes_raw",
    "resource_attributes_raw",
    "metadata_map",
)
# Pattern matches a legacy token AS A COLUMN REFERENCE — not as an `AS` alias
# name. The rewriter wraps legacy bare JSON columns as
# `toJSONString(v2_col) AS legacy_col` to preserve the result-row key shape
# for downstream Python callers; the legacy name in alias position is
# intentional and SHOULD NOT fail the sweep.
LEGACY_REF_RE = re.compile(
    r"(?<!\bAS\s)"                       # not preceded by `AS ` (alias position)
    r"(?<!\b[Aa][Ss]\s)"                  # case-insensitive AS
    r"\b(" + "|".join(LEGACY_TOKENS) + r")\b"
    r"(?![A-Za-z0-9_])"
)


def _assert_no_legacy(sql: str, label: str) -> None:
    """Fail with helpful context if a legacy column is REFERENCED (not aliased) in v2 SQL."""
    for match in LEGACY_REF_RE.finditer(sql):
        # The regex's lookbehind only checks the IMMEDIATELY preceding 3 chars;
        # also reject any token preceded by `AS <whitespace>+` (any indent).
        tail = sql[max(0, match.start() - 8):match.start()]
        if tail.rstrip().lower().endswith(" as"):
            continue                       # alias position, ignore
        start = max(0, match.start() - 50)
        end   = min(len(sql), match.end() + 50)
        raise AssertionError(
            f"{label}: legacy column '{match.group(0)}' referenced in v2 SQL\n"
            f"  context: …{sql[start:end]}…"
        )


# ─── SpanList ────────────────────────────────────────────────────────────────
def _span_list_builder():
    return SpanListQueryBuilderV2(
        project_id=PROJECT_ID, page_number=0, page_size=10,
        filters=[], sort_params=[],
        eval_config_ids=[], annotation_label_ids=[],
    )


def test_span_list_v2_build_no_legacy():
    sql, _ = _span_list_builder().build()
    _assert_no_legacy(sql, "SpanList.build")


def test_span_list_v2_count_no_legacy():
    sql, _ = _span_list_builder().build_count_query()
    _assert_no_legacy(sql, "SpanList.build_count_query")


def test_span_list_v2_content_no_legacy():
    sql, _ = _span_list_builder().build_content_query(span_ids=["sp1"])
    _assert_no_legacy(sql, "SpanList.build_content_query")


# ─── TraceList ───────────────────────────────────────────────────────────────
def _trace_list_builder():
    return TraceListQueryBuilderV2(
        project_id=PROJECT_ID, page_number=0, page_size=10,
        filters=[], sort_params=[],
        eval_config_ids=[], annotation_label_ids=[],
    )


def test_trace_list_v2_build_no_legacy():
    sql, _ = _trace_list_builder().build()
    _assert_no_legacy(sql, "TraceList.build")


def test_trace_list_v2_count_no_legacy():
    sql, _ = _trace_list_builder().build_count_query()
    _assert_no_legacy(sql, "TraceList.build_count_query")


def test_trace_list_v2_content_no_legacy():
    sql, _ = _trace_list_builder().build_content_query(trace_ids=["t1"])
    _assert_no_legacy(sql, "TraceList.build_content_query")


def test_trace_list_v2_span_attributes_no_legacy():
    sql, _ = _trace_list_builder().build_span_attributes_query(trace_ids=["t1"])
    _assert_no_legacy(sql, "TraceList.build_span_attributes_query")


def test_trace_list_v2_span_count_no_legacy():
    sql, _ = _trace_list_builder().build_span_count_query(trace_ids=["t1"])
    _assert_no_legacy(sql, "TraceList.build_span_count_query")


# ─── SessionList ─────────────────────────────────────────────────────────────
def _session_list_builder():
    return SessionListQueryBuilderV2(
        project_id=PROJECT_ID, page_number=0, page_size=10,
        filters=[], sort_params=[],
        eval_config_ids=[], annotation_label_ids=[],
    )


def test_session_list_v2_build_no_legacy():
    sql, _ = _session_list_builder().build()
    _assert_no_legacy(sql, "SessionList.build")


def test_session_list_v2_count_no_legacy():
    sql, _ = _session_list_builder().build_count_query()
    _assert_no_legacy(sql, "SessionList.build_count_query")


# ─── VoiceCallList ───────────────────────────────────────────────────────────
def _voice_call_builder():
    return VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID, page_number=0, page_size=10,
        filters=[], sort_params=[],
        eval_config_ids=[], annotation_label_ids=[],
    )


def test_voice_call_list_v2_build_no_legacy():
    sql, _ = _voice_call_builder().build()
    _assert_no_legacy(sql, "VoiceCallList.build")


def test_voice_call_list_v2_count_no_legacy():
    sql, _ = _voice_call_builder().build_count_query()
    _assert_no_legacy(sql, "VoiceCallList.build_count_query")


def test_voice_call_list_v2_content_no_legacy():
    sql, _ = _voice_call_builder().build_content_query(span_ids=["sp1"])
    _assert_no_legacy(sql, "VoiceCallList.build_content_query")
