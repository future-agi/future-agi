"""A call's dataset row is read only from its own organization's datasets (T6 #7d).

``PATCH simulate/api/alk-simulate/call-executions/<id>/result/`` merges the body's
``call_metadata`` into the call caller-wins. ``reject_unknown_fields`` checks
top-level keys only (tfc/utils/api_contracts.py), so a nested
``call_metadata.row_id`` used to be stored as given. Every read of a call's
dataset row takes ``CallExecution.row_id`` and falls back to
``call_metadata["row_id"]`` when that column is null, which it is for a scenario
without a dataset. Row, Column and Cell have no workspace field, so the model
managers add no tenant filter to them, and ``all_objects`` adds no ``deleted``
filter either. A row id from another organization therefore came back on the
caller's own pages:

- ``GET simulate/test-executions/<id>/`` (TestExecutionDetailView): the session
  id from the row's metadata, and the row's cells, column names and types and
  dataset id in ``scenario_columns``.
- ``GET simulate/call-executions/<id>/`` (CallExecutionDetailView): the same,
  through the serializer's per-row fallback reads.
- The v3 results rows (``build_call_rows``): persona, goal, outcome and
  situation cells.

Ingest now accepts only a row of the call's own scenario dataset (or the row id
the call already carries, or none). The reads keep only non-deleted rows and
cells of datasets that belong to the execution's organization, so a stored
foreign row id (written before the fix) renders exactly like a row id that names
nothing.

DB-free: Row, Column and Cell live in memory and every query on them is answered
by evaluating its real WHERE tree (joined Dataset/Row/Column columns included)
against those rows. Every other model gets canned results, and pytest-django
refuses any real database access.
"""

