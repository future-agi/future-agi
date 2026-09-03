"""Security contracts for authenticated ``my_annotations`` filters."""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db.models import Q

from tracer.services.exact_aggregation_cache import snapshot_cache_key
from tracer.services.filter_principal_context import (
    FilterPrincipalContextError,
    bind_my_annotations_principal,
    bound_my_annotations_principal,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"


def _my_annotations_filter(value=True, *, user_id="client-selected-user"):
    return {
        "column_id": "my_annotations",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
            "col_type": "ANNOTATION",
            "user_id": user_id,
        },
    }


@pytest.mark.unit
def test_binding_overwrites_client_principal_recursively_without_mutating_input():
    payload = {"filters": [_my_annotations_filter()]}

    bound = bind_my_annotations_principal(payload, principal_id=USER_A)

    assert bound["filters"][0]["filter_config"]["user_id"] == USER_A
    assert payload["filters"][0]["filter_config"]["user_id"] == ("client-selected-user")
    assert bound_my_annotations_principal(bound) == USER_A


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, False])
def test_user_relative_filter_without_authenticated_principal_is_rejected(value):
    with pytest.raises(FilterPrincipalContextError):
        bind_my_annotations_principal(
            [_my_annotations_filter(value)],
            principal_id=None,
        )


@pytest.mark.unit
def test_bound_principal_is_part_of_exact_graph_cache_identity():
    filters_a = bind_my_annotations_principal(
        [_my_annotations_filter()], principal_id=USER_A
    )
    filters_b = bind_my_annotations_principal(
        [_my_annotations_filter()], principal_id=USER_B
    )
    common = {
        "project_id": PROJECT_ID,
        "interval": "day",
        "metric_id": "latency",
    }

    assert snapshot_cache_key(
        "observe-system-graph", {**common, "filters": filters_a}
    ) != snapshot_cache_key("observe-system-graph", {**common, "filters": filters_b})


@pytest.mark.unit
def test_observe_trace_list_binds_principal_before_cursor_and_query(monkeypatch):
    from tracer.views import trace as trace_view

    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        validated_query_data={
            "project_id": PROJECT_ID,
            "filters": [_my_annotations_filter(False)],
        },
        organization=organization,
        user=SimpleNamespace(id=USER_A, organization=organization),
    )
    project_scope = MagicMock()
    project_scope.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )
    list_impl = MagicMock(return_value="observe-result")
    monkeypatch.setattr(
        trace_view, "_project_queryset_for_request", lambda _request: project_scope
    )
    monkeypatch.setattr(
        trace_view, "_get_request_organization", lambda _request: organization
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        trace_view.TraceView, "_list_traces_of_session_clickhouse", list_impl
    )
    view = trace_view.TraceView()
    view.request = request

    assert (
        unwrap(trace_view.TraceView.list_traces_of_session)(view, request)
        == "observe-result"
    )
    bound_filters = list_impl.call_args.args[2]["filters"]
    assert bound_filters[0]["filter_config"]["user_id"] == USER_A


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "operator"),
    [(True, " IN ("), (False, " NOT IN (")],
)
def test_clickhouse_compiler_has_exact_true_and_false_semantics(value, operator):
    from tracer.services.clickhouse.query_builders.filters import (
        ClickHouseFilterBuilder,
    )

    where, params = ClickHouseFilterBuilder().translate(
        bind_my_annotations_principal(
            [_my_annotations_filter(value)],
            principal_id=USER_A,
        )
    )

    assert operator in where
    assert params == {"uid_1": USER_A}


@pytest.mark.unit
def test_clickhouse_compiler_fails_closed_for_legacy_unbound_filter():
    from tracer.services.clickhouse.query_builders.filters import (
        ClickHouseFilterBuilder,
    )

    unbound = _my_annotations_filter()
    unbound["filter_config"].pop("user_id")
    where, params = ClickHouseFilterBuilder().translate([unbound])

    assert "0 = 1" in where
    assert params == {}


@pytest.mark.unit
def test_task_filter_serializer_persists_authenticated_not_client_principal():
    from tracer.serializers.eval_task import EvalTaskSerializer

    request = SimpleNamespace(user=SimpleNamespace(id=USER_A))
    serializer = EvalTaskSerializer(context={"request": request})
    payload = {"filters": [_my_annotations_filter(False)]}

    bound = serializer.validate_filters(payload)

    assert bound["filters"][0]["filter_config"]["user_id"] == USER_A


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, False])
def test_postgres_task_compiler_is_exact_and_fails_closed(value):
    from tracer.utils.filters import FilterEngine

    bound_filter = bind_my_annotations_principal(
        [_my_annotations_filter(value)], principal_id=USER_A
    )
    condition, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
        bound_filter,
        user_id=USER_A,
    )
    condition_text = str(condition)
    assert "Exists" in condition_text
    assert ("NOT" in condition_text) is (not value)

    unbound_condition, _ = (
        FilterEngine.get_filter_conditions_for_voice_call_annotations(
            [_my_annotations_filter(value, user_id=None)],
            user_id=None,
        )
    )
    assert unbound_condition == Q(pk__in=[])


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, False])
def test_eval_task_runtime_uses_persisted_principal_for_both_boolean_values(
    value, monkeypatch
):
    from tracer.models.eval_task import RowType
    from tracer.utils.eval_tasks import parsing_evaltask_filters
    from tracer.utils.filters import FilterEngine

    captured = {}
    original = FilterEngine.get_filter_conditions_for_voice_call_annotations

    def capture_principal(items, user_id=None, **kwargs):
        captured["user_id"] = user_id
        return original(items, user_id=user_id, **kwargs)

    monkeypatch.setattr(
        FilterEngine,
        "get_filter_conditions_for_voice_call_annotations",
        capture_principal,
    )

    payload = {
        "filters": bind_my_annotations_principal(
            [_my_annotations_filter(value)],
            principal_id=USER_A,
        )
    }

    condition, _ = parsing_evaltask_filters(payload, row_type=RowType.SPANS)

    condition_text = str(condition)
    assert "Exists" in condition_text
    assert captured["user_id"] == USER_A
    assert ("NOT" in condition_text) is (not value)
