"""Executing a run test runs only that run test's own scenarios (T6 #7e).

``POST /simulate/run-tests/{run_test_id}/execute/`` and the prompt-simulation
execute endpoint take a ``scenario_ids`` list. Nothing checked that the ids were
the caller's: the view copied them onto the new ``TestExecution`` and every
runner that later read ``TestExecution.scenario_ids`` loaded whatever scenario an
id named. The hosted runner job builder (services/hosted_runner.py
``_load_scenarios``), the runner's ``/batch`` ingest (services/alk_simulate_ingestion.py
``_build_expected_call_executions``, called with the internal service secret, so
no workspace context is bound) and the dataset row read behind it
(services/test_executor.py ``_get_row_data_and_generate_prompt``) then copied
another organization's persona rows, cell values and simulator prompt into the
caller's own calls. A hosted rerun replays the stored ids through the same
builders, with or without ``HOSTED_RUNNER_ENABLED``.

Now:

* Execute accepts a scenario id only when it names a live scenario of the
  caller's organization (and, through the ``Scenarios.objects`` manager, the
  caller's workspace) that is attached to the run test being executed.
  Another organization's scenario, a scenario of the caller's that the run test
  does not include, a deleted scenario and an id that names nothing all get the
  same 400, with a fixed body that names no id, before any execution is created
  or dispatched. A run test the caller cannot see still answers as before (404
  on the run-test route), whatever ``scenario_ids`` says.
* The runners read a stored execution's scenarios, and their dataset rows, only
  from the execution's own organization, so an execution stored before the fix
  cannot be replayed across organizations.

DB-free: every model row lives in memory and each query the code builds is
answered by evaluating its real WHERE tree against those rows (a query on a model
the test did not stock, a filter on an unmodelled column, an unsupported lookup or
any real database access fails the test). Dispatch (Temporal, Celery, the hosted
runner workflow) is mocked.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db.models.lookups import Exact, In, IsNull
from django.db.models.query import (
    FlatValuesListIterable,
    ModelIterable,
    QuerySet,
    ValuesIterable,
    ValuesListIterable,
)
from django.db.models.sql.where import NothingNode, WhereNode
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, Workspace
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    CallExecution,
    RunTest,
    Scenarios,
    SimulatorAgent,
)
from simulate.models.test_execution import TestExecution
from simulate.serializers.requests.run_test import ExecuteRunTestSerializer
from simulate.services import alk_simulate_ingestion, hosted_runner
from simulate.services.alk_simulate_ingestion import ALKSimulateIngestionError
from simulate.services.test_executor import TestExecutor
from simulate.temporal.activities import xl as xl_activities
from simulate.views import run_test as run_test_views
from simulate.views.prompt_simulation import ExecutePromptSimulationView
from simulate.views.run_test import RunTestExecutionView, _dispatch_hosted_rerun
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.settings import settings as app_settings

SCENARIO = Scenarios._meta.db_table
RUN_TEST = RunTest._meta.db_table
THROUGH = RunTest.scenarios.through._meta.db_table
WORKSPACE = Workspace._meta.db_table
TEST_EXECUTION = TestExecution._meta.db_table
CALL_EXECUTION = CallExecution._meta.db_table
PROMPT_TEMPLATE = PromptTemplate._meta.db_table
DATASET = Dataset._meta.db_table
ROW = Row._meta.db_table
COLUMN = Column._meta.db_table
CELL = Cell._meta.db_table

NOT_IN_RUN_TEST = "One or more selected scenarios are not part of this run test."

VICTIM_SECRET = "org-b persona: account 4417, prefers Dr. Okafor"
VICTIM_PROMPT = "ORG B SIMULATOR PROMPT - confidential"
VICTIM_SCENARIO_NAME = "Org B renewals escalations"
VICTIM_DATASET_NAME = "org-b-renewals-dataset"
VICTIM_COLUMN_NAME = "org_b_account_notes"


# --------------------------------------------------------------------------
# In-memory tables
# --------------------------------------------------------------------------


def _same(actual, expected):
    if isinstance(actual, uuid.UUID) or isinstance(expected, uuid.UUID):
        return str(actual) == str(expected)
    return actual == expected


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
    if isinstance(node, In):  # includes RelatedIn
        return actual is not None and any(_same(actual, item) for item in node.rhs)
    if isinstance(node, Exact):  # includes RelatedExact
        return actual is not None and _same(actual, node.rhs)
    raise AssertionError(f"unsupported lookup {type(node).__name__} on {key}")


def _constraints(query):
    """The (table, column, value) equalities every matching row must meet (top-level AND)."""
    where = query.where
    assert where.connector == "AND" and not where.negated
    found = set()
    for child in where.children:
        if isinstance(child, Exact):
            table = query.alias_map[child.lhs.alias].table_name
            found.add((table, child.lhs.target.column, str(child.rhs)))
    return found


def _value(obj, field):
    """``field`` of ``obj``, following ``__`` through related objects."""
    for part in field.split("__"):
        if obj is None:
            return None
        obj = getattr(obj, part)
    return obj


def _shape(obj, queryset):
    fields = queryset.query.values_select
    iterable = queryset._iterable_class
    if iterable is ModelIterable:
        return obj
    if iterable is FlatValuesListIterable:
        (field,) = fields
        return _value(obj, field)
    if iterable is ValuesListIterable:
        return tuple(_value(obj, field) for field in fields)
    if iterable is ValuesIterable:
        return {field: _value(obj, field) for field in fields}
    raise AssertionError(f"unsupported result shape {iterable.__name__}")


def _ordered(objects, query):
    for name in reversed(list(query.order_by)):
        reverse = name.startswith("-")
        attr = name.lstrip("-")
        attr = "pk" if attr == "pk" else attr
        objects = sorted(objects, key=lambda o: str(getattr(o, attr)), reverse=reverse)
    return objects


class FakeDB:
    def __init__(self):
        self.tables = {}
        self.queries = []

    def add(self, model, obj, columns, *, attachments=None):
        self.tables.setdefault(model, []).append((columns, attachments, obj))
        return obj

    def _rows(self, model, query):
        tables = {join.table_name for join in query.alias_map.values()}
        for columns, attachments, obj in self.tables.get(model, []):
            if attachments is not None and THROUGH in tables:
                # INNER JOIN through the run-test m2m: one row per attachment.
                for attachment in attachments:
                    yield {**columns, **attachment}, obj
            else:
                yield columns, obj

    def fetch_all(self, queryset):
        if queryset._result_cache is not None:
            return
        model = queryset.model
        assert model in self.tables, f"unexpected query on {model.__name__}"
        query = queryset.query
        self.queries.append((model, query))
        found = [
            obj
            for columns, obj in self._rows(model, query)
            if _matches(query.where, columns, query)
        ]
        found = _ordered(found, query)[query.low_mark : query.high_mark]
        queryset._result_cache = [_shape(obj, queryset) for obj in found]
        queryset._prefetch_done = True

    def queried(self, model):
        return [
            query for queried_model, query in self.queries if queried_model is model
        ]


@pytest.fixture
def db():
    fake = FakeDB()
    for model in (Scenarios, RunTest, TestExecution, CallExecution, Row, Column, Cell):
        fake.tables.setdefault(model, [])

    def fetch_all(queryset):
        fake.fetch_all(queryset)

    with patch.object(QuerySet, "_fetch_all", fetch_all):
        yield fake


@pytest.fixture(autouse=True)
def no_ambient_workspace():
    clear_workspace_context()
    yield
    clear_workspace_context()


def _workspace_columns(workspace):
    # LEFT JOIN accounts_workspace: NULLs for a workspace-less row.
    return {
        (WORKSPACE, "is_default"): workspace.is_default if workspace else None,
        (WORKSPACE, "organization_id"): workspace.organization_id
        if workspace
        else None,
    }


class World:
    """Two organizations' run tests, scenarios, datasets and stored executions."""

    def __init__(self, db):
        self.db = db
        self.org_a = Organization(id=uuid.uuid4(), name="Org A")
        self.org_b = Organization(id=uuid.uuid4(), name="Org B")
        self.a_default = Workspace(
            id=uuid.uuid4(), name="A default", organization=self.org_a, is_default=True
        )
        self.a_one = Workspace(
            id=uuid.uuid4(), name="A one", organization=self.org_a, is_default=False
        )
        self.a_two = Workspace(
            id=uuid.uuid4(), name="A two", organization=self.org_a, is_default=False
        )
        self.b_default = Workspace(
            id=uuid.uuid4(), name="B default", organization=self.org_b, is_default=True
        )

    def run_test(self, *, organization, workspace, deleted=False, **fields):
        run_test = RunTest(
            id=uuid.uuid4(),
            name=f"run-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            deleted=deleted,
            **fields,
        )
        columns = {
            (RUN_TEST, "id"): run_test.id,
            (RUN_TEST, "organization_id"): organization.id,
            (RUN_TEST, "workspace_id"): workspace.id if workspace else None,
            (RUN_TEST, "deleted"): deleted,
            (RUN_TEST, "prompt_template_id"): run_test.prompt_template_id,
            (RUN_TEST, "source_type"): run_test.source_type,
            **_workspace_columns(workspace),
        }
        run_test._attached = []
        return self.db.add(RunTest, run_test, columns)

    def dataset(self, *, organization, values, name="ds"):
        """A dataset with one row whose cells hold ``values``."""
        dataset = Dataset(id=uuid.uuid4(), name=name, organization=organization)
        dataset._columns = {}
        row = Row(id=uuid.uuid4(), dataset=dataset, order=0)
        self.db.add(
            Row,
            row,
            {
                (ROW, "id"): row.id,
                (ROW, "dataset_id"): dataset.id,
                (ROW, "deleted"): False,
                (DATASET, "organization_id"): organization.id,
            },
        )
        for column_name, value in values.items():
            column = Column(id=uuid.uuid4(), name=column_name, dataset=dataset)
            dataset._columns[column_name] = column
            self.db.add(
                Column,
                column,
                {
                    (COLUMN, "id"): column.id,
                    (COLUMN, "dataset_id"): dataset.id,
                    (COLUMN, "deleted"): False,
                    (DATASET, "organization_id"): organization.id,
                },
            )
            cell = Cell(
                id=uuid.uuid4(), dataset=dataset, column=column, row=row, value=value
            )
            self.db.add(
                Cell,
                cell,
                {
                    (CELL, "row_id"): row.id,
                    (CELL, "column_id"): column.id,
                    (CELL, "deleted"): False,
                },
            )
        dataset.column_order = [str(column.id) for column in dataset._columns.values()]
        dataset._row = row
        return dataset

    def scenario(
        self,
        *,
        organization,
        workspace,
        attached_to=(),
        deleted=False,
        status=StatusType.COMPLETED.value,
        dataset=None,
        simulator_agent=None,
        name="scenario",
    ):
        scenario = Scenarios(
            id=uuid.uuid4(),
            name=name,
            source="src",
            organization=organization,
            workspace=workspace,
            deleted=deleted,
            status=status,
            dataset=dataset,
            simulator_agent=simulator_agent,
        )
        attachments = [
            {
                (THROUGH, "runtest_id"): run_test.id,
                (RUN_TEST, "id"): run_test.id,
                (RUN_TEST, "organization_id"): run_test.organization_id,
                (RUN_TEST, "deleted"): run_test.deleted,
            }
            for run_test in attached_to
        ]
        for run_test in attached_to:
            run_test._attached.append(scenario)
        return self.db.add(
            Scenarios,
            scenario,
            {
                (SCENARIO, "id"): scenario.id,
                (SCENARIO, "organization_id"): organization.id,
                (SCENARIO, "workspace_id"): workspace.id if workspace else None,
                (SCENARIO, "deleted"): deleted,
                (SCENARIO, "status"): status,
                (SCENARIO, "dataset_id"): dataset.id if dataset else None,
                **_workspace_columns(workspace),
            },
            attachments=attachments,
        )

    def stored_execution(self, run_test, scenario_ids, **fields):
        execution = TestExecution(
            id=uuid.uuid4(),
            run_test=run_test,
            scenario_ids=[str(sid) for sid in scenario_ids],
            status=TestExecution.ExecutionStatus.COMPLETED,
            **fields,
        )
        return self.db.add(
            TestExecution,
            execution,
            {(TEST_EXECUTION, "id"): execution.id, (TEST_EXECUTION, "deleted"): False},
        )

    def call(self, execution, scenario, row_id=None):
        call = CallExecution(
            id=uuid.uuid4(),
            test_execution=execution,
            scenario=scenario,
            row_id=row_id,
        )
        return self.db.add(
            CallExecution,
            call,
            {
                (CALL_EXECUTION, "id"): call.id,
                (CALL_EXECUTION, "test_execution_id"): execution.id,
                (CALL_EXECUTION, "deleted"): False,
            },
        )


