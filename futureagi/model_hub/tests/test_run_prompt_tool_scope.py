"""Tool resolution contracts, with query and execution boundaries mocked."""

import inspect
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from django.db import transaction
from django.db.models import Q

from model_hub.views import run_prompt
from tfc import ee_gates


ERROR = "One or more tools are unavailable for this workspace."


class _SavedModel(SimpleNamespace):
    """Saved model stand-in: hashable by primary key, as Q comparison requires."""

    def __hash__(self):
        return hash(self.id)


@pytest.fixture
def request_scope():
    organization = _SavedModel(id=uuid.uuid4())
    workspace = _SavedModel(
        id=uuid.uuid4(), organization_id=organization.id, is_default=False
    )
    return SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=SimpleNamespace(organization=SimpleNamespace(id=uuid.uuid4())),
    )


@pytest.fixture
def tool_query(monkeypatch):
    query = Mock(return_value=[])
    monkeypatch.setattr(
        run_prompt, "Tools", SimpleNamespace(objects=SimpleNamespace(filter=query))
    )
    return query


@pytest.mark.parametrize("default_workspace", [False, True])
def test_resolver_scopes_query_and_accepts_the_complete_set(
    request_scope, tool_query, default_workspace
):
    request_scope.workspace.is_default = default_workspace
    tool = SimpleNamespace(id=uuid.uuid4())
    tool_query.return_value = [tool]

    resolved = run_prompt._resolve_tools_for_request(
        request_scope, [{"id": str(tool.id)}]
    )

    workspace = request_scope.workspace
    workspace_filter = Q(workspace=workspace)
    if default_workspace:
        workspace_filter |= Q(
            workspace__is_default=True,
            workspace__organization_id=workspace.organization_id,
        ) | Q(workspace__isnull=True)
    tool_query.assert_called_once_with(
        workspace_filter,
        id__in={tool.id},
        organization=request_scope.organization,
        deleted=False,
    )
    assert resolved == [tool]


def test_resolver_retains_legacy_no_workspace_and_user_organization_fallback(
    request_scope, tool_query
):
    request_scope.workspace = None
    request_scope.organization = None
    tool = SimpleNamespace(id=uuid.uuid4())
    tool_query.return_value = [tool]

    assert run_prompt._resolve_tools_for_request(request_scope, [tool.id]) == [tool]
    tool_query.assert_called_once_with(
        Q(),
        id__in={tool.id},
        organization=request_scope.user.organization,
        deleted=False,
    )


@pytest.mark.parametrize("returned_count", [0, 1])
def test_resolver_rejects_an_incomplete_scoped_result(
    request_scope, tool_query, returned_count
):
    tools = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]
    tool_query.return_value = tools[:returned_count]

    with pytest.raises(ValueError, match=ERROR):
        run_prompt._resolve_tools_for_request(
            request_scope, [{"id": str(tool.id)} for tool in tools]
        )


def test_resolver_normalizes_uuid_spellings_and_duplicate_ids(
    request_scope, tool_query
):
    tool = SimpleNamespace(id=uuid.uuid4())
    tool_query.return_value = [tool]

    resolved = run_prompt._resolve_tools_for_request(
        request_scope,
        [{"id": str(tool.id).upper()}, {"id": tool.id.hex}, tool.id],
    )

    assert resolved == [tool]
    assert tool_query.call_args.kwargs["id__in"] == {tool.id}


@pytest.mark.parametrize("tools", [None, []])
def test_resolver_accepts_no_tools_without_a_query(request_scope, tool_query, tools):
    assert run_prompt._resolve_tools_for_request(request_scope, tools) == []
    tool_query.assert_not_called()


@pytest.mark.parametrize("tools", [[{}], [{"id": None}], [{"id": "invalid"}]])
def test_resolver_rejects_invalid_references_without_a_query(
    request_scope, tool_query, tools
):
    with pytest.raises(ValueError, match=ERROR):
        run_prompt._resolve_tools_for_request(request_scope, tools)
    tool_query.assert_not_called()


