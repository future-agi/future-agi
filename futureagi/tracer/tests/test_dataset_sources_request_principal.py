"""Observe dataset previews resolve annotation cohorts for the requesting user."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from tracer.services.dataset_sources import preview_sources
from tracer.views.dataset import DatasetView

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
ORG_ID = UUID("22222222-2222-4222-8222-222222222222")
TRACE_ID = "33333333-3333-4333-8333-333333333333"
USER_ID = "44444444-4444-4444-8444-444444444444"
OTHER_USER_ID = "55555555-5555-4555-8555-555555555555"
ANNOTATION_CASES = [
    pytest.param("my_annotations", True, id="my-annotations-true"),
    pytest.param("my_annotations", False, id="my-annotations-false"),
    pytest.param("annotator", [OTHER_USER_ID], id="explicit-annotator"),
]


@dataclass(frozen=True)
class WorkspaceStub:
    """Q equality hashes workspace values, just as it hashes saved model instances."""

    id: str
    is_default: bool = False


def make_request(kind, column, value):
    organization = SimpleNamespace(id=ORG_ID)
    project = SimpleNamespace(
        id=PROJECT_ID, organization=organization, organization_id=ORG_ID
    )
    request = SimpleNamespace(
        method="POST",
        user=SimpleNamespace(
            id=USER_ID, is_authenticated=True, organization=organization
        ),
        organization=organization,
        workspace=WorkspaceStub(id="workspace", is_default=False),
        data={
            "project": str(PROJECT_ID),
            "source_kind": kind,
            "failures_only": False,
            "exclude_ids": ["excluded"],
            "filters": [
                {
                    "column_id": column,
                    "filter_config": {
                        "filter_type": "boolean"
                        if column == "my_annotations"
                        else "annotator",
                        "filter_op": "equals" if column == "my_annotations" else "in",
                        "filter_value": value,
                        "col_type": "ANNOTATION",
                    },
                },
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            "2026-08-01T00:00:00Z",
                            "2026-08-03T00:00:00Z",
                        ],
                    },
                },
            ],
        },
    )
    return request, project


def make_reader():
    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader._client.query.return_value = SimpleNamespace(
        result_rows=[
            (
                "selected",
                TRACE_ID,
                None,
                "Chosen cohort",
                datetime(2026, 8, 2, tzinfo=UTC),
            )
        ]
    )
    return reader


@pytest.mark.parametrize("kind", ["traces", "spans"])
@pytest.mark.parametrize(("column", "value"), ANNOTATION_CASES)
def test_preview_action_resolves_authenticated_annotation_cohort(kind, column, value):
    """Exercise the real request validator, view, binding and preview service."""
    request, project = make_request(kind, column, value)
    original = deepcopy(request.data)
    expected_filters = deepcopy(original["filters"])
    if column == "my_annotations":
        expected_filters[0]["filter_config"]["user_id"] = USER_ID
    selected_id = TRACE_ID if kind == "traces" else "selected"
    resolver_name = f"resolve_filtered_{'trace' if kind == 'traces' else 'span'}_ids"
    reader = make_reader()
    with (
        patch("tracer.views.dataset.Project.no_workspace_objects") as projects,
        patch(
            f"model_hub.services.bulk_selection.{resolver_name}",
            return_value=SimpleNamespace(ids=[selected_id], truncated=False),
        ) as resolve,
        patch("tracer.services.dataset_sources.get_reader", return_value=reader),
    ):
        projects.filter.return_value.first.return_value = project
        response = DatasetView().preview_sources(request)
    assert response.status_code == 200, response.data
    projects.filter.assert_called_once_with(
        Q(workspace=request.workspace), id=PROJECT_ID, organization=request.organization
    )
    resolve.assert_called_once_with(
        project_id=PROJECT_ID,
        organization=request.organization,
        filters=expected_filters,
        exclude_ids=["excluded"],
        cap=200,
        user=request.user,
    )
    params = reader._client.query.call_args.kwargs["parameters"]
    assert params["trace_ids" if kind == "traces" else "span_ids"] == (selected_id,)
    assert params["org"] == str(ORG_ID)
    assert params["project"] == str(PROJECT_ID)
    assert params["since"] == datetime(2026, 8, 1, tzinfo=UTC)
    assert params["now"] == datetime(2026, 8, 3, tzinfo=UTC)
    assert [item["span_id"] for item in response.data["result"]["items"]] == [
        "selected"
    ]
    assert request.data == original
    assert request.validated_serializer.validated_data["filters"] == original["filters"]


@pytest.mark.parametrize("kind", ["traces", "spans"])
@pytest.mark.parametrize("value", [True, False])
def test_preview_service_rebinds_prior_principal_without_mutating_options(kind, value):
    request, project = make_request(kind, "my_annotations", value)
    # A server-side caller may reuse an already-bound payload. Never reuse its user.
    request.data["filters"][0]["filter_config"]["user_id"] = OTHER_USER_ID
    original = deepcopy(request.data)
    resolver_name = f"resolve_filtered_{'trace' if kind == 'traces' else 'span'}_ids"
    with (
        patch(
            f"model_hub.services.bulk_selection.{resolver_name}",
            return_value=SimpleNamespace(ids=[], truncated=False),
        ) as resolve,
        patch("tracer.services.dataset_sources.get_reader") as reader,
    ):
        result = preview_sources(project, request.data, request=request)
    resolve.assert_called_once()
    kwargs = resolve.call_args.kwargs
    expected_filters = deepcopy(original["filters"])
    expected_filters[0]["filter_config"]["user_id"] = USER_ID
    assert kwargs["filters"] == expected_filters
    assert kwargs["user"] is request.user
    assert request.data == original
    assert result == {"items": [], "limited": False, "limit": 200}
    reader.assert_not_called()


@pytest.mark.parametrize(("column", "value"), ANNOTATION_CASES)
def test_annotation_preview_without_request_never_reads_a_broader_cohort(column, value):
    request, project = make_request("spans", column, value)
    with (
        patch("model_hub.services.bulk_selection.Project.objects") as projects,
        patch("tracer.services.dataset_sources.get_reader") as reader,
        pytest.raises(ValidationError) as error,
    ):
        preview_sources(project, request.data)
    assert "filters" in error.value.detail
    projects.get.assert_not_called()
    reader.assert_not_called()


def test_preview_request_binding_does_not_bypass_project_scope():
    request, _ = make_request("spans", "my_annotations", True)
    with (
        patch("tracer.views.dataset.Project.no_workspace_objects") as projects,
        patch("model_hub.services.bulk_selection.resolve_filtered_span_ids") as resolve,
        patch("tracer.services.dataset_sources.get_reader") as reader,
    ):
        projects.filter.return_value.first.return_value = None
        response = DatasetView().preview_sources(request)
    assert response.status_code == 400
    projects.filter.assert_called_once_with(
        Q(workspace=request.workspace), id=PROJECT_ID, organization=request.organization
    )
    resolve.assert_not_called()
    reader.assert_not_called()
