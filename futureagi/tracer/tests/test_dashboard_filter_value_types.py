"""Dashboard eval/annotation filters compile against the value's real type.

A canonical dashboard filter carries the column's declared value type but not
its storage type. The compiler used to default an annotation to ``numeric`` and
an eval to ``SCORE`` whenever the optional ``output_type`` hint was absent, so
every text / categorical / thumbs / choice filter reached ClickHouse as a
Float64 predicate and failed there as an opaque HTTP 400. These tests pin the
authoritative resolution and the build-time rejection that replaces it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tracer.serializers.dashboard import DashboardQuerySerializer
from tracer.services.clickhouse.query_builders import dashboard as dashboard_builder
from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
)
from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata
from tracer.views.dashboard import (
    DashboardExactReadError,
    DashboardViewSet,
    _invalid_metric_combination_cause,
    _normalize_dashboard_query_filters,
)

LABEL_ID = "3f1d6b6c-9c4f-4b26-9b86-1f5a9a2b7d10"
EVAL_ID = "6d0f0b5a-2f43-4f2f-9d6a-64a6a5f0c2b1"


def _filter(column_id, col_type, filter_type, filter_op, filter_value):
    return {
        "column_id": column_id,
        "source": "traces",
        "filter_config": {
            "col_type": col_type,
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def _validated_query(filters):
    body = {
        "workflow": "observability",
        "project_ids": [str(uuid4())],
        "time_range": {"preset": "7D"},
        "granularity": "day",
        "metrics": [{"name": "latency", "type": "system_metric", "aggregation": "avg"}],
        "filters": filters,
    }
    serializer = DashboardQuerySerializer(data=body)
    assert serializer.is_valid(), serializer.errors
    normalized = _normalize_dashboard_query_filters(serializer.validated_data)
    normalized["organization_id"] = str(uuid4())
    normalized["workspace_id"] = str(uuid4())
    return normalized


def _build(config):
    return DashboardQueryBuilder(config).build_all_queries()[0][0]


def _legacy_query(filters):
    """One builder config carrying the flattened, pre-canonical filter shape."""

    return {
        "project_ids": [str(uuid4())],
        "organization_id": str(uuid4()),
        "workspace_id": str(uuid4()),
        "granularity": "day",
        "time_range": {
            "custom_start": "2026-01-01T00:00:00Z",
            "custom_end": "2026-07-01T00:00:00Z",
        },
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ],
        "filters": filters,
        "breakdowns": [],
    }


def _refuse_database(*_args, **_kwargs):
    raise AssertionError("the query compiler must not read PostgreSQL here")


@pytest.fixture
def annotation_label_type(monkeypatch):
    """Serve one annotation label type without touching PostgreSQL."""

    def _serve(label_type):
        # ``raising=False`` keeps this module runnable against a tree without
        # the resolver, so every assertion below is an assertion about the
        # compiled SQL rather than about the patch target existing.
        monkeypatch.setattr(
            dashboard_builder,
            "resolve_annotation_label_output_type",
            lambda label_id, organization_id=None: (
                label_type if label_id == LABEL_ID else None
            ),
            raising=False,
        )

    return _serve


@pytest.fixture
def eval_output_type(monkeypatch):
    """Serve one eval template identity and output type without PostgreSQL."""

    def _serve(output_type):
        monkeypatch.setattr(
            DashboardQueryBuilder,
            "_resolve_eval_template_identity",
            lambda self, payload, fallback: fallback,
        )
        monkeypatch.setattr(
            dashboard_builder,
            "resolve_eval_filter_metadata",
            lambda eval_id, project_ids: EvalFilterMetadata(
                config_ids=(eval_id,), output_type=output_type
            ),
            raising=False,
        )

    return _serve


@pytest.mark.unit
def test_text_annotation_filter_compiles_the_text_payload(annotation_label_type):
    annotation_label_type("text")

    sql = _build(
        _validated_query(
            [_filter(LABEL_ID, "ANNOTATION", "text", "contains", "release")]
        )
    )

    assert "'text', 'Nullable(String)'" in sql
    assert "Nullable(Float64)" not in sql


@pytest.mark.unit
def test_categorical_annotation_filter_compiles_array_membership(
    annotation_label_type,
):
    annotation_label_type("categorical")

    sql = _build(
        _validated_query(
            [_filter(LABEL_ID, "ANNOTATION", "categorical", "in", ["accurate"])]
        )
    )

    assert "'selected', 'Array(String)'" in sql
    assert "Nullable(Float64)" not in sql


@pytest.mark.unit
def test_thumbs_annotation_filter_compiles_the_vote_payload(annotation_label_type):
    annotation_label_type("thumbs_up_down")

    sql = _build(
        _validated_query(
            [_filter(LABEL_ID, "ANNOTATION", "thumbs", "equals", "thumbs_up")]
        )
    )

    assert "'value', 'Nullable(String)'" in sql
    assert "Nullable(Float64)" not in sql


@pytest.mark.unit
def test_numeric_annotation_filter_still_compiles_a_numeric_predicate(
    annotation_label_type,
):
    annotation_label_type("numeric")

    sql = _build(
        _validated_query([_filter(LABEL_ID, "ANNOTATION", "number", "greater_than", 3)])
    )

    assert "Nullable(Float64)" in sql


@pytest.mark.unit
def test_explicit_output_type_hint_still_wins(annotation_label_type, monkeypatch):
    annotation_label_type("numeric")
    leaf = _filter(LABEL_ID, "ANNOTATION", "text", "contains", "release")
    leaf["output_type"] = "text"

    sql = _build(_validated_query([leaf]))

    assert "'text', 'Nullable(String)'" in sql


@pytest.mark.unit
def test_text_operator_on_a_numeric_annotation_is_rejected_at_build_time(
    annotation_label_type,
):
    # ClickHouse answers ``LIKE`` on Float64 with a type error, not rows.
    annotation_label_type("numeric")
    config = _validated_query(
        [_filter(LABEL_ID, "ANNOTATION", "text", "contains", "release")]
    )

    with pytest.raises(InvalidMetricCombinationError, match="holds numbers"):
        _build(config)


@pytest.mark.unit
def test_text_value_on_a_numeric_annotation_is_rejected_at_build_time(
    annotation_label_type,
):
    annotation_label_type("numeric")
    config = _validated_query(
        [_filter(LABEL_ID, "ANNOTATION", "text", "equals", "release")]
    )

    with pytest.raises(InvalidMetricCombinationError, match="holds numbers"):
        _build(config)


@pytest.mark.unit
def test_a_filter_on_an_unknown_annotation_identity_is_rejected(
    annotation_label_type,
):
    annotation_label_type("numeric")
    config = _validated_query(
        [_filter("annotator", "ANNOTATION", "annotator", "equals", str(uuid4()))]
    )

    with pytest.raises(
        InvalidMetricCombinationError, match="not a known annotation label"
    ):
        _build(config)


@pytest.mark.unit
def test_choice_eval_filter_compiles_against_the_output_string(eval_output_type):
    eval_output_type("CHOICE")

    sql = _build(
        _validated_query(
            [_filter(EVAL_ID, "EVAL_METRIC", "categorical", "equals", "Passed")]
        )
    )

    assert ".eval_output_str =" in sql


@pytest.mark.unit
def test_pass_fail_eval_filter_compiles_against_the_canonical_label(eval_output_type):
    eval_output_type("PASS_FAIL")

    sql = _build(
        _validated_query(
            [_filter(EVAL_ID, "EVAL_METRIC", "categorical", "equals", "Passed")]
        )
    )

    assert "'Passed', 'Failed'" in sql


@pytest.mark.unit
def test_text_value_on_a_score_eval_is_rejected_at_build_time(eval_output_type):
    eval_output_type("SCORE")
    config = _validated_query(
        [_filter(EVAL_ID, "EVAL_METRIC", "categorical", "equals", "Passed")]
    )

    with pytest.raises(InvalidMetricCombinationError, match="holds numbers"):
        _build(config)


@pytest.mark.unit
def test_the_exact_read_lane_keeps_the_combination_reachable_from_the_boundary():
    # ``_prepare_metric_queries`` wraps the per-metric failure, so the API
    # boundary must recover it from the cause chain to name it.
    class _Builder:
        metrics = [{"name": "latency"}]

        @staticmethod
        def build_metric_query(_metric):
            raise InvalidMetricCombinationError("'rating' holds numbers")

    with pytest.raises(DashboardExactReadError) as excinfo:
        DashboardViewSet._prepare_metric_queries(_Builder())

    cause = _invalid_metric_combination_cause(excinfo.value)
    assert str(cause) == "'rating' holds numbers"


@pytest.mark.unit
def test_an_unrelated_failure_is_not_reported_as_a_filter_combination():
    assert _invalid_metric_combination_cause(RuntimeError("compiler invariant")) is None


@pytest.mark.unit
def test_a_bare_legacy_annotation_leaf_keeps_the_numeric_default(monkeypatch):
    # Only the canonical API contract carries an authoritative identity to
    # resolve. A leaf assembled in-process keeps the builder's documented
    # default rather than turning the compiler into a PostgreSQL client.
    monkeypatch.setattr(
        dashboard_builder,
        "resolve_annotation_label_output_type",
        _refuse_database,
        raising=False,
    )

    sql = _build(
        _legacy_query(
            [
                {
                    "metric_type": "annotation_metric",
                    "metric_name": LABEL_ID,
                    "operator": "greater_than",
                    "value": "0.5",
                }
            ]
        )
    )

    assert "Nullable(Float64)" in sql


@pytest.mark.unit
def test_a_bare_legacy_eval_leaf_keeps_the_score_default(monkeypatch):
    monkeypatch.setattr(
        dashboard_builder,
        "resolve_eval_filter_metadata",
        _refuse_database,
        raising=False,
    )

    sql = _build(
        _legacy_query(
            [
                {
                    "metric_type": "eval_metric",
                    "metric_name": EVAL_ID,
                    "operator": "greater_than",
                    "value": "0.5",
                }
            ]
        )
    )

    assert "eval_score" in sql
