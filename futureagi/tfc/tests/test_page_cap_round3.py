"""Round-three cloud rows: reject oversized reads before touching any data.

No local DB execution is needed to author these tests. The cloud runner supplies
Django settings and the fail-closed provider-egress guard.
"""

import ast
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from accounts.authentication import custom_exception_handler
from tfc.utils.pagination import MAX_PAGE_SIZE, PageSizeTooLarge

# module, class, method, parameter, required URL arguments
EARLY_SITES = [
    (
        "simulate.views.agent_version",
        "AgentVersionListView",
        "get",
        "limit",
        {"agent_id": "unused"},
    ),
    (
        "simulate.views.agent_version",
        "AgentVersionCallExecutionView",
        "get",
        "page_size",
        {"agent_id": "unused", "version_id": "unused"},
    ),
    ("simulate.views.scenarios", "ScenariosListView", "get", "limit", {}),
    ("agent_playground.views.graph", "GraphViewSet", "list", "page_size", {}),
    ("agent_playground.views.graph", "GraphViewSet", "list_versions", "page_size", {}),
    (
        "agent_playground.views.graph_execution",
        "GraphExecutionViewSet",
        "list",
        "page_size",
        {},
    ),
    (
        "agent_playground.views.dataset_link",
        "GraphDatasetViewSet",
        "retrieve",
        "page_size",
        {},
    ),
    ("sdk.views.analytics", "SimulationMetricsView", "get", "limit", {}),
    ("sdk.views.analytics", "SimulationRunsView", "get", "limit", {}),
    ("tracer.views.project", "ProjectView", "list", "page_size", {}),
    (
        "tracer.views.observation_span",
        "ObservationSpanView",
        "get_queryset",
        "page_size",
        {},
    ),
    (
        "tracer.views.observability_provider",
        "ObservabilityProviderViewSet",
        "list",
        "page_size",
        {},
    ),
    (
        "tracer.views.project_version",
        "ProjectVersionView",
        "get_export_data",
        "page_size",
        {},
    ),
    (
        "tracer.views.project_version",
        "ProjectVersionView",
        "get_project_version_ids",
        "page_size",
        {},
    ),
    ("accounts.views.workspace_management", "ManageTeamView", "get", "page_size", {}),
    (
        "simulate.views.prompt_simulation",
        "PromptSimulationScenariosView",
        "get",
        "limit",
        {},
    ),
    (
        "simulate.views.run_test",
        "RunTestCallExecutionsView",
        "get",
        "limit",
        {"run_test_id": "unused"},
    ),
    (
        "simulate.views.run_test",
        "TestExecutionDetailView",
        "get",
        "limit",
        {"test_execution_id": "unused"},
    ),
]
SITE_IDS = [f"{row[1]}-{row[2]}" for row in EARLY_SITES]


def request_for(params):
    request = Request(APIRequestFactory().get("/", params))
    request.organization = SimpleNamespace(id="org")
    request.workspace = SimpleNamespace(id="workspace")
    request.validated_query_data = dict(params)
    return request


def assert_refusal(exc):
    response = custom_exception_handler(exc, {})
    assert response.status_code == 400
    assert response.data["code"] == "page_size_too_large"
    assert response.data["type"] == "validation_error"
    assert response.data["message"] == str(PageSizeTooLarge.default_detail)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "module_name,class_name,method_name,key,kwargs", EARLY_SITES, ids=SITE_IDS
)
def test_oversize_precedes_all_query_and_materialization(
    module_name, class_name, method_name, key, kwargs, django_assert_num_queries
):
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    view = cls()
    request = request_for({key: MAX_PAGE_SIZE + 1})
    view.request = request
    view.kwargs = {}
    # Deliberately bypass documentation/serializer decorators: the actual view
    # must also guard raw aliases before even looking up the parent object.
    method = inspect.unwrap(getattr(cls, method_name))
    with django_assert_num_queries(0), pytest.raises(PageSizeTooLarge) as raised:
        if method_name == "get_queryset":
            method(view)
        else:
            method(view, request, **kwargs)
    assert_refusal(raised.value)


