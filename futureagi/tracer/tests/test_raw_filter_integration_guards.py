"""Offline integration guards for explicit attribute provenance.

Exercise real request normalization and builder/selector decisions, not a SQL
interpreter or a production benchmark. Native controls retain their contract.
"""

from datetime import datetime, timedelta

import pytest
from rest_framework.exceptions import ValidationError

from tracer.selectors.trace_filter_reads import long_filtered_read_requires_cursor
from tracer.services.clickhouse.bounded_graph_reads import _filters_for_window
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.filter_attestation import (
    applied_filter_attestation,
    applied_filter_leaves,
    graph_execution_filters,
)
from tracer.tests.test_explicit_attribute_source_precedence import (
    PROJECT,
    _drop_legacy_ch_spans_mvs,  # noqa: F401 -- disable integration DDL
    _ensure_test_score_tenant_column,  # noqa: F401 -- disable integration DDL
    _offline_only,  # noqa: F401 -- no network
    leaf,
    public,
)

pytestmark = pytest.mark.unit
END = datetime(2026, 9, 5)
CONFIG = "00000000-0000-4000-8000-000000000009"
LABEL = "00000000-0000-4000-8000-000000000008"


def window(days=7):
    return leaf(
        "created_at",
        "datetime",
        "between",
        [
            (END - timedelta(days=days)).isoformat(),
            END.isoformat(),
        ],
        "SYSTEM_METRIC",
    )


@pytest.mark.parametrize("key", ["created_at", "start_time"])
@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "operation,value", [("equals", "clock"), ("not_equals", "clock"), ("is_null", None)]
)
def test_graph_windows_and_attestation_retain_raw_dates(key, days, operation, value):
    raw = leaf(key, "text", operation, value)
    items = public([window(days), raw])
    execution = graph_execution_filters(items)
    assert raw in execution
    assert applied_filter_leaves(execution) == [raw]
    assert BaseQueryBuilder.parse_time_range(execution, strict=True) == (
        END - timedelta(days=days),
        END,
    )
    stratum = _filters_for_window(
        execution, window_start=END - timedelta(hours=1), window_end=END
    )
    assert raw in stratum
    assert BaseQueryBuilder.parse_time_range(stratum, strict=True) == (
        END - timedelta(hours=1),
        END,
    )
    evidence = applied_filter_attestation(
        project_id=PROJECT, observe_type="trace", filters=execution
    )
    empty = applied_filter_attestation(
        project_id=PROJECT, observe_type="trace", filters=[window(days)]
    )
    assert evidence["query_applied_filter_count"] == 1
    assert (
        evidence["query_applied_filter_sha256"] != empty["query_applied_filter_sha256"]
    )


@pytest.mark.parametrize(
    "builder_cls",
    [
        TraceListQueryBuilder,
        TraceListQueryBuilderV2,
        SpanListQueryBuilder,
        SpanListQueryBuilderV2,
    ],
)
@pytest.mark.parametrize("key", ["created_at", "start_time"])
def test_raw_date_is_an_active_filter_requiring_long_window_cursor(builder_cls, key):
    raw = leaf(key)
    builder = builder_cls(project_id=PROJECT, filters=public([window(), raw]))
    assert builder._active_non_time_filters() == [raw]
    assert builder.supports_bounded_filter_scan()
    assert builder.requires_cursor_for_long_filtered_read()
    assert long_filtered_read_requires_cursor(
        builder.filters, request_start=END - timedelta(days=7), request_end=END
    )
    assert not long_filtered_read_requires_cursor(
        [window()], request_start=END - timedelta(days=7), request_end=END
    )
    assert not long_filtered_read_requires_cursor(
        builder.filters, request_start=END - timedelta(minutes=30), request_end=END
    )


@pytest.mark.parametrize("key", ["created_at", "start_time"])
def test_raw_date_changes_cannot_bypass_membership_shape_validation(key):
    with pytest.raises(ValueError, match="differ only by positive time bounds"):
        TraceListQueryBuilderV2(
            project_id=PROJECT,
            filters=public([window(7), leaf(key, value=1)]),
            bounded_membership_filters=public([window(30), leaf(key, value=2)]),
        )
    same = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=public([window(7), leaf(key, value=1)]),
        bounded_membership_filters=public([window(30), leaf(key, value=1)]),
    )
    assert same._bounded_membership_shape(same.filters) == [leaf(key, value=1)]


