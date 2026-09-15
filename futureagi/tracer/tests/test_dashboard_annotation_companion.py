"""Service-free compilation contracts for annotation companion memberships.

These inspect the actual base/V2 queries, not a second implementation of the
membership algorithm. Database execution, null-mode result fixtures and native
CDC transport are independently owned qualification steps.
"""

import re
from copy import deepcopy
from unittest.mock import patch

import pytest

from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)

LABEL = "44444444-4444-4444-4444-444444444444"
MODES = ("base", "v2-physical", "v2-latest")
KINDS = ("numeric", "star", "text", "thumbs_up_down", "categorical", "choice")


def config_for(kind="numeric", metric="latency", aggregation="avg"):
    return {
        "organization_id": "22222222-2222-4222-8222-222222222222",
        "project_ids": ["11111111-1111-4111-8111-111111111111"],
        "time_range": {
            "custom_start": "2026-09-09T00:00:00Z",
            "custom_end": "2026-09-11T00:00:00Z",
        },
        "granularity": "day",
        "metrics": [
            {"name": metric, "type": "system_metric", "aggregation": aggregation}
        ],
        "filters": [],
        "breakdowns": [
            {
                "name": LABEL,
                "label_id": LABEL,
                "type": "annotation_metric",
                "output_type": kind,
            }
        ],
    }


def compile_query(config, mode):
    original = deepcopy(config)
    cls = DashboardQueryBuilder if mode == "base" else DashboardQueryBuilderV2
    builder = cls(config)
    builder._latest_state_spans_required = mode == "v2-latest"
    sql, params = builder.build_metric_query(config["metrics"][0])
    assert config == original
    return builder, sql, params


def compact(sql):
    return " ".join(sql.split())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("kind", KINDS)
def test_companion_is_latest_typed_distinct_membership_not_raw_score_join(mode, kind):
    config = config_for(kind)
    _, sql, params = compile_query(config, mode)
    text = compact(sql)
    assert "LEFT JOIN model_hub_score AS ann0" not in sql
    assert "SELECT DISTINCT project_id, trace_id," in text
    assert "AS group_key, toUInt8(1) AS matched" in text
    assert "ann0.project_id = s.project_id AND ann0.trace_id = s.trace_id" in text
    assert "avg(s.latency_ms) AS value" in text
    assert "s.parent_span_id IS NULL OR s.parent_span_id = ''" in text
    assert "toStartOfDay(s.start_time) AS time_bucket" in text
    assert params["_ann_bd_label_0"] == LABEL
    assert params["_ann_bd_org_0"] == config["organization_id"]
    assert "arrayJoin" not in sql.split("\nFROM", 1)[0]
    if kind in ("categorical", "choice"):
        assert (
            "arrayDistinct(JSONExtract(ann0_subject.value, 'selected', 'Array(String)'))"
            in text
        )
        assert "arrayMap(item -> toNullable(item)," in text
        assert "CAST(NULL, 'Nullable(String)')" in text
    elif kind in ("numeric", "star"):
        assert "JSONHas(ann0_subject.value, 'rating')" in text
        assert "JSONExtract(ann0_subject.value, 'value', 'Nullable(Float64)')" in text
        assert ", 1))) AS group_key" in text
    else:
        key = "text" if kind == "text" else "value"
        assert (
            f"ifNull(JSONExtract(ann0_subject.value, '{key}', 'Nullable(String)'), '(not set)')"
            in text
        )


