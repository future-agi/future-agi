"""Exact system-graph relation and complement compiler contracts.

These tests pin SQL shape only. They make no ClickHouse calls and are not
performance benchmarks.
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest

from tracer.services.clickhouse.query_builders.agent_graph import AgentGraphQueryBuilder
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
EVAL_CONFIG_ID = "22222222-2222-4222-8222-222222222222"
ANNOTATION_LABEL_ID = "33333333-3333-4333-8333-333333333333"
SECOND_ANNOTATION_LABEL_ID = "55555555-5555-4555-8555-555555555555"
ANNOTATOR_ID = "44444444-4444-4444-8444-444444444444"
START = datetime(2026, 1, 1)
END = datetime(2026, 2, 1)


def _time_filter() -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"],
        },
    }


def _eval_filter() -> dict:
    return {
        "column_id": EVAL_CONFIG_ID,
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than_or_equal",
            "filter_value": 80,
        },
    }


def _annotation_filter() -> dict:
    return {
        "column_id": ANNOTATION_LABEL_ID,
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "approved",
        },
    }


def _end_user_filter() -> dict:
    return {
        "column_id": "user_id",
        "filter_config": {
            "col_type": "TRACE_END_USER",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "customer-42",
        },
    }


def _patch_eval_resolution():
    values = mock.MagicMock()
    values.__iter__ = lambda self: iter([EVAL_CONFIG_ID])
    values.first.return_value = None
    query = mock.MagicMock()
    query.exists.return_value = True
    query.filter.return_value = query
    query.values_list.return_value = values
    objects = mock.MagicMock()
    objects.filter.return_value = query
    templates = mock.MagicMock()
    templates.filter.return_value.values.return_value.first.return_value = None
    return (
        mock.patch(
            "tracer.models.custom_eval_config.CustomEvalConfig.objects",
            objects,
        ),
        mock.patch(
            "model_hub.models.evals_metric.EvalTemplate.no_workspace_objects",
            templates,
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("observe_type", "identity_sql"),
    [
        ("trace", "trace_id IN ("),
        ("span", "tuple(trace_id, id) IN ("),
    ],
)
def test_eval_metric_filter_reads_only_eval_relation(observe_type, identity_sql):
    patch_configs, patch_templates = _patch_eval_resolution()
    with patch_configs, patch_templates:
        plan = compile_exact_graph_row_predicates(
            [_time_filter(), _eval_filter()],
            project_id=PROJECT_ID,
            observe_type=observe_type,
        )

    assert len(plan.predicates) == 1
    predicate = plan.predicates[0]
    assert identity_sql in predicate
    assert "FROM tracer_eval_logger" in predicate
    assert "AS eval_scan" in predicate
    assert "LIMIT 1 BY eval_scan.id" in predicate
    assert "FROM spans" not in predicate
    assert plan.required_matches == (True,)
    assert plan.output_window_only == (False,)
    assert plan.params["graph_filter_1_eval_cfg_1"] == (EVAL_CONFIG_ID,)


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_annotation_filter_uses_one_strictly_project_scoped_score_read(observe_type):
    plan = compile_exact_graph_row_predicates(
        [_time_filter(), _annotation_filter()],
        project_id=PROJECT_ID,
        observe_type=observe_type,
    )

    predicate = plan.predicates[0]
    assert predicate.count("FROM model_hub_score AS s FINAL") == 1
    assert "s.tracer_project_id = toUUID(" in predicate
    assert "s._peerdb_is_deleted = 0" in predicate
    assert "graph_relation_entity_key" in predicate
    assert "FROM spans" not in predicate
    assert plan.params["graph_filter_1_relation_project_id"] == PROJECT_ID
    assert plan.params["graph_filter_1_annotation_text_1"] == "approved"


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("wants_complete", [True, False])
def test_has_annotation_uses_all_configured_labels_with_correct_boolean_algebra(
    observe_type,
    wants_complete,
):
    has_annotation = {
        "column_id": "has_annotation",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": wants_complete,
        },
    }
    plan = compile_exact_graph_row_predicates(
        [_time_filter(), has_annotation],
        project_id=PROJECT_ID,
        observe_type=observe_type,
        annotation_label_ids=[ANNOTATION_LABEL_ID, SECOND_ANNOTATION_LABEL_ID],
    )

    assert len(plan.predicates) == 2
    assert all("s.label_id = toUUID(" in predicate for predicate in plan.predicates)
    assert all("s._peerdb_is_deleted = 0" in predicate for predicate in plan.predicates)
    assert plan.required_matches == (wants_complete, wants_complete)
    if wants_complete:
        assert plan.match_condition_groups == (((0, True),), ((1, True),))
    else:
        # Incomplete means at least one configured label is missing, not that
        # every label must be missing.
        assert plan.match_condition_groups == (((0, False), (1, False)),)
    assert plan.params["graph_filter_1_annotation_label_1"] == ANNOTATION_LABEL_ID
    assert (
        plan.params["graph_filter_1_requirement_1_annotation_label_1"]
        == SECOND_ANNOTATION_LABEL_ID
    )


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("wants_complete", [True, False])
def test_has_annotation_with_no_configured_labels_is_exact(
    observe_type,
    wants_complete,
):
    has_annotation = {
        "column_id": "has_annotation",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": wants_complete,
        },
    }
    plan = compile_exact_graph_row_predicates(
        [_time_filter(), has_annotation],
        project_id=PROJECT_ID,
        observe_type=observe_type,
        annotation_label_ids=[],
    )

    assert plan.predicates == ("1 = 1",)
    assert plan.required_matches == (wants_complete,)
    assert plan.match_condition_groups == (((0, wants_complete),),)
    assert plan.params == {}


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_has_annotation_incomplete_renders_or_of_missing_labels(observe_type):
    has_annotation = {
        "column_id": "has_annotation",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": False,
        },
    }
    query, _params = TimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), has_annotation],
        interval="day",
        exact_snapshot=True,
        observe_type=observe_type,
        start_date=START,
        end_date=END,
        annotation_label_ids=[ANNOTATION_LABEL_ID, SECOND_ANNOTATION_LABEL_ID],
    ).build()

    if observe_type == "trace":
        assert "(graph_match_0 = 0 OR graph_match_1 = 0)" in query
    else:
        assert "(graph_row_match_0 = 0 OR graph_row_match_1 = 0)" in query


@pytest.mark.unit
def test_agent_graph_uses_the_same_missing_label_or_contract():
    has_annotation = {
        "column_id": "has_annotation",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": False,
        },
    }
    query, _params = AgentGraphQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), has_annotation],
        annotation_label_ids=[ANNOTATION_LABEL_ID, SECOND_ANNOTATION_LABEL_ID],
    ).build()

    assert "(graph_match_0 = 0 OR graph_match_1 = 0)" in query


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_score_identity_prefers_the_view_authoritative_key(observe_type):
    plan = compile_exact_graph_row_predicates(
        [_time_filter(), _annotation_filter()],
        project_id=PROJECT_ID,
        observe_type=observe_type,
    )

    predicate = plan.predicates[0]
    has_span = "notEmpty(ifNull(s.observation_span_id, ''))"
    valid_trace = "NOT isNull(s.trace_id)"
    if observe_type == "span":
        assert f"if({has_span}, concat('span:'" in predicate
        assert f"if(NOT ({has_span}) AND ({valid_trace}" in predicate
    else:
        assert f"if({valid_trace}" in predicate
        assert "if(NOT (NOT isNull(s.trace_id)" in predicate
        assert f"AND ({has_span}), concat('span:'" in predicate


@pytest.mark.unit
def test_end_user_filter_expands_curated_and_remapped_ids_without_spans_subquery():
    plan = compile_exact_graph_row_predicates(
        [_time_filter(), _end_user_filter()],
        project_id=PROJECT_ID,
        observe_type="trace",
    )

    predicate = plan.predicates[0]
    assert predicate.count("FROM end_users AS eu FINAL") == 1
    assert predicate.count("FROM end_user_id_remap FINAL") == 1
    assert "eu.project_id = toUUID(" in predicate
    assert "graph_relation_end_user_id" in predicate
    assert "FROM spans" not in predicate
    assert plan.params["graph_filter_1_relation_project_id"] == PROJECT_ID


@pytest.mark.unit
def test_negative_end_user_filter_requires_no_matching_sibling():
    negative_filter = _end_user_filter()
    negative_filter["filter_config"]["filter_op"] = "not_equals"

    plan = compile_exact_graph_row_predicates(
        [_time_filter(), negative_filter],
        project_id=PROJECT_ID,
        observe_type="trace",
    )

    assert len(plan.predicates) == 1
    assert plan.required_matches == (False,)
    assert "end_user_id IN (" in plan.predicates[0]
    assert "NOT (" not in plan.predicates[0]


@pytest.mark.unit
def test_null_end_user_filter_uses_direct_presence_without_relation_scan():
    null_filter = _end_user_filter()
    null_filter["filter_config"].update(
        filter_op="is_null",
        filter_value=None,
    )

    plan = compile_exact_graph_row_predicates(
        [_time_filter(), null_filter],
        project_id=PROJECT_ID,
        observe_type="trace",
    )

    assert plan.required_matches == (False,)
    assert "NOT isNull(end_user_id)" in plan.predicates[0]
    assert "FROM end_users" not in plan.predicates[0]


@pytest.mark.unit
def test_negative_global_annotator_is_existence_and_no_match_requirements():
    annotator_filter = {
        "column_id": "annotator",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "annotator",
            "filter_op": "not_in",
            "filter_value": [ANNOTATOR_ID],
        },
    }

    plan = compile_exact_graph_row_predicates(
        [_time_filter(), annotator_filter],
        project_id=PROJECT_ID,
        observe_type="trace",
    )

    assert len(plan.predicates) == 2
    assert plan.required_matches == (True, False)
    assert all("FROM model_hub_score AS s FINAL" in p for p in plan.predicates)
    assert "s.annotator_id IN" not in plan.predicates[0]
    assert "s.annotator_id IN" in plan.predicates[1]
    assert plan.params["graph_filter_1_requirement_1_annotators_1"] == (ANNOTATOR_ID,)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operator", "value", "expected_ranges"),
    [
        ("not_equals", "2026-01-10T12:34:56.123456Z", 1),
        (
            "not_between",
            ["2026-01-10T00:00:00Z", "2026-01-12T00:00:00Z"],
            1,
        ),
    ],
)
def test_datetime_complements_are_contribution_predicates_not_trace_flags(
    operator,
    value,
    expected_ranges,
):
    complement = {
        "column_id": "start_time",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": operator,
            "filter_value": value,
        },
    }
    plan = compile_exact_graph_row_predicates(
        [_time_filter(), complement],
        project_id=PROJECT_ID,
        observe_type="trace",
    )

    assert plan.predicates == ()
    assert len(plan.contribution_predicates) == expected_ranges
    assert "fromUnixTimestamp64Micro" in plan.contribution_predicates[0]
    assert set(plan.params) == {
        "graph_datetime_0_start",
        "graph_datetime_0_end",
    }


@pytest.mark.unit
def test_combined_relations_and_attributes_keep_one_compact_spans_source():
    scalar_filter = {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["Rechazado", "Aprobado"],
        },
    }
    patch_configs, patch_templates = _patch_eval_resolution()
    with patch_configs, patch_templates:
        query, params = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=[
                _time_filter(),
                _eval_filter(),
                _annotation_filter(),
                _end_user_filter(),
                scalar_filter,
            ],
            interval="day",
            exact_snapshot=True,
            observe_type="trace",
            start_date=START,
            end_date=END,
        ).build()

    assert query.count("FROM spans") == 1
    assert "FROM spans FINAL" not in query
    assert "argMax(" in query
    assert "AS graph_physical_versions" in query
    assert query.count("FROM tracer_eval_logger") == 1
    assert query.count("AS eval_scan") == 1
    assert query.count("FROM model_hub_score AS s FINAL") == 1
    assert query.count("FROM end_users AS eu FINAL") == 1
    assert "additional_table_filters" not in query
    assert "SAMPLE" not in query.upper()
    assert "OVER (PARTITION BY trace_id) AS graph_match_" not in query
    assert "GROUP BY trace_id, graph_bucket, graph_in_output_window" in query
    assert "groupArrayIf(" in query
    assert "ARRAY JOIN graph_output_buckets" in query
    assert "sum(tupleElement(graph_output_bucket, 2))" in query
    assert "greatest(sum(tupleElement(graph_output_bucket, 5)), 1)" in query

    prewhere = query.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "project_id" in prewhere and "start_time" in prewhere
    assert "attrs_" not in prewhere
    compact_suffix = query.split(") AS graph_physical_versions", 1)[1]
    assert "attrs_string" not in compact_suffix
    assert "attrs_number" not in compact_suffix
    assert "attrs_bool" not in compact_suffix
    assert "attributes_extra" not in compact_suffix
    assert params["graph_witness_start_date"] < START
    assert params["graph_witness_end_date"] > END


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_negative_relation_requirement_is_applied_at_the_correct_scope(observe_type):
    no_annotations = {
        "column_id": "has_annotation",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": False,
        },
    }
    query, _params = TimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(), no_annotations],
        interval="day",
        exact_snapshot=True,
        observe_type=observe_type,
        start_date=START,
        end_date=END,
    ).build()

    if observe_type == "trace":
        assert "max(graph_bucket_match_0) AS graph_match_0" in query
        assert "HAVING graph_match_0 = 0" in query
        assert "max(toUInt8(ifNull((NOT" not in query
    else:
        assert "graph_bucket_match_0" not in query
        assert "toUInt8(ifNull((arrayExists(" in query
        assert "graph_row_match_0 = 0" in query
