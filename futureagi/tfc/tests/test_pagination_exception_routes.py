"""Cloud regression rows for C-bucket round-two pagination propagation.

The accounts and run-test list serializers deliberately cap requests at 100.
Do not widen those contracts to exercise the shared 5,000-row paginator: test
real bad pages and the stricter boundary, and inject its typed refusal at the
paginator boundary separately. Other routes exercise the real shared cap.
"""

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from tfc.utils.pagination import MAX_PAGE_SIZE, PageSizeTooLarge

SHARED_CAP_ROUTES = [
    pytest.param("/simulate/agent-definitions/", id="agents"),
    pytest.param("/tracer/replay-session/", id="replay"),
    pytest.param("/simulate/api/test-executions/", id="executions"),
    pytest.param("/simulate/api/call-executions/", id="calls"),
    pytest.param("/simulate/api/agent-prompt-optimiser/", id="optimiser"),
]
STRICTER_CAP_ROUTES = [
    pytest.param("/accounts/workspace/list/", id="workspaces"),
    pytest.param("/accounts/user/list/", id="users"),
    pytest.param("/simulate/run-tests/", id="run-tests"),
    pytest.param("/simulate/api/run-tests/", id="api-run-tests"),
]


def _assert_page_size_refusal(response):
    assert response.status_code == 400, response.data
    assert response.data["code"] == "page_size_too_large"
    assert response.data["type"] == "validation_error"
    assert response.data["message"] == (
        f"The maximum page size is {MAX_PAGE_SIZE}. "
        "Fetch more pages to retrieve additional results."
    )


@pytest.mark.django_db
@pytest.mark.parametrize("url", SHARED_CAP_ROUTES)
def test_shared_cap_route_rejects_oversized_page(auth_client, url):
    response = auth_client.get(url, {"limit": MAX_PAGE_SIZE + 1})
    _assert_page_size_refusal(response)


@pytest.mark.django_db
@pytest.mark.parametrize("url", SHARED_CAP_ROUTES)
def test_shared_cap_route_bad_page_is_not_found(auth_client, url):
    response = auth_client.get(url, {"limit": MAX_PAGE_SIZE, "page": 100})
    assert response.status_code == 404, response.data
    assert response.data["code"] == "not_found"
    assert response.data["message"] == "Invalid page."