FIELDS = [
    (
        "simulate.serializers.test_execution",
        "TestExecutionListQuerySerializer",
        "limit",
    ),
    ("simulate.serializers.test_execution", "ExecutionDetailQuerySerializer", "limit"),
    ("simulate.serializers.requests.scenarios", "ScenarioFilterSerializer", "limit"),
    (
        "simulate.serializers.requests.call_execution",
        "CallExecutionFilterSerializer",
        "limit",
    ),
    (
        "simulate.serializers.requests.agent_definition",
        "AgentDefinitionFilterSerializer",
        "limit",
    ),
    ("tracer.serializers.project", "ProjectViewSetListQuerySerializer", "page_size"),
]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "module_name,class_name,key", FIELDS, ids=[row[1] for row in FIELDS]
)
def test_request_field_rejects_oversize_with_typed_400(
    module_name, class_name, key, django_assert_num_queries
):
    cls = getattr(importlib.import_module(module_name), class_name)
    serializer = cls(data={key: MAX_PAGE_SIZE + 1})
    assert serializer.fields[key].max_value == MAX_PAGE_SIZE
    with django_assert_num_queries(0), pytest.raises(PageSizeTooLarge) as raised:
        serializer.is_valid(raise_exception=True)
    assert_refusal(raised.value)


@pytest.mark.parametrize(
    "module_name,class_name,key", FIELDS, ids=[row[1] for row in FIELDS]
)
def test_request_field_accepts_exact_cap(module_name, class_name, key):
    cls = getattr(importlib.import_module(module_name), class_name)
    serializer = cls(data={key: MAX_PAGE_SIZE})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data[key] == MAX_PAGE_SIZE


PROPAGATION_SITES = EARLY_SITES[:9]


@pytest.mark.parametrize(
    "module_name,class_name,method_name,key,kwargs", PROPAGATION_SITES, ids=SITE_IDS[:9]
)
def test_every_outer_handler_preserves_api_exceptions(
    module_name, class_name, method_name, key, kwargs
):
    source = Path(__file__).resolve().parents[2] / (
        module_name.replace(".", "/") + ".py"
    )
    tree = ast.parse(source.read_text())
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    outer = next(node for node in method.body if isinstance(node, ast.Try))
    broad_index = next(
        i
        for i, h in enumerate(outer.handlers)
        if isinstance(h.type, ast.Name) and h.type.id == "Exception"
    )
    handler = next(
        (
            h
            for h in outer.handlers[:broad_index]
            if isinstance(h.type, ast.Name) and h.type.id == "APIException"
        ),
        None,
    )
    assert handler is not None
    assert len(handler.body) == 1
    assert isinstance(handler.body[0], ast.Raise)
    assert handler.body[0].exc is None


class UntouchableRows:
    def __len__(self):
        raise AssertionError("Oversized request counted rows")

    def __iter__(self):
        raise AssertionError("Oversized request materialized rows")

    def __getitem__(self, key):
        raise AssertionError("Oversized request sliced rows")


@pytest.mark.django_db
def test_common_paginator_oversize_is_typed_before_data_access(
    django_assert_num_queries,
):
    from common.utils.pagination import paginate_queryset

    with django_assert_num_queries(0), pytest.raises(PageSizeTooLarge) as raised:
        paginate_queryset(
            UntouchableRows(), request_for({"page_size": MAX_PAGE_SIZE + 1})
        )
    assert_refusal(raised.value)


def test_common_paginator_accepts_exact_cap():
    from common.utils.pagination import paginate_queryset

    rows, metadata = paginate_queryset(
        list(range(MAX_PAGE_SIZE + 1)), request_for({"page_size": MAX_PAGE_SIZE})
    )
    assert len(rows) == MAX_PAGE_SIZE
    assert metadata["total_count"] == MAX_PAGE_SIZE + 1
    assert metadata["next_page"] == 2


@pytest.mark.parametrize("page", [0, -1, 3, "bad"])
def test_common_paginator_bad_page_is_404(page):
    from common.utils.pagination import paginate_queryset

    with pytest.raises(NotFound) as raised:
        paginate_queryset(
            [1], request_for({"page_size": MAX_PAGE_SIZE, "page_number": page})
        )
    assert custom_exception_handler(raised.value, {}).status_code == 404


def test_existing_narrow_project_cap_is_not_widened():
    from tracer.serializers.project import ProjectListQuerySerializer

    serializer = ProjectListQuerySerializer(data={"page_size": 100})
    assert serializer.is_valid(), serializer.errors
    assert serializer.fields["page_size"].max_value == 100
    assert not ProjectListQuerySerializer(data={"page_size": 101}).is_valid()
