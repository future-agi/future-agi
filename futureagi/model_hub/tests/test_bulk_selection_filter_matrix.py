"""DB-free regression matrix for queue filter-mode resolver drift."""

from __future__ import annotations

import uuid

import pytest
from django.db.models import Q

from model_hub.services import bulk_selection
from model_hub.services.bulk_selection import (
    _SESSION_FIELD_MAP,
    _SESSION_PRE_AGG_FIELDS,
    _apply_call_execution_filters,
    _apply_span_filters,
    _apply_trace_filters,
    _filter_col_type,
    _filter_column_id,
)
from model_hub.utils import annotation_queue_helpers
from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS
from tracer.utils.filters import FilterEngine, apply_created_at_filters


class RecordingQuerySet:
    def __init__(self):
        self.calls = []

    def filter(self, *args, **kwargs):
        self.calls.append(("filter", args, kwargs))
        return self

    def exclude(self, *args, **kwargs):
        self.calls.append(("exclude", args, kwargs))
        return self

    def annotate(self, *args, **kwargs):
        self.calls.append(("annotate", args, kwargs))
        return self


class FakeUser:
    id = uuid.uuid4()


def _filter(
    column_id,
    col_type,
    filter_type,
    filter_op,
    filter_value,
):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": col_type,
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def _value_for(filter_type, filter_op):
    if filter_op in {"is_null", "is_not_null"}:
        return None
    if filter_op in {"between", "not_between"}:
        return [5, 10]
    if filter_op in {"in", "not_in"}:
        return ["alpha", "beta"] if filter_type == "text" else [5, 10]
    if filter_type == "number":
        return 7
    return "alpha"


def _call_keys(qs):
    keys = []
    for method, _args, kwargs in qs.calls:
        keys.extend((method, key) for key in kwargs)
    return set(keys)


def test_filter_helpers_read_canonical_snake_filter_payloads_only():
    snake = _filter("label-1", "ANNOTATION", "text", "contains", "x")
    camel = {
        "columnId": "label-2",
        "filterConfig": {
            "colType": "ANNOTATION",
            "filterType": "text",
            "filterOp": "contains",
            "filterValue": "x",
        },
    }

    assert _filter_column_id(snake) == "label-1"
    assert _filter_col_type(snake) == "ANNOTATION"
    assert _filter_column_id(camel) == ""
    assert _filter_col_type(camel) == ""


def test_rule_field_mapping_uses_canonical_snake_case_ids_only():
    for source_mapping in annotation_queue_helpers.FIELD_MAPPING.values():
        assert not any(
            any(char.isupper() for char in field_id) for field_id in source_mapping
        )


@pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["text"]))
def test_call_execution_text_filters_accept_contract_operators(filter_op):
    qs = RecordingQuerySet()
    qs, unsupported = _apply_call_execution_filters(
        qs,
        [
            _filter(
                "status",
                "SYSTEM_METRIC",
                "text",
                filter_op,
                _value_for("text", filter_op),
            )
        ],
    )

    assert unsupported == []
    assert qs.calls
    keys = _call_keys(qs)
    if filter_op == "starts_with":
        assert ("filter", "status__istartswith") in keys
    elif filter_op == "ends_with":
        assert ("filter", "status__iendswith") in keys
    elif filter_op == "not_contains":
        assert ("exclude", "status__icontains") in keys


@pytest.mark.parametrize("filter_op", sorted(FILTER_TYPE_ALLOWED_OPS["number"]))
def test_call_execution_number_filters_accept_contract_operators(filter_op):
    qs = RecordingQuerySet()
    qs, unsupported = _apply_call_execution_filters(
        qs,
        [
            _filter(
                "duration_seconds",
                "SYSTEM_METRIC",
                "number",
                filter_op,
                _value_for("number", filter_op),
            )
        ],
    )

    assert unsupported == []
    assert qs.calls
    keys = _call_keys(qs)
    if filter_op == "greater_than":
        assert ("filter", "duration_seconds__gt") in keys
    elif filter_op == "greater_than_or_equal":
        assert ("filter", "duration_seconds__gte") in keys
    elif filter_op == "less_than":
        assert ("filter", "duration_seconds__lt") in keys
    elif filter_op == "less_than_or_equal":
        assert ("filter", "duration_seconds__lte") in keys
    elif filter_op == "not_between":
        assert ("exclude", "duration_seconds__range") in keys


