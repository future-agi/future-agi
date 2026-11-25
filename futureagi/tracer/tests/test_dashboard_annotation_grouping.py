"""Service-free contracts for own-label annotation metric grouping.

These tests compile real base/V2 SQL; they do not claim database execution or
companion span-metric latest/observation-only join qualification.
"""

from copy import deepcopy
from uuid import UUID

import pytest

from tracer.constants import dashboard as dashboard_constants
from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
)
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)

LABEL = "44444444-4444-4444-4444-444444444444"
OTHER_LABEL = "55555555-5555-5555-5555-555555555555"
KINDS = ("numeric", "star", "text", "thumbs_up_down", "single", "multiple")
MODES = ("base", "v2-physical", "v2-latest")
NUMBER = (
    "if(JSONHas(a.value, 'rating'), "
    "JSONExtract(a.value, 'rating', 'Nullable(Float64)'), "
    "JSONExtract(a.value, 'value', 'Nullable(Float64)'))"
)


def config_for(kind="numeric", *, grouped=True):
    output_type = "categorical" if kind in ("single", "multiple") else kind
    metric = {
        "id": LABEL,
        "name": "same display name",
        "label_id": LABEL,
        "type": "annotation_metric",
        "source": "both",
        "output_type": output_type,
        "aggregation": "count" if kind == "text" else "avg",
    }
    return {
        "project_ids": ["11111111-1111-4111-8111-111111111111"],
        "organization_id": "22222222-2222-4222-8222-222222222222",
        "workspace_id": "33333333-3333-4333-8333-333333333333",
        "time_range": {
            "custom_start": "2026-09-09T00:00:00Z",
            "custom_end": "2026-09-11T00:00:00Z",
        },
        "granularity": "day",
        "metrics": [metric],
        "filters": [],
        "breakdowns": [
            {
                "name": LABEL,
                "label_id": LABEL,
                "type": "annotation_metric",
                "source": "both",
                "output_type": output_type,
            }
        ]
        if grouped
        else [],
    }


def compile_metric(config, mode):
    before = deepcopy(config)
    builder = (DashboardQueryBuilder if mode == "base" else DashboardQueryBuilderV2)(
        config
    )
    builder._latest_state_spans_required = mode == "v2-latest"
    try:
        return builder.build_metric_query(config["metrics"][0])
    finally:
        assert config == before


def expected_aggregate(kind):
    if kind in ("single", "multiple", "text"):
        return "count()"
    if kind == "thumbs_up_down":
        value = "JSONExtract(a.value, 'value', 'Nullable(String)')"
        return f"countIf({value} = 'up') * 100.0 / greatest(countIf({value} IS NOT NULL), 1)"
    return f"avg({NUMBER})"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("kind", KINDS)
