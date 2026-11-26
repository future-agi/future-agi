from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tracer.serializers.dashboard import (
    DashboardFilterValuesQuerySerializer,
    DashboardMetricsCatalogQuerySerializer,
)
from tracer.views.dashboard import DashboardViewSet

WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"


def _enable_catalog_reads(settings):
    settings.PROPERTY_CATALOG_DATABASE = "property_catalog_dev_clean"


def _request(**validated_overrides):
    validated = {
        "project_ids": [PROJECT_ID],
        "category": "custom_attribute",
        "source": "traces",
        "search": "customer",
        "page_size": 50,
        "cursor_mode": True,
        "per_eval_config": False,
        "exclude_custom_attributes": False,
    }
    validated.update(validated_overrides)
    organization = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        pk="11111111-1111-1111-1111-111111111111",
    )
    user = SimpleNamespace(id="user-1", organization=organization)
    return SimpleNamespace(
        workspace=SimpleNamespace(
            id=WORKSPACE_ID, pk=WORKSPACE_ID, organization=organization
        ),
        organization=organization,
        user=user,
        auth=SimpleNamespace(id="token-1"),
        query_params={
            "cursor_mode": "true",
            "page_size": "50",
            "category": "custom_attribute",
        },
        validated_query_data=validated,
    )


def test_metrics_cursor_contract_requires_explicit_bounded_mode():
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"cursor": "signed", "page_size": 50}
    ).is_valid()
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True}
    ).is_valid()
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True, "page": 1, "page_size": 50}
    ).is_valid()
    assert not DashboardMetricsCatalogQuerySerializer(
        data={
            "cursor_mode": True,
            "page_size": 50,
            "exclude_custom_attributes": True,
        }
    ).is_valid()
    valid = DashboardMetricsCatalogQuerySerializer(
        data={
            "cursor_mode": True,
            "page_size": 50,
            "search": "customer",
            "role": "metric",
        }
    )
    assert valid.is_valid(), valid.errors
    assert valid.validated_data["role"] == "metric"
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True, "page_size": 50, "role": "aggregate"}
    ).is_valid()
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"page": 1, "page_size": 50, "role": "metric"}
    ).is_valid()
    cursor_page_too_large = DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True, "page_size": 51}
    )
    assert not cursor_page_too_large.is_valid()
    legacy_page_200 = DashboardMetricsCatalogQuerySerializer(
        data={"page": 1, "page_size": 200}
    )
    assert legacy_page_200.is_valid(), legacy_page_200.errors

    for logical_source in (
        "spans",
        "sessions",
        "users",
        "voice_calls",
        "prompts",
    ):
        logical = DashboardMetricsCatalogQuerySerializer(
            data={
                "cursor_mode": True,
                "page_size": 50,
                "source": logical_source,
            }
        )
        assert logical.is_valid(), logical.errors

        legacy = DashboardMetricsCatalogQuerySerializer(
            data={"page": 1, "page_size": 50, "source": logical_source}
        )
        assert not legacy.is_valid()

    multibyte_search = DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True, "page_size": 50, "search": "💡" * 129}
    )
    assert not multibyte_search.is_valid()