@pytest.fixture
def world(db):
    return World(db)


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


def _request(path, body, *, organization, workspace, set_organization=False):
    """POST as a member of ``organization``.

    With a ``workspace`` the request is bound the way APIKeyAuthentication binds
    it (request.organization, request.workspace and the model-manager context).
    Without one no workspace is bound and, unless ``set_organization``,
    ``request.user.organization`` is all the view has.
    """
    request = APIRequestFactory().post(path, body, format="json")
    user = SimpleNamespace(
        id=uuid.uuid4(),
        is_authenticated=True,
        organization=organization,
        email="member@example.com",
    )
    if set_organization or workspace is not None:
        request.organization = organization
    if workspace is not None:
        request.workspace = workspace
        set_workspace_context(
            workspace=workspace,
            organization=organization,
            user=user,
            require_workspace=True,
        )
    force_authenticate(request, user=user)
    return request


def _ok_result(run_test, scenario_ids):
    return {
        "success": True,
        "run_test_id": str(run_test.id),
        "execution_id": str(uuid.uuid4()),
        "status": "started",
        "total_scenarios": len(scenario_ids),
        "total_calls": 0,
    }


class Dispatch:
    """Every way RunTestExecutionView can start an execution, mocked."""

    def __init__(self, arm):
        self.arm = arm
        self.executor = MagicMock(name="TestExecutor()")
        self.temporal = MagicMock(name="_execute_with_temporal")
        self.hosted = MagicMock(name="_execute_with_hosted_runner")
        self.repository = MagicMock(name="_dispatch_repository_harness_rerun")

    def __enter__(self):
        def ok(*, run_test, scenario_ids, **_):
            return _ok_result(run_test, scenario_ids)

        def legacy_ok(*, run_test_id, scenario_ids, **_):
            return {**_ok_result(SimpleNamespace(id=run_test_id), scenario_ids)}

        self.temporal.side_effect = ok
        self.hosted.side_effect = ok
        self.executor.execute_test.side_effect = legacy_ok
        self._patches = [
            patch.object(
                app_settings, "HOSTED_RUNNER_ENABLED", self.arm == "hosted", create=True
            ),
            patch.object(
                app_settings,
                "TEMPORAL_TEST_EXECUTION_ENABLED",
                self.arm == "temporal",
                create=True,
            ),
            patch.object(
                RunTestExecutionView, "_hosted_runner_eligible", return_value=True
            ),
            patch.object(RunTestExecutionView, "_execute_with_temporal", self.temporal),
            patch.object(
                RunTestExecutionView, "_execute_with_hosted_runner", self.hosted
            ),
            patch.object(run_test_views, "TestExecutor", return_value=self.executor),
            patch.object(
                run_test_views,
                "_latest_repository_harness_execution",
                return_value=None,
            ),
            patch.object(
                run_test_views, "_dispatch_repository_harness_rerun", self.repository
            ),
        ]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in reversed(self._patches):
            item.stop()

    def dispatched_ids(self):
        if self.arm == "legacy":
            calls = self.executor.execute_test.call_args_list
        else:
            calls = getattr(self, self.arm).call_args_list
        return [[str(sid) for sid in call.kwargs["scenario_ids"]] for call in calls]

    def assert_nothing_dispatched(self):
        self.executor.execute_test.assert_not_called()
        self.temporal.assert_not_called()
        self.hosted.assert_not_called()
        self.repository.assert_not_called()


