"""Success and bad-page controls for the round-three manual page consumers."""

import importlib
import inspect
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.exceptions import NotFound

from accounts.authentication import custom_exception_handler
from tfc.tests.test_page_cap_round3 import request_for
from tfc.utils.pagination import MAX_PAGE_SIZE


def empty_query():
    query = MagicMock()
    for name in (
        "all",
        "filter",
        "exclude",
        "order_by",
        "select_related",
        "prefetch_related",
        "annotate",
        "values",
        "values_list",
        "distinct",
        "using",
    ):
        getattr(query, name).return_value = query
    query.count.return_value = 0
    query.first.return_value = None
    query.__iter__.return_value = iter(())
    query.__getitem__.return_value = []
    return query


MANUAL_SITES = [
    ("tracer.views.project", "ProjectView", "list", "page_size", "page_number", 0),
    (
        "tracer.views.observation_span",
        "ObservationSpanView",
        "get_queryset",
        "page_size",
        "page_number",
        0,
    ),
    (
        "tracer.views.observability_provider",
        "ObservabilityProviderViewSet",
        "list",
        "page_size",
        "page_number",
        0,
    ),
    (
        "tracer.views.project_version",
        "ProjectVersionView",
        "get_export_data",
        "page_size",
        "page_number",
        0,
    ),
    (
        "tracer.views.project_version",
        "ProjectVersionView",
        "get_project_version_ids",
        "page_size",
        "page_number",
        0,
    ),
    (
        "accounts.views.workspace_management",
        "ManageTeamView",
        "get",
        "page_size",
        "page",
        1,
    ),
    (
        "simulate.views.prompt_simulation",
        "PromptSimulationScenariosView",
        "get",
        "limit",
        "page",
        1,
    ),
    ("simulate.views.run_test", "RunTestCallExecutionsView", "get", "limit", "page", 1),
]
MANUAL_IDS = [f"{row[1]}-{row[2]}" for row in MANUAL_SITES]


def call_manual(site, page):
    module_name, class_name, method_name, key, page_key, first_page = site
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    request = request_for({key: MAX_PAGE_SIZE, page_key: page})
    request.user = SimpleNamespace(id="user", organization=request.organization)
    view = cls()
    view.request = request
    view.kwargs = {}
    query = empty_query()
    with ExitStack() as stack:

        def mocked(target, **kwargs):
            return stack.enter_context(patch(target, **kwargs))

        if class_name in {
            "ProjectView",
            "ObservabilityProviderViewSet",
            "ProjectVersionView",
        }:
            stack.enter_context(patch.object(view, "get_queryset", return_value=query))
            stack.enter_context(
                patch.object(
                    view, "get_serializer", return_value=SimpleNamespace(data=[])
                )
            )
        if class_name == "ProjectView":
            page_query = empty_query()
            query.__getitem__.return_value = page_query
        if class_name == "ObservationSpanView":
            mocked(
                "tfc.utils.base_viewset.BaseModelViewSetMixin.get_queryset",
                return_value=query,
            )
        if method_name == "get_export_data":
            mocked(
                module_name + ".ProjectVersionExportSerializer",
                return_value=SimpleNamespace(
                    is_valid=lambda: True,
                    validated_data={"project_id": "project", "runs_ids": []},
                ),
            )
            mocked(
                module_name + ".Project.objects.get",
                return_value=SimpleNamespace(name="project"),
            )
            mocked(
                module_name + "._project_workspace_scope_q", return_value=MagicMock()
            )
            mocked(module_name + ".ObservationSpan.objects.filter", return_value=query)
            mocked(
                module_name + ".EvalLogger.objects.filter", return_value=empty_query()
            )
            mocked(
                module_name + ".CustomEvalConfig.objects.filter",
                return_value=empty_query(),
            )
            mocked(
                module_name + ".AnnotationsLabels.objects.filter",
                return_value=empty_query(),
            )
            mocked(
                module_name + ".ProjectVersionWinner.objects.filter",
                return_value=empty_query(),
            )
        if class_name == "ManageTeamView":
            request.organization.display_name = "Org"
            mocked(module_name + ".resolve_org", return_value=request.organization)
            mocked(
                module_name + ".resolve_org_role",
                return_value=module.OrganizationRoles.OWNER,
            )
            mocked(module_name + ".User.objects.filter", return_value=query)
            mocked(
                module_name + ".UserSerializer", return_value=SimpleNamespace(data=[])
            )
        if class_name == "PromptSimulationScenariosView":
            mocked(
                module_name + ".get_request_organization",
                return_value=request.organization,
            )
            mocked(module_name + ".Scenarios.objects.filter", return_value=query)
        kwargs = {}
        if class_name == "RunTestCallExecutionsView":
            kwargs["run_test_id"] = "run"
            mocked(
                module_name + ".get_object_or_404",
                return_value=SimpleNamespace(id="run"),
            )
            mocked(module_name + ".CallExecution.objects.filter", return_value=query)
            mocked(module_name + ".build_eval_configs_map", return_value={})
            mocked(
                module_name + ".get_combined_call_executions_and_snapshots_query",
                return_value=("page sql", []),
            )
            mocked(
                module_name + ".get_combined_call_executions_and_snapshots_count_query",
                return_value=("count sql", []),
            )
            cursor = MagicMock()
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = (0,)
            mocked(
                module_name + ".connection.cursor"
            ).return_value.__enter__.return_value = cursor
        method = inspect.unwrap(getattr(cls, method_name))
        if method_name == "get_queryset":
            return method(view)
        return method(view, request, **kwargs)