def test_metrics_cursor_normalizes_exact_custom_attribute_sources():
    for source in ("traces", "spans", "voice_calls", "prompts"):
        serializer = DashboardMetricsCatalogQuerySerializer(
            data={
                "cursor_mode": True,
                "page_size": 50,
                "category": "custom_attribute",
                "source": source,
            }
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["source"] == "traces"


@pytest.mark.parametrize(
    "source", ["sessions", "users", "datasets", "simulation", "all", "both"]
)
def test_metrics_cursor_rejects_unsupported_custom_attribute_sources(source):
    serializer = DashboardMetricsCatalogQuerySerializer(
        data={
            "cursor_mode": True,
            "page_size": 50,
            "category": "custom_attribute",
            "source": source,
        }
    )

    assert not serializer.is_valid()
    assert "source" in serializer.errors


def test_filter_values_normalizes_logical_definition_sources_to_native_transport():
    cases = (
        ("system_attribute:spans:latency", "spans", "traces"),
        ("system_attribute:users:user", "users", "sessions"),
        ("system_attribute:voice_calls:latency", "voice_calls", "traces"),
        ("system_attribute:prompts:avg_latency", "prompts", "traces"),
        ("custom_attribute:customer.plan", "spans", "traces"),
        ("custom_attribute:customer.plan", "voice_calls", "traces"),
        ("custom_attribute:customer.plan", "prompts", "traces"),
    )

    for property_id, source, expected_transport in cases:
        serializer = DashboardFilterValuesQuerySerializer(
            data={
                "property_id": property_id,
                "source": source,
                "page_size": 25,
            }
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["source"] == expected_transport


@pytest.mark.parametrize(
    ("property_id", "source"),
    [
        ("annotation:11111111-1111-4111-8111-111111111111", "both"),
        ("annotation:11111111-1111-4111-8111-111111111111", "all"),
        ("eval_config:22222222-2222-4222-8222-222222222222", "both"),
        ("eval_template:33333333-3333-4333-8333-333333333333", "all"),
    ],
)
def test_filter_values_accepts_shared_definition_sources(property_id, source):
    serializer = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": property_id,
            "source": source,
            "page_size": 25,
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["source"] == source


def test_filter_values_rejects_unsupported_custom_attribute_source():
    serializer = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": "custom_attribute:customer.plan",
            "source": "sessions",
            "page_size": 25,
        }
    )

    assert not serializer.is_valid()
    assert "property_id" in serializer.errors

    legacy_serializer = DashboardFilterValuesQuerySerializer(
        data={
            "metric_name": "customer.plan",
            "metric_type": "custom_attribute",
            "source": "sessions",
            "page_size": 25,
        }
    )
    assert not legacy_serializer.is_valid()
    assert "source" in legacy_serializer.errors


@pytest.mark.parametrize(
    "project_ids",
    [
        "not-a-uuid",
        ",".join(
            [f"00000000-0000-4000-8000-{index:012x}" for index in range(1024)]
            + ["not-a-uuid"]
        ),
    ],
    ids=("malformed", "malformed_after_large_valid_scope"),
)
def test_filter_values_rejects_malformed_project_scope(project_ids):
    serializer = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": "custom_attribute:customer.plan",
            "source": "traces",
            "page_size": 25,
            "project_ids": project_ids,
        }
    )

    assert not serializer.is_valid()
    assert "project_ids" in serializer.errors


@pytest.mark.parametrize("project_count", (65, 178, 257, 1024))
@pytest.mark.parametrize("endpoint", ("properties", "values"))
def test_catalog_serializers_accept_full_project_inventory(project_count, endpoint):
    projects = [
        f"00000000-0000-4000-8000-{index:012x}" for index in range(project_count)
    ]
    data = {"page_size": 25, "project_ids": ",".join(projects)}
    if endpoint == "properties":
        serializer = DashboardMetricsCatalogQuerySerializer(
            data={**data, "cursor_mode": True}
        )
    else:
        serializer = DashboardFilterValuesQuerySerializer(
            data={
                **data,
                "property_id": "custom_attribute:customer.plan",
                "source": "traces",
            }
        )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["project_ids"] == projects


@pytest.mark.parametrize(
    ("catalog_max", "dashboard_max"),
    [(7, 11), (11, 7)],
)
def test_filter_values_page_size_honors_both_configured_maxima(
    settings, catalog_max, dashboard_max
):
    settings.PROPERTY_CATALOG_MAX_PAGE_SIZE = catalog_max
    settings.DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE = dashboard_max
    admitted_max = min(catalog_max, dashboard_max)

    accepted = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": "custom_attribute:customer.plan",
            "source": "traces",
            "page_size": admitted_max,
        }
    )
    rejected = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": "custom_attribute:customer.plan",
            "source": "traces",
            "page_size": admitted_max + 1,
        }
    )

    assert accepted.is_valid(), accepted.errors
    assert not rejected.is_valid()
    assert "page_size" in rejected.errors


def test_filter_values_maps_reader_value_error_to_400(settings):
    _enable_catalog_reads(settings)
    reader = Mock()
    reader.read_page.side_effect = ValueError("page_size must be between 1 and 25")
    request = _request(
        metric_name="customer.plan",
        metric_type="custom_attribute",
        property_id="custom_attribute:customer.plan",
        _property_kind="custom_attribute",
        source="traces",
        search="",
        page_size=26,
    )

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch(
            "tracer.views.dashboard.PropertyCatalogValueReader",
            return_value=reader,
        ),
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), request
        )

    assert response.status_code == 400
    reader.read_page.assert_called_once()