def _execute(run_test_id, body, *, organization, workspace=None):
    request = _request(
        f"/simulate/run-tests/{run_test_id}/execute/",
        body,
        organization=organization,
        workspace=workspace,
    )
    try:
        return RunTestExecutionView.as_view()(request, run_test_id=str(run_test_id))
    finally:
        clear_workspace_context()


def _assert_refused_alike(response, made_up):
    assert response.status_code == 400, response.data
    assert made_up.status_code == 400, made_up.data
    assert response.data == made_up.data
    assert response.data["details"] == {"scenario_ids": [NOT_IN_RUN_TEST]}
    assert response.data["message"] == f"scenario_ids: {NOT_IN_RUN_TEST}"


def _no_ids_in(response, *ids):
    body = json.dumps(response.data, default=str)
    for value in ids:
        assert str(value) not in body


# --------------------------------------------------------------------------
# (1) Execute: another organization's scenario
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bound", [True, False], ids=["bound-workspace", "unbound"])
@pytest.mark.parametrize("arm", ["legacy", "temporal", "hosted"])
def test_execute_with_a_foreign_scenario_id_gets_the_same_400_as_a_made_up_id(
    world, bound, arm
):
    own_run = world.run_test(organization=world.org_a, workspace=world.a_one)
    own = world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[own_run]
    )
    victim_run = world.run_test(organization=world.org_b, workspace=world.b_default)
    foreign = world.scenario(
        organization=world.org_b, workspace=world.b_default, attached_to=[victim_run]
    )
    caller = {
        "organization": world.org_a,
        "workspace": world.a_one if bound else None,
    }

    with Dispatch(arm) as dispatch:
        response = _execute(own_run.id, {"scenario_ids": [str(foreign.id)]}, **caller)
        mixed = _execute(
            own_run.id, {"scenario_ids": [str(own.id), str(foreign.id)]}, **caller
        )
        made_up = _execute(own_run.id, {"scenario_ids": [str(uuid.uuid4())]}, **caller)

        dispatch.assert_nothing_dispatched()
    _assert_refused_alike(response, made_up)
    _assert_refused_alike(mixed, made_up)
    _no_ids_in(response, foreign.id, own.id)


