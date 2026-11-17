"""An unknown test-execution id is a 404, never a 500.

``GET /simulate/test-executions/<uuid:test_execution_id>/`` (TestExecutionDetailView,
CLI ``get_test_execution``) documents a 404, and resolves the execution with
``get_object_or_404`` scoped to the caller's organization and workspace. That
lookup sat inside the action's broad ``except Exception``, so its ``Http404``
became an HTTP 500, which the CLI maps to "server unavailable, retry": a
missing execution was retried forever.

- A well-formed id that names nothing gets the typed not-found response.
- So do an execution of another organization and one in another workspace of
  the caller's organization, with the same body: a foreign id is not an
  existence oracle.
- A malformed id never reaches the view: the ``<uuid:>`` converter refuses
  it at URL resolution, which is Django's resolver 404.
- The caller's own execution still returns 200.

DB-free: TestExecution rows live in memory and the lookup query the view
builds is answered by evaluating its real WHERE tree against them (an
unmodelled column or an unsupported lookup fails the test). Every query on the
other models the success path reads (call executions, eval configs, scenarios)
is answered with no rows, and ``TestExecution.save`` is recorded, not run.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db.models.lookups import Exact, IsNull
from django.db.models.query import ModelIterable, QuerySet
from django.db.models.sql.where import NothingNode, WhereNode
from django.urls import Resolver404, resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, Workspace
from model_hub.models.develop_dataset import Cell, Column, Row
from simulate.models import (
    CallExecution,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
)
from simulate.models import TestExecution as Execution
from simulate.views.run_test import TestExecutionDetailView as DetailView
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

EXECUTION_TABLE = Execution._meta.db_table
RUN_TEST_TABLE = RunTest._meta.db_table
WORKSPACE_TABLE = Workspace._meta.db_table

# Models the success path reads after the lookup; each answers with no rows.
EMPTY_MODELS = {CallExecution, SimulateEvalConfig, Scenarios, Row, Column, Cell}


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


class ExecutionTable:
    def __init__(self):
        self.rows = []
        self.lookups = []
        self.saved = []

    def add(self, *, organization, workspace, run_test_deleted=False):
        run_test = RunTest(
            id=uuid.uuid4(),
            name="run",
            organization=organization,
            workspace=workspace,
            deleted=run_test_deleted,
        )
        execution = Execution(
            id=uuid.uuid4(),
            run_test=run_test,
            status=Execution.ExecutionStatus.COMPLETED,
            scenario_ids=[],
            execution_metadata={},
        )
        self.rows.append(
            (
                {
                    (EXECUTION_TABLE, "id"): execution.id,
                    (EXECUTION_TABLE, "deleted"): False,
                    (RUN_TEST_TABLE, "organization_id"): organization.id,
                    (RUN_TEST_TABLE, "workspace_id"): workspace.id,
                    (RUN_TEST_TABLE, "deleted"): run_test_deleted,
                    (WORKSPACE_TABLE, "is_default"): workspace.is_default,
                    (WORKSPACE_TABLE, "organization_id"): workspace.organization_id,
                },
                execution,
            )
        )
        return execution


@pytest.fixture
def executions():
    table = ExecutionTable()

    def fetch_all(queryset):
        if queryset._result_cache is not None:
            return
        query = queryset.query
        if queryset.model is Execution:
            assert queryset._iterable_class is ModelIterable
            table.lookups.append(query)
            found = [
                execution
                for columns, execution in table.rows
                if _matches(query.where, columns, query)
            ]
            result = found[query.low_mark : query.high_mark]
        else:
            assert queryset.model in EMPTY_MODELS, (
                f"unexpected query on {queryset.model.__name__}"
            )
            result = []
        queryset._result_cache = result
        queryset._prefetch_done = True

    def count(queryset):
        fetch_all(queryset)
        return len(queryset._result_cache)

    def save(execution, *args, **kwargs):
        table.saved.append((execution.id, kwargs.get("update_fields")))

    with (
        patch.object(QuerySet, "_fetch_all", fetch_all),
        patch.object(QuerySet, "count", count),
        patch.object(Execution, "save", save),
    ):
        yield table


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


def _get(test_execution_id, *, organization, workspace, params=None):
    """GET as a member of ``organization`` bound to ``workspace``, the way
    authentication binds it (request.organization, request.workspace and the
    model-manager context)."""
    request = APIRequestFactory().get(
        f"/simulate/test-executions/{test_execution_id}/", params or {}
    )
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
        return DetailView.as_view()(request, test_execution_id=test_execution_id)
    finally:
        clear_workspace_context()


def _assert_typed_not_found(response):
    assert response.status_code == 404, response.data
    assert response.data["status"] is False
    assert response.data["type"] == "not_found"
    assert response.data["code"] == "not_found"


@pytest.mark.parametrize("bound", ["a_one", "a_default"])
def test_a_well_formed_id_that_names_nothing_is_a_typed_404(executions, tenancy, bound):
    executions.add(organization=tenancy.org_a, workspace=getattr(tenancy, bound))

    response = _get(
        uuid.uuid4(), organization=tenancy.org_a, workspace=getattr(tenancy, bound)
    )

    _assert_typed_not_found(response)
    assert "Test execution not found" in str(response.data)
    assert executions.lookups, "the view never ran its scoped lookup"
    assert executions.saved == []


@pytest.mark.parametrize("bound", ["a_one", "a_default"])
@pytest.mark.parametrize(
    "owner",
    [
        ("org_b", "b_default", False),
        ("org_a", "a_two", False),
        ("org_a", None, True),
    ],
    ids=["another-org", "another-workspace", "deleted-run-test"],
)
def test_a_foreign_id_gets_the_same_404_as_a_made_up_one(
    executions, tenancy, bound, owner
):
    org_name, workspace_name, run_test_deleted = owner
    workspace = getattr(tenancy, bound)
    foreign = executions.add(
        organization=getattr(tenancy, org_name),
        workspace=getattr(tenancy, workspace_name) if workspace_name else workspace,
        run_test_deleted=run_test_deleted,
    )

    response = _get(foreign.id, organization=tenancy.org_a, workspace=workspace)
    made_up = _get(uuid.uuid4(), organization=tenancy.org_a, workspace=workspace)

    _assert_typed_not_found(response)
    assert response.data == made_up.data
    assert executions.saved == []


def test_a_malformed_id_is_refused_at_url_resolution():
    """The route's ``<uuid:>`` converter answers a non-UUID with a 404 before
    the view runs; the view itself only ever sees a UUID."""
    with pytest.raises(Resolver404):
        resolve("/simulate/test-executions/not-a-uuid/")
    match = resolve(f"/simulate/test-executions/{uuid.uuid4()}/")
    assert match.func.view_class is DetailView


@pytest.mark.parametrize("bound", ["a_one", "a_default"])
def test_the_callers_own_execution_is_still_200(executions, tenancy, bound):
    workspace = getattr(tenancy, bound)
    own = executions.add(organization=tenancy.org_a, workspace=workspace)

    response = _get(own.id, organization=tenancy.org_a, workspace=workspace)

    assert response.status_code == 200, response.data
    assert response.data["status"] == Execution.ExecutionStatus.COMPLETED
    assert response.data["count"] == 0
    assert response.data["results"] == []
    assert response.data["provider"] == "prompt"
    assert response.data["column_order"]


@pytest.mark.parametrize("grouped", [False, True], ids=["plain", "grouped"])
def test_detail_cap_refuses_before_lookup_or_grouping(executions, tenancy, grouped):
    from tfc.utils.pagination import MAX_PAGE_SIZE

    own = executions.add(organization=tenancy.org_a, workspace=tenancy.a_one)
    params = {"limit": MAX_PAGE_SIZE + 1}
    if grouped:
        params["row_groups"] = '["status"]'
    with patch.object(DetailView.utils, "_apply_grouping") as grouping:
        response = _get(
            own.id, organization=tenancy.org_a, workspace=tenancy.a_one, params=params
        )
    assert response.status_code == 400, response.data
    assert response.data["code"] == "page_size_too_large"
    assert executions.lookups == []
    assert executions.saved == []
    grouping.assert_not_called()


@pytest.mark.parametrize("page", [1, 100], ids=["boundary", "empty-page-compatibility"])
def test_detail_cap_preserves_success_and_empty_page_compatibility(
    executions, tenancy, page
):
    from tfc.utils.pagination import MAX_PAGE_SIZE

    own = executions.add(organization=tenancy.org_a, workspace=tenancy.a_one)
    response = _get(
        own.id,
        organization=tenancy.org_a,
        workspace=tenancy.a_one,
        params={"limit": MAX_PAGE_SIZE, "page": page},
    )
    assert response.status_code == 200, response.data
    assert response.data["results"] == []
    assert response.data["count"] == 0
    assert response.data["current_page"] == page


def test_grouped_detail_accepts_exact_cap(executions, tenancy):
    from tfc.utils.pagination import MAX_PAGE_SIZE

    own = executions.add(organization=tenancy.org_a, workspace=tenancy.a_one)
    groups = [{"status": "completed"}] * (MAX_PAGE_SIZE + 1)
    with patch.object(DetailView.utils, "_apply_grouping", return_value=groups):
        response = _get(
            own.id,
            organization=tenancy.org_a,
            workspace=tenancy.a_one,
            params={"limit": MAX_PAGE_SIZE, "row_groups": '["status"]'},
        )
    assert response.status_code == 200, response.data
    assert len(response.data["results"]) == MAX_PAGE_SIZE
    assert response.data["count"] == MAX_PAGE_SIZE + 1
    assert response.data["next"] == "?page=2"


@pytest.mark.parametrize("page", [1, 100], ids=["in-range", "out-of-range"])
def test_grouped_detail_page_bounds(executions, tenancy, page):
    from tfc.utils.pagination import MAX_PAGE_SIZE

    own = executions.add(organization=tenancy.org_a, workspace=tenancy.a_one)
    groups = [{"status": "completed"}]
    with patch.object(DetailView.utils, "_apply_grouping", return_value=groups):
        response = _get(
            own.id,
            organization=tenancy.org_a,
            workspace=tenancy.a_one,
            params={"limit": MAX_PAGE_SIZE, "page": page, "row_groups": '["status"]'},
        )
    if page == 100:
        _assert_typed_not_found(response)
        assert response.data["message"] == "Invalid page."
    else:
        assert response.status_code == 200, response.data
        assert response.data["results"] == groups
        assert response.data["count"] == 1
        assert response.data["total_pages"] == 1
        assert response.data["current_page"] == 1
        assert response.data["next"] is None
        assert response.data["previous"] is None
