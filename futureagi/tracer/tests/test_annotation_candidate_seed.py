"""Per-label annotations seed exact trace reads without an ordered population walk."""

from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
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

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
LABEL = "22222222-2222-4222-8222-222222222222"


def leaf(kind, op, value=None):
    return {
        "column_id": LABEL + ("**annotator" if kind == "annotator" else ""),
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


@pytest.mark.parametrize(
    "kind,value",
    [
        ("text", "50%_done"),
        ("number", 4),
        ("boolean", True),
        ("thumbs", "up"),
        ("categorical", ["yes"]),
        ("annotator", PROJECT),
    ],
)
@pytest.mark.parametrize("op", ["equals", "not_equals", "is_not_null"])
def test_per_label_positive_score_relation_seeds_and_reclassifies(kind, value, op):
    start = datetime(2025, 9, 5, tzinfo=UTC)
    end = datetime(2026, 9, 5, tzinfo=UTC)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start.isoformat(), end.isoformat()],
                },
            },
            leaf(kind, op, value),
        ],
    )
    assert builder.supports_filter_candidate_seed_page()
    assert not builder.supports_filter_root_time_discovery()
    predicate, params = builder._positive_relational_seed()
    assert "model_hub_score AS s FINAL" in predicate
    assert "s.tracer_project_id = toUUID(%(project_id)s)" in predicate
    assert "s._peerdb_is_deleted = 0" in predicate
    assert "s.deleted = false" in predicate
    assert "s.created_at >=" not in predicate
    assert LABEL in params.values()
    # Public selector slices use the builder's normalized UTC-naive bounds.
    slice_start, slice_end = builder.parse_time_range(builder.filters)
    assert builder.recommended_filter_initial_slice_width() == slice_end - slice_start
    assert builder.recommended_filter_max_slice_width() == slice_end - slice_start
    sql, _ = builder.build_filter_candidate_seed_page(
        slice_start=slice_start,
        slice_end=slice_end,
        limit=26,
    )
    assert "model_hub_score" in sql
    assert builder._bounded_match_filters() == builder.filters
    assert builder.filter_candidate_seed_proves_result_order()


@pytest.mark.parametrize(
    "kind", ["text", "number", "thumbs", "categorical", "annotator"]
)
def test_missing_annotation_has_no_positive_seed(kind):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=[leaf(kind, "is_null")]
    )
    assert builder._positive_relational_seed_filter() is None


def test_same_named_raw_attribute_is_not_a_score_relation():
    item = leaf("text", "equals", "yes")
    item["filter_config"]["col_type"] = "SPAN_ATTRIBUTE"
    builder = TraceListQueryBuilderV2(project_id=PROJECT, filters=[item])
    assert builder._positive_relational_seed_filter() is None


@pytest.mark.parametrize("cls", [SpanListQueryBuilder, SpanListQueryBuilderV2])
@pytest.mark.parametrize(
    "kind,value",
    [
        ("text", "50%_done"),
        ("number", 4),
        ("thumbs", "up"),
        ("categorical", ["yes"]),
        ("annotator", PROJECT),
    ],
)
@pytest.mark.parametrize("op", ["equals", "not_equals", "is_not_null"])
def test_span_annotation_seed_keeps_scoped_identity_and_exact_reclassification(
    cls, kind, value, op
):
    builder = cls(
        project_id=PROJECT, filters=[leaf(kind, op, value)], bounded_internal_scan=True
    )
    start, end = builder.parse_time_range(builder.filters)
    assert builder.supports_filter_candidate_seed_page()
    assert builder.recommended_filter_initial_slice_width() == end - start
    assert builder.recommended_filter_max_slice_width() == end - start
    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=start, slice_end=end, limit=26
    )
    assert "tuple(trace_id, id) IN (" in sql
    assert "s.tracer_project_id = toUUID(%(project_id)s)" in sql
    assert "s._peerdb_is_deleted = 0" in sql
    assert "s.created_at >=" not in sql
    assert "root_sp.trace_id = toString(s.trace_id)" in sql
    assert "parent_span_id IS NULL OR parent_span_id = ''" in sql
    if cls is SpanListQueryBuilderV2:
        # CH25 replaces within the complete hourly physical key; corrected
        # exact timestamps are values of that identity, not new identities.
        assert (
            "LIMIT 1 BY project_id, trace_id, id, toStartOfHour(start_time), "
            "observation_type, service_name" in sql
        )
    else:
        assert "LIMIT 1 BY project_id, trace_id, id, start_time" in sql
    assert params["filter_seed_limit"] == 26
    assert builder.filter_candidate_seed_proves_result_order()
    classified, _ = builder.build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT,
                "trace_id": "trace",
                "id": "span",
                "start_time": start,
                "observation_type": "span",
                "service_name": "test-service",
            }
        ]
    )
    assert "model_hub_score AS s FINAL" in classified
    assert "candidate_span_entities" in classified
    assert "WITH resolved_annotation_candidates AS (" in classified
    assert "argMax(tuple(parent_span_id)" in classified
    assert "SELECT * EXCEPT (parent_span_id)" in classified
    if cls is SpanListQueryBuilderV2:
        # Membership stays on each latest physical row, with one finite
        # candidate-set guard per Score arm instead of scored/root span joins.
        assert classified.count("FROM resolved_annotation_candidates") == (
            1 + classified.count("FROM model_hub_score AS s FINAL")
        )
        assert "ifNull(parent_span_id, '') = ''" in classified
        assert "toString(s.tracer_project_id)" in classified
        assert "root_sp." not in classified
        assert "scored_sp." not in classified
    else:
        assert "FROM resolved_annotation_candidates WHERE project_id" in classified
        assert "tuple(trace_id, id, toUnixTimestamp64Micro(start_time))" in classified
        assert "root_sp.start_time" in classified
    # Only the version-resolving candidate source can read spans.
    assert "SELECT id, trace_id FROM spans" not in classified