@pytest.mark.parametrize("bound", [True, False], ids=["bound-workspace", "unbound"])
def test_execute_with_a_foreign_workspace_less_scenario_gets_the_same_400(world, bound):
    """Org B's workspace-less scenario: the default-workspace branch of the
    manager admits workspace-less rows only of the caller's organization."""
    own_run = world.run_test(organization=world.org_a, workspace=world.a_default)
    world.scenario(
        organization=world.org_a, workspace=world.a_default, attached_to=[own_run]
    )
    foreign = world.scenario(organization=world.org_b, workspace=None)
    caller = {
        "organization": world.org_a,
        "workspace": world.a_default if bound else None,
    }

    with Dispatch("legacy") as dispatch:
        response = _execute(own_run.id, {"scenario_ids": [str(foreign.id)]}, **caller)
        made_up = _execute(own_run.id, {"scenario_ids": [str(uuid.uuid4())]}, **caller)
        dispatch.assert_nothing_dispatched()
    _assert_refused_alike(response, made_up)


def test_execute_with_a_scenario_attached_to_the_run_test_but_owned_by_another_org(
    world,
):
    """Defence in depth: even a foreign scenario that somehow got attached to the
    caller's run test is refused, because the scenario's own organization is
    checked, not just the attachment."""
    own_run = world.run_test(organization=world.org_a, workspace=world.a_one)
    world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[own_run]
    )
    smuggled = world.scenario(
        organization=world.org_b, workspace=world.b_default, attached_to=[own_run]
    )

    with Dispatch("legacy") as dispatch:
        response = _execute(
            own_run.id,
            {"scenario_ids": [str(smuggled.id)]},
            organization=world.org_a,
        )
        made_up = _execute(
            own_run.id, {"scenario_ids": [str(uuid.uuid4())]}, organization=world.org_a
        )
        dispatch.assert_nothing_dispatched()
    _assert_refused_alike(response, made_up)


# --------------------------------------------------------------------------
# (2) Execute: the caller's own scenario that this run test does not include,
#     another workspace's scenario, a deleted scenario
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bound", [True, False], ids=["bound-workspace", "unbound"])
def test_execute_with_an_own_scenario_not_in_the_run_test_gets_the_same_400(
    world, bound
):
    own_run = world.run_test(organization=world.org_a, workspace=world.a_one)
    other_run = world.run_test(organization=world.org_a, workspace=world.a_one)
    world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[own_run]
    )
    elsewhere = world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[other_run]
    )
    loose = world.scenario(organization=world.org_a, workspace=world.a_one)
    caller = {
        "organization": world.org_a,
        "workspace": world.a_one if bound else None,
    }

    with Dispatch("legacy") as dispatch:
        attached_elsewhere = _execute(
            own_run.id, {"scenario_ids": [str(elsewhere.id)]}, **caller
        )
        unattached = _execute(own_run.id, {"scenario_ids": [str(loose.id)]}, **caller)
        made_up = _execute(own_run.id, {"scenario_ids": [str(uuid.uuid4())]}, **caller)
        dispatch.assert_nothing_dispatched()
    _assert_refused_alike(attached_elsewhere, made_up)
    _assert_refused_alike(unattached, made_up)


def test_execute_with_another_workspaces_scenario_gets_the_same_400(world):
    """The Scenarios manager scopes to the bound workspace: a run test in the
    caller's workspace that (by an older attach) includes a scenario of another
    workspace of the same organization does not run it from this workspace."""
    own_run = world.run_test(organization=world.org_a, workspace=world.a_one)
    sibling = world.scenario(
        organization=world.org_a, workspace=world.a_two, attached_to=[own_run]
    )

    with Dispatch("legacy") as dispatch:
        response = _execute(
            own_run.id,
            {"scenario_ids": [str(sibling.id)]},
            organization=world.org_a,
            workspace=world.a_one,
        )
        made_up = _execute(
            own_run.id,
            {"scenario_ids": [str(uuid.uuid4())]},
            organization=world.org_a,
            workspace=world.a_one,
        )
        dispatch.assert_nothing_dispatched()
    _assert_refused_alike(response, made_up)


def test_execute_with_a_deleted_scenario_gets_the_same_400(world):
    own_run = world.run_test(organization=world.org_a, workspace=world.a_one)
    world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[own_run]
    )
    gone = world.scenario(
        organization=world.org_a,
        workspace=world.a_one,
        attached_to=[own_run],
        deleted=True,
    )

    with Dispatch("legacy") as dispatch:
        response = _execute(
            own_run.id, {"scenario_ids": [str(gone.id)]}, organization=world.org_a
        )
        made_up = _execute(
            own_run.id, {"scenario_ids": [str(uuid.uuid4())]}, organization=world.org_a
        )
        dispatch.assert_nothing_dispatched()
    _assert_refused_alike(response, made_up)


def test_the_ownership_lookup_is_filtered_by_org_and_run_test(world):
    """Unbound, the manager adds no tenant filter, so the serializer's own filter
    is the boundary: the caller's organization, live, attached to this live run
    test of the caller's organization."""
    own_run = world.run_test(organization=world.org_a, workspace=world.a_one)
    requested = uuid.uuid4()

    with Dispatch("legacy"):
        _execute(
            own_run.id, {"scenario_ids": [str(requested)]}, organization=world.org_a
        )

    (query,) = world.db.queried(Scenarios)
    assert {
        (SCENARIO, "organization_id", str(world.org_a.id)),
        (SCENARIO, "deleted", "False"),
        (THROUGH, "runtest_id", str(own_run.id)),
        (RUN_TEST, "organization_id", str(world.org_a.id)),
        (RUN_TEST, "deleted", "False"),
    } <= _constraints(query)


# --------------------------------------------------------------------------
# (3) Execute: the caller's own scenarios still run
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario_workspace", "caller_workspace"),
    [
        ("a_one", "a_one"),
        ("a_default", "a_default"),
        (None, "a_default"),
        ("a_one", None),
    ],
    ids=["same-workspace", "default-workspace", "workspace-less", "unbound"],
)
@pytest.mark.parametrize("arm", ["legacy", "temporal", "hosted"])
def test_own_attached_scenarios_still_execute(
    world, arm, scenario_workspace, caller_workspace
):
    ws = getattr(world, scenario_workspace) if scenario_workspace else None
    run_ws = getattr(world, caller_workspace) if caller_workspace else ws
    own_run = world.run_test(organization=world.org_a, workspace=run_ws)
    first = world.scenario(
        organization=world.org_a, workspace=ws, attached_to=[own_run]
    )
    second = world.scenario(
        organization=world.org_a, workspace=ws, attached_to=[own_run]
    )
    world.scenario(organization=world.org_a, workspace=ws, attached_to=[own_run])

    with Dispatch(arm) as dispatch:
        response = _execute(
            own_run.id,
            {"scenario_ids": [str(first.id), str(second.id)]},
            organization=world.org_a,
            workspace=getattr(world, caller_workspace) if caller_workspace else None,
        )

        assert response.status_code == 200, response.data
        assert dispatch.dispatched_ids() == [[str(first.id), str(second.id)]]
    assert response.data["scenario_ids"] == [str(first.id), str(second.id)]