def test_resolver_requires_an_organization(request_scope, tool_query):
    request_scope.organization = None
    request_scope.user.organization = None

    with pytest.raises(ValueError, match=ERROR):
        run_prompt._resolve_tools_for_request(request_scope, [{"id": str(uuid.uuid4())}])
    tool_query.assert_not_called()


@pytest.mark.parametrize(
    "view_class",
    [
        run_prompt.LitellmAPIView,
        run_prompt.AddRunPromptColumnView,
        run_prompt.PreviewRunPromptColumnView,
        run_prompt.EditRunPromptColumnView,
    ],
)
def test_views_report_resolution_failure_before_mutation_or_execution(
    monkeypatch, request_scope, tool_query, view_class
):
    # Exercise the view method without middleware, a DB, or a model provider.
    tools = [{"id": str(uuid.uuid4())}]
    request_scope.validated_data = {
        "dataset_id": uuid.uuid4(),
        "column_id": uuid.uuid4(),
        "name": "Prompt",
        "first_n_rows": 1,
        "model": "test-model",
        "tools": tools,
        "config": {"model": "test-model", "tools": tools},
    }
    dataset = SimpleNamespace(
        id=request_scope.validated_data["dataset_id"],
        organization_id=request_scope.organization.id,
        workspace=request_scope.workspace,
    )
    column = SimpleNamespace(
        source=run_prompt.SourceChoices.RUN_PROMPT.value, source_id=uuid.uuid4()
    )
    dataset_manager = Mock()
    dataset_manager.get.return_value = dataset
    dataset_manager.filter.return_value.first.return_value = dataset
    column_manager = Mock()
    column_manager.filter.return_value.exists.return_value = False
    column_manager.filter.return_value.first.return_value = column
    row_manager = MagicMock()
    row_manager.filter.return_value.order_by.return_value.values_list.return_value = [0]
    for name, manager in [
        ("Dataset", dataset_manager),
        ("Column", column_manager),
        ("Row", row_manager),
    ]:
        monkeypatch.setattr(run_prompt, name, SimpleNamespace(objects=manager))
    monkeypatch.setattr(
        run_prompt, "_request_dataset_queryset", lambda request: dataset_manager
    )
    monkeypatch.setattr(
        run_prompt, "_request_column_queryset", lambda request: column_manager
    )
    resolve = Mock(side_effect=ValueError(ERROR))
    monkeypatch.setattr(run_prompt, "_resolve_tools_for_request", resolve, raising=False)
    monkeypatch.setattr(run_prompt, "is_model_in_catalog", lambda *a, **k: True)
    monkeypatch.setattr(ee_gates, "turing_oss_gate_response", lambda model: None)
    monkeypatch.setattr(transaction, "atomic", nullcontext)
    mutations = Mock()
    mutations.create.side_effect = AssertionError("unexpected prompt creation")
    mutations.lock.side_effect = AssertionError("unexpected prompt lock")
    mutations.cells.side_effect = AssertionError("unexpected cell update")
    mutations.execute.side_effect = AssertionError("unexpected model execution")
    monkeypatch.setattr(
        run_prompt, "RunPrompter", SimpleNamespace(objects=mutations)
    )
    monkeypatch.setattr(run_prompt, "_request_run_prompter_queryset", mutations.lock)
    monkeypatch.setattr(
        run_prompt, "Cell", SimpleNamespace(objects=SimpleNamespace(filter=mutations.cells))
    )
    monkeypatch.setattr(run_prompt, "RunPrompt", mutations.execute)
    view = view_class()
    view._gm = Mock()
    view._gm.bad_request.return_value = SimpleNamespace(status_code=400)

    response = inspect.unwrap(view_class.post)(view, request_scope)

    assert response.status_code == 400
    resolve.assert_called_once_with(request_scope, tools)
    view._gm.bad_request.assert_called_once_with(ERROR)
    assert mutations.mock_calls == []