@pytest.mark.parametrize("mode", MODES)
def test_score_replay_is_id_bounded_but_all_versions_before_live_scope_checks(mode):
    _, sql, _ = compile_query(config_for(), mode)
    text = compact(sql)
    replay = text.split("FROM model_hub_score AS ann0_score ", 1)[1].split(
        ") AS ann0_latest", 1
    )[0]
    assert (
        replay
        == "PREWHERE ann0_score.id IN (SELECT id FROM ann0_ids) GROUP BY ann0_score.id "
    )
    assert (
        "argMax(tuple(ann0_score.value, ann0_score.label_id, ann0_score.organization_id, ann0_score.source_type, ann0_score.trace_id, ann0_score.observation_span_id, ann0_score.tracer_project_id, ann0_score.deleted, ann0_score._peerdb_is_deleted), ann0_score._peerdb_version) AS score_state"
        in text
    )
    assert (
        "WHERE tupleElement(score_state, 8) = 0 AND tupleElement(score_state, 9) = 0"
        in text
    )
    assert "tupleElement(score_state, 2) = toUUID(%(_ann_bd_label_0)s)" in text
    assert "tupleElement(score_state, 3) = toUUIDOrNull(%(_ann_bd_org_0)s)" in text
    candidates = text.split("ann0_ids AS (", 1)[1].split("), ann0_scores AS", 1)[0]
    assert "ann0_candidate.label_id = toUUID(%(_ann_bd_label_0)s)" in candidates
    assert (
        "ann0_candidate.organization_id = toUUIDOrNull(%(_ann_bd_org_0)s)" in candidates
    )
    assert (
        "ann0_candidate.observation_span_id IN (SELECT id FROM ann0_children)"
        in candidates
    )
    assert "deleted" not in candidates and "created_at" not in candidates
    assert "source_type" not in candidates and ".value" not in candidates
    assert "FROM traces" not in sql and "trace_dict" not in sql


@pytest.mark.parametrize("mode", MODES)
def test_children_are_all_time_latest_unique_and_project_bounded(mode):
    _, sql, params = compile_query(config_for(), mode)
    text = compact(sql)
    children = text.split("ann0_children AS (", 1)[1].split("), ann0_ids AS", 1)[0]
    assert "start_time" not in children and "created_at" not in children
    assert "ann0_span_candidate.project_id IN %(project_ids)s" in children
    assert (
        "(ann0_span_candidate.project_id, ann0_span_candidate.trace_id) IN (SELECT project_id, trace_id FROM ann0_traces)"
        in children
    )
    assert "ann0_span_scan.project_id IN %(project_ids)s" in children
    assert (
        "uniqExact(tuple(ann0_span_scan.project_id, ann0_span_scan.trace_id)) AS identity_count"
        in children
    )
    assert "WHERE identity_count = 1 AND tupleElement(span_state, 3) = 0" in children
    version, deleted = (
        ("_peerdb_version", "_peerdb_is_deleted")
        if mode == "base"
        else ("_version", "is_deleted")
    )
    assert f"ann0_span_scan.{version}" in children
    assert f"ann0_span_scan.{deleted}" in children
    assert "s.start_time >= %(start_date)s" in sql
    assert "s.start_time < %(end_date)s" in sql
    assert params["project_ids"] == config_for()["project_ids"]


@pytest.mark.parametrize("mode", MODES)
def test_supported_subjects_reject_conflicts_and_optional_project_mismatch(mode):
    _, sql, _ = compile_query(config_for(), mode)
    text = compact(sql)
    assert "source_type IN ('trace', 'observation_span')" in text
    assert (
        "ann0_live.source_type = 'trace' AND ifNull(ann0_live.observation_span_id, '') = ''"
        in text
    )
    assert (
        "ann0_live.source_type = 'observation_span' AND ifNull(ann0_child.matched, 0) = 1"
        in text
    )
    assert "ann0_child.project_id = ann0_trace.project_id" in text
    assert (
        "ann0_live.trace_id IS NULL OR toString(ann0_live.trace_id) = ann0_child.trace_id"
        in text
    )
    assert (
        "ann0_live.tracer_project_id IS NULL OR ann0_live.tracer_project_id = ann0_trace.project_id"
        in text
    )
    assert "ann0_live.project_id" not in sql
    assert "HAVING uniqExact(s.project_id) = 1" in text


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("kind", KINDS)
def test_explicit_match_marker_and_empty_sentinel_do_not_depend_on_join_null_mode(
    mode, kind
):
    _, sql, _ = compile_query(config_for(kind), mode)
    text = compact(sql)
    # Same expression handles unmatched marker NULL (join_use_nulls=1) and 0
    # (join_use_nulls=0); no mutable session setting is added by this builder.
    assert (
        "if(ifNull(ann0.matched, 0) = 0, '(not set)', assumeNotNull(ann0.group_key)) AS breakdown_value"
        in text
    )
    assert "(ifNull(ann0.matched, 0) = 0 OR ann0.group_key IS NOT NULL)" in text
    assert "ann0.id IS NULL" not in sql
    assert "join_use_nulls" not in sql


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "metric,aggregation,expression",
    [
        ("latency", "sum", "sum(s.latency_ms)"),
        ("latency", "avg", "avg(s.latency_ms)"),
        ("trace_count", "count", "uniqExact(s.trace_id)"),
        ("span_count", "count", "uniqExact(s.id)"),
    ],
)
def test_fanout_is_removed_in_membership_not_by_changing_metric_aggregation(
    mode, metric, aggregation, expression
):
    _, sql, _ = compile_query(config_for(metric=metric, aggregation=aggregation), mode)
    assert f"{expression} AS value" in sql
    assert "SELECT DISTINCT project_id, trace_id," in compact(sql)
    assert (
        "ANY JOIN" not in sql and "sumDistinct" not in sql and "avgDistinct" not in sql
    )