def test_no_scenario_ids_still_runs_every_attached_scenario(world):
    own_run = world.run_test(organization=world.org_a, workspace=world.a_one)
    first = world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[own_run]
    )
    second = world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[own_run]
    )

    with Dispatch("legacy") as dispatch:
        response = _execute(own_run.id, {}, organization=world.org_a)

        assert response.status_code == 200, response.data
        (dispatched,) = dispatch.dispatched_ids()
    assert sorted(dispatched) == sorted([str(first.id), str(second.id)])


# --------------------------------------------------------------------------
# (4) Execute: a run test the caller cannot see answers as before
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bound", [True, False], ids=["bound-workspace", "unbound"])
def test_a_foreign_run_test_still_gets_the_run_test_404(world, bound):
    victim_run = world.run_test(organization=world.org_b, workspace=world.b_default)
    foreign = world.scenario(
        organization=world.org_b, workspace=world.b_default, attached_to=[victim_run]
    )
    caller = {
        "organization": world.org_a,
        "workspace": world.a_one if bound else None,
    }

    with Dispatch("legacy") as dispatch:
        response = _execute(
            victim_run.id, {"scenario_ids": [str(foreign.id)]}, **caller
        )
        made_up = _execute(
            uuid.uuid4(), {"scenario_ids": [str(uuid.uuid4())]}, **caller
        )
        dispatch.assert_nothing_dispatched()
    assert response.status_code == 404
    assert made_up.status_code == 404
    assert response.data == made_up.data
    assert "Run test not found" in str(response.data)


# --------------------------------------------------------------------------
# (5) The view's own check answers exactly like the serializer
# --------------------------------------------------------------------------


def test_the_view_refuses_an_unattached_id_even_without_the_serializer(world):
    """The serializer is the boundary; the view re-checks the ids against the run
    test it loaded and answers with the byte-identical 400, so the two cannot
    drift into an oracle."""
    own_run = world.run_test(organization=world.org_a, workspace=world.a_one)
    world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[own_run]
    )
    victim_run = world.run_test(organization=world.org_b, workspace=world.b_default)
    foreign = world.scenario(
        organization=world.org_b, workspace=world.b_default, attached_to=[victim_run]
    )

    with Dispatch("legacy") as dispatch:
        by_serializer = _execute(
            own_run.id, {"scenario_ids": [str(foreign.id)]}, organization=world.org_a
        )
        with patch.object(
            ExecuteRunTestSerializer,
            "validate_scenario_ids",
            lambda self, value: value,
            create=True,
        ):
            by_view = _execute(
                own_run.id,
                {"scenario_ids": [str(foreign.id)]},
                organization=world.org_a,
            )
        dispatch.assert_nothing_dispatched()
    assert by_serializer.status_code == by_view.status_code == 400
    assert by_view.data == by_serializer.data


# --------------------------------------------------------------------------
# (6) Prompt-simulation execute
# --------------------------------------------------------------------------


class PromptWorld:
    def __init__(self, world, *, organization, workspace):
        self.template = PromptTemplate(
            id=uuid.uuid4(), name="tpl", organization=organization, workspace=workspace
        )
        world.db.add(
            PromptTemplate,
            self.template,
            {
                (PROMPT_TEMPLATE, "id"): self.template.id,
                (PROMPT_TEMPLATE, "organization_id"): organization.id,
                (PROMPT_TEMPLATE, "workspace_id"): workspace.id if workspace else None,
                (PROMPT_TEMPLATE, "deleted"): False,
                **_workspace_columns(workspace),
            },
        )
        version = PromptVersion(id=uuid.uuid4(), original_template=self.template)
        self.run_test = world.run_test(
            organization=organization,
            workspace=workspace,
            prompt_template=self.template,
            prompt_version=version,
            source_type="prompt",
        )


def _execute_prompt(prompt, body, *, organization, workspace=None):
    # The prompt view resolves the organization from request.organization or a
    # membership row (accounts.utils.get_request_organization); there are no
    # membership rows here, so the organization is always on the request.
    request = _request(
        f"/simulate/prompt-templates/{prompt.template.id}/simulations/"
        f"{prompt.run_test.id}/execute/",
        body,
        organization=organization,
        workspace=workspace,
        set_organization=True,
    )
    try:
        return ExecutePromptSimulationView.as_view()(
            request,
            prompt_template_id=str(prompt.template.id),
            run_test_id=str(prompt.run_test.id),
        )
    finally:
        clear_workspace_context()


@pytest.fixture
def prompt_executor():
    executor = MagicMock(name="TestExecutor()")
    executor.execute_test.side_effect = lambda *, run_test_id, scenario_ids, **_: {
        **_ok_result(SimpleNamespace(id=run_test_id), scenario_ids),
        "total_calls": 0,
    }
    with patch("simulate.views.prompt_simulation.TestExecutor", return_value=executor):
        yield executor


@pytest.mark.parametrize("bound", [True, False], ids=["bound-workspace", "unbound"])
def test_prompt_execute_with_a_foreign_scenario_id_gets_the_same_400(
    world, prompt_executor, bound
):
    prompt = PromptWorld(world, organization=world.org_a, workspace=world.a_one)
    own = world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[prompt.run_test]
    )
    foreign = world.scenario(organization=world.org_b, workspace=world.b_default)
    caller = {
        "organization": world.org_a,
        "workspace": world.a_one if bound else None,
    }

    response = _execute_prompt(
        prompt, {"scenario_ids": [str(own.id), str(foreign.id)]}, **caller
    )
    made_up = _execute_prompt(prompt, {"scenario_ids": [str(uuid.uuid4())]}, **caller)

    prompt_executor.execute_test.assert_not_called()
    _assert_refused_alike(response, made_up)
    _no_ids_in(response, foreign.id, own.id)


