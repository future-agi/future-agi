"""PAGE-CAP-DSX: pagination errors retain their DRF status and fixed detail."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from rest_framework.exceptions import NotFound
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.tests.network_isolation import (
    no_external_lookups as no_external_lookups,
)
from model_hub.views import dataset_optimization, develop_annotations, experiments
from model_hub.views import prompt_template as prompt_views
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.utils.pagination import MAX_PAGE_SIZE, PageSizeTooLarge

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]

SITES = {
    "dataset-optimization": (dataset_optimization.DatasetOptimizationViewSet, "list"),
    "annotation-labels": (develop_annotations.AnnotationsLabelsViewSet, "list"),
    "experiments": (experiments.ExperimentListAPIView, None),
    "versions": (prompt_views.PromptTemplateViewSet, "versions"),
    "prompt-executions": (prompt_views.PromptExecutionViewSet, "list"),
    "combined-prompts": (prompt_views.PromptExecutionViewSet, "list"),
    "prompt-history": (prompt_views.PromptHistoryExecutionViewSet, "list"),
}


@pytest.fixture
def call_page_site(organization, workspace, user, prompt_template):
    """Use real dispatch/pagination, replacing only data sources and the EE gate."""

    def call(site, query, pagination_error=None):
        view_class, action = SITES[site]
        query = dict(query)
        if site == "combined-prompts":
            query["send_all"] = "true"
        request = APIRequestFactory().get("/model-hub/page-cap/", query)
        request.organization = organization
        request.workspace = workspace
        force_authenticate(request, user=user)
        set_workspace_context(
            organization=organization,
            workspace=workspace,
            user=user,
            require_workspace=True,
        )
        try:
            with ExitStack() as stack:
                stack.enter_context(patch("tfc.ee_gating.check_ee_feature"))
                lookup = stack.enter_context(
                    patch.object(
                        view_class,
                        "get_queryset",
                        return_value=(
                            develop_annotations.AnnotationsLabels.objects.none()
                            if site == "annotation-labels"
                            else view_class.queryset.none()
                        ),
                    )
                )
                combined = stack.enter_context(
                    patch.object(
                        prompt_views.PromptExecutionViewSet,
                        "_get_combined_templates_and_folders",
                        return_value=([], 0),
                    )
                )
                if site == "versions":
                    stack.enter_context(
                        patch.object(
                            view_class, "get_object", return_value=prompt_template
                        )
                    )
                if pagination_error is not None:
                    stack.enter_context(
                        patch.object(
                            view_class.pagination_class,
                            "paginate_queryset",
                            side_effect=pagination_error,
                        )
                    )
                view = (
                    view_class.as_view({"get": action})
                    if action is not None
                    else view_class.as_view()
                )
                response = view(request, pk=str(prompt_template.id))
            return response, lookup, combined
        finally:
            clear_workspace_context()

    return call


@pytest.mark.parametrize("site", list(SITES))
def test_bad_page_keeps_404(call_page_site, site):
    response, _, _ = call_page_site(site, {"page": "999999"})

    assert response.status_code == 404, response.data
    assert response.data["code"] == "not_found"
    assert response.data["message"] == "Invalid page."


@pytest.mark.parametrize("site", list(SITES))
def test_over_cap_keeps_typed_400(call_page_site, site):
    response, _, _ = call_page_site(site, {"limit": str(MAX_PAGE_SIZE + 1)})

    assert response.status_code == 400, response.data
    if site == "annotation-labels":
        # This existing serializer has a tighter 500-row limit. Keep it rather
        # than weakening it to 5000; the injected test below exercises the
        # PageSizeTooLarge exception at this site's paginator boundary.
        assert response.data["code"] == "max_value"
        assert response.data["details"]["limit"] == [
            "Ensure this value is less than or equal to 500."
        ]
    else:
        assert response.data["code"] == "page_size_too_large"
        assert PageSizeTooLarge.default_detail in response.data["message"]


@pytest.mark.parametrize("site", list(SITES))
def test_at_cap_still_succeeds(call_page_site, site):
    cap = 500 if site == "annotation-labels" else MAX_PAGE_SIZE
    response, _, _ = call_page_site(site, {"limit": str(cap), "page": "1"})

    assert response.status_code == 200, response.data


@pytest.mark.parametrize(
    "limit", [501, MAX_PAGE_SIZE], ids=["above-local-cap", "shared-cap"]
)
def test_annotation_labels_keeps_existing_narrower_cap(call_page_site, limit):
    response, _, _ = call_page_site("annotation-labels", {"limit": str(limit)})

    assert response.status_code == 400, response.data
    assert response.data["code"] == "max_value"
    assert response.data["details"]["limit"] == [
        "Ensure this value is less than or equal to 500."
    ]


@pytest.mark.parametrize("site", list(SITES))
@pytest.mark.parametrize("error_type", [NotFound, PageSizeTooLarge])
def test_paginator_api_exception_is_not_swallowed(call_page_site, site, error_type):
    # Inject at the actual paginator to cover each broad-except site even when
    # query-serializer validation would otherwise reject an oversized limit first.
    error = error_type()
    response, _, _ = call_page_site(site, {}, pagination_error=error)

    assert response.status_code == error.status_code, response.data
    assert response.data["code"] == error.default_code
    assert response.data["message"] == (
        "Invalid page."
        if site == "versions" and error_type is NotFound
        else error.default_detail
    )