@pytest.mark.parametrize("has_more", [False, True])
@pytest.mark.parametrize("counts", [None, {"all": 516, "custom_attribute": 516}])
@pytest.mark.parametrize(
    "category,cursor,include_counts",
    [
        ("", None, True),
        ("custom_attribute", None, False),
        ("system_metric", None, False),
        ("", "next-page", False),
    ],
)
def test_current_metrics_envelope_describes_the_index_page_only(
    settings, has_more, counts, category, cursor, include_counts
):
    settings.PROPERTY_CATALOG_DATABASE = "test_index"
    reader = Mock()
    reader.read_page.return_value = SimpleNamespace(
        metrics=({"id": "custom_attribute:key"},) if has_more else (),
        has_more=has_more,
        next_cursor="next-page" if has_more else None,
        category_counts=counts,
        category_counts_exact=counts is not None,
    )
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch(
            "tracer.views.dashboard.resolve_property_catalog_agent_scope",
            return_value="",
        ),
        patch("tracer.views.dashboard.PropertyCatalogReader", return_value=reader),
        patch("tracer.services.clickhouse.client.ClickHouseClient") as extra_client,
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request(category=category, cursor=cursor)
        )
    assert response.status_code == 200
    result = response.data["result"]
    assert result["query_provenance"] == "current_property_catalog"
    assert result["query_exact"] is False and result["total"] is None
    assert result["query_complete"] is True and result["query_status"] == "complete"
    assert result["has_more"] is has_more
    assert result["next_cursor"] == ("next-page" if has_more else None)
    assert reader.read_page.call_args.kwargs["include_counts"] is include_counts
    if counts is not None:
        assert result["category_counts"] == counts
        assert result["category_counts_exact"] is True
    else:
        assert "category_counts" not in result
    extra_client.assert_not_called()
    assert not (
        {
            "catalog_epoch",
            "catalog_revision",
            "activation_fingerprint",
            "coverage_reason",
            "coverage_floor",
        }
        & result.keys()
    )


@pytest.mark.parametrize("action", ["metrics", "filter_values"])
def test_catalog_read_post_validates_large_body_and_rejects_mixed_parameters(action):
    from django.http import QueryDict
    from rest_framework.response import Response

    method = getattr(DashboardViewSet, action)
    assert method.mapping == {"get": action, "post": action}
    assert method._read_query_post is True
    data = (
        {"cursor_mode": True, "cursor": "x" * 24000, "page_size": 1}
        if action == "metrics"
        else {
            "property_id": "custom_attribute:key",
            "source": "traces",
            "cursor": "x" * 24000,
            "page_size": 1,
        }
    )
    request = _request()
    request.method, request.data, request.query_params = "POST", data, QueryDict()
    # Execute the real decorator and stop at scope resolution; a long body must
    # reach the same authorization path, not fall back to a different reader.
    with patch(
        "tracer.views.dashboard.resolve_property_catalog_project_scope",
        side_effect=ValueError("fixture scope rejection"),
    ) as scope:
        response = method(DashboardViewSet(), request)
    assert isinstance(response, Response) and response.status_code == 400
    scope.assert_called_once()
    assert request.validated_query_data["cursor"] == data["cursor"]
    request.query_params = QueryDict("page_size=2")
    with patch(
        "tracer.views.dashboard.resolve_property_catalog_project_scope"
    ) as scope:
        assert method(DashboardViewSet(), request).status_code == 400
    scope.assert_not_called()


@pytest.mark.parametrize("permission", ["project", "agent"])
def test_current_metrics_rechecks_scope_before_reader(permission):
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ) as project,
        patch(
            "tracer.views.dashboard.resolve_property_catalog_agent_scope",
            return_value="",
        ) as agent,
        patch("tracer.views.dashboard.PropertyCatalogReader") as reader,
    ):
        (project if permission == "project" else agent).side_effect = ValueError(
            "Scope is invalid"
        )
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request()
        )
    assert response.status_code == 400
    reader.assert_not_called()


