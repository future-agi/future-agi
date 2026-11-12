"""Exercise remaining broad handlers with the real paginator and empty reads."""

import importlib
import inspect
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db.models import Value
from rest_framework.exceptions import NotFound

from accounts.authentication import custom_exception_handler
from tfc.tests.test_page_cap_manual_round3 import empty_query
from tfc.tests.test_page_cap_round3 import EARLY_SITES, SITE_IDS, request_for
from tfc.utils.pagination import (
    MAX_PAGE_SIZE,
    ExtendedPageNumberPagination,
    PageSizeTooLarge,
)

SITES = EARLY_SITES[:9]


def call_propagation(site, page, *, injected=None):
    module_name, class_name, method_name, key, kwargs = site
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    page_key = "page_number" if module_name.startswith("agent_playground") else "page"
    request = request_for({key: MAX_PAGE_SIZE, page_key: page})
    view = cls()
    view.request = request
    view.kwargs = {}
    query = empty_query()
    with ExitStack() as stack:

        def mocked(target, **values):
            return stack.enter_context(patch(target, **values))

        if hasattr(view, "get_queryset"):
            stack.enter_context(patch.object(view, "get_queryset", return_value=query))
        if class_name == "AgentVersionListView":
            agent = SimpleNamespace(get_version_history=lambda: query)
            mocked(module_name + ".AgentDefinition.objects.get", return_value=agent)
        elif class_name == "AgentVersionCallExecutionView":
            mocked(
                module_name + ".AgentVersion.objects.get",
                return_value=SimpleNamespace(id="version"),
            )
            mocked(module_name + ".CallExecution.objects.filter", return_value=query)
        elif class_name == "ScenariosListView":
            mocked(module_name + ".Scenarios.objects.filter", return_value=query)
            mocked(module_name + ".Row.objects.filter", return_value=empty_query())
            mocked(module_name + ".Subquery", return_value=Value(0))
        elif class_name == "GraphViewSet":
            mocked(
                module_name + ".annotate_graph_list_fields",
                side_effect=lambda rows: rows,
            )
            stack.enter_context(
                patch.object(
                    view, "get_object", return_value=SimpleNamespace(id="graph")
                )
            )
            mocked(
                module_name + ".GraphVersion.no_workspace_objects.filter",
                return_value=query,
            )
            mocked(
                module_name + ".get_global_variable_names_for_versions", return_value={}
            )
        elif class_name == "GraphDatasetViewSet":
            stack.enter_context(
                patch.object(
                    view,
                    "get_object",
                    return_value=SimpleNamespace(
                        dataset=SimpleNamespace(id="dataset", name="Dataset"),
                        graph=SimpleNamespace(id="graph"),
                    ),
                )
            )
            mocked(
                module_name + ".GraphVersion.no_workspace_objects.filter",
                return_value=empty_query(),
            )
            mocked(
                module_name + ".Column.no_workspace_objects.filter",
                return_value=empty_query(),
            )
            mocked(module_name + ".Row.no_workspace_objects.filter", return_value=query)
        elif class_name.startswith("Simulation"):
            helper = (
                "_handle_run_test"
                if class_name == "SimulationMetricsView"
                else "_handle_run_test_executions"
            )

            def paged(request, *args):
                paginator = ExtendedPageNumberPagination()
                rows = paginator.paginate_queryset([], request)
                return paginator.get_paginated_response(rows)

            stack.enter_context(patch.object(view, helper, side_effect=paged))
        if injected is not None:
            if module_name.startswith("agent_playground"):
                mocked(module_name + ".paginate_queryset", side_effect=injected)
            else:
                mocked(
                    "tfc.utils.pagination.ExtendedPageNumberPagination.paginate_queryset",
                    side_effect=injected,
                )
        return inspect.unwrap(getattr(cls, method_name))(view, request, **kwargs)


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS[:9])
def test_remaining_handler_accepts_exact_cap(site):
    response = call_propagation(site, 1)
    assert response.status_code == 200, response.data


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS[:9])
def test_remaining_handler_bad_page_is_404(site):
    with pytest.raises(NotFound) as raised:
        call_propagation(site, 100)
    assert custom_exception_handler(raised.value, {}).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS[:9])
def test_remaining_handler_preserves_injected_typed_refusal(site):
    with pytest.raises(PageSizeTooLarge) as raised:
        call_propagation(site, 1, injected=PageSizeTooLarge())
    response = custom_exception_handler(raised.value, {})
    assert response.status_code == 400
    assert response.data["code"] == "page_size_too_large"


@pytest.mark.parametrize(
    "error", [NotFound(), PageSizeTooLarge()], ids=["bad-page", "oversize"]
)
def test_sdk_execution_detail_helper_propagates(error):
    from sdk.views.analytics import SimulationRunsView

    request = request_for({"limit": MAX_PAGE_SIZE})
    request.validated_query_data["execution_id"] = "execution"
    view = SimulationRunsView()
    with patch.object(view, "_handle_execution_detail", side_effect=error) as helper:
        with pytest.raises(type(error)):
            inspect.unwrap(SimulationRunsView.get)(view, request)
    helper.assert_called_once()