def test_prompt_execute_runs_own_attached_scenarios(world, prompt_executor):
    prompt = PromptWorld(world, organization=world.org_a, workspace=world.a_one)
    own = world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[prompt.run_test]
    )

    # Stock the real scope predicates; do not replace them with a true verdict.
    version = prompt.run_test.prompt_version
    version_table = PromptVersion._meta.db_table
    world.db.add(
        PromptVersion,
        version,
        {
            (version_table, "id"): version.id,
            (version_table, "original_template_id"): prompt.template.id,
            (version_table, "deleted"): False,
            (PROMPT_TEMPLATE, "organization_id"): world.org_a.id,
            (PROMPT_TEMPLATE, "workspace_id"): world.a_one.id,
        },
    )
    for columns, _, scenario in world.db.tables[Scenarios]:
        for relation in (
            "dataset",
            "simulator_agent",
            "agent_definition",
            "prompt_template",
        ):
            field = Scenarios._meta.get_field(relation)
            assert getattr(scenario, field.attname) is None
            columns[(SCENARIO, field.column)] = None
            table = field.related_model._meta.db_table
            columns[(table, "organization_id")] = None
            columns[(table, "workspace_id")] = None
        assert scenario.prompt_version_id is None
        columns[(SCENARIO, "prompt_version_id")] = None
        columns[(version_table, "deleted")] = None
        columns[(PROMPT_TEMPLATE, "deleted")] = None

    # exists() otherwise bypasses FakeDB's _fetch_all and reaches the real DB.
    with patch.object(QuerySet, "exists", lambda queryset: bool(queryset)):
        response = _execute_prompt(
            prompt,
            {"scenario_ids": [str(own.id)]},
            organization=world.org_a,
            workspace=world.a_one,
        )

    assert response.status_code == 200, response.data
    prompt_executor.execute_test.assert_called_once()
    assert prompt_executor.execute_test.call_args.kwargs["scenario_ids"] == [
        str(own.id)
    ]


def test_prompt_select_all_treats_the_ids_as_an_exclusion_list(world, prompt_executor):
    """With select_all, scoped attached ids remove scenarios from the run's own set."""
    prompt = PromptWorld(world, organization=world.org_a, workspace=world.a_one)
    keep = world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[prompt.run_test]
    )
    drop = world.scenario(
        organization=world.org_a, workspace=world.a_one, attached_to=[prompt.run_test]
    )
    world.scenario(organization=world.org_b, workspace=world.b_default)

    # Stock the real scope predicates; do not replace them with a true verdict.
    version = prompt.run_test.prompt_version
    version_table = PromptVersion._meta.db_table
    world.db.add(
        PromptVersion,
        version,
        {
            (version_table, "id"): version.id,
            (version_table, "original_template_id"): prompt.template.id,
            (version_table, "deleted"): False,
            (PROMPT_TEMPLATE, "organization_id"): world.org_a.id,
            (PROMPT_TEMPLATE, "workspace_id"): world.a_one.id,
        },
    )
    for columns, _, scenario in world.db.tables[Scenarios]:
        for relation in (
            "dataset",
            "simulator_agent",
            "agent_definition",
            "prompt_template",
        ):
            field = Scenarios._meta.get_field(relation)
            assert getattr(scenario, field.attname) is None
            columns[(SCENARIO, field.column)] = None
            table = field.related_model._meta.db_table
            columns[(table, "organization_id")] = None
            columns[(table, "workspace_id")] = None
        assert scenario.prompt_version_id is None
        columns[(SCENARIO, "prompt_version_id")] = None
        columns[(version_table, "deleted")] = None
        columns[(PROMPT_TEMPLATE, "deleted")] = None

    # exists() otherwise bypasses FakeDB's _fetch_all and reaches the real DB.
    with patch.object(QuerySet, "exists", lambda queryset: bool(queryset)):
        response = _execute_prompt(
            prompt,
            {"select_all": True, "scenario_ids": [str(drop.id)]},
            organization=world.org_a,
            workspace=world.a_one,
        )

    assert response.status_code == 200, response.data
    assert prompt_executor.execute_test.call_args.kwargs["scenario_ids"] == [
        str(keep.id)
    ]


# --------------------------------------------------------------------------
# (7) Executions stored before the fix: hosted runner, rerun, /batch ingest
# --------------------------------------------------------------------------


class StoredRun:
    """Org A's run test with an execution that names org B's scenario too."""

    def __init__(self, world, *, manifest=None):
        agent = AgentDefinition(
            id=uuid.uuid4(),
            agent_name="support-bot",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            organization=world.org_a,
        )
        version = AgentVersion(
            id=uuid.uuid4(),
            agent_definition=agent,
            version_number=1,
            configuration_snapshot={"agent_type": "text"},
        )
        own_simulator = SimulatorAgent(
            id=uuid.uuid4(), name="own-sim", prompt="own simulator prompt"
        )
        victim_simulator = SimulatorAgent(
            id=uuid.uuid4(), name="victim-sim", prompt=VICTIM_PROMPT
        )
        self.run_test = world.run_test(
            organization=world.org_a,
            workspace=world.a_one,
            agent_definition=agent,
            agent_version=version,
            simulator_agent=own_simulator,
        )
        self.own_dataset = world.dataset(
            organization=world.org_a,
            values={"persona": '{"name": "Own Ada"}', "situation": "own billing"},
        )
        self.own = world.scenario(
            organization=world.org_a,
            workspace=world.a_one,
            attached_to=[self.run_test],
            dataset=self.own_dataset,
            simulator_agent=own_simulator,
            name="own",
        )
        self.victim_dataset = world.dataset(
            organization=world.org_b,
            values={
                "persona": json.dumps({"name": VICTIM_SECRET}),
                "situation": VICTIM_SECRET,
                VICTIM_COLUMN_NAME: VICTIM_SECRET,
            },
            name=VICTIM_DATASET_NAME,
        )
        victim_run = world.run_test(organization=world.org_b, workspace=world.b_default)
        self.victim = world.scenario(
            organization=world.org_b,
            workspace=world.b_default,
            attached_to=[victim_run],
            dataset=self.victim_dataset,
            simulator_agent=victim_simulator,
            name=VICTIM_SCENARIO_NAME,
        )
        self.execution = world.stored_execution(
            self.run_test,
            [self.victim.id, self.own.id],
            agent_version=version,
            simulator_agent=own_simulator,
            execution_metadata=manifest(self) if manifest else {},
        )
        self.own_call = world.call(
            self.execution, self.own, row_id=self.own_dataset._row.id
        )
        self.world = world

    def assert_victim_never_read(self):
        db = self.world.db
        victim_row = str(self.victim_dataset._row.id)
        victim_dataset = str(self.victim_dataset.id)
        for model, query in db.queries:
            if model in (Row, Cell):
                text = str(query.where)
                assert victim_row not in text, f"read victim {model.__name__}"
                assert victim_dataset not in text, f"read victim {model.__name__}"


