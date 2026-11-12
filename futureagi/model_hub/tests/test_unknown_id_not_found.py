"""Unknown and malformed ids are a typed 404 on two model-hub reads.

``GET /model-hub/eval-groups/<id>/`` (EvalGroupView.retrieve, CLI
``get_eval_group``) looked the group up by hand. A well-formed id that names
no group of the caller came back as HTTP 400 "Eval group does not exist for
this user." (CLI exit 2, "check command parameters"), and an id that is not a
UUID raised Django's ValidationError inside the action's broad
``except Exception``: HTTP 500 (CLI exit 6, "server unavailable, retry").

``GET /model-hub/prompt-templates/<id>/versions/``
(PromptTemplateViewSet.versions, CLI ``list_prompt_versions``) resolves the
template with DRF's ``get_object``, whose ``Http404`` (raised for an id that
names nothing and, by DRF's ``get_object_or_404``, for one that is not a UUID)
sat inside the action's broad ``except Exception``: HTTP 500.

Both are routed by the DRF router, whose default lookup admits any path
segment. A malformed id names nothing, so it gets the same 404 as a
well-formed id that names nothing: what DRF's own ``get_object`` does with a
malformed pk, and what the ``<uuid:>`` routes return.

For each view:

- a well-formed id that names nothing: typed 404;
- a malformed id: the same 404 body;
- a row of another organization, of another workspace of the caller's
  organization, or a deleted one: the same 404 body (no existence oracle);
- the caller's own row: still 200 with the unchanged success body.

DB-free: the looked-up model's rows live in memory and every query on it is
answered by evaluating the view's real WHERE tree against them (an unmodelled
column or an unsupported lookup fails the test). The models the success paths
read after the lookup are answered with no rows.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db.models.lookups import Exact, IsNull
from django.db.models.query import ModelIterable, QuerySet
from django.db.models.sql.where import NothingNode, WhereNode
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, Workspace
from model_hub.models.eval_groups import EvalGroup, History
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.views.eval_group import EvalGroupView
from model_hub.views.prompt_template import PromptTemplateViewSet
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

WORKSPACE_TABLE = Workspace._meta.db_table
MALFORMED_IDS = ["not-a-uuid", "1234", "00000000-0000"]


def _matches(node, columns, query):
    """Evaluate a WHERE tree against one row; refuse anything not understood."""
    if isinstance(node, NothingNode):
        return False
    if isinstance(node, WhereNode):
        results = [_matches(child, columns, query) for child in node.children]
        value = all(results) if node.connector == "AND" else any(results)
        return value != node.negated
    table = query.alias_map[node.lhs.alias].table_name
    key = (table, node.lhs.target.column)
    assert key in columns, f"query filters on an unmodelled column {key}"
    actual = columns[key]
    if isinstance(node, IsNull):  # includes RelatedIsNull
        return (actual is None) == bool(node.rhs)
    if isinstance(node, Exact):  # includes RelatedExact
        return actual is not None and actual == node.rhs
    raise AssertionError(f"unsupported lookup {type(node).__name__} on {key}")


class Table:
    """One model's rows, scoped by organization and workspace."""

    def __init__(self, model, build):
        self.model = model
        self.build = build
        self.rows = []
        self.lookups = []

    def add(self, *, organization, workspace, deleted=False):
        row = self.build(organization=organization, workspace=workspace)
        table = self.model._meta.db_table
        self.rows.append(
            (
                {
                    (table, "id"): row.id,
                    (table, "organization_id"): organization.id,
                    (table, "workspace_id"): workspace.id,
                    (table, "deleted"): deleted,
                    (table, "is_sample"): False,
                    (WORKSPACE_TABLE, "is_default"): workspace.is_default,
                    (WORKSPACE_TABLE, "organization_id"): workspace.organization_id,
                },
                row,
            )
        )
        return row