@pytest.mark.parametrize("site", MANUAL_SITES, ids=MANUAL_IDS)
def test_manual_consumer_accepts_exact_cap(site):
    result = call_manual(site, site[-1])
    if site[2] == "get_queryset":
        assert result == []
    else:
        assert result.status_code == 200, getattr(result, "data", None)
        if hasattr(result, "close"):
            result.close()


@pytest.mark.parametrize("site", MANUAL_SITES, ids=MANUAL_IDS)
def test_manual_consumer_bad_page_is_404(site):
    with pytest.raises(NotFound) as raised:
        call_manual(site, 100)
    response = custom_exception_handler(raised.value, {})
    assert response.status_code == 404
    assert response.data["code"] == "not_found"


def test_version_ids_slice_before_serializer_materializes():
    from tracer.views.project_version import ProjectVersionView

    class LazyVersions:
        def __init__(self):
            self.slices = []

        def using(self, alias):
            return self

        def count(self):
            return MAX_PAGE_SIZE + 1

        def __getitem__(self, key):
            self.slices.append(key)
            return [
                {"id": str(i), "name": f"version-{i}"}
                for i in range(key.start, min(key.stop, self.count()))
            ]

        def __iter__(self):
            raise AssertionError("Full version queryset materialized before slicing")

    request = request_for({"page_size": MAX_PAGE_SIZE, "page_number": 0})
    view = ProjectVersionView()
    view.request = request
    rows = LazyVersions()

    def serialize(page, many):
        assert isinstance(page, list), "Serializer received an unbounded queryset"
        assert len(page) <= MAX_PAGE_SIZE
        return SimpleNamespace(data=page)

    with (
        patch.object(view, "get_queryset", return_value=rows),
        patch.object(view, "get_serializer", side_effect=serialize),
    ):
        response = inspect.unwrap(ProjectVersionView.get_project_version_ids)(
            view, request
        )
    assert response.status_code == 200, response.data
    assert rows.slices == [slice(0, MAX_PAGE_SIZE)]
    assert response.data["result"]["count"] == MAX_PAGE_SIZE + 1
    assert response.data["result"]["next"] is True
    assert len(response.data["result"]["project_version_ids"]) == MAX_PAGE_SIZE