@pytest.mark.parametrize(
    "legacy_op",
    ["more_than", "more_than_or_equal", "gt", "gte", "not_in_between", "eq", "ne"],
)
def test_call_execution_rejects_legacy_numeric_operator_aliases(legacy_op):
    qs = RecordingQuerySet()
    qs, unsupported = _apply_call_execution_filters(
        qs,
        [_filter("duration_seconds", "SYSTEM_METRIC", "number", legacy_op, 7)],
    )

    assert unsupported == ["duration_seconds"]
    assert not qs.calls


def _install_filter_engine_spies(monkeypatch):
    calls = {}

    def system(filters, *args, **kwargs):
        calls["system"] = list(filters)
        return Q()

    def non_system(filters, *args, **kwargs):
        calls["non_system"] = list(filters)
        return Q()

    def annotations(filters, *args, **kwargs):
        calls["annotations"] = list(filters)
        calls["annotation_kwargs"] = kwargs
        return Q(), {}

    def span_attributes(filters, *args, **kwargs):
        calls["span_attributes"] = list(filters)
        return Q()

    def has_eval(filters, *args, **kwargs):
        calls["has_eval"] = list(filters)
        calls["has_eval_kwargs"] = kwargs
        return Q()

    def has_annotation(filters, *args, **kwargs):
        calls["has_annotation"] = list(filters)
        calls["has_annotation_kwargs"] = kwargs
        return Q()

    monkeypatch.setattr(
        FilterEngine,
        "get_filter_conditions_for_system_metrics",
        staticmethod(system),
    )
    monkeypatch.setattr(
        FilterEngine,
        "get_filter_conditions_for_non_system_metrics",
        staticmethod(non_system),
    )
    monkeypatch.setattr(
        FilterEngine,
        "get_filter_conditions_for_voice_call_annotations",
        staticmethod(annotations),
    )
    monkeypatch.setattr(
        FilterEngine,
        "get_filter_conditions_for_span_attributes",
        staticmethod(span_attributes),
    )
    monkeypatch.setattr(
        FilterEngine,
        "get_filter_conditions_for_has_eval",
        staticmethod(has_eval),
    )
    monkeypatch.setattr(
        FilterEngine,
        "get_filter_conditions_for_has_annotation",
        staticmethod(has_annotation),
    )
    return calls


def _mixed_observe_filters():
    return [
        _filter("latency_ms", "SYSTEM_METRIC", "number", "greater_than", 10),
        _filter("metric-1", "EVAL_METRIC", "number", "greater_than", 80),
        _filter("label-snake", "ANNOTATION", "text", "contains", "alpha"),
        _filter(
            "label-category",
            "ANNOTATION",
            "categorical",
            "in",
            ["yes", "no"],
        ),
        _filter("annotator", "NORMAL", "annotator", "equals", str(uuid.uuid4())),
        _filter(
            "span_attributes.provider", "SPAN_ATTRIBUTE", "text", "equals", "openai"
        ),
        _filter("has_eval", "NORMAL", "boolean", "equals", True),
    ]


@pytest.mark.parametrize(
    "apply_filters,expected_has_eval_type",
    [
        (
            lambda qs, filters, calls: _apply_trace_filters(
                qs,
                filters,
                user=FakeUser(),
                organization=object(),
                annotation_label_ids=[],
            ),
            "trace",
        ),
        (
            lambda qs, filters, calls: _apply_span_filters(
                qs, filters, user=FakeUser(), organization=object()
            ),
            "span",
        ),
    ],
)
def test_trace_and_span_filter_mode_do_not_route_annotation_filters_to_eval_branch(
    monkeypatch, apply_filters, expected_has_eval_type
):
    calls = _install_filter_engine_spies(monkeypatch)

    apply_filters(RecordingQuerySet(), _mixed_observe_filters(), calls)

    non_system_columns = {_filter_column_id(f) for f in calls["non_system"]}
    assert "metric-1" in non_system_columns
    assert "label-snake" not in non_system_columns
    assert "label-category" not in non_system_columns
    assert "annotator" not in non_system_columns
    assert calls["annotations"]
    assert calls["has_eval_kwargs"] == {"observe_type": expected_has_eval_type}


def test_span_filter_mode_passes_span_scoped_annotation_subquery_kwargs(monkeypatch):
    calls = _install_filter_engine_spies(monkeypatch)

    _apply_span_filters(
        RecordingQuerySet(),
        _mixed_observe_filters(),
        user=FakeUser(),
        organization=object(),
    )

    assert "span_filter_kwargs" in calls["annotation_kwargs"]
    assert "observation_span_id" in calls["annotation_kwargs"]["span_filter_kwargs"]