# These cases traverse JWT authentication, workspace authorization, the actual
# validated action and the real scoped Postgres catalog reader. No reader mock.
CATALOG_URL = "/tracer/dashboard/metrics/"


@pytest.fixture
def catalog_actor(user, workspace, observe_project):
    from rest_framework.test import APIClient

    client = APIClient()
    login = client.post(
        "/accounts/token/",
        {"email": user.email, "password": "testpassword123"},
        format="json",
    )
    assert login.status_code == 200
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return SimpleNamespace(
        client=client,
        user=user,
        organization=user.organization,
        workspace=workspace,
        project=observe_project,
    )


def _catalog_http(actor, method="get", **query):
    data = {
        "cursor_mode": True,
        "page_size": 50,
        "category": "metric",
        "project_ids": str(actor.project.id),
        **query,
    }
    if method == "post":
        return actor.client.post(CATALOG_URL, data, format="json")
    return actor.client.get(CATALOG_URL, data)


def _catalog_items(response):
    from tracer.serializers.dashboard import DashboardMetricCatalogItemSerializer

    assert response.status_code == 200, response.data
    items = response.json()["result"]["metrics"]
    for item in items:
        typed = DashboardMetricCatalogItemSerializer(data=item)
        assert typed.is_valid(), typed.errors
        # In particular, serialization must not discard descriptor metadata or
        # trim the accepted display labels (including newline/tab boundaries).
        assert typed.data == item
    return items


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("kind", ["share", "count"])
def test_http_metric_labels_projection_and_b06_roundtrip(catalog_actor, method, kind):
    from tracer.tests.test_property_catalog_metric_fields import (
        create_metric,
        validate_descriptor,
    )
    from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS

    labels = {"match": "\nResolved\tfully\n", "other": "\tNot\nresolved\t"}
    metric, active = create_metric(catalog_actor, kind=kind, value_labels=labels)
    session = _catalog_items(
        _catalog_http(catalog_actor, method, context="sessions", level="conversation")
    )[0]
    assert session["metric_id"] == str(metric.id)
    assert session["column_id"] == session["property_id"] == f"metric_field:{metric.id}"
    assert session["property_kind"] == "metric_field"
    assert session["category"] == "metric"
    assert session["version_id"] == str(active.id) and session["version"] == 1
    assert session["definition_hash"] == active.definition_hash
    assert session["kind"] == kind and session["group_dims"] == ["locale"]
    assert session["type"] == session["output_type"] == "boolean"
    assert session["value_labels"] == labels and session["filterable"] is True
    assert set(session["operators"]) == FILTER_TYPE_ALLOWED_OPS["boolean"]
    for label in labels.values():
        validate_descriptor(catalog_actor, session, "sessions", value=label)
    projected = _catalog_items(
        _catalog_http(catalog_actor, method, context="users", level="conversation")
    )[0]
    assert projected["property_id"] == session["property_id"]
    assert projected["level"] == "user" and projected["source_level"] == "conversation"
    assert projected["projection"] == "per_user" and projected["source"] == "users"
    assert projected["type"] == projected["output_type"] == "number"
    assert projected["value_labels"] == {} and projected["filterable"] is True
    assert (projected["unit"], projected["value_format"]) == (
        ("%", "percent") if kind == "share" else ("", "number")
    )
    assert set(projected["operators"]) == FILTER_TYPE_ALLOWED_OPS["number"]
    validate_descriptor(catalog_actor, projected, "users", value=0)


