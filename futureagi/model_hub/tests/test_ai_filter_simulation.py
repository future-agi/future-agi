"""Simulation-specific Property Registry grounding for the AI filter."""

from types import SimpleNamespace
from unittest import mock

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.views import ai_filter

pytestmark = pytest.mark.unit

ORG_ID = "00000000-0000-4000-8000-000000000010"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000011"
AGENT_ID = "00000000-0000-4000-8000-000000000012"
RUN_TEST_ID = "00000000-0000-4000-8000-000000000013"
EVAL_CONFIG_ID = "00000000-0000-4000-8000-000000000014"


def _workspace():
    return SimpleNamespace(
        id=WORKSPACE_ID,
        organization=SimpleNamespace(id=ORG_ID),
    )


def _simulation_eval_schema():
    return [
        {
            "field": EVAL_CONFIG_ID,
            "property_id": f"eval_config:{EVAL_CONFIG_ID}",
            "label": "Resolution",
            "category": "eval",
            "type": "string",
        }
    ]


def test_simulation_eval_config_is_authorized_in_native_scope():
    template = SimpleNamespace(
        deleted=False,
        config={"output": "choice"},
        choices=["Resolved", "Escalated"],
    )
    config = SimpleNamespace(
        id=EVAL_CONFIG_ID,
        eval_template=template,
        mapping={"key": "resolution-v2"},
    )
    queryset = mock.MagicMock()
    queryset.filter.return_value = queryset
    queryset.select_related.return_value = queryset
    queryset.first.return_value = config

    with mock.patch(
        "simulate.models.eval_config.SimulateEvalConfig.objects",
        queryset,
    ):
        authorized = ai_filter._authorize_smart_property_schema(
            _simulation_eval_schema(),
            source="simulation",
            workspace=_workspace(),
            simulation_scope={
                "organization_id": ORG_ID,
                "workspace_id": WORKSPACE_ID,
                "agent_definition_id": AGENT_ID,
                "run_test_id": RUN_TEST_ID,
            },
        )

    assert authorized[0]["choices"] == ["Resolved", "Escalated"]
    assert authorized[0]["_simulation_eval_key"] == "resolution-v2"
    initial_scope = queryset.filter.call_args_list[0].kwargs
    assert initial_scope["run_test__organization"].id == ORG_ID
    assert initial_scope["run_test__workspace"].id == WORKSPACE_ID
    assert initial_scope["id"] == EVAL_CONFIG_ID
    assert queryset.filter.call_args_list[1].kwargs == {
        "run_test__agent_definition_id": AGENT_ID
    }
    assert queryset.filter.call_args_list[2].kwargs == {"run_test_id": RUN_TEST_ID}


def test_simulation_eval_config_from_another_workspace_is_rejected():
    queryset = mock.MagicMock()
    queryset.filter.return_value = queryset
    queryset.select_related.return_value = queryset
    queryset.first.return_value = None

    with (
        mock.patch(
            "simulate.models.eval_config.SimulateEvalConfig.objects",
            queryset,
        ),
        pytest.raises(ai_filter.SmartFilterGroundingError) as error,
    ):
        ai_filter._authorize_smart_property_schema(
            _simulation_eval_schema(),
            source="simulation",
            workspace=_workspace(),
            simulation_scope={
                "organization_id": ORG_ID,
                "workspace_id": WORKSPACE_ID,
                "run_test_id": RUN_TEST_ID,
            },
        )

    assert error.value.status_code == 422
    initial_scope = queryset.filter.call_args_list[0].kwargs
    assert initial_scope["run_test__organization"].id == ORG_ID
    assert initial_scope["run_test__workspace"].id == WORKSPACE_ID


def test_simulation_system_values_use_exact_simulation_tables(monkeypatch):
    service = mock.MagicMock()
    service.execute_ch_query.return_value = SimpleNamespace(
        data=[{"value": "completed"}, {"value": "failed"}]
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.is_clickhouse_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        lambda: service,
    )

    values = ai_filter._fetch_simulation_field_values(
        {
            "organization_id": ORG_ID,
            "workspace_id": WORKSPACE_ID,
            "agent_definition_id": AGENT_ID,
            "run_test_id": RUN_TEST_ID,
        },
        "status",
        "system_metric",
        search_query="fail",
    )

    assert values == ["completed", "failed"]
    query, params = service.execute_ch_query.call_args.args
    assert "FROM simulate_call_execution AS c FINAL" in query
    assert "INNER JOIN simulate_test_execution AS te FINAL" in query
    assert "INNER JOIN simulate_run_test AS rt FINAL" in query
    assert "rt.organization_id = toUUID(%(organization_id)s)" in query
    assert "rt.workspace_id = toUUID(%(workspace_id)s)" in query
    assert params["agent_definition_id"] == AGENT_ID
    assert params["run_test_id"] == RUN_TEST_ID
    assert service.execute_ch_query.call_args.kwargs["timeout_ms"] <= 4_000


def test_simulation_smart_dispatch_never_falls_back_to_trace_reader():
    factory = APIRequestFactory()
    request = factory.post(
        "/model-hub/ai-filter/",
        {
            "mode": "smart",
            "source": "simulation",
            "query": "show failed calls",
            "agent_definition_id": AGENT_ID,
            "schema": [
                {
                    "field": "status",
                    "property_id": "system_attribute:simulation:status",
                    "label": "Status",
                    "category": "system",
                    "type": "string",
                }
            ],
        },
        format="json",
    )
    request.workspace = _workspace()
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    scope = {
        "organization_id": ORG_ID,
        "workspace_id": WORKSPACE_ID,
        "agent_definition_id": AGENT_ID,
    }

    def run_agent(_query, _schema, fetch_values, **_kwargs):
        assert fetch_values(
            "system_attribute:simulation:status",
            search_query="fail",
        ) == ["failed"]
        return []

    with (
        mock.patch.object(ai_filter, "_resolve_simulation_scope", return_value=scope),
        mock.patch.object(
            ai_filter,
            "_fetch_simulation_field_values",
            return_value=["failed"],
        ) as simulation_reader,
        mock.patch.object(ai_filter, "_fetch_trace_field_values") as trace_reader,
        mock.patch.object(ai_filter, "_run_smart_agent", side_effect=run_agent),
    ):
        response = ai_filter.AIFilterView.as_view()(request)

    assert response.status_code == 200
    simulation_reader.assert_called_once()
    trace_reader.assert_not_called()


def test_simulation_smart_request_without_native_scope_is_typed_422():
    factory = APIRequestFactory()
    request = factory.post(
        "/model-hub/ai-filter/",
        {
            "mode": "smart",
            "source": "simulation",
            "query": "show failed calls",
            "project_id": "00000000-0000-4000-8000-000000000099",
            "schema": [
                {
                    "field": "status",
                    "property_id": "system_attribute:simulation:status",
                    "category": "system",
                    "type": "string",
                }
            ],
        },
        format="json",
    )
    request.workspace = _workspace()
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))

    response = ai_filter.AIFilterView.as_view()(request)

    assert response.status_code == 422
    assert response.data["code"] == "ai_filter_grounding_too_broad"