@pytest.mark.parametrize("mode", MODES)
def test_reuses_original_span_source_for_candidate_clock_and_remaps(mode):
    config = config_for(metric="user_count", aggregation="count_distinct")
    cls = DashboardQueryBuilder if mode == "base" else DashboardQueryBuilderV2
    builder = cls(config)
    builder._latest_state_spans_required = mode == "v2-latest"
    with patch.object(builder, "_spans_source", wraps=builder._spans_source) as source:
        sql, _ = builder.build_metric_query(config["metrics"][0])
    assert any(
        call.args[:3] == ("user_count", [], "s") for call in source.call_args_list
    )
    assert "ann0_traces AS (" in sql
    assert "id_remap" in sql
    assert "s.start_time >= %(start_date)s" in sql


@pytest.mark.parametrize("mode", MODES)
def test_multiple_labels_have_independent_namespaces_without_mutating_inputs(mode):
    config = config_for()
    other = deepcopy(config["breakdowns"][0])
    other["name"] = other["label_id"] = "55555555-5555-4555-8555-555555555555"
    config["breakdowns"].append(other)
    _, sql, params = compile_query(config, mode)
    assert params["_ann_bd_label_1"] == other["label_id"]
    assert "ann1_score.id IN (SELECT id FROM ann1_ids)" in sql
    assert "ann1.project_id = s.project_id" in sql
    assert "ann1.group_key IS NOT NULL" in sql
    assert "concat(" in sql


@pytest.mark.parametrize("latest", [False, True])
def test_v2_batching_keeps_identical_membership_and_separates_metric_filters(latest):
    config = config_for()
    builder = DashboardQueryBuilderV2(config)
    builder._latest_state_spans_required = latest
    metrics = [dict(config["metrics"][0], aggregation=agg) for agg in ("sum", "avg")]
    prepared = tuple((m, *builder.build_metric_query(m)) for m in metrics)
    groups = builder.group_prepared_metric_queries(prepared)
    assert len(groups) == 1 and groups[0][0] == (0, 1)
    grouped = groups[0][1]
    assert grouped is not None
    assert "sum(s.latency_ms)" in grouped.sql and "avg(s.latency_ms)" in grouped.sql
    assert "ann0_score.id IN (SELECT id FROM ann0_ids)" in grouped.sql
    assert grouped.sql.count("SETTINGS") <= 1
    metrics[1]["filters"] = [
        {
            "metric_type": "system_metric",
            "metric_name": "span_kind",
            "operator": "equal_to",
            "value": "LLM",
        }
    ]
    prepared = tuple((m, *builder.build_metric_query(m)) for m in metrics)
    assert len(builder.group_prepared_metric_queries(prepared)) == 2
    assert not re.search(r"ann0_score\.(?:is_deleted|_version)\b", grouped.sql)
