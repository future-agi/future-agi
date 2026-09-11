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


def test_observed_value_envelope_does_not_claim_exact_range_or_current_types(settings):
    settings.PROPERTY_CATALOG_DATABASE = "test_index"
    page = SimpleNamespace(
        values=(SimpleNamespace(value=True, attribute_type="array"),),
        has_more=False,
        next_cursor=None,
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
    ):
        factory.return_value.read_page.return_value = page
        response = invoke(request())
    assert response.status_code == 200
    result = response.data["result"]
    assert result["values"] == [{"value": True, "type": "array", "label": "true"}]
    assert result["query_provenance"] == "current_property_catalog"
    assert result["query_exact"] is False and result["attribute_types_exact"] is False
    assert not (
        {
            "catalog_epoch",
            "catalog_revision",
            "activation_fingerprint",
            "query_window_start",
            "query_window_end",
        }
        & result.keys()
    )
    native.assert_not_called()


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


def test_value_page_returns_coverage_derived_from_the_authorized_scope():
    """The helper owns coverage because it owns the resolved scope.

    Regression: coverage was first computed at the response site in
    ``filter_values``, where ``scope`` is not bound -- it is a local of this
    helper. Every unit test passed because they exercised
    ``observed_scope_coverage`` directly, and the view wiring only broke against
    a live request, with ``UnboundLocalError: cannot access local variable
    'scope'`` surfacing as a 500 on every custom-attribute value lookup.

    Asserting the helper returns the pair keeps the two bound together.
    """
    req = request(project_ids=[])
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as reader,
        patch("tracer.views.dashboard.observed_scope_coverage") as coverage,
    ):
        result = _read_property_catalog_value_page(
            req, req.validated_query_data, deadline=ReadDeadline.start(1000)
        )

    assert isinstance(result, tuple) and len(result) == 2
    returned_coverage, page = result
    assert returned_coverage is coverage.return_value
    assert page is reader.return_value.read_page.return_value
    # Coverage must be judged against the same authorized scope the page used,
    # never a differently-built one.
    assert (
        coverage.call_args.kwargs["scope"]
        is reader.return_value.read_page.call_args.kwargs["scope"]
    )