@pytest.mark.parametrize("method", ["get", "post"])
def test_http_active_version_not_draft_and_explicit_users_only(catalog_actor, method):
    from rest_framework.exceptions import ValidationError

    from tracer.models.derived_metric import DerivedMetric
    from tracer.tests.test_property_catalog_metric_fields import (
        create_metric,
        create_version,
        validate_descriptor,
    )

    metric, active = create_metric(catalog_actor)
    before = _catalog_items(_catalog_http(catalog_actor, method, context="sessions"))
    draft = create_version(
        metric,
        version=2,
        kind="count",
        level="user",
        user_rule="any",
        value_labels={"match": "New\nmatch", "other": "New\tother"},
        definition_hash="a" * 64,
    )
    DerivedMetric.no_workspace_objects.filter(id=metric.id).update(current_version=2)
    assert (
        _catalog_items(_catalog_http(catalog_actor, method, context="sessions"))
        == before
    )
    DerivedMetric.activate_version(metric.id, draft.id, expected_active_id=active.id)
    for query in (
        {},
        {"source": "users"},
        {"context": "sessions"},
        {"context": "tasks"},
    ):
        assert _catalog_items(_catalog_http(catalog_actor, method, **query)) == []
    item = _catalog_items(_catalog_http(catalog_actor, method, context="users"))[0]
    assert item["version"] == 2 and item["version_id"] == str(draft.id)
    assert item["definition_hash"] == "a" * 64 and item["kind"] == "count"
    assert item["value_labels"] == draft.value_labels
    assert item["source_level"] == item["level"] == "user"
    assert item["projection"] == "user_metric" and item["filterable"] is False
    with pytest.raises(ValidationError, match="not available"):
        validate_descriptor(catalog_actor, item, "users")


@pytest.mark.parametrize("method", ["get", "post"])
def test_http_catalog_scope_and_viewer_read_only(catalog_actor, method, settings):
    from accounts.models import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tracer.models.derived_metric import DerivedMetric
    from tracer.models.project import Project
    from tracer.tests.test_property_catalog_metric_fields import create_metric

    actor = catalog_actor
    visible, _ = create_metric(actor)
    sibling = Project.no_workspace_objects.create(
        name="Other project",
        workspace=actor.workspace,
        organization=actor.organization,
        trace_type="observe",
        model_type=actor.project.model_type,
    )
    create_metric(actor, project=sibling)
    foreign_ws = Workspace.no_workspace_objects.create(
        name="Other workspace",
        organization=actor.organization,
        created_by=actor.user,
    )
    foreign_project = Project.no_workspace_objects.create(
        name="Foreign project",
        workspace=foreign_ws,
        organization=actor.organization,
        trace_type="observe",
        model_type=actor.project.model_type,
    )
    create_metric(actor, project=foreign_project)
    deleted, _ = create_metric(actor)
    DerivedMetric.no_workspace_objects.filter(id=deleted.id).update(deleted=True)
    wrong_org, _ = create_metric(actor)
    other_org = Organization.objects.create(name="Other catalog organization")
    DerivedMetric.no_workspace_objects.filter(id=wrong_org.id).update(
        organization=other_org
    )
    wrong_ws, _ = create_metric(actor)
    DerivedMetric.no_workspace_objects.filter(id=wrong_ws.id).update(
        workspace=foreign_ws
    )
    OrganizationMembership.no_workspace_objects.filter(user=actor.user).update(level=1)
    WorkspaceMembership.no_workspace_objects.filter(user=actor.user).update(level=1)
    items = _catalog_items(_catalog_http(actor, method, context="sessions"))
    assert [item["metric_id"] for item in items] == [str(visible.id)]
    assert (
        _catalog_http(actor, method, project_ids=str(foreign_project.id)).status_code
        == 400
    )
    assert actor.client.post("/tracer/dashboard/", {}, format="json").status_code == 403
    assert (
        actor.client.post("/tracer/dashboard/query/", {}, format="json").status_code
        == 403
    )
    expected = 403 if settings.METRICS_ENABLED else 404
    assert (
        actor.client.post("/tracer/derived-metrics/", {}, format="json").status_code
        == expected
    )
    # DEFAULT/unset, explicit OFF and ON runners all execute this assertion;
    # catalog metadata availability must not turn the feature's API on.
    assert actor.client.get("/tracer/derived-metrics/capabilities/").status_code == (
        200 if settings.METRICS_ENABLED else 404
    )
    Project.no_workspace_objects.filter(id=actor.project.id).update(deleted=True)
    assert _catalog_http(actor, method, context="sessions").status_code == 400
    actor.client.credentials()
    assert (
        actor.client.get(CATALOG_URL, {"cursor_mode": True, "page_size": 1}).status_code
        == 401
    )


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("field,value", [("context", "users"), ("level", "span")])
def test_http_cursor_rejects_changed_context_or_level(
    catalog_actor, method, field, value
):
    from tracer.tests.test_property_catalog_metric_fields import create_metric

    for _ in range(2):
        create_metric(catalog_actor)
    query = {"context": "sessions", "level": "conversation", "page_size": 1}
    first = _catalog_http(catalog_actor, method, **query)
    assert len(_catalog_items(first)) == 1
    assert first.data["result"]["has_more"] is True
    cursor = first.data["result"]["next_cursor"]
    valid = _catalog_http(catalog_actor, method, **query, cursor=cursor)
    assert len(_catalog_items(valid)) == 1
    assert valid.data["result"]["metrics"] != first.data["result"]["metrics"]
    replay = _catalog_http(
        catalog_actor, method, **{**query, field: value}, cursor=cursor
    )
    assert replay.status_code == 400
    assert "cursor_mismatch" in str(replay.data)


