"""Regressions for the dormant grouped observability catalog helper.

The public metrics action uses the unified catalog; these tests deliberately
exercise the compatibility helper without changing endpoint routing.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.core.exceptions import FieldError
from django.db import OperationalError, ProgrammingError

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from tracer.views.dashboard import DashboardViewSet


@pytest.fixture
def label_factory(organization, workspace, observe_project):
    def create(name, label_type="numeric", **overrides):
        fields = {
            "name": name,
            "type": label_type,
            "organization": organization,
            "workspace": workspace,
            "project": observe_project,
        }
        fields.update(overrides)
        return AnnotationsLabels.no_workspace_objects.create(**fields)

    return create


@pytest.mark.django_db
@pytest.mark.parametrize("scope", ["explicit", "workspace"])
def test_legacy_catalog_includes_project_annotation_labels(
    scope, label_factory, observe_project, workspace
):
    labels = [
        label_factory(f"Project {kind.value}", kind.value)
        for kind in AnnotationTypeChoices
    ]
    request = SimpleNamespace(
        workspace=workspace,
        query_params=(
            {"project_ids": str(observe_project.id)} if scope == "explicit" else {}
        ),
    )
    with patch("tracer.views.dashboard.AnalyticsQueryService") as analytics:
        analytics.return_value.get_span_attribute_keys_ch.return_value = []
        response = DashboardViewSet()._metrics_observability_legacy(request)

    assert response.status_code == 200
    payload = response.data["result"]
    assert {row["name"]: row for row in payload["annotation_metrics"]} == {
        str(label.id): {
            "name": str(label.id),
            "display_name": label.name,
            "output_type": label.type,
        }
        for label in labels
    }
    assert {"latency", "cost"} <= {row["name"] for row in payload["system_metrics"]}
    assert payload["eval_metrics"] == []
    assert payload["custom_attributes"] == []


@pytest.mark.django_db
def test_legacy_catalog_keeps_project_and_label_tenant_scope(
    label_factory, observe_project, project, workspace, organization, user
):
    local_label = label_factory("Local label")
    legacy_label = label_factory("Legacy null-workspace label")
    # Update after creation to bypass automatic workspace assignment.
    AnnotationsLabels.no_workspace_objects.filter(id=legacy_label.id).update(
        workspace=None
    )
    label_factory("Other project", project=project)
    label_factory("Unassigned project", project=None)
    label_factory("Deleted label", deleted=True)
    other_workspace = Workspace.objects.create(
        name="Other label workspace",
        organization=organization,
        created_by=user,
    )
    label_factory("Other workspace", workspace=other_workspace)
    other_organization = Organization.objects.create(name="Other label organization")
    foreign_label = label_factory("Foreign organization")
    # Model historical inconsistent ownership without relying on save validation.
    AnnotationsLabels.no_workspace_objects.filter(id=foreign_label.id).update(
        organization=other_organization
    )
    request = SimpleNamespace(
        workspace=workspace,
        query_params={"project_ids": str(observe_project.id)},
    )
    with patch("tracer.views.dashboard.AnalyticsQueryService") as analytics:
        analytics.return_value.get_span_attribute_keys_ch.return_value = []
        response = DashboardViewSet()._metrics_observability_legacy(request)

    assert response.status_code == 200
    assert {row["name"] for row in response.data["result"]["annotation_metrics"]} == {
        str(local_label.id),
        str(legacy_label.id),
    }


@pytest.fixture
def legacy_request():
    request = SimpleNamespace(
        workspace=SimpleNamespace(id=uuid4(), organization_id=uuid4()),
        query_params={"project_ids": str(uuid4())},
    )
    with (
        patch("tracer.views.dashboard.Project.objects.filter") as projects,
        patch(
            "tracer.views.dashboard.CustomEvalConfig.no_workspace_objects.filter"
        ) as eval_configs,
        patch("tracer.views.dashboard.AnalyticsQueryService") as analytics,
    ):
        projects.return_value.count.return_value = 1
        eval_configs.return_value.values.return_value = []
        analytics.return_value.get_span_attribute_keys_ch.return_value = []
        yield request


@pytest.mark.parametrize("failure_stage", ["filter", "iteration"])
def test_legacy_catalog_reports_annotation_read_outage(legacy_request, failure_stage):
    error = OperationalError("private connection detail")
    with (
        patch.object(AnnotationsLabels.no_workspace_objects, "filter") as labels,
        patch("tracer.views.dashboard.logger.warning") as warning,
    ):
        if failure_stage == "filter":
            labels.side_effect = error
        else:
            rows = MagicMock()
            rows.__iter__.side_effect = error
            labels.return_value.values.return_value = rows
        response = DashboardViewSet()._metrics_observability_legacy(legacy_request)

    assert response.status_code == 503
    assert "private connection detail" not in str(response.data)
    assert "annotation_metrics" not in str(response.data)
    warning.assert_called_once_with(
        "dashboard_annotation_metrics_unavailable", error_type="OperationalError"
    )


@pytest.mark.parametrize(
    "error_type", [FieldError, ProgrammingError, TypeError, KeyError]
)
def test_legacy_catalog_does_not_hide_programming_errors(legacy_request, error_type):
    with (
        patch.object(
            AnnotationsLabels.no_workspace_objects,
            "filter",
            side_effect=error_type("invalid annotation query"),
        ),
        pytest.raises(error_type),
    ):
        DashboardViewSet()._metrics_observability_legacy(legacy_request)


def test_legacy_catalog_rejects_invalid_projects_before_label_read(legacy_request):
    with (
        patch("tracer.views.dashboard.Project.objects.filter") as projects,
        patch.object(AnnotationsLabels.no_workspace_objects, "filter") as labels,
    ):
        projects.return_value.count.return_value = 0
        response = DashboardViewSet()._metrics_observability_legacy(legacy_request)

    assert response.status_code == 400
    labels.assert_not_called()