def _in_memory(table, empty_models):
    def fetch_all(queryset):
        if queryset._result_cache is not None:
            return
        query = queryset.query
        if queryset.model is table.model:
            assert queryset._iterable_class is ModelIterable
            table.lookups.append(query)
            found = [
                row
                for columns, row in table.rows
                if _matches(query.where, columns, query)
            ]
            result = found[query.low_mark : query.high_mark]
        else:
            assert queryset.model in empty_models, (
                f"unexpected query on {queryset.model.__name__}"
            )
            result = []
        queryset._result_cache = result
        queryset._prefetch_done = True

    def count(queryset):
        fetch_all(queryset)
        return len(queryset._result_cache)

    return (
        patch.object(QuerySet, "_fetch_all", fetch_all),
        patch.object(QuerySet, "count", count),
    )


@pytest.fixture
def tenancy():
    org_a = Organization(id=uuid.uuid4(), name="Org A")
    org_b = Organization(id=uuid.uuid4(), name="Org B")
    return SimpleNamespace(
        org_a=org_a,
        org_b=org_b,
        a_default=Workspace(
            id=uuid.uuid4(), name="A default", organization=org_a, is_default=True
        ),
        a_one=Workspace(
            id=uuid.uuid4(), name="A one", organization=org_a, is_default=False
        ),
        a_two=Workspace(
            id=uuid.uuid4(), name="A two", organization=org_a, is_default=False
        ),
        b_default=Workspace(
            id=uuid.uuid4(), name="B default", organization=org_b, is_default=True
        ),
    )


@pytest.fixture(autouse=True)
def no_ambient_workspace():
    clear_workspace_context()
    yield
    clear_workspace_context()


def _dispatch(view, path, pk, *, organization, workspace):
    """GET as a member of ``organization`` bound to ``workspace``, the way
    authentication binds it (request.organization, request.workspace and the
    model-manager context)."""
    request = APIRequestFactory().get(path)
    user = SimpleNamespace(
        id=uuid.uuid4(),
        is_authenticated=True,
        organization=organization,
        email="member@example.com",
    )
    request.organization = organization
    request.workspace = workspace
    set_workspace_context(
        workspace=workspace,
        organization=organization,
        user=user,
        require_workspace=True,
    )
    force_authenticate(request, user=user)
    try:
        return view(request, pk=str(pk))
    finally:
        clear_workspace_context()


def _assert_typed_not_found(response):
    assert response.status_code == 404, response.data
    assert response.data["status"] is False
    assert response.data["type"] == "not_found"
    assert response.data["code"] == "not_found"


FOREIGN_OWNERS = pytest.mark.parametrize(
    "owner",
    [
        ("org_b", "b_default", False),
        ("org_a", "a_two", False),
        ("org_a", None, True),
    ],
    ids=["another-org", "another-workspace", "deleted"],
)
BOUND = pytest.mark.parametrize("bound", ["a_one", "a_default"])


def _add_foreign(table, tenancy, owner, workspace):
    org_name, workspace_name, deleted = owner
    return table.add(
        organization=getattr(tenancy, org_name),
        workspace=getattr(tenancy, workspace_name) if workspace_name else workspace,
        deleted=deleted,
    )


# --------------------------------------------------------------------------
# GET /model-hub/eval-groups/<id>/
# --------------------------------------------------------------------------


@pytest.fixture
def eval_groups():
    table = Table(
        EvalGroup,
        lambda organization, workspace: EvalGroup(
            id=uuid.uuid4(),
            name="group",
            organization=organization,
            workspace=workspace,
            description="d",
            is_sample=False,
        ),
    )
    rows, counts = _in_memory(
        table, {EvalGroup.eval_templates.through, EvalTemplate, History}
    )
    with rows, counts:
        yield table


def _get_group(pk, **caller):
    return _dispatch(
        EvalGroupView.as_view({"get": "retrieve"}),
        f"/model-hub/eval-groups/{pk}/",
        pk,
        **caller,
    )


@BOUND
def test_eval_group_id_that_names_nothing_is_a_typed_404(eval_groups, tenancy, bound):
    caller = {"organization": tenancy.org_a, "workspace": getattr(tenancy, bound)}
    eval_groups.add(**caller)

    response = _get_group(uuid.uuid4(), **caller)

    _assert_typed_not_found(response)
    assert "Eval group does not exist for this user." in str(response.data)
    assert eval_groups.lookups, "the view never ran its scoped lookup"