@pytest.mark.parametrize("key", ["has_eval", "has_annotation"])
def test_raw_existence_name_never_selects_relational_seed(key):
    raw = leaf(key, "boolean", "equals", True)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=public([window(), raw]),
        eval_config_ids=[CONFIG],
        annotation_label_ids=[LABEL],
    )
    assert builder._positive_relational_seed_filter() is None
    # Raw booleans can use the scalar witness accelerator, but never a score
    # relation just because their key resembles a native existence property.
    assert builder.supports_filter_candidate_seed_page()
    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=END - timedelta(days=7), slice_end=END, limit=26
    )
    assert "tracer_eval_logger" not in sql and "model_hub_score" not in sql
    assert "matching_scalar_trace_identities" in sql
    assert "attrs_bool" in sql and key in params.values()
    assert params["filter_seed_limit"] == 26
    classifier, classifier_params = builder.build_filter_match_query(["candidate"])
    assert "attrs_bool" in classifier
    assert key in classifier_params.values()
    assert "WHERE latest_is_deleted = 0" in classifier


@pytest.mark.parametrize("family", ["SYSTEM_METRIC", "NORMAL", ""])
@pytest.mark.parametrize("key", ["has_eval", "has_annotation"])
def test_native_and_legacy_existence_seed_controls_still_work(family, key):
    native = leaf(key, "boolean", "equals", True, family)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=[window(), native],
        eval_config_ids=[CONFIG],
        annotation_label_ids=[LABEL],
    )
    assert builder._positive_relational_seed_filter() == native
    negative = leaf(key, "boolean", "equals", False, family)
    builder.filters = [window(), negative]
    assert builder._positive_relational_seed_filter() is None


@pytest.mark.parametrize(
    "raw_key,native_key",
    [("has_eval", "has_annotation"), ("has_annotation", "has_eval")],
)
def test_raw_name_does_not_hide_a_real_relational_and_leaf(raw_key, native_key):
    raw = leaf(raw_key, "boolean", "equals", True)
    native = leaf(native_key, "boolean", "equals", True, "SYSTEM_METRIC")
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=public([window(), raw, native]),
        eval_config_ids=[CONFIG],
        annotation_label_ids=[LABEL],
    )
    assert builder._positive_relational_seed_filter() == native


@pytest.mark.parametrize(
    "key",
    [
        "cost",
        "model",
        "duration",
        "gen_ai.usage.input_tokens",
        "user_id",
        "end_user_id",
        "created_at",
        "start_time",
    ],
)
def test_all_explicit_raw_aliases_keep_custom_replay_envelope(key):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=public([window(), leaf(key)])
    )
    assert builder._custom_span_attribute_filter_count() == 1
    assert builder.recommended_filter_classify_batch_size() == 200
    assert builder._uses_scalar_coordinate_replay()
    assert "SELECT DISTINCT observation_type, service_name," in (
        builder._filter_classifier_coordinate_predicate()
    )
    assert builder.recommended_filter_classify_read_settings() is not None


@pytest.mark.parametrize("key", ["created_at", "start_time"])
def test_raw_date_witness_alignment_and_structured_cost_count(key):
    raw = leaf(key)
    other = leaf("company_id", "text", "equals", "company")
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=public([window(), raw, other])
    )
    plans = builder._candidate_witness_plans()
    assert len(plans) == 2
    assert plans[0].params["latest_filter_key_0"] == key
    assert plans[1].params["latest_filter_key_1"] == "company_id"
    long_text = leaf(key, "text", "equals", "long-customer-value-" * 8)
    assert builder._candidate_witness_filter_is_selective_exact_text(long_text)
    assert builder._candidate_witness_filter_is_expensive(long_text)
    structured = leaf(key, "array", "contains", ["x"])
    builder.filters = public([window(), structured])
    assert builder._structured_attribute_filter_count() == 1
    assert builder._candidate_witness_filter_is_expensive(structured)


@pytest.mark.parametrize(
    "builder_cls", [TraceListQueryBuilderV2, SpanListQueryBuilderV2]
)
@pytest.mark.parametrize("explicit_window", [False, True])
def test_raw_date_does_not_invent_task_full_state_window(builder_cls, explicit_window):
    filters = ([window()] if explicit_window else []) + [leaf("created_at")]
    builder = builder_cls(
        project_id=PROJECT, filters=public(filters), bounded_internal_scan=True
    )
    sql, params = builder.build_filter_match_query(
        ["candidate"], candidate_full_state=True
    )
    assert ("candidate_start_date" in params) == explicit_window
    assert "attrs_number" in sql
    assert "WHERE latest_is_deleted = 0" in sql


def test_attestation_complement_fixture_must_be_native_public_datetime():
    raw_datetime = leaf("created_at", "datetime", "not_equals", "2026-09-03T00:00:00Z")
    with pytest.raises(
        ValidationError, match="Unsupported filter_type.*SPAN_ATTRIBUTE"
    ):
        public([raw_datetime])
    native = leaf(
        "created_at", "datetime", "not_equals", "2026-09-03T00:00:00Z", "SYSTEM_METRIC"
    )
    execution = graph_execution_filters(public([window(), native]))
    assert applied_filter_leaves(execution) == [native]
    assert BaseQueryBuilder.analyze_bounded_datetime_filters(execution).exclusions == (
        (datetime(2026, 9, 3), datetime(2026, 9, 3, 0, 0, 0, 1)),
    )