@pytest.mark.parametrize(
    "column_id,filter_type,filter_op,field_map",
    [
        ("duration", "number", "between", _SESSION_FIELD_MAP),
        ("first_message", "text", "starts_with", _SESSION_FIELD_MAP),
        ("last_message", "text", "not_contains", _SESSION_FIELD_MAP),
        ("total_cost", "number", "greater_than_or_equal", _SESSION_FIELD_MAP),
        ("total_tokens", "number", "not_between", _SESSION_FIELD_MAP),
        ("user_id", "text", "in", _SESSION_PRE_AGG_FIELDS),
    ],
)
def test_session_field_maps_accept_contract_operator_shapes(
    column_id, filter_type, filter_op, field_map
):
    q_filter = FilterEngine.get_filter_conditions_for_system_metrics(
        [
            _filter(
                column_id,
                "SYSTEM_METRIC",
                filter_type,
                filter_op,
                _value_for(filter_type, filter_op),
            )
        ],
        field_map=field_map,
    )

    assert q_filter


def test_apply_created_at_filters_accepts_canonical_filter_payload():
    qs = RecordingQuerySet()
    filtered_qs, remaining = apply_created_at_filters(
        qs,
        [
            _filter(
                "created_at",
                "SYSTEM_METRIC",
                "datetime",
                "greater_than",
                "2026-05-01T00:00:00Z",
            )
        ],
    )

    assert filtered_qs is qs
    assert remaining == []
    assert ("filter", "created_at__gt") in _call_keys(qs)


def test_call_execution_unknown_simulation_filter_fails_closed():
    _qs, unsupported = bulk_selection._apply_call_execution_filters(
        RecordingQuerySet(),
        [_filter("unmapped_simulation_metric", "SYSTEM_METRIC", "number", "equals", 1)],
    )

    assert unsupported == ["unmapped_simulation_metric"]


# --------------------------------------------------------------------------
# Strict CH filter translation — the automation-rule resolve must fail loud on
# a filter it can't translate (which would silently drop from the WHERE clause
# and over-match) so the caller falls back to the PG FilterEngine.
# --------------------------------------------------------------------------


def _ch_filter(col_id, col_type, filter_type, filter_op, filter_value):
    return {
        "column_id": col_id,
        "filter_config": {
            "col_type": col_type,
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def test_strict_translation_raises_on_dropped_filter():
    """A filter that reaches the general condition builder but yields no WHERE
    fragment silently vanishes → over-match. Strict mode raises so the rule
    resolve falls back to PG; lenient mode (the grid) keeps dropping it."""
    from tracer.services.clickhouse.query_builders.filters import (
        ClickHouseFilterBuilder,
        FilterTranslationError,
    )

    class _DropBuilder(ClickHouseFilterBuilder):
        # Stand in for any operator/value shape the real builder can't translate.
        def _build_condition(self, *args, **kwargs):
            return None

    payload = [_ch_filter("x", "NORMAL", "text", "equals", "y")]

    where, _params = _DropBuilder(table="spans").translate(payload, strict=False)
    assert where == ""  # lenient: silently dropped (pre-existing grid behavior)

    with pytest.raises(FilterTranslationError):
        _DropBuilder(table="spans").translate(payload, strict=True)


def test_strict_translation_allows_supported_filter():
    """A translatable filter passes strict mode unchanged — the guard must not
    force the PG fallback for filters CH handles (that would reintroduce the
    slow path this PR removed)."""
    from tracer.services.clickhouse.query_builders.filters import (
        ClickHouseFilterBuilder,
    )

    payload = [_ch_filter("customer_tier", "SPAN_ATTRIBUTE", "text", "equals", "vip")]
    where, _params = ClickHouseFilterBuilder(table="spans").translate(
        payload, strict=True
    )
    assert where  # a real WHERE fragment, no raise


def test_strict_translation_skips_legitimate_non_condition_filters():
    """Date filters (handled by parse_time_range) and empty toggle filters are
    legitimate skips, not translation failures — strict mode must not raise."""
    from tracer.services.clickhouse.query_builders.filters import (
        ClickHouseFilterBuilder,
    )

    payload = [
        _ch_filter(
            "start_time", "NORMAL", "datetime", "between", ["2020-01-01", "2020-01-02"]
        ),
        _ch_filter("has_eval", "NORMAL", "boolean", "equals", None),
    ]
    # Must not raise; the date filter is scoped separately and the empty toggle
    # is a no-op, so neither reaches the general condition builder.
    ClickHouseFilterBuilder(table="spans").translate(payload, strict=True)
