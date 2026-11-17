"""A dashboard share link answers only what the shared dashboard page shows.

The page (``frontend/src/pages/shared/SharedView.jsx``) shows the dashboard's
name, description and widget count, and for each widget its name,
description, size, chart type, time range preset and metric labels. Anyone
holding the link reads the answer, so it carries nothing else: no user
record, workspace, organization, project ids, filters or breakdowns.
"""

import uuid
from types import SimpleNamespace

import pytest
from rest_framework import status

from tracer.models.dashboard import Dashboard, DashboardWidget

DASHBOARD_KEYS = {"name", "description", "widget_count", "widgets"}
WIDGET_KEYS = {
    "id",
    "name",
    "description",
    "width",
    "height",
    "query_config",
    "chart_config",
}
# Keys of a user record and of the tenant it belongs to, plus the saved query
# parts the page never shows.
UNSHOWN_KEYS = {
    "created_by",
    "updated_by",
    "email",
    "organization",
    "organization_role",
    "role",
    "goals",
    "workspace",
    "project_ids",
    "filters",
    "breakdowns",
}
FILTER_VALUE = "checkout-internal"


def _walk(value):
    """Every ``(key, value)`` pair in a JSON answer, at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


@pytest.fixture
def shared_dashboard(api_client, auth_client, workspace, user):
    """A two-widget dashboard, resolved through a public share link while
    signed out, as the shared page reads it."""
    other_project_id = str(uuid.uuid4())
    dashboard = Dashboard.objects.create(
        workspace=workspace,
        name="Checkout health",
        description="Latency and cost of the checkout agent",
        created_by=user,
        updated_by=user,
    )
    current = DashboardWidget.objects.create(
        dashboard=dashboard,
        name="Latency p95",
        description="P95 response time",
        position=0,
        width=6,
        height=4,
        query_config={
            "workflow": "observability",
            "project_ids": [other_project_id],
            "time_range": {
                "preset": "7D",
                "custom_start": "2026-11-01T00:00:00Z",
                "custom_end": "2026-11-08T00:00:00Z",
            },
            "granularity": "day",
            "metrics": [
                {
                    "id": "metric-1",
                    "name": "latency",
                    "display_name": "Latency",
                    "type": "system_metric",
                    "source": "traces",
                    "aggregation": "p95",
                    "filters": [
                        {
                            "column_id": "customer",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": FILTER_VALUE,
                            },
                        }
                    ],
                }
            ],
            "filters": [
                {
                    "column_id": "customer",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": FILTER_VALUE,
                    },
                }
            ],
            "breakdowns": [{"name": "model", "type": "system_metric"}],
        },
        chart_config={"chart_type": "line", "axis_config": {"left_y": {}}},
        created_by=user,
    )
    older = DashboardWidget.objects.create(
        dashboard=dashboard,
        name="Cost",
        position=1,
        width=12,
        height=3,
        query_config={
            "timeRange": {"preset": "30D", "custom_start": "2026-10-01"},
            "metrics": [
                {
                    "name": "cost",
                    "displayName": "Cost",
                    "aggregation": "sum",
                    "type": "system_metric",
                }
            ],
        },
        chart_config={"chart_type": "metric"},
        created_by=user,
    )

    created = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "dashboard",
            "resource_id": str(dashboard.id),
            "access_type": "public",
        },
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED
    token = created.json()["result"]["token"]

    resolved = api_client.get(f"/tracer/shared/{token}/")
    assert resolved.status_code == status.HTTP_200_OK
    body = resolved.json()
    assert body["resource_type"] == "dashboard"
    assert body["resource_id"] == str(dashboard.id)
    return SimpleNamespace(
        data=body["data"],
        dashboard=dashboard,
        widgets=[current, older],
        user=user,
        workspace=workspace,
        other_project_id=other_project_id,
    )


@pytest.mark.django_db
def test_shared_dashboard_answer_has_only_the_fields_the_page_shows(
    shared_dashboard,
):
    assert set(shared_dashboard.data) == DASHBOARD_KEYS


@pytest.mark.django_db
def test_shared_dashboard_widgets_have_only_the_fields_the_page_shows(
    shared_dashboard,
):
    widgets = shared_dashboard.data["widgets"]

    assert len(widgets) == 2
    for widget in widgets:
        assert set(widget) == WIDGET_KEYS
        assert set(widget["chart_config"]) == {"chart_type"}


@pytest.mark.django_db
def test_shared_widget_query_keeps_only_metric_labels_and_time_range_preset(
    shared_dashboard,
):
    current, older = shared_dashboard.data["widgets"]

    assert set(current["query_config"]) == {"metrics", "time_range"}
    assert current["query_config"]["time_range"] == {"preset": "7D"}
    assert current["query_config"]["metrics"] == [
        {
            "id": "metric-1",
            "name": "latency",
            "display_name": "Latency",
            "aggregation": "p95",
        }
    ]
    # A widget saved with the older camelCase keys keeps the ones the page
    # still reads.
    assert set(older["query_config"]) == {"metrics", "timeRange"}
    assert older["query_config"]["timeRange"] == {"preset": "30D"}
    assert older["query_config"]["metrics"] == [
        {"name": "cost", "displayName": "Cost", "aggregation": "sum"}
    ]


@pytest.mark.django_db
def test_shared_dashboard_answer_names_no_user_workspace_or_organization(
    shared_dashboard,
):
    pairs = list(_walk(shared_dashboard.data))
    keys = {key for key, _ in pairs}
    values = {
        str(value) for _, value in pairs if not isinstance(value, (dict, list))
    }
    user = shared_dashboard.user

    assert keys.isdisjoint(UNSHOWN_KEYS)
    assert values.isdisjoint(
        {
            user.email,
            str(user.id),
            str(shared_dashboard.workspace.id),
            str(user.organization_id),
            user.organization.name,
            shared_dashboard.other_project_id,
            FILTER_VALUE,
        }
    )


@pytest.mark.django_db
def test_shared_dashboard_answer_still_carries_what_the_page_shows(
    shared_dashboard,
):
    data = shared_dashboard.data
    current, older = data["widgets"]

    assert data["name"] == "Checkout health"
    assert data["description"] == "Latency and cost of the checkout agent"
    assert data["widget_count"] == 2
    assert [widget["id"] for widget in data["widgets"]] == [
        str(widget.id) for widget in shared_dashboard.widgets
    ]
    assert current["name"] == "Latency p95"
    assert current["description"] == "P95 response time"
    assert (current["width"], current["height"]) == (6, 4)
    assert current["chart_config"]["chart_type"] == "line"
    assert current["query_config"]["time_range"]["preset"] == "7D"
    metric = current["query_config"]["metrics"][0]
    assert (metric["display_name"], metric["aggregation"]) == ("Latency", "p95")
    assert older["name"] == "Cost"
    assert (older["width"], older["height"]) == (12, 3)
    assert older["chart_config"]["chart_type"] == "metric"
    assert older["query_config"]["timeRange"]["preset"] == "30D"
    assert older["query_config"]["metrics"][0]["displayName"] == "Cost"