def test_grouping_uses_own_score_and_preserves_ungrouped_query(kind, mode):
    grouped = config_for(kind)
    flat = config_for(kind, grouped=False)
    sql, params = compile_metric(grouped, mode)
    flat_sql, flat_params = compile_metric(flat, mode)
    marker = "\nFROM model_hub_score AS a FINAL\n"
    select, tail = sql.split(marker, 1)
    flat_select, flat_tail = flat_sql.split(marker, 1)

    assert "toStartOfDay(a.created_at) AS time_bucket" in select
    assert f"{expected_aggregate(kind)} AS value" in select
    assert f"{expected_aggregate(kind)} AS value" in flat_select
    assert "breakdown_value" not in flat_sql
    if kind in ("numeric", "star"):
        dimension = f"if({NUMBER} IS NULL, '(not set)', toString(round({NUMBER}, 1)))"
    elif kind in ("single", "multiple"):
        # No configured choices or synthetic missing membership; [] emits no group.
        dimension = "arrayJoin(arrayDistinct(JSONExtract(a.value, 'selected', 'Array(String)')))"
    else:
        key = "text" if kind == "text" else "value"
        dimension = (
            f"ifNull(JSONExtract(a.value, '{key}', 'Nullable(String)'), '(not set)')"
        )
    assert f"{dimension} AS breakdown_value" in select
    assert "GROUP BY time_bucket, breakdown_value" in tail
    assert "ORDER BY time_bucket, breakdown_value" in tail
    assert select == flat_select + f", {dimension} AS breakdown_value"
    # Stronger than checking a few guard substrings: every FROM/JOIN/WHERE
    # clause, setting and binding is unchanged by selecting this dimension.
    assert tail.replace("time_bucket, breakdown_value", "time_bucket") == flat_tail
    assert params == flat_params
    assert "JOIN model_hub_score" not in sql
    assert params["annotation_label_id"] == LABEL
    assert params["annotation_organization_id"] == grouped["organization_id"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "aggregation", ["count", "count_distinct", "sum", "min", "max", "p95"]
)
def test_numeric_aggregation_is_not_changed_by_grouping(mode, aggregation):
    config = config_for()
    config["metrics"][0]["aggregation"] = aggregation
    sql, params = compile_metric(config, mode)
    flat = deepcopy(config)
    flat["breakdowns"] = []
    flat_sql, flat_params = compile_metric(flat, mode)
    assert sql.split("\nFROM", 1)[0].startswith(flat_sql.split("\nFROM", 1)[0] + ", ")
    assert params == flat_params


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("placement", ["global", "metric"])
def test_annotation_filter_stays_subject_membership(mode, placement):
    config = config_for()
    target = config if placement == "global" else config["metrics"][0]
    target["filters"] = [
        {
            "metric_type": "annotation_metric",
            "metric_name": LABEL,
            "operator": "equal_to",
            "value": 25,
            "output_type": "numeric",
        }
    ]
    sql, params = compile_metric(config, mode)
    flat = deepcopy(config)
    flat["breakdowns"] = []
    flat_sql, flat_params = compile_metric(flat, mode)
    assert (
        sql.split("\nFROM", 1)[1].replace("time_bucket, breakdown_value", "time_bucket")
        == flat_sql.split("\nFROM", 1)[1]
    )
    assert "annotation_ann_metric_filter_0" in sql
    assert params["ann_metric_0_val"] == 25
    assert params == flat_params