def test_resolved_annotation_relation_cannot_be_a_user_supplied_sql_fragment():
    from tracer.services.clickhouse.query_builders.filters import (
        ClickHouseFilterBuilder,
    )

    for table in ("spans; SELECT 1", "foo.bar", "(SELECT 1)"):
        with pytest.raises(ValueError, match="internal span relation"):
            ClickHouseFilterBuilder(
                query_mode="span", resolved_candidate_spans_table=table
            )


@pytest.mark.parametrize("mode", ["is_null", "raw", "org", "sample", "custom_sort"])
def test_span_annotation_candidate_path_excludes_unsupported_acquisition(mode):
    item = leaf("text", "is_null" if mode == "is_null" else "equals", "value")
    kwargs = {"project_id": PROJECT, "filters": [item]}
    if mode == "raw":
        item["filter_config"]["col_type"] = "SPAN_ATTRIBUTE"
    elif mode == "org":
        kwargs = {"project_ids": [PROJECT], "filters": [item]}
    elif mode == "sample":
        kwargs.update(bounded_sampling_rate=10, bounded_sampling_salt="test")
    elif mode == "custom_sort":
        kwargs["sort_params"] = [{"column_id": "cost"}]
    builder = SpanListQueryBuilderV2(**kwargs)
    assert not builder.supports_filter_candidate_seed_page()
    assert builder.recommended_filter_initial_slice_width() is None
    assert builder.recommended_filter_query_timeout_ms() is None


@pytest.mark.parametrize("cls", [SpanListQueryBuilder, SpanListQueryBuilderV2])
def test_annotation_span_query_budget_uses_existing_request_settings(cls, settings):
    settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS = 17_000
    settings.FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS = 30_000
    builder = cls(project_id=PROJECT, filters=[leaf("text", "not_in", ["value"])])
    assert builder.recommended_filter_query_timeout_ms() == 17_000
    settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS = 45_000
    assert builder.recommended_filter_query_timeout_ms() == 30_000


@pytest.mark.parametrize(
    "cls",
    [
        SpanListQueryBuilder,
        SpanListQueryBuilderV2,
        TraceListQueryBuilderV2,
        VoiceCallListQueryBuilder,
        VoiceCallListQueryBuilderV2,
    ],
)
@pytest.mark.parametrize("minutes", [1, 5, 30])
def test_annotation_seed_slice_recommendation_respects_short_requests(cls, minutes):
    start = datetime(2026, 9, 4, tzinfo=UTC)
    width = timedelta(minutes=minutes)
    builder = cls(
        project_id=PROJECT,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start.isoformat(), (start + width).isoformat()],
                },
            },
            leaf("text", "equals", "value"),
        ],
    )
    assert builder.supports_filter_candidate_seed_page()
    expected = width if minutes >= 5 else None
    assert builder.recommended_filter_initial_slice_width() == expected
    assert builder.recommended_filter_max_slice_width() == expected