@pytest.fixture
def no_dynamic_prompt():
    """The prompt template engine is not under test: echo what it was given."""

    def echo(self, prompt_template, row_data, agent_version, call_type=None):
        return f"{prompt_template} | {json.dumps(row_data, sort_keys=True)}"

    with patch.object(TestExecutor, "_generate_dynamic_prompt", echo):
        yield


@pytest.fixture
def chat_target():
    with patch.object(
        hosted_runner,
        "_build_target",
        return_value={"adapter": "http", "config": {"url": "https://agent.example"}},
    ):
        yield


def _assert_runner_job_is_own_only(job, stored):
    blob = json.dumps(job, default=str)
    assert VICTIM_SECRET not in blob
    assert VICTIM_PROMPT not in blob
    dataset = job["spec"]["scenario"]["dataset"]
    assert [case["persona"]["name"] for case in dataset] == ["Own Ada"]
    (scenario_query,) = stored.world.db.queried(Scenarios)
    assert (SCENARIO, "organization_id", str(stored.world.org_a.id)) in _constraints(
        scenario_query
    )
    stored.assert_victim_never_read()


def test_hosted_runner_job_for_a_stored_execution_skips_the_foreign_scenario(
    world, chat_target
):
    """The execute-path worker: build_start_runner_job reads the stored ids in a
    Temporal activity with no workspace context bound."""
    stored = StoredRun(world)

    job = hosted_runner.build_start_runner_job(
        test_execution_id=str(stored.execution.id),
        run_test_id=str(stored.run_test.id),
        scenario_ids=list(stored.execution.scenario_ids),
        mode="chat",
    )

    _assert_runner_job_is_own_only(job, stored)


def test_hosted_rerun_of_a_stored_execution_never_loads_the_foreign_scenario(
    world, chat_target
):
    """The rerun path (no HOSTED_RUNNER_ENABLED needed for a TEXT agent):
    _dispatch_hosted_rerun replays the stored ids into the runner workflow, whose
    build activity must not load the other organization's scenario."""
    stored = StoredRun(world)

    with patch(
        "simulate.temporal.client.start_simulation_runner_workflow",
        return_value="wf-1",
    ) as start:
        workflow_id = _dispatch_hosted_rerun(
            stored.execution, [str(stored.own_call.id)]
        )

    assert workflow_id == "wf-1"
    dispatched = start.call_args.kwargs
    assert dispatched["org_id"] == str(world.org_a.id)
    job = hosted_runner.build_start_runner_job(
        test_execution_id=dispatched["test_execution_id"],
        run_test_id=dispatched["run_test_id"],
        scenario_ids=dispatched["scenario_ids"],
        mode=dispatched["mode"],
        call_execution_ids=dispatched["call_execution_ids"],
    )

    _assert_runner_job_is_own_only(job, stored)


def test_batch_ingest_of_a_stored_execution_builds_only_own_calls(
    world, no_dynamic_prompt
):
    """The runner's /batch (internal service secret, no workspace bound) and the
    in-request pre-create both allocate calls through _build_expected_call_executions."""
    stored = StoredRun(world)

    calls = alk_simulate_ingestion._build_expected_call_executions(stored.execution)

    assert [call.scenario_id for call in calls] == [stored.own.id]
    blob = json.dumps([call.call_metadata for call in calls], default=str)
    assert VICTIM_SECRET not in blob
    assert VICTIM_PROMPT not in blob
    assert "Own Ada" in blob
    scenario_queries = world.db.queried(Scenarios)
    assert scenario_queries
    for query in scenario_queries:
        assert (SCENARIO, "organization_id", str(world.org_a.id)) in _constraints(query)
    stored.assert_victim_never_read()


def test_batch_ingest_of_a_harness_manifest_refuses_a_foreign_scenario(
    world, no_dynamic_prompt
):
    def manifest(stored):
        return {
            "harness_executions": [
                {
                    "scenario_id": str(stored.victim.id),
                    "dataset_row_id": str(stored.victim_dataset._row.id),
                    "execution_key": "k-1",
                    "scenario_key": "s-1",
                    "trial_index": 0,
                }
            ]
        }

    stored = StoredRun(world, manifest=manifest)

    with (
        patch.object(alk_simulate_ingestion, "_pinned_agent_prompt", return_value=""),
        pytest.raises(ALKSimulateIngestionError, match="no longer available"),
    ):
        alk_simulate_ingestion._build_expected_call_executions(stored.execution)

    stored.assert_victim_never_read()


def test_batch_ingest_reads_a_manifest_row_only_from_the_executions_org(
    world, no_dynamic_prompt
):
    """The row read at test_executor._get_row_data_and_generate_prompt is scoped
    to the execution's organization, so a manifest row of another organization's
    dataset yields no cell values."""

    def manifest(stored):
        return {
            "harness_executions": [
                {
                    "scenario_id": str(stored.own.id),
                    "dataset_row_id": str(stored.victim_dataset._row.id),
                    "execution_key": "k-1",
                    "scenario_key": "s-1",
                    "trial_index": 0,
                }
            ]
        }

    stored = StoredRun(world, manifest=manifest)

    with patch.object(alk_simulate_ingestion, "_pinned_agent_prompt", return_value=""):
        (call,) = alk_simulate_ingestion._build_expected_call_executions(
            stored.execution
        )

    blob = json.dumps(call.call_metadata, default=str)
    assert VICTIM_SECRET not in blob
    assert call.call_metadata["row_data"] == {}
    (row_query,) = world.db.queried(Row)
    assert (DATASET, "organization_id", str(world.org_a.id)) in _constraints(row_query)
    assert world.db.queried(Cell) == []