import json
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db.models.lookups import Exact, In, IsNull
from django.db.models.query import (
    FlatValuesListIterable,
    ModelIterable,
    QuerySet,
    ValuesListIterable,
)
from django.db.models.sql.where import NothingNode, WhereNode
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, Workspace
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import (
    CallExecution,
    CallExecutionSnapshot,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.serializers.test_execution import CallExecutionDetailSerializer
from simulate.services import alk_simulate_ingestion as ingestion
from simulate.services.run_results_v3 import build_call_rows
from simulate.views.alk_simulate_ingestion import ALKSimulateIngestionViewSet
from simulate.views.run_test import CallExecutionDetailView, TestExecutionDetailView
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.utils.pagination import ExtendedPageNumberPagination

ROW = Row._meta.db_table
COLUMN = Column._meta.db_table
CELL = Cell._meta.db_table
DATASET = Dataset._meta.db_table

VICTIM = "victim"  # every string of the other organization's row contains this


# --------------------------------------------------------------------------
# In-memory model_hub_row / model_hub_column / model_hub_cell
# --------------------------------------------------------------------------


def _norm(value):
    return str(value) if isinstance(value, uuid.UUID) else value


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
    actual = _norm(columns[key])
    if isinstance(node, IsNull):  # includes RelatedIsNull
        return (actual is None) == bool(node.rhs)
    if isinstance(node, Exact):  # includes RelatedExact
        return actual is not None and actual == _norm(node.rhs)
    if isinstance(node, In):  # includes RelatedIn
        return actual in {_norm(value) for value in node.rhs}
    raise AssertionError(f"unsupported lookup {type(node).__name__} on {key}")


def _top_level_exacts(query):
    """(table, column) -> value for every Exact the whole WHERE tree must meet."""
    where = query.where
    found = {}
    if where.connector != "AND" or where.negated:
        return found
    for child in where.children:
        if isinstance(child, Exact):
            table = query.alias_map[child.lhs.alias].table_name
            found[(table, child.lhs.target.column)] = _norm(child.rhs)
    return found


class Record(SimpleNamespace):
    """One in-memory table row: its columns, its values, the model instance."""


class DatasetTables:
    def __init__(self):
        self.records = {Row: [], Column: [], Cell: []}
        self.canned = {}
        self.queries = []  # (model, query) for every Row/Column/Cell read

    # -- building ----------------------------------------------------------

    def dataset(self, organization, name):
        return Dataset(
            id=uuid.uuid4(), organization=organization, name=name, column_order=[]
        )

    def column(self, dataset, name, *, deleted=False, in_order=True):
        column = Column(
            id=uuid.uuid4(),
            dataset=dataset,
            name=name,
            data_type="text",
            source="OTHERS",
            deleted=deleted,
        )
        if in_order:
            dataset.column_order.append(str(column.id))
        self.records[Column].append(
            Record(
                obj=column,
                columns={
                    (COLUMN, "id"): column.id,
                    (COLUMN, "dataset_id"): dataset.id,
                    (COLUMN, "name"): name,
                    (COLUMN, "deleted"): deleted,
                    **self._dataset_columns(dataset),
                },
                values={"id": column.id, "name": name},
            )
        )
        return column

    def row(self, dataset, *, metadata=None, deleted=False):
        row = Row(
            id=uuid.uuid4(),
            dataset=dataset,
            order=len(self.records[Row]),
            metadata=metadata or {},
            deleted=deleted,
        )
        self.records[Row].append(
            Record(
                obj=row,
                columns={
                    (ROW, "id"): row.id,
                    (ROW, "dataset_id"): dataset.id,
                    (ROW, "deleted"): deleted,
                    **self._dataset_columns(dataset),
                },
                values={"id": row.id, "metadata": row.metadata},
            )
        )
        return row

    def cell(self, row, column, value, *, deleted=False):
        cell = Cell(
            id=uuid.uuid4(),
            dataset=row.dataset,
            row=row,
            column=column,
            value=value,
            deleted=deleted,
        )
        self.records[Cell].append(
            Record(
                obj=cell,
                columns={
                    (CELL, "id"): cell.id,
                    (CELL, "row_id"): row.id,
                    (CELL, "column_id"): column.id,
                    (CELL, "dataset_id"): row.dataset.id,
                    (CELL, "deleted"): deleted,
                    (ROW, "id"): row.id,
                    (ROW, "dataset_id"): row.dataset.id,
                    (ROW, "deleted"): row.deleted,
                    (COLUMN, "id"): column.id,
                    (COLUMN, "name"): column.name,
                    (COLUMN, "dataset_id"): column.dataset_id,
                    (COLUMN, "deleted"): column.deleted,
                    **self._dataset_columns(row.dataset),
                },
                values={
                    "id": cell.id,
                    "row_id": row.id,
                    "column_id": column.id,
                    "column__name": column.name,
                    "value": value,
                },
            )
        )
        return cell

    @staticmethod
    def _dataset_columns(dataset):
        return {
            (DATASET, "id"): dataset.id,
            (DATASET, "organization_id"): dataset.organization_id,
            (DATASET, "deleted"): False,
        }

    # -- answering ---------------------------------------------------------

    def answer(self, queryset):
        model = queryset.model
        query = queryset.query
        if model in self.records:
            self.queries.append((model, query))
            found = [
                record
                for record in self.records[model]
                if _matches(query.where, record.columns, query)
            ][query.low_mark : query.high_mark]
            iterable = queryset._iterable_class
            if iterable is ModelIterable:
                return [record.obj for record in found]
            fields = queryset._fields
            if iterable is FlatValuesListIterable:
                return [record.values[fields[0]] for record in found]
            if iterable is ValuesListIterable:
                return [
                    tuple(record.values[name] for name in fields) for record in found
                ]
            raise AssertionError(f"unsupported iterable {iterable.__name__}")
        assert model in self.canned, f"unexpected query on {model.__name__}"
        canned = self.canned[model]
        return list(canned(queryset) if callable(canned) else canned)

    def queries_on(self, *models):
        return [query for model, query in self.queries if model in models]


@pytest.fixture
def tables():
    table = DatasetTables()

    def fetch_all(queryset):
        if queryset._result_cache is not None:
            return
        queryset._result_cache = table.answer(queryset)
        queryset._prefetch_done = True

    def exists(queryset):
        clone = queryset._chain()
        fetch_all(clone)
        return bool(clone._result_cache)

    def count(queryset):
        clone = queryset._chain()
        fetch_all(clone)
        return len(clone._result_cache)

    with (
        patch.object(QuerySet, "_fetch_all", fetch_all),
        patch.object(QuerySet, "exists", exists),
        patch.object(QuerySet, "count", count),
    ):
        yield table


# --------------------------------------------------------------------------
# Two organizations, their datasets and the caller's run
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_ambient_workspace():
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.fixture
def world(tables):
    org_a = Organization(id=uuid.uuid4(), name="Org A (caller)")
    org_b = Organization(id=uuid.uuid4(), name="Org B (victim)")
    workspace = Workspace(
        id=uuid.uuid4(), name="A default", organization=org_a, is_default=True
    )

    # The caller's own scenario dataset.
    own_ds = tables.dataset(org_a, "own scenarios")
    own_cols = {
        name: tables.column(own_ds, name)
        for name in ("persona", "situation", "outcome", "use_case")
    }
    own_row = tables.row(own_ds, metadata={"session_id": "own-session"})
    tables.cell(own_row, own_cols["persona"], "{'name': 'Own Persona'}")
    tables.cell(own_row, own_cols["outcome"], "own outcome")
    tables.cell(own_row, own_cols["use_case"], "own goal")
    # A soft-deleted cell of a live own row.
    tables.cell(own_row, own_cols["situation"], "own deleted situation", deleted=True)
    # A soft-deleted own row with live cells.
    deleted_row = tables.row(
        own_ds, metadata={"session_id": "own-deleted-row-session"}, deleted=True
    )
    tables.cell(deleted_row, own_cols["persona"], "{'name': 'Deleted Row Persona'}")
    tables.cell(deleted_row, own_cols["use_case"], "deleted row goal")
    # A second live own row, the one an ingest test names.
    other_own_row = tables.row(own_ds, metadata={})

    # The other organization's dataset.
    victim_ds = tables.dataset(org_b, "victim scenarios")
    victim_cols = {
        name: tables.column(victim_ds, name)
        for name in ("persona", "situation", "outcome", "use_case", "victim_secret")
    }
    victim_row = tables.row(
        victim_ds,
        metadata={"session_id": "victim-session", "trace_id": "victim-trace"},
    )
    for name, column in victim_cols.items():
        value = (
            "{'name': 'victim persona'}"
            if name == "persona"
            else f"victim {name} value"
        )
        tables.cell(victim_row, column, value)

    run_test = RunTest(id=uuid.uuid4(), name="caller run", organization=org_a)
    execution = TestExecution(
        id=uuid.uuid4(),
        run_test=run_test,
        scenario_ids=[],
        execution_metadata={"column_order": [{"id": "status"}], "Provider": True},
        status=TestExecution.ExecutionStatus.RUNNING,
    )
    dataset_scenario = Scenarios(
        id=uuid.uuid4(), name="dataset scenario", organization=org_a, dataset=own_ds
    )
    graph_scenario = Scenarios(
        id=uuid.uuid4(), name="graph scenario", organization=org_a, dataset=None
    )

    now = timezone.now()

    def call(*, row_id=None, metadata_row_id=None, scenario=None):
        metadata = {"external_runner": "alk"}
        if metadata_row_id is not None:
            metadata["row_id"] = str(metadata_row_id)
        return CallExecution(
            id=uuid.uuid4(),
            test_execution=execution,
            scenario=scenario or graph_scenario,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            # Terminal and unscored, so the v3 scoring clock has nothing to time.
            status=CallExecution.CallStatus.FAILED,
            row_id=row_id,
            call_metadata=metadata,
            provider_call_data={},
            created_at=now,
            updated_at=now,
            started_at=now,
            ended_at=now,
            completed_at=now,
        )

    calls = SimpleNamespace(
        # The caller's own dataset row, as batch creation writes it.
        own=call(
            row_id=own_row.id, metadata_row_id=own_row.id, scenario=dataset_scenario
        ),
        # Pre-fix data: a graph scenario's call (row_id NULL) whose result PATCH
        # smuggled the other organization's row id into call_metadata.
        foreign=call(metadata_row_id=victim_row.id),
        # A row id that names nothing, the answer the foreign one must match.
        missing=call(metadata_row_id=uuid.uuid4()),
        # The caller's own row, since soft-deleted.
        deleted=call(row_id=deleted_row.id, scenario=dataset_scenario),
    )

    tables.canned.update(
        {
            SimulateEvalConfig: [],
            Scenarios: [],
            CallExecutionSnapshot: [],
        }
    )
    return SimpleNamespace(
        saved_metadata=[],  # execution_metadata as each TestExecution.save saw it
        org_a=org_a,
        org_b=org_b,
        workspace=workspace,
        own_ds=own_ds,
        own_cols=own_cols,
        own_row=own_row,
        other_own_row=other_own_row,
        deleted_row=deleted_row,
        victim_ds=victim_ds,
        victim_cols=victim_cols,
        victim_row=victim_row,
        run_test=run_test,
        execution=execution,
        dataset_scenario=dataset_scenario,
        graph_scenario=graph_scenario,
        calls=calls,
        make_call=call,
    )


def _user(organization):
    return SimpleNamespace(
        id=uuid.uuid4(),
        is_authenticated=True,
        organization=organization,
        email="member@example.com",
    )


def _bind(request, world):
    """Bind the request the way APIKeyAuthentication binds an org A member."""
    user = _user(world.org_a)
    request.organization = world.org_a
    request.workspace = world.workspace
    set_workspace_context(
        workspace=world.workspace,
        organization=world.org_a,
        user=user,
        require_workspace=True,
    )
    force_authenticate(request, user=user)
    return request


def _no_victim_data(payload):
    text = json.dumps(payload, default=str)
    assert VICTIM not in text, text


def _assert_org_scoped(tables, organization):
    """Every Row/Column/Cell read is limited to the organization, and Row/Cell
    reads to live rows (and live cells)."""
    reads = tables.queries
    assert reads, "no dataset row was read"
    for model, query in reads:
        exacts = _top_level_exacts(query)
        assert exacts.get((DATASET, "organization_id")) == str(organization.id), (
            model.__name__,
            str(query),
        )
        if model is Row:
            assert exacts.get((ROW, "deleted")) is False, str(query)
        if model is Cell:
            assert exacts.get((CELL, "deleted")) is False, str(query)
            assert exacts.get((ROW, "deleted")) is False, str(query)


# --------------------------------------------------------------------------
# (1) Ingest: call_metadata.row_id must be a row of the call's scenario dataset
# --------------------------------------------------------------------------


@pytest.fixture
def ingest_side_effects():
    """Everything ingest touches past validation, DB-free and recorded."""
    atomic = MagicMock(side_effect=lambda *a, **k: nullcontext())
    with (
        patch.object(ingestion, "transaction", SimpleNamespace(atomic=atomic)),
        patch.object(ingestion, "_apply_conversation_metrics") as metrics,
        patch.object(ingestion, "_deduct_alk_sim_cost_once") as deduct,
        patch.object(ingestion, "_roll_up_external_execution") as roll_up,
        patch.object(ingestion, "notify_simulation_update") as notify,
        patch("simulate.tasks.chat_sim.monitor_test_execution_for_chat") as monitor,
        patch.object(CallExecution, "save", autospec=True) as save,
    ):
        yield SimpleNamespace(
            atomic=atomic,
            metrics=metrics,
            deduct=deduct,
            roll_up=roll_up,
            notify=notify,
            monitor=monitor,
            save=save,
        )


def _patch_result(world, call, body):
    request = APIRequestFactory().patch(
        f"/simulate/api/alk-simulate/call-executions/{call.id}/result/",
        body,
        format="json",
    )
    _bind(request, world)
    try:
        return ALKSimulateIngestionViewSet.as_view({"patch": "result"})(
            request, call_execution_id=str(call.id)
        )
    finally:
        clear_workspace_context()


def _ingest(world, tables, call, row_id):
    tables.canned[CallExecution] = [call]
    return _patch_result(
        world,
        call,
        {"status": "cancelled", "call_metadata": {"row_id": str(row_id)}},
    )


def _assert_rejected_and_nothing_stored(response, call, before, effects):
    assert response.status_code == 400, response.data
    assert "row_id" in json.dumps(response.data)
    assert call.call_metadata == before
    assert call.status == CallExecution.CallStatus.FAILED
    effects.atomic.assert_not_called()
    effects.save.assert_not_called()
    effects.deduct.assert_not_called()
    effects.roll_up.assert_not_called()
    effects.notify.assert_not_called()
    effects.monitor.apply_async.assert_not_called()


@pytest.mark.parametrize("scenario", ["graph", "dataset"])
def test_ingest_rejects_another_orgs_row_id_and_stores_nothing(
    world, tables, ingest_side_effects, scenario
):
    call = world.make_call(
        scenario=world.graph_scenario if scenario == "graph" else world.dataset_scenario
    )
    before = dict(call.call_metadata)

    response = _ingest(world, tables, call, world.victim_row.id)

    _assert_rejected_and_nothing_stored(response, call, before, ingest_side_effects)


def test_ingest_rejects_foreign_deleted_and_made_up_row_ids_alike(
    world, tables, ingest_side_effects
):
    """Same 400 body for another org's row, an own soft-deleted row, a row id
    that names nothing and a malformed id: no existence oracle."""
    answers = []
    for row_id in (
        world.victim_row.id,
        world.deleted_row.id,
        uuid.uuid4(),
        "not-a-uuid",
    ):
        call = world.make_call(scenario=world.dataset_scenario)
        before = dict(call.call_metadata)
        response = _ingest(world, tables, call, row_id)
        _assert_rejected_and_nothing_stored(response, call, before, ingest_side_effects)
        answers.append(response.data)

    assert all(answer == answers[0] for answer in answers), answers


def test_ingest_rejects_an_own_row_of_another_dataset(
    world, tables, ingest_side_effects
):
    """The row must be in the call's own scenario dataset, not just the org's."""
    other_ds = tables.dataset(world.org_a, "another own dataset")
    other_row = tables.row(other_ds)
    call = world.make_call(scenario=world.dataset_scenario)
    before = dict(call.call_metadata)

    response = _ingest(world, tables, call, other_row.id)

    _assert_rejected_and_nothing_stored(response, call, before, ingest_side_effects)


@pytest.mark.parametrize("which", ["same-row", "other-own-row"])
def test_ingest_accepts_a_row_of_the_calls_own_scenario_dataset(
    world, tables, ingest_side_effects, which
):
    call = world.make_call(
        row_id=world.own_row.id,
        metadata_row_id=world.own_row.id,
        scenario=world.dataset_scenario,
    )
    row = world.own_row if which == "same-row" else world.other_own_row

    response = _ingest(world, tables, call, row.id)

    assert response.status_code == 200, response.data
    assert call.call_metadata["row_id"] == str(row.id)
    assert call.status == CallExecution.CallStatus.CANCELLED
    ingest_side_effects.save.assert_called()


@pytest.mark.parametrize("row_id", [None, ""])
def test_ingest_accepts_an_empty_row_id(world, tables, ingest_side_effects, row_id):
    """``_build_call_execution`` writes ``"row_id": None`` for a dataset-less
    scenario, so a client echoing call_metadata back sends it."""
    call = world.make_call()
    tables.canned[CallExecution] = [call]

    response = _patch_result(
        world, call, {"status": "cancelled", "call_metadata": {"row_id": row_id}}
    )

    assert response.status_code == 200, response.data
    assert call.call_metadata["row_id"] == row_id


def test_ingest_without_a_row_id_is_unchanged(world, tables, ingest_side_effects):
    call = world.make_call()
    tables.canned[CallExecution] = [call]

    response = _patch_result(
        world, call, {"status": "cancelled", "call_metadata": {"note": "kept"}}
    )

    assert response.status_code == 200, response.data
    assert call.call_metadata["note"] == "kept"
    assert tables.queries_on(Row) == []


# --------------------------------------------------------------------------
# (2) Reads: GET test-executions/<id>/ (sites run_test.py 2975, 2987, 3002, 3015)
# --------------------------------------------------------------------------


class ScenarioFieldsSerializer(CallExecutionDetailSerializer):
    """The real serializer, limited to the fields that read dataset rows."""

    class Meta(CallExecutionDetailSerializer.Meta):
        fields = ["id", "session_id", "scenario_columns"]


def _get_test_execution_response(world, tables, calls):
    """The whole GET test-executions/<id>/ body. The view's scenario column
    reconciliation runs for real (it reads the first call's dataset row), and
    TestExecution.save records instead of writing."""
    calls = list(calls)
    tables.canned[CallExecution] = lambda qs: (
        calls if qs._iterable_class is ModelIterable else []
    )

    def get_object(klass, *args, **kwargs):
        assert getattr(klass, "model", klass) is TestExecution
        return world.execution

    request = APIRequestFactory().get(
        f"/simulate/test-executions/{world.execution.id}/"
    )
    _bind(request, world)
    try:
        with (
            patch("simulate.views.run_test.get_object_or_404", get_object),
            patch(
                "simulate.views.run_test.CallExecutionDetailSerializer",
                ScenarioFieldsSerializer,
            ),
            patch.object(
                TestExecution,
                "save",
                autospec=True,
                side_effect=lambda execution, **kw: world.saved_metadata.append(
                    json.loads(json.dumps(execution.execution_metadata, default=str))
                ),
            ),
            patch(
                "simulate.views.run_test.reconcile_eval_column_order",
                lambda **kw: (kw["column_order"], False),
            ),
            patch.object(
                ExtendedPageNumberPagination,
                "paginate_queryset",
                lambda self, queryset, request, view=None: calls,
            ),
            patch.object(
                ExtendedPageNumberPagination,
                "get_paginated_response",
                lambda self, data: Response({"results": data}),
            ),
        ):
            response = TestExecutionDetailView.as_view()(
                request, test_execution_id=str(world.execution.id)
            )
    finally:
        clear_workspace_context()
    assert response.status_code == 200, response.data
    return response.data


def _get_test_execution(world, tables, calls):
    data = _get_test_execution_response(world, tables, calls)
    return {item["id"]: item for item in data["results"]}


def _strip_id(item):
    return {key: value for key, value in item.items() if key != "id"}


def test_execution_detail_returns_no_other_orgs_row(world, tables):
    calls = world.calls
    results = _get_test_execution(world, tables, [calls.foreign, calls.missing])

    foreign = results[str(calls.foreign.id)]
    missing = results[str(calls.missing.id)]
    assert foreign["session_id"] is None  # site 2975
    assert foreign["scenario_columns"] == {}  # sites 2987, 3002, 3015
    assert _strip_id(foreign) == _strip_id(missing)
    _no_victim_data(results)
    _assert_org_scoped(tables, world.org_a)


def test_execution_detail_drops_another_orgs_column_from_own_column_order(
    world, tables
):
    """Site 3002: a column id in the caller's own dataset's column_order that
    belongs to another organization's dataset is not read."""
    world.own_ds.column_order.append(str(world.victim_cols["victim_secret"].id))

    results = _get_test_execution(world, tables, [world.calls.own])

    columns = results[str(world.calls.own.id)]["scenario_columns"]
    assert "victim_secret" not in columns
    assert set(columns) == {"persona", "situation", "Ideal Outcome", "use_case"}
    _no_victim_data(results)
    _assert_org_scoped(tables, world.org_a)


def test_execution_detail_skips_soft_deleted_rows_and_cells(world, tables):
    calls = world.calls
    results = _get_test_execution(
        world, tables, [calls.own, calls.deleted, calls.missing]
    )

    deleted = results[str(calls.deleted.id)]
    assert deleted["session_id"] is None
    assert deleted["scenario_columns"] == {}
    assert _strip_id(deleted) == _strip_id(results[str(calls.missing.id)])
    own = results[str(calls.own.id)]["scenario_columns"]
    assert own["situation"]["value"] == ""  # its only cell is soft-deleted
    assert "deleted" not in json.dumps(results)


def test_execution_detail_still_renders_the_callers_own_row(world, tables):
    results = _get_test_execution(world, tables, [world.calls.own])

    own = results[str(world.calls.own.id)]
    assert own["session_id"] == "own-session"
    columns = own["scenario_columns"]
    assert columns["persona"]["value"] == {"name": "Own Persona"}
    assert columns["Ideal Outcome"]["value"] == "own outcome"
    assert columns["use_case"]["value"] == "own goal"
    assert columns["Ideal Outcome"]["dataset_id"] == str(world.own_ds.id)
    assert columns["Ideal Outcome"]["dataset_column_id"] == str(
        world.own_cols["outcome"].id
    )


# --------------------------------------------------------------------------
# (2) Reads: GET test-executions/<id>/ column_order. When no scenario of the
# execution has dataset columns, reconcile_scenario_column_order
# (simulate/utils/test_execution_utils.py) rebuilds the scenario columns from
# the first call's call_metadata row, and the view returns and saves the result.
# --------------------------------------------------------------------------

STATUS_ONLY = [{"id": "status"}]
OWN_COLUMN_NAMES = ["persona", "situation", "Ideal Outcome", "use_case"]


def _scenario_columns_of(column_order):
    return [
        column
        for column in column_order
        if isinstance(column, dict) and column.get("type") == "scenario_dataset_column"
    ]


@pytest.mark.parametrize("first", ["foreign", "missing"])
def test_execution_detail_column_order_lists_no_other_orgs_columns(
    world, tables, first
):
    """Another org's row id in the first call's metadata reads like a row id
    that names nothing, whichever of the two calls comes first: the column
    order, the saved execution metadata and the rows all match."""
    calls = world.calls
    order = (
        [calls.foreign, calls.missing]
        if first == "foreign"
        else [calls.missing, calls.foreign]
    )

    data = _get_test_execution_response(world, tables, order)

    assert data["column_order"] == STATUS_ONLY
    _no_victim_data(data)
    _no_victim_data(world.execution.execution_metadata)
    _no_victim_data(world.saved_metadata)
    results = {item["id"]: item for item in data["results"]}
    assert _strip_id(results[str(calls.foreign.id)]) == _strip_id(
        results[str(calls.missing.id)]
    )
    _assert_org_scoped(tables, world.org_a)


def test_execution_detail_rebuilds_away_saved_foreign_scenario_columns(world, tables):
    """A column order saved before the fix already holds the other org's column
    entries. The next GET drops them and saves the clean order, so stored data
    heals without a migration."""
    victim_column = world.victim_cols["victim_secret"]
    world.execution.execution_metadata["column_order"] = [
        {"id": "status"},
        {
            "id": "victim_secret",
            "column_name": "victim_secret",
            "visible": True,
            "data_type": "text",
            "type": "scenario_dataset_column",
            "scenario_id": None,
            "dataset_id": str(world.victim_ds.id),
            "dataset_column_ids": [str(victim_column.id)],
        },
    ]

    data = _get_test_execution_response(world, tables, [world.calls.foreign])

    assert data["column_order"] == STATUS_ONLY
    _no_victim_data(data)
    assert world.saved_metadata, "the rebuilt column order was not saved"
    assert world.saved_metadata[-1]["column_order"] == STATUS_ONLY
    _assert_org_scoped(tables, world.org_a)


def test_execution_detail_column_order_skips_a_soft_deleted_row(world, tables):
    deleted_first = world.make_call(metadata_row_id=world.deleted_row.id)

    data = _get_test_execution_response(world, tables, [deleted_first])

    assert data["column_order"] == STATUS_ONLY
    assert world.saved_metadata == []
    _assert_org_scoped(tables, world.org_a)


def test_execution_detail_column_order_drops_another_orgs_column_of_own_dataset(
    world, tables
):
    """A column id in the caller's own dataset's column_order that belongs to
    another organization's dataset is not listed."""
    world.own_ds.column_order.append(str(world.victim_cols["victim_secret"].id))

    data = _get_test_execution_response(world, tables, [world.calls.own])

    columns = _scenario_columns_of(data["column_order"])
    assert [column["column_name"] for column in columns] == OWN_COLUMN_NAMES
    _no_victim_data(data)
    _no_victim_data(world.saved_metadata)
    _assert_org_scoped(tables, world.org_a)


def test_execution_detail_column_order_still_lists_the_callers_own_columns(
    world, tables
):
    data = _get_test_execution_response(world, tables, [world.calls.own])

    columns = _scenario_columns_of(data["column_order"])
    assert [column["column_name"] for column in columns] == OWN_COLUMN_NAMES
    assert {column["dataset_id"] for column in columns} == {str(world.own_ds.id)}
    outcome = next(c for c in columns if c["column_name"] == "Ideal Outcome")
    assert outcome["dataset_column_ids"] == [str(world.own_cols["outcome"].id)]
    assert world.saved_metadata[-1]["column_order"] == data["column_order"]


def test_reconcile_reads_only_the_given_orgs_live_columns(world, tables):
    """The scenario datasets' own column_order (the normal path, no fallback)
    is read through the same scope."""
    from simulate.utils.test_execution_utils import reconcile_scenario_column_order

    set_workspace_context(
        workspace=world.workspace, organization=world.org_a, require_workspace=True
    )
    tables.canned[CallExecution] = []
    world.own_ds.column_order.append(str(world.victim_cols["victim_secret"].id))
    tables.column(world.own_ds, "soft_deleted_column", deleted=True)

    column_order, changed = reconcile_scenario_column_order(
        scenarios=[world.dataset_scenario],
        call_executions=CallExecution.objects.none(),
        column_order=[],
        organization_id=world.org_a.id,
    )

    columns = _scenario_columns_of(column_order)
    assert [column["column_name"] for column in columns] == OWN_COLUMN_NAMES
    assert changed is True
    _no_victim_data(column_order)
    assert "soft_deleted_column" not in json.dumps(column_order)
    _assert_org_scoped(tables, world.org_a)


def test_reconcile_cannot_run_without_the_organization(world, tables):
    from simulate.utils.test_execution_utils import reconcile_scenario_column_order

    tables.canned[CallExecution] = []
    with pytest.raises(TypeError):
        reconcile_scenario_column_order(
            scenarios=[],
            call_executions=CallExecution.objects.none(),
            column_order=[],
        )


# --------------------------------------------------------------------------
# (2) Reads: GET call-executions/<id>/ (site run_test.py 3781 and the
# serializer's per-row fallback, which this view always takes)
# --------------------------------------------------------------------------


def _get_call_execution(world, tables, call):
    def get_object(klass, *args, **kwargs):
        assert getattr(klass, "model", klass) is CallExecution
        return call

    request = APIRequestFactory().get(f"/simulate/call-executions/{call.id}/")
    _bind(request, world)
    try:
        with (
            patch("simulate.views.run_test.get_object_or_404", get_object),
            patch(
                "simulate.views.run_test.CallExecutionDetailSerializer",
                ScenarioFieldsSerializer,
            ),
            patch("simulate.views.run_test.build_eval_configs_map", lambda c: {}),
        ):
            response = CallExecutionDetailView.as_view()(
                request, call_execution_id=str(call.id)
            )
    finally:
        clear_workspace_context()
    assert response.status_code == 200, response.data
    return response.data


def test_call_execution_detail_returns_no_other_orgs_row(world, tables):
    foreign = _get_call_execution(world, tables, world.calls.foreign)
    missing = _get_call_execution(world, tables, world.calls.missing)

    assert foreign["session_id"] is None  # site 3781
    assert foreign["scenario_columns"] == {}  # serializer fallback
    assert _strip_id(foreign) == _strip_id(missing)
    _no_victim_data(foreign)
    _assert_org_scoped(tables, world.org_a)


def test_call_execution_detail_skips_soft_deleted_rows_and_cells(world, tables):
    deleted = _get_call_execution(world, tables, world.calls.deleted)
    missing = _get_call_execution(world, tables, world.calls.missing)
    own = _get_call_execution(world, tables, world.calls.own)

    assert deleted["session_id"] is None
    assert _strip_id(deleted) == _strip_id(missing)
    assert own["scenario_columns"]["situation"]["value"] == ""
    assert "deleted" not in json.dumps([deleted, own])


def test_call_execution_detail_still_renders_the_callers_own_row(world, tables):
    own = _get_call_execution(world, tables, world.calls.own)

    assert own["session_id"] == "own-session"
    columns = own["scenario_columns"]
    assert columns["persona"]["value"] == {"name": "Own Persona"}
    assert columns["Ideal Outcome"]["value"] == "own outcome"
    assert columns["use_case"]["value"] == "own goal"


def test_serializer_fallback_reads_only_the_calls_own_org(world, tables):
    """Every other CallExecutionDetailSerializer caller (v3 call detail, the run
    call list, the test-execution utils) passes no prefetched rows and takes
    this fallback."""
    set_workspace_context(
        workspace=world.workspace, organization=world.org_a, require_workspace=True
    )
    for context in ({}, {"rows_map": {}, "columns_by_dataset": {}, "cells_by_row": {}}):
        serializer = CallExecutionDetailSerializer(context=context)
        foreign = serializer.get_scenario_columns(world.calls.foreign)
        missing = serializer.get_scenario_columns(world.calls.missing)
        deleted = serializer.get_scenario_columns(world.calls.deleted)
        own = serializer.get_scenario_columns(world.calls.own)

        assert foreign == missing == deleted == {}
        assert own["use_case"]["value"] == "own goal"
        assert own["situation"]["value"] == ""
    _assert_org_scoped(tables, world.org_a)


# --------------------------------------------------------------------------
# (2) Reads: v3 results rows (site run_results_v3.py 361, _row_dimensions)
# --------------------------------------------------------------------------


@pytest.fixture
def v3_tables(tables):
    from simulate.models.hosted_harness import (
        HostedHarnessExecution,
        HostedHarnessJob,
        HostedHarnessScenario,
    )

    tables.canned.update(
        {HostedHarnessExecution: [], HostedHarnessJob: [], HostedHarnessScenario: []}
    )
    return tables


def _v3_rows(world, calls):
    set_workspace_context(
        workspace=world.workspace, organization=world.org_a, require_workspace=True
    )
    rows, _ = build_call_rows(
        world.execution,
        list(calls),
        [],
        set(),
        {},
        runnable_ids=set(),
        now=timezone.now(),
    )
    return {row["id"]: row for row in rows}


_ROW_FIELDS = (
    "persona",
    "persona_details",
    "goal",
    "ideal_outcome",
    "scenario_details",
)


def test_v3_results_return_no_other_orgs_cells(world, v3_tables):
    calls = world.calls
    rows = _v3_rows(world, [calls.foreign, calls.missing])

    foreign = rows[str(calls.foreign.id)]
    missing = rows[str(calls.missing.id)]
    assert {key: foreign[key] for key in _ROW_FIELDS} == {
        key: missing[key] for key in _ROW_FIELDS
    }
    _no_victim_data(rows)
    _assert_org_scoped(v3_tables, world.org_a)


def test_v3_results_skip_soft_deleted_rows_and_cells(world, v3_tables):
    calls = world.calls
    rows = _v3_rows(world, [calls.own, calls.deleted])

    deleted = rows[str(calls.deleted.id)]
    assert deleted["persona"] is None
    assert deleted["goal"] == world.dataset_scenario.name
    assert rows[str(calls.own.id)]["scenario_details"] is None
    assert "deleted" not in json.dumps(rows, default=str)


def test_v3_results_still_render_the_callers_own_row(world, v3_tables):
    rows = _v3_rows(world, [world.calls.own])

    own = rows[str(world.calls.own.id)]
    assert own["persona"] == "Own Persona"
    assert own["goal"] == "own goal"
    assert own["ideal_outcome"] == "own outcome"


def test_row_dimensions_read_only_the_given_orgs_live_cells(world, tables):
    """The v3 scan's goal fallback (run_results_v3_scan) reads through
    ``_row_dimensions`` with no calls, only row ids, and the execution, the
    same way build_call_rows does."""
    from simulate.services.run_results_v3 import _row_dimensions

    set_workspace_context(
        workspace=world.workspace, organization=world.org_a, require_workspace=True
    )
    row_ids = {
        str(world.own_row.id),
        str(world.deleted_row.id),
        str(world.victim_row.id),
        str(uuid.uuid4()),
    }

    dimensions = _row_dimensions(
        [], row_ids, names=("use_case", "goal"), execution=world.execution
    )

    assert dict(dimensions) == {str(world.own_row.id): {"use_case": "own goal"}}
    _assert_org_scoped(tables, world.org_a)


def test_row_dimensions_cannot_be_read_without_the_execution():
    from simulate.services.run_results_v3 import _row_dimensions

    with pytest.raises(TypeError):
        _row_dimensions([], {str(uuid.uuid4())})


def test_no_organization_reads_no_rows(world, tables):
    from simulate.utils.scenario_rows import org_cells, org_columns, org_rows

    set_workspace_context(
        workspace=world.workspace, organization=world.org_a, require_workspace=True
    )

    assert list(org_rows(None)) == []
    assert list(org_columns(None)) == []
    assert list(org_cells(None)) == []


# --------------------------------------------------------------------------
# (2) Reads: the eval playground's call context
# (model_hub/utils/eval_playground_call_context.py). POST model_hub/eval-playground/
# with the caller's own call puts the call's scenario columns into the
# evaluator's call context ({{call.scenario.columns.X}}, code-eval kwargs).
# --------------------------------------------------------------------------


def _playground_scenario(world, call):
    from model_hub.utils.eval_playground_call_context import (
        build_eval_playground_scenario_context,
    )

    set_workspace_context(
        workspace=world.workspace, organization=world.org_a, require_workspace=True
    )
    return build_eval_playground_scenario_context(call)


def test_eval_playground_context_returns_no_other_orgs_cells(world, tables):
    foreign = _playground_scenario(world, world.calls.foreign)
    missing = _playground_scenario(world, world.calls.missing)

    assert foreign == missing
    assert foreign["columns"] == {}
    _no_victim_data(foreign)
    _assert_org_scoped(tables, world.org_a)


def test_eval_playground_context_prefers_the_calls_own_row_id(world, tables):
    """A pre-fix result PATCH could overwrite call_metadata.row_id on a dataset
    scenario's call. The call's own row_id column wins, as it does for the
    detail views, so the caller sees their own row and nothing of the other."""
    overwritten = world.make_call(
        row_id=world.own_row.id,
        metadata_row_id=world.victim_row.id,
        scenario=world.dataset_scenario,
    )

    context = _playground_scenario(world, overwritten)

    assert context == _playground_scenario(world, world.calls.own)
    _no_victim_data(context)
    _assert_org_scoped(tables, world.org_a)


def test_eval_playground_context_skips_soft_deleted_rows_and_cells(world, tables):
    in_metadata = world.make_call(
        metadata_row_id=world.deleted_row.id, scenario=world.dataset_scenario
    )
    missing = world.make_call(
        metadata_row_id=uuid.uuid4(), scenario=world.dataset_scenario
    )

    deleted = _playground_scenario(world, in_metadata)
    in_column = _playground_scenario(world, world.calls.deleted)
    own = _playground_scenario(world, world.calls.own)

    assert deleted == in_column == _playground_scenario(world, missing)
    assert deleted["columns"] == {}
    assert own["columns"]["situation"] == ""  # its only cell is soft-deleted
    assert "deleted" not in json.dumps([deleted, own])
    _assert_org_scoped(tables, world.org_a)


def test_eval_playground_context_still_renders_the_callers_own_row(world, tables):
    own = _playground_scenario(world, world.calls.own)

    assert own["name"] == world.dataset_scenario.name
    assert own["columns"]["persona"] == {"name": "Own Persona"}
    assert own["columns"]["outcome"] == "own outcome"
    assert own["columns"]["use_case"] == "own goal"