@pytest.mark.django_db
@pytest.mark.parametrize("url", SHARED_CAP_ROUTES)
def test_shared_cap_route_accepts_boundary(auth_client, url):
    response = auth_client.get(url, {"limit": MAX_PAGE_SIZE, "page": 1})
    assert response.status_code == 200, response.data
    if url.endswith("agent-prompt-optimiser/"):
        # This route keeps its existing table envelope rather than DRF's page.
        assert isinstance(response.data["result"]["table"], list)
        assert response.data["result"]["metadata"]["total_rows"] == 0
    else:
        assert isinstance(response.data["results"], list)
        assert response.data["current_page"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("url", STRICTER_CAP_ROUTES)
def test_stricter_cap_route_preserves_typed_paginator_refusal(auth_client, url):
    # Real route, auth, request validation and view handler; only the paginator
    # failure is injected because the existing serializer refuses limits >100.
    with patch(
        "tfc.utils.pagination.ExtendedPageNumberPagination.paginate_queryset",
        side_effect=PageSizeTooLarge(),
    ) as paginate:
        response = auth_client.get(url, {"limit": 100})
    paginate.assert_called_once()
    _assert_page_size_refusal(response)


@pytest.mark.django_db
@pytest.mark.parametrize("url", STRICTER_CAP_ROUTES)
def test_stricter_cap_route_bad_page_is_not_found(auth_client, url):
    response = auth_client.get(url, {"limit": 100, "page": 100})
    assert response.status_code == 404, response.data
    assert response.data["code"] == "not_found"
    assert response.data["message"] == "Invalid page."


@pytest.mark.django_db
@pytest.mark.parametrize("url", STRICTER_CAP_ROUTES)
def test_stricter_cap_route_accepts_its_boundary(auth_client, url):
    response = auth_client.get(url, {"limit": 100, "page": 1})
    assert response.status_code == 200, response.data
    assert isinstance(response.data["results"], list)
    assert response.data["current_page"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("url", STRICTER_CAP_ROUTES)
def test_stricter_cap_is_not_widened(auth_client, url):
    with patch(
        "tfc.utils.pagination.ExtendedPageNumberPagination.paginate_queryset"
    ) as paginate:
        response = auth_client.get(url, {"limit": MAX_PAGE_SIZE})
    assert response.status_code == 400, response.data
    paginate.assert_not_called()


# Pin every edited broad handler, including nested pagination and the summary
# helper caller. Route tests above cover the actual renderer; these structural
# rows ensure that another branch in the same file cannot silently regress.
OWNED_HANDLERS = [
    ("accounts/views/workspace_management.py", "WorkspaceListAPIView", "get"),
    ("accounts/views/workspace_management.py", "UserListAPIView", "get"),
    ("tracer/views/replay_session.py", "ReplaySessionView", "list"),
    ("simulate/views/agent_definition.py", "AgentDefinitionView", "get"),
    (
        "simulate/views/agent_prompt_optimiser.py",
        "AgentPromptOptimiserRunViewSet",
        "list",
    ),
    ("simulate/views/run_test.py", "RunTestListView", "get"),
    ("simulate/views/run_test.py", "RunTestAPIView", "get"),
    ("simulate/views/run_test.py", "TestExecutionAPIView", "get"),
    ("simulate/views/run_test.py", "CallExecutionAPIView", "get"),
    ("simulate/views/run_test.py", "TestExecutionDetailView", "get"),
    ("simulate/views/run_test.py", "CallExecutionLogsView", "get"),
    ("simulate/views/run_test.py", "RunTestScenariosView", "get"),
    ("simulate/views/run_test.py", "RunTestExecutionsView", "get"),
]


@pytest.mark.parametrize(
    "relative_path,class_name,method_name",
    OWNED_HANDLERS,
    ids=[row[1] for row in OWNED_HANDLERS],
)
def test_owned_pagination_handler_reraises_api_exception(
    relative_path, class_name, method_name
):
    source = Path(__file__).resolve().parents[2] / relative_path
    tree = ast.parse(source.read_text())
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "rest_framework.exceptions"
        and any(alias.name == "APIException" for alias in node.names)
        for node in tree.body
    )
    view = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in view.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    matched = 0
    for node in ast.walk(method):
        if not isinstance(node, ast.Try):
            continue
        broad_index = next(
            (
                index
                for index, handler in enumerate(node.handlers)
                if handler.type is None
                or (
                    isinstance(handler.type, ast.Name)
                    and handler.type.id in {"Exception", "BaseException"}
                )
            ),
            None,
        )
        if broad_index is None:
            continue
        has_pagination = any(
            isinstance(child, ast.Call)
            and (
                (
                    isinstance(child.func, ast.Attribute)
                    and child.func.attr == "paginate_queryset"
                )
                or (
                    isinstance(child.func, ast.Name)
                    and child.func.id == "_execution_summaries"
                )
            )
            for statement in node.body
            for child in ast.walk(statement)
        )
        if not has_pagination:
            continue
        matched += 1
        handler = next(
            (
                candidate
                for candidate in node.handlers[:broad_index]
                if isinstance(candidate.type, ast.Name)
                and candidate.type.id == "APIException"
            ),
            None,
        )
        assert handler is not None, f"{class_name}.{method_name}:{node.lineno}"
        assert len(handler.body) == 1
        assert isinstance(handler.body[0], ast.Raise)
        assert handler.body[0].exc is None
        assert handler.body[0].cause is None
    assert matched >= 1