INVALID_BREAKDOWNS = [
    [{"name": OTHER_LABEL, "type": "annotation_metric"}],
    [{"name": LABEL, "label_id": OTHER_LABEL, "type": "annotation_metric"}],
    [{"name": LABEL, "type": "system_metric"}],
    [{"name": LABEL, "type": "custom_attribute"}],
    [{"name": LABEL, "type": "eval_metric"}],
    [{"name": LABEL}],
    [{"name": LABEL, "type": "annotation_metric", "source": "datasets"}],
    [{"name": LABEL, "type": "annotation_metric", "source": "simulation"}],
    [{"name": LABEL, "type": "annotation_metric", "source": "unknown"}],
    [{"name": LABEL, "type": "annotation_metric", "source": None}],
    [{"name": LABEL, "type": "annotation_metric"}] * 2,
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("breakdowns", INVALID_BREAKDOWNS)
def test_builder_rejects_unsupported_breakdowns_without_mutation(mode, breakdowns):
    config = config_for()
    config["breakdowns"] = deepcopy(breakdowns)
    with pytest.raises(InvalidMetricCombinationError, match="[Aa]nnotation"):
        compile_metric(config, mode)


@pytest.mark.parametrize("source", ["traces", "all", "both", ""])
@pytest.mark.parametrize("fallback", [False, True])
def test_shared_guard_canonical_identity_and_trace_sources(source, fallback):
    config = config_for()
    metric, breakdown = config["metrics"][0], config["breakdowns"][0]
    metric["source"] = breakdown["source"] = source
    if fallback:
        metric.pop("label_id")
        metric["name"] = LABEL
        breakdown.pop("label_id")
    else:
        # A UUID instance and its canonical string denote the same label.
        metric["label_id"] = UUID(LABEL)
        breakdown["name"] = "display name is not the identity"
    before = deepcopy(config)
    assert (
        dashboard_constants.annotation_breakdown_error(
            config["metrics"], config["breakdowns"]
        )
        is None
    )
    assert config == before


@pytest.mark.parametrize("breakdowns", INVALID_BREAKDOWNS)
def test_shared_guard_rejects_invalid_without_mutation(breakdowns):
    config = config_for()
    config["breakdowns"] = deepcopy(breakdowns)
    before = deepcopy(config)
    assert dashboard_constants.annotation_breakdown_error(
        config["metrics"], config["breakdowns"]
    )
    assert config == before


@pytest.mark.parametrize("label", ["", "same display name", "not-a-uuid"])
def test_display_names_cannot_establish_label_identity(label):
    config = config_for()
    for item in [config["metrics"][0], config["breakdowns"][0]]:
        item.pop("label_id")
        item["name"] = label
    assert dashboard_constants.annotation_breakdown_error(
        config["metrics"], config["breakdowns"]
    )
    with pytest.raises(InvalidMetricCombinationError):
        compile_metric(config, "base")


@pytest.mark.parametrize("source", ["datasets", "simulation"])
def test_shared_guard_does_not_own_other_adapters(source):
    config = config_for()
    config["metrics"][0]["source"] = source
    config["breakdowns"] = INVALID_BREAKDOWNS[2]
    before = deepcopy(config)
    assert (
        dashboard_constants.annotation_breakdown_error(
            config["metrics"], config["breakdowns"]
        )
        is None
    )
    assert config == before


def test_shared_guard_no_breakdown_and_non_annotation_metrics_unchanged():
    config = config_for(grouped=False)
    config["metrics"][0]["label_id"] = "legacy display identity"
    assert dashboard_constants.annotation_breakdown_error(config["metrics"], []) is None
    assert (
        dashboard_constants.annotation_breakdown_error(
            [{"type": "system_metric", "name": "trace_count"}],
            INVALID_BREAKDOWNS[-1],
        )
        is None
    )


def test_shared_guard_checks_every_annotation_metric_in_mixed_widget():
    config = config_for()
    second = deepcopy(config["metrics"][0])
    config["metrics"] += [{"type": "system_metric", "name": "trace_count"}, second]
    assert (
        dashboard_constants.annotation_breakdown_error(
            config["metrics"], config["breakdowns"]
        )
        is None
    )
    second["label_id"] = OTHER_LABEL
    before = deepcopy(config)
    assert dashboard_constants.annotation_breakdown_error(
        config["metrics"], config["breakdowns"]
    )
    assert config == before


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("source", ["datasets", "simulation", "unknown", None])
def test_grouped_metric_cannot_be_misrouted_to_trace_builder(mode, source):
    config = config_for()
    config["metrics"][0]["source"] = source
    with pytest.raises(InvalidMetricCombinationError, match="[Aa]nnotation"):
        compile_metric(config, mode)


@pytest.mark.parametrize("mode", MODES)
def test_mixed_widget_builder_does_not_ignore_other_annotation_label(mode):
    config = config_for()
    second = deepcopy(config["metrics"][0])
    second["label_id"] = OTHER_LABEL
    config["metrics"].append(second)
    before = deepcopy(config)
    builder = (DashboardQueryBuilder if mode == "base" else DashboardQueryBuilderV2)(
        config
    )
    builder._latest_state_spans_required = mode == "v2-latest"
    with pytest.raises(InvalidMetricCombinationError, match="same annotation label"):
        builder.build_all_queries()
    assert config == before


@pytest.mark.parametrize(
    "breakdown_label",
    [
        "abcdef00-1234-4234-8234-1234567890ab",
        "abcdef001234423482341234567890ab",
    ],
)
def test_shared_guard_normalizes_uuid_spelling_without_rewriting_input(breakdown_label):
    config = config_for()
    config["metrics"][0]["label_id"] = "ABCDEF00-1234-4234-8234-1234567890AB"
    config["breakdowns"][0]["label_id"] = breakdown_label
    before = deepcopy(config)
    assert (
        dashboard_constants.annotation_breakdown_error(
            config["metrics"], config["breakdowns"]
        )
        is None
    )
    assert config == before
