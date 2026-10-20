"""Observed suggestions keep their public envelope without activation metadata."""

import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.property_catalog.value_cursor import (
    PropertyCatalogValueCursorError,
)
from tracer.services.clickhouse.v2.property_catalog.value_reader import (
    PropertyCatalogValueUnavailable,
)
from tracer.tests.test_unified_property_catalog_api import PROJECT_ID, _request
from tracer.views.dashboard import DashboardViewSet, _read_property_catalog_value_page


def request(**overrides):
    return _request(
        metric_name="key",
        metric_type="custom_attribute",
        property_id="custom_attribute:key",
        _property_kind="custom_attribute",
        **overrides,
    )


def invoke(req):
    return inspect.unwrap(DashboardViewSet.filter_values)(DashboardViewSet(), req)


def test_native_choice_search_precedes_inventory_limit_and_preserves_paging():
    from tracer.views.dashboard import _FINITE_NATIVE_FILTER_VALUE_MAX

    req = _request(
        property_id=f"eval_template:{PROJECT_ID}",
        _property_kind="eval_template",
        metric_name=PROJECT_ID,
        metric_type="eval_metric",
        source="traces",
        page_size=1,
        search="needle",
    )
    choices = [f"unrelated-{i}" for i in range(_FINITE_NATIVE_FILTER_VALUE_MAX)]
    choices += ["needle-a", "needle-b"]
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.CurrentDefinitionSource") as definitions,
    ):
        definitions.return_value.resolve.return_value = SimpleNamespace(
            details={"choices": choices}
        )
        first = invoke(req)
        assert first.status_code == 200, first.data
        assert first.data["result"]["values"][0]["value"] == "needle-a"
        req.validated_query_data["cursor"] = first.data["result"]["next_cursor"]
        assert req.validated_query_data["cursor"]
        second = invoke(req)
        assert second.status_code == 200, second.data
        assert second.data["result"]["values"][0]["value"] == "needle-b"
        assert second.data["result"]["has_more"] is False
        req.validated_query_data.update(search="", cursor=None)
        assert invoke(req).status_code == 422


@pytest.mark.parametrize(
    "empty,has_more", [(True, False), (False, False), (False, True)]
)
def test_observed_value_envelope_describes_the_index_page_only(
    settings, empty, has_more
):
    settings.PROPERTY_CATALOG_DATABASE = "test_index"
    page = SimpleNamespace(
        values=() if empty else (SimpleNamespace(value=True, attribute_type="array"),),
        has_more=has_more,
        next_cursor="next-page" if has_more else None,
        attribute_types=("array",),
        query_count=2,
    )
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as factory,
        patch("tracer.views.dashboard.AttributeReadSelector") as native,
        patch("tracer.services.clickhouse.client.ClickHouseClient") as extra_client,
    ):
        factory.return_value.read_page.return_value = page
        response = invoke(request())
    assert response.status_code == 200
    result = response.data["result"]
    assert result["values"] == (
        [] if empty else [{"value": True, "type": "array", "label": "true"}]
    )
    assert result["query_provenance"] == "current_property_catalog"
    assert result["query_exact"] is False and result["attribute_types_exact"] is False
    assert result["query_complete"] is True and result["query_status"] == "complete"
    assert result["has_more"] is has_more
    assert result["next_cursor"] == ("next-page" if has_more else None)
    assert result["browse_status"] == ("continuation" if has_more else "exhausted")
    assert not (
        {
            "catalog_epoch",
            "catalog_revision",
            "activation_fingerprint",
            "query_window_start",
            "query_window_end",
            "coverage_reason",
            "coverage_floor",
        }
        & result.keys()
    )
    native.assert_not_called()
    extra_client.assert_not_called()


@pytest.mark.parametrize(
    "error,status,code",
    [
        (
            PropertyCatalogValueCursorError("cursor_expired", "Restart"),
            400,
            "cursor_expired",
        ),
        (
            PropertyCatalogValueCursorError("cursor_mismatch", "Invalid scope"),
            400,
            "cursor_mismatch",
        ),
        (PropertyCatalogValueUnavailable("query_failed"), 503, "service_unavailable"),
    ],
)
def test_errors_never_fall_back_to_fact_scan(settings, error, status, code):
    settings.PROPERTY_CATALOG_DATABASE = "test_index"
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as factory,
        patch("tracer.views.dashboard.AttributeReadSelector") as native,
    ):
        factory.return_value.read_page.side_effect = error
        response = invoke(request())
    assert response.status_code == status
    assert response.data["code"] == code
    native.assert_not_called()


def test_foreign_scope_is_rejected_before_reader():
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            side_effect=ValueError("Some project_ids are invalid"),
        ),
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as reader,
    ):
        assert invoke(request()).status_code == 400
    reader.assert_not_called()


def test_authorization_and_all_scope_binding_precede_observed_query(settings):
    settings.PROPERTY_CATALOG_DATABASE = "test_index"
    req = request(project_ids=[])
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ) as authorize,
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as reader,
    ):
        _read_property_catalog_value_page(
            req, req.validated_query_data, deadline=ReadDeadline.start(1000)
        )
    assert authorize.call_args.kwargs["include_workspace_projects"]
    scope = reader.return_value.read_page.call_args.kwargs["scope"]
    assert scope["workspace_scope"] and scope["project_ids"] == [PROJECT_ID]


def test_value_page_returns_only_the_authorized_reader_page():
    """No second source-history lookup is needed to serve index suggestions."""
    req = request(project_ids=[])
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as reader,
    ):
        result = _read_property_catalog_value_page(
            req, req.validated_query_data, deadline=ReadDeadline.start(1000)
        )

    assert result is reader.return_value.read_page.return_value
    scope = reader.return_value.read_page.call_args.kwargs["scope"]
    assert scope["project_ids"] == [PROJECT_ID]
    assert scope["workspace_scope"] is True