# --------------------------------------------------------------------------
# (8) Grading a stored execution's calls: the scenario-column gate
# --------------------------------------------------------------------------
#
# Both eval workers (TestExecutor._run_single_simulate_evaluation, and
# _run_single_evaluation in the Temporal xl activity) resolve an eval mapping
# value that names a dataset column by first collecting the column ids of the
# execution's stored scenarios, then reading that column's cell on the call's
# row. They run with no workspace bound, so the stored ids decide whose
# columns pass that gate: a foreign id stored before execute checked
# scenario_ids would let a pre-fix call on the other organization's row be
# graded with that organization's cell value. Everything outside the mapping
# resolution is mocked: the run_eval_func result, the context map (its
# scenario_columns subject reads the call's own row, which is the separate
# row-scope lane) and every save.

TRANSCRIPT_DATA = {"transcript": "agent: hello"}


def _grade_with_test_executor(eval_config, call):
    TestExecutor(initialize_voice_service=False)._run_single_simulate_evaluation(
        eval_config, call, TRANSCRIPT_DATA
    )


def _grade_with_xl(eval_config, call):
    xl_activities._run_single_evaluation(eval_config, call, TRANSCRIPT_DATA)


GRADERS = [
    pytest.param(_grade_with_test_executor, id="test_executor"),
    pytest.param(_grade_with_xl, id="temporal-xl"),
]


@pytest.fixture
def grading():
    run_eval = MagicMock(
        name="run_eval_func",
        return_value={"output": "Passed", "reason": "ok", "output_type": "Pass/Fail"},
    )
    no_context = MagicMock(name="build_simulation_context_map", return_value=({}, {}))
    with (
        patch("simulate.services.test_executor.run_eval_func", run_eval),
        patch("model_hub.views.utils.evals.run_eval_func", run_eval),
        patch(
            "simulate.services.test_executor.build_simulation_context_map", no_context
        ),
        patch.object(xl_activities, "build_simulation_context_map", no_context),
        patch.object(CallExecution, "save"),
    ):
        yield run_eval


def _eval_config(column):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="account-notes-check",
        eval_template=SimpleNamespace(
            owner="user", name="custom-check", output_type_normalized="pass_fail"
        ),
        mapping={"input": str(column.id)},
        config={},
        model=None,
        kb_id=None,
        error_localizer=False,
        status=None,
        save=MagicMock(name="eval_config.save"),
    )


def _graded_call(stored, scenario, dataset):
    call = stored.world.call(stored.execution, scenario, row_id=dataset._row.id)
    call.call_metadata = {"row_id": str(dataset._row.id)}
    call.agent_version = stored.execution.agent_version
    call.eval_outputs = {}
    return call


def _assert_reads_scoped_to(world, organization):
    scenario_queries = world.db.queried(Scenarios)
    assert scenario_queries
    for query in scenario_queries:
        assert (SCENARIO, "organization_id", str(organization.id)) in _constraints(
            query
        )
    for query in world.db.queried(Column):
        assert (DATASET, "organization_id", str(organization.id)) in _constraints(query)


def _assert_no_victim_names(text):
    for secret in (
        VICTIM_SECRET,
        VICTIM_SCENARIO_NAME,
        VICTIM_DATASET_NAME,
        VICTIM_COLUMN_NAME,
    ):
        assert secret not in text, secret


@pytest.mark.parametrize("grade", GRADERS)
def test_grading_a_stored_foreign_call_never_reads_the_foreign_cell(
    world, grading, grade
):
    """A call made on the other organization's scenario before the fix (its row
    is that organization's row) and an eval mapped to one of that scenario's
    columns: the column is not one of the execution's own organization's
    scenario columns, so the eval is refused as a column mismatch, as for any
    column outside the run, and the foreign cell is never read."""
    stored = StoredRun(world)
    victim_call = _graded_call(stored, stored.victim, stored.victim_dataset)
    eval_config = _eval_config(stored.victim_dataset._columns[VICTIM_COLUMN_NAME])

    with pytest.raises(ValueError, match="Column mapping mismatch") as refused:
        grade(eval_config, victim_call)

    grading.assert_not_called()
    _assert_no_victim_names(str(refused.value))
    _assert_no_victim_names(json.dumps(victim_call.eval_outputs, default=str))
    assert world.db.queried(Column), "the mismatch lookup was never evaluated"
    _assert_reads_scoped_to(world, world.org_a)
    stored.assert_victim_never_read()


@pytest.mark.parametrize("grade", GRADERS)
def test_grading_a_column_outside_the_run_names_only_own_scenarios(
    world, grading, grade
):
    """The mismatch message still names the caller's own column and scenario,
    and lists the execution's scenarios without the other organization's."""
    stored = StoredRun(world)
    other_dataset = world.dataset(
        organization=world.org_a, values={"tone": "calm"}, name="own-other-ds"
    )
    world.scenario(
        organization=world.org_a,
        workspace=world.a_one,
        dataset=other_dataset,
        name="own other scenario",
    )
    own_call = _graded_call(stored, stored.own, stored.own_dataset)
    eval_config = _eval_config(other_dataset._columns["tone"])

    with pytest.raises(ValueError, match="Column mapping mismatch") as refused:
        grade(eval_config, own_call)

    message = str(refused.value)
    assert "'tone'" in message
    assert "own other scenario" in message
    assert "[own]" in message
    _assert_no_victim_names(message)
    grading.assert_not_called()
    _assert_reads_scoped_to(world, world.org_a)


@pytest.mark.parametrize("grade", GRADERS)
def test_grading_an_own_call_still_reads_its_own_scenario_column(world, grading, grade):
    """Regression guard: the execution's own scenario columns still resolve to
    the call's own cell value, with a foreign id stored alongside."""
    stored = StoredRun(world)
    own_call = _graded_call(stored, stored.own, stored.own_dataset)
    eval_config = _eval_config(stored.own_dataset._columns["situation"])

    grade(eval_config, own_call)

    grading.assert_called_once()
    assert grading.call_args.kwargs["mappings"] == {"input": "own billing"}
    assert own_call.eval_outputs[str(eval_config.id)]["output"] == "Passed"
    stored.assert_victim_never_read()