@pytest.mark.parametrize("method", ["get", "post"])
def test_http_trace_fields_stay_disabled(catalog_actor, method, settings):
    from tracer.models.derived_metric import DerivedMetric
    from tracer.services.derived_metrics.errors import MetricInputError
    from tracer.tests.test_property_catalog_metric_fields import (
        create_metric,
        create_version,
    )

    assert not getattr(settings, "DERIVED_METRICS_TRACE_ENABLED", False)
    metric, active = create_metric(catalog_actor)
    draft = create_version(metric, version=2, level="trace")
    with pytest.raises(MetricInputError, match="Trace metrics are not enabled"):
        DerivedMetric.activate_version(
            metric.id, draft.id, expected_active_id=active.id
        )
    assert (
        _catalog_items(
            _catalog_http(catalog_actor, method, context="traces", level="trace")
        )
        == []
    )
    assert (
        len(_catalog_items(_catalog_http(catalog_actor, method, context="sessions")))
        == 1
    )


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "query",
    [
        {"context": "invalid"},
        {"level": "session"},
        {"cursor_mode": False, "context": "users"},
        {"cursor_mode": False, "level": "conversation"},
        {"cursor_mode": False, "category": "metric"},
        {"workflow": "observability", "context": "sessions"},
    ],
)
def test_http_rejects_invalid_or_legacy_metric_selectors(catalog_actor, method, query):
    assert _catalog_http(catalog_actor, method, **query).status_code == 400


@pytest.mark.parametrize("workflow", ["observability", "dataset", "simulation"])
def test_legacy_workflow_contract_still_admitted(workflow):
    serializer = DashboardMetricsCatalogQuerySerializer(data={"workflow": workflow})
    assert serializer.is_valid(), serializer.errors


def test_live_catalog_schema_describes_request_and_metric_fields():
    from django.urls import include, path
    from drf_yasg import openapi
    from drf_yasg.generators import OpenAPISchemaGenerator
    from rest_framework.routers import SimpleRouter

    router = SimpleRouter()
    router.register("dashboard", DashboardViewSet, basename="dashboard")
    schema = OpenAPISchemaGenerator(
        info=openapi.Info(title="Catalog contract", default_version="v1"),
        patterns=[path("tracer/", include(router.urls))],
    ).get_schema(public=True)
    route = next(
        value for key, value in schema["paths"].items() if key.endswith("/metrics/")
    )
    params = {p["name"]: p for p in route["get"]["parameters"]}
    assert "users" in params["context"]["enum"]
    assert set(params["level"]["enum"]) == {"conversation", "span", "trace", "user"}
    assert "metric" in params["category"]["enum"]
    definitions = schema["definitions"]
    body = next(p for p in route["post"]["parameters"] if p["in"] == "body")
    request = definitions[body["schema"]["$ref"].rsplit("/", 1)[-1]]["properties"]
    assert {"context", "level", "category"} <= set(request)
    item = definitions["DashboardMetricCatalogItem"]["properties"]
    assert "metric_field" in item["property_kind"]["enum"]
    assert {
        "metric_id",
        "column_id",
        "project_id",
        "version_id",
        "version",
        "kind",
        "level",
        "source_level",
        "projection",
        "value_format",
        "value_labels",
        "definition_hash",
        "group_dims",
        "operators",
        "filterable",
    } <= set(item)
    assert item["value_labels"]["additionalProperties"]["type"] == "string"
    assert item["filterable"]["type"] == "boolean"
    assert item["version"]["type"] == "integer"
    assert item["operators"]["items"]["type"] == "string"
