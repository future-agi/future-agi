"""User-facing tracing failures must not expose ClickHouse internals."""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from clickhouse_driver.errors import ServerException

PRIVATE_ERROR = "Code: 159. DB::Exception secret SQL and stack trace"


@pytest.mark.unit
def test_eval_name_picker_sanitizes_clickhouse_failure():
    from tracer.views.trace import TraceView

    view = TraceView()
    request = SimpleNamespace(query_params={"project_id": "project-1"})
    view.request = request

    project_scope = MagicMock()
    project_scope.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )
    config_manager = MagicMock()
    config_manager.filter.return_value.values_list.return_value = ["config-1"]
    analytics = MagicMock()
    analytics.get_eval_config_ids_with_data_ch.side_effect = ServerException(
        PRIVATE_ERROR, code=159
    )

    with (
        patch(
            "tracer.views.trace._project_queryset_for_request",
            return_value=project_scope,
        ),
        patch("tracer.views.trace.CustomEvalConfig.objects", config_manager),
        patch("tracer.views.trace.V2AnalyticsQueryService", return_value=analytics),
    ):
        # Exercise the view boundary itself. ``validated_request`` requires a
        # real DRF request, which this focused unit test deliberately replaces
        # with a minimal request double.
        response = unwrap(TraceView.get_eval_names)(view, request)

    assert response.status_code == 503
    assert response.data["result"] == (
        "Evaluation names are temporarily unavailable. Please retry."
    )
    assert PRIVATE_ERROR not in str(response.data)
    assert "DB::Exception" not in str(response.data)


@pytest.mark.unit
def test_evaluation_detail_sanitizes_clickhouse_failure():
    from tracer.views.observation_span import ObservationSpanView

    view = ObservationSpanView()
    organization = SimpleNamespace(id="org-1")
    request = SimpleNamespace(
        query_params={
            "observation_span_id": "span-1",
            "custom_eval_config_id": "config-1",
        },
        organization=organization,
        workspace=None,
        user=SimpleNamespace(organization=organization),
    )
    view.request = request
    config_manager = MagicMock()
    config_manager.filter.return_value.values.return_value.first.return_value = {
        "project_id": "00000000-0000-0000-0000-000000000001"
    }
    analytics = MagicMock()
    analytics.get_eval_detail_ch.side_effect = ServerException(PRIVATE_ERROR, code=159)

    with (
        patch(
            "tracer.views.observation_span.CustomEvalConfig.no_workspace_objects",
            config_manager,
        ),
        patch(
            "tracer.views.observation_span.V2AnalyticsQueryService",
            return_value=analytics,
        ),
    ):
        # Exercise the view boundary itself. ``validated_request`` requires a
        # real DRF request, which this focused unit test deliberately replaces
        # with a minimal request double.
        response = unwrap(ObservationSpanView.get_evaluation_details)(view, request)

    assert response.status_code == 503
    assert response.data["result"] == (
        "Evaluation details are temporarily unavailable. Please retry."
    )
    assert PRIVATE_ERROR not in str(response.data)
    assert "DB::Exception" not in str(response.data)