@BOUND
@pytest.mark.parametrize("malformed", MALFORMED_IDS)
def test_malformed_eval_group_id_gets_the_same_404(
    eval_groups, tenancy, bound, malformed
):
    caller = {"organization": tenancy.org_a, "workspace": getattr(tenancy, bound)}
    eval_groups.add(**caller)

    response = _get_group(malformed, **caller)
    made_up = _get_group(uuid.uuid4(), **caller)

    _assert_typed_not_found(response)
    assert response.data == made_up.data


@BOUND
@FOREIGN_OWNERS
def test_foreign_eval_group_id_gets_the_same_404_as_a_made_up_one(
    eval_groups, tenancy, bound, owner
):
    caller = {"organization": tenancy.org_a, "workspace": getattr(tenancy, bound)}
    foreign = _add_foreign(eval_groups, tenancy, owner, caller["workspace"])

    response = _get_group(foreign.id, **caller)
    made_up = _get_group(uuid.uuid4(), **caller)

    _assert_typed_not_found(response)
    assert response.data == made_up.data


@BOUND
def test_own_eval_group_is_still_200(eval_groups, tenancy, bound):
    caller = {"organization": tenancy.org_a, "workspace": getattr(tenancy, bound)}
    own = eval_groups.add(**caller)

    response = _get_group(own.id, **caller)

    assert response.status_code == 200, response.data
    assert response.data["status"] is True
    result = response.data["result"]
    assert result["eval_group"]["id"] == str(own.id)
    assert result["eval_group"]["created_by"] == "Org A"
    assert result["members"] == []
    assert result["required_keys"] == []
    assert result["models"] == []
    assert result["function_params_requirements"] == {}


# --------------------------------------------------------------------------
# GET /model-hub/prompt-templates/<id>/versions/
# --------------------------------------------------------------------------


@pytest.fixture
def prompt_templates():
    table = Table(
        PromptTemplate,
        lambda organization, workspace: PromptTemplate(
            id=uuid.uuid4(),
            name="template",
            organization=organization,
            workspace=workspace,
        ),
    )
    rows, counts = _in_memory(table, {PromptVersion})
    with rows, counts:
        yield table


def _list_versions(pk, **caller):
    return _dispatch(
        PromptTemplateViewSet.as_view({"get": "versions"}),
        f"/model-hub/prompt-templates/{pk}/versions/",
        pk,
        **caller,
    )


@BOUND
def test_prompt_template_id_that_names_nothing_is_a_typed_404(
    prompt_templates, tenancy, bound
):
    caller = {"organization": tenancy.org_a, "workspace": getattr(tenancy, bound)}
    prompt_templates.add(**caller)

    response = _list_versions(uuid.uuid4(), **caller)

    _assert_typed_not_found(response)
    assert "Prompt template not found" in str(response.data)
    assert prompt_templates.lookups, "the view never ran its scoped lookup"


@BOUND
@pytest.mark.parametrize("malformed", MALFORMED_IDS)
def test_malformed_prompt_template_id_gets_the_same_404(
    prompt_templates, tenancy, bound, malformed
):
    caller = {"organization": tenancy.org_a, "workspace": getattr(tenancy, bound)}
    prompt_templates.add(**caller)

    response = _list_versions(malformed, **caller)
    made_up = _list_versions(uuid.uuid4(), **caller)

    _assert_typed_not_found(response)
    assert response.data == made_up.data


@BOUND
@FOREIGN_OWNERS
def test_foreign_prompt_template_id_gets_the_same_404_as_a_made_up_one(
    prompt_templates, tenancy, bound, owner
):
    caller = {"organization": tenancy.org_a, "workspace": getattr(tenancy, bound)}
    foreign = _add_foreign(prompt_templates, tenancy, owner, caller["workspace"])

    response = _list_versions(foreign.id, **caller)
    made_up = _list_versions(uuid.uuid4(), **caller)

    _assert_typed_not_found(response)
    assert response.data == made_up.data


@BOUND
def test_own_prompt_template_versions_are_still_200(prompt_templates, tenancy, bound):
    caller = {"organization": tenancy.org_a, "workspace": getattr(tenancy, bound)}
    own = prompt_templates.add(**caller)

    response = _list_versions(own.id, **caller)

    assert response.status_code == 200, response.data
    assert response.data["count"] == 0
    assert response.data["results"] == []
    assert response.data["current_page"] == 1
