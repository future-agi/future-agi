"""A malformed eval-task id is a 404, never a 500.

``GET /tracer/eval-task/<id>/`` (EvalTaskView.retrieve, CLI ``get_eval_task``)
is routed by the DRF router, whose default lookup admits any path segment.
``get_queryset`` filters on that id, and Django's UUIDField refuses a value
that is not a UUID with ``django.core.exceptions.ValidationError``. DRF's
exception handler does not know that exception, so ``'not-a-uuid'`` came back
as an HTTP 500, which the CLI maps to "server unavailable, retry".

A malformed id names no task, so it gets the same 404 as a well-formed id that
names nothing; that is what DRF's own ``get_object`` does with a malformed
pk, and what the ``<uuid:>`` routes return.

- A well-formed id that names nothing: typed 404 (unchanged). It is the
  ``Http404`` of the lookup rendered by the global DRF exception handler
  (``type: not_found``; that handler gives Django's ``Http404`` the code
  ``error``, unchanged here).
- A task in another organization, in another workspace of the caller's
  organization, or in a deleted project: the same 404 body as a made-up id.
- A malformed id: the same 404 body as a made-up id.
- The caller's own task: still 200.

DB-free: EvalTask rows live in memory and every EvalTask query the view builds
is answered by evaluating its real WHERE tree against them (an unmodelled
column or an unsupported lookup fails the test). The compatibility-evals read
on CustomEvalConfig is answered with no rows and the bounded-read transaction
is replaced by a no-op context.
"""

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db.models.lookups import Exact, IsNull
from django.db.models.query import ModelIterable, QuerySet
from django.db.models.sql.where import NothingNode, WhereNode
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, Workspace
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.project import Project
from tracer.views import eval_task as eval_task_views
from tracer.views.eval_task import EvalTaskView

TASK_TABLE = EvalTask._meta.db_table
PROJECT_TABLE = Project._meta.db_table
WORKSPACE_TABLE = Workspace._meta.db_table

# Read after the lookup to attach eval names; answers with no rows.
EMPTY_MODELS = {CustomEvalConfig}


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


class TaskTable:
    def __init__(self):
        self.rows = []
        self.lookups = []

    def add(self, *, organization, workspace, project_deleted=False):
        project = Project(
            id=uuid.uuid4(),
            name="project",
            organization=organization,
            workspace=workspace,
            deleted=project_deleted,
        )
        task = EvalTask(
            id=uuid.uuid4(),
            project=project,
            name="task",
            filters={},
            sampling_rate=100.0,
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.PENDING,
            evals_details=[],
            failed_spans=[],
        )
        self.rows.append(
            (
                {
                    (TASK_TABLE, "id"): task.id,
                    (TASK_TABLE, "deleted"): False,
                    (PROJECT_TABLE, "organization_id"): organization.id,
                    (PROJECT_TABLE, "workspace_id"): workspace.id,
                    (PROJECT_TABLE, "deleted"): project_deleted,
                    (WORKSPACE_TABLE, "is_default"): workspace.is_default,
                    (WORKSPACE_TABLE, "organization_id"): workspace.organization_id,
                },
                task,
            )
        )
        return task


@pytest.fixture
def tasks():
    table = TaskTable()

    def fetch_all(queryset):
        if queryset._result_cache is not None:
            return
        query = queryset.query
        if queryset.model is EvalTask:
            assert queryset._iterable_class is ModelIterable
            table.lookups.append(query)
            found = [
                task
                for columns, task in table.rows
                if _matches(query.where, columns, query)
            ]
            # The preflight annotates the stored JSON size; these rows are small.
            for task in found:
                for name in query.annotations:
                    setattr(task, name, 0)
            result = found[query.low_mark : query.high_mark]
        else:
            assert queryset.model in EMPTY_MODELS, (
                f"unexpected query on {queryset.model.__name__}"
            )
            result = []
        queryset._result_cache = result
        queryset._prefetch_done = True

    with (
        patch.object(QuerySet, "_fetch_all", fetch_all),
        patch.object(
            eval_task_views,
            "_bounded_eval_task_read_transaction",
            lambda deadline: nullcontext(),
        ),
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


def _get(task_id, *, organization, workspace):
    """GET as a member of ``organization`` bound to ``workspace``, the way
    authentication binds it (request.organization, request.workspace and the
    model-manager context)."""
    request = APIRequestFactory().get(f"/tracer/eval-task/{task_id}/")
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
        return EvalTaskView.as_view({"get": "retrieve"})(request, pk=str(task_id))
    finally:
        clear_workspace_context()


def _assert_typed_not_found(response):
    assert response.status_code == 404, response.data
    assert response.data["status"] is False
    assert response.data["type"] == "not_found"
    assert response.data["detail"] == "No EvalTask matches the given query."


@pytest.mark.parametrize("bound", ["a_one", "a_default"])
def test_a_well_formed_id_that_names_nothing_is_a_typed_404(tasks, tenancy, bound):
    workspace = getattr(tenancy, bound)
    tasks.add(organization=tenancy.org_a, workspace=workspace)

    response = _get(uuid.uuid4(), organization=tenancy.org_a, workspace=workspace)

    _assert_typed_not_found(response)
    assert tasks.lookups, "the view never ran its scoped lookup"


@pytest.mark.parametrize("bound", ["a_one", "a_default"])
@pytest.mark.parametrize("malformed", ["not-a-uuid", "1234", "00000000-0000"])
def test_a_malformed_id_gets_the_same_404_as_a_made_up_one(
    tasks, tenancy, bound, malformed
):
    workspace = getattr(tenancy, bound)
    tasks.add(organization=tenancy.org_a, workspace=workspace)

    response = _get(malformed, organization=tenancy.org_a, workspace=workspace)
    made_up = _get(uuid.uuid4(), organization=tenancy.org_a, workspace=workspace)

    _assert_typed_not_found(response)
    assert response.data == made_up.data


@pytest.mark.parametrize("bound", ["a_one", "a_default"])
@pytest.mark.parametrize(
    "owner",
    [
        ("org_b", "b_default", False),
        ("org_a", "a_two", False),
        ("org_a", None, True),
    ],
    ids=["another-org", "another-workspace", "deleted-project"],
)
def test_a_foreign_id_gets_the_same_404_as_a_made_up_one(tasks, tenancy, bound, owner):
    org_name, workspace_name, project_deleted = owner
    workspace = getattr(tenancy, bound)
    foreign = tasks.add(
        organization=getattr(tenancy, org_name),
        workspace=getattr(tenancy, workspace_name) if workspace_name else workspace,
        project_deleted=project_deleted,
    )

    response = _get(foreign.id, organization=tenancy.org_a, workspace=workspace)
    made_up = _get(uuid.uuid4(), organization=tenancy.org_a, workspace=workspace)

    _assert_typed_not_found(response)
    assert response.data == made_up.data


@pytest.mark.parametrize("bound", ["a_one", "a_default"])
def test_the_callers_own_task_is_still_200(tasks, tenancy, bound):
    workspace = getattr(tenancy, bound)
    own = tasks.add(organization=tenancy.org_a, workspace=workspace)

    response = _get(own.id, organization=tenancy.org_a, workspace=workspace)

    assert response.status_code == 200, response.data
    assert response.data["id"] == str(own.id)
    assert response.data["project"] == own.project_id
    assert response.data["name"] == "task"
    assert response.data["evals"] == []
