"""Reruns of classification, extraction, JSON-extraction, Python-code and API
columns reach a worker activity that accepts them (T6 #1b, release-lead
ruling).

develop_dataset.py and dynamic_columns.py both define these activities under
the same drop-in names. The registry keeps one function per name: whichever
module the process imports last. Which module that is depends on the install:

- Separate worker containers (the distributed install) import the activity
  modules in tfc/temporal/common/registry.py TEMPORAL_ACTIVITY_MODULES order,
  so develop_dataset's copies win.
- The standalone install runs the worker inside the API process
  (deploy/standalone/supervisord.conf FI_EMBEDDED_TEMPORAL_WORKER=true).
  Importing tfc.asgi builds the URL graph first (warm_http_urlconf), and
  model_hub/urls.py imports develop_dataset and then dynamic_columns, so
  dynamic_columns' copies win.

dynamic_columns' RerunOperationView dispatches every rerun with a trailing
positional ``is_rerun=True``, and the develop_dataset copies took no
``is_rerun``. So in the distributed install every rerun of these five column
types died in the worker with a TypeError, after RerunOperationView had already
cleared every cell of the column to RUNNING with value None.

Both registry probes run the real registration path in a fresh interpreter
(offline: tfc.settings.test, outbound sockets refused), so each winner is the
install's, not this test process's import order. The rerun tests capture the
args from the real ``_rerun_*`` call sites and hand them to each install's
winner: a rerun must store the same cells whichever install runs it. DB-free:
the ORM is replaced with in-memory fakes.
"""

import importlib
import inspect
import json
import os
import socket
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import transaction

from model_hub.models.choices import CellStatus, StatusType
from model_hub.services import selective_rerun
from model_hub.tests.rerun_fixtures import (
    MemoryQuerySet,
    generation_orm,
    isolated_transactions,
)
from model_hub.views import develop_dataset, dynamic_columns
from model_hub.views.utils import rerun_cells

BACKEND_DIR = Path(__file__).resolve().parents[2]

ACTIVITIES = [
    "classify_column_async",
    "extract_async",
    "extract_json_async",
    "execute_python_code_async",
    "add_api_column_async",
]

DATASET_ID = "00000000-0000-0000-0000-0000000001d1"
SOURCE_COLUMN_ID = "00000000-0000-0000-0000-0000000001c0"
TARGET_COLUMN_ID = "00000000-0000-0000-0000-0000000001c1"
ROW_ID = "00000000-0000-0000-0000-0000000001e1"
PUBLIC_ROW_ERROR = "Unable to process this row. Please try again."
SENSITIVE_ERROR = "sensitive-request-sentinel"
MISSING_SOURCE_ERROR = "Source cell is missing for this row."
ORG_ID = "00000000-0000-0000-0000-0000000001a1"
WORKSPACE_ID = "00000000-0000-0000-0000-0000000001b1"
API_CONFIG = {
    "url": "http://api.fixture.test/lookup",
    "method": "GET",
    "output_type": "string",
    "params": {},
    "headers": {},
    "body": {},
}


# --- the registry of each install --------------------------------------------

# Runs in a fresh interpreter: the activity modules must be imported in the
# install's order, which this pytest process has already disturbed.
_PROBE = r"""
import inspect, json, os, socket, sys, types

def _refuse(*args, **kwargs):
    raise OSError("network refused in the registry probe")

socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse
socket.getaddrinfo = _refuse
try:
    import sentence_transformers  # noqa: F401
except ImportError:
    stub = types.ModuleType("sentence_transformers")
    stub.SentenceTransformer = object
    sys.modules["sentence_transformers"] = stub

install = sys.argv[2]
if install == "standalone":
    # The standalone app process: granian imports tfc.asgi, which sets up
    # Django and builds the URL graph; the embedded worker registers the
    # activities later, at lifespan startup.
    import tfc.asgi  # noqa: F401
    from tfc.temporal import embedded

    embedded_worker = embedded.enabled()
else:
    # A separate worker container.
    import django

    django.setup()
    embedded_worker = None

from tfc.temporal.common import registry
from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

registry._ensure_activities_registered()

names = json.loads(sys.argv[1])
order = list(sys.modules)
report = {"import_order": {}, "activities": {}, "embedded_worker": embedded_worker}
for module_name in ("model_hub.views.dynamic_columns", "model_hub.views.develop_dataset"):
    report["import_order"][module_name] = (
        order.index(module_name) if module_name in sys.modules else None
    )
for name in names:
    entry = _ACTIVITY_REGISTRY[name]
    func = entry["func"]
    module = sys.modules[func.__module__]
    report["activities"][name] = {
        "module": func.__module__,
        "is_module_function": getattr(module, name)._original_func is func,
        "signature": str(inspect.signature(func)),
        "queue": entry["queue"],
    }
print("REGISTRY-PROBE " + json.dumps(report))
"""


def _probe_registry(install):
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "tfc.settings.test",
        "PYTHONDONTWRITEBYTECODE": "1",
        "UV_OFFLINE": "1",
        "PIP_NO_INDEX": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    if install == "standalone":
        # deploy/standalone/supervisord.conf, the app program.
        env["FI_EMBEDDED_TEMPORAL_WORKER"] = "true"
    else:
        env.pop("FI_EMBEDDED_TEMPORAL_WORKER", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _PROBE,
            json.dumps([*ACTIVITIES, "conditional_column_async"]),
            install,
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    lines = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith("REGISTRY-PROBE ")
    ]
    assert completed.returncode == 0 and lines, completed.stdout + completed.stderr
    return json.loads(lines[-1].removeprefix("REGISTRY-PROBE "))


@pytest.fixture(scope="module")
def worker_registry():
    """The function a separate worker container registers for each name."""
    return _probe_registry("worker")


@pytest.fixture(scope="module")
def standalone_registry():
    """The function the standalone install's embedded worker registers."""
    return _probe_registry("standalone")


INSTALLS = ["worker", "standalone"]


@pytest.fixture(params=INSTALLS)
def install_winner(request):
    """Each install's registered function: ``install_winner(name)``."""
    registry = request.getfixturevalue(f"{request.param}_registry")
    return lambda name: _winner(registry, name)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Nothing here may reach a model, an API or a database over the network.
    def refuse(*args, **kwargs):
        raise OSError("network refused in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def _winner(worker_registry, name):
    module = importlib.import_module(worker_registry["activities"][name]["module"])
    return getattr(module, name)._original_func


@pytest.mark.parametrize("name", ACTIVITIES)
def test_worker_registry_runs_the_develop_dataset_copy(worker_registry, name):
    # Both modules import cleanly in the worker's order (a failed import of
    # develop_dataset would hand the name to dynamic_columns instead), and the
    # registered function is develop_dataset's.
    order = worker_registry["import_order"]
    assert None not in order.values(), order
    winner = worker_registry["activities"][name]
    assert winner["module"] == "model_hub.views.develop_dataset", winner
    assert winner["is_module_function"] is True, winner


@pytest.mark.parametrize("name", ACTIVITIES)
def test_standalone_registry_runs_the_dynamic_columns_copy(standalone_registry, name):
    # The embedded worker is on, both modules imported, and the registered
    # function is dynamic_columns' own: develop_dataset's copies never run in
    # the standalone install.
    assert standalone_registry["embedded_worker"] is True
    order = standalone_registry["import_order"]
    assert None not in order.values(), order
    winner = standalone_registry["activities"][name]
    assert winner["module"] == "model_hub.views.dynamic_columns", winner
    assert winner["is_module_function"] is True, winner


@pytest.mark.parametrize("name", ACTIVITIES)
def test_worker_registry_winner_accepts_a_rerun(worker_registry, name):
    winner = worker_registry["activities"][name]
    assert "is_rerun" in inspect.signature(_winner(worker_registry, name)).parameters, (
        winner
    )


@pytest.mark.parametrize("name", ACTIVITIES)
def test_both_copies_of_a_shared_activity_name_take_the_same_args(name):
    # The worker keeps one function per activity name; either copy must
    # accept every call the other module dispatches.
    older, current = getattr(develop_dataset, name), getattr(dynamic_columns, name)
    assert older._activity_name == current._activity_name == name
    assert inspect.signature(older._original_func) == inspect.signature(
        current._original_func
    )


# --- the rerun call sites ----------------------------------------------------

RERUNS = {
    # activity name: (RerunOperationView method, stored column metadata)
    "classify_column_async": (
        "_rerun_classification",
        {
            "column_id": SOURCE_COLUMN_ID,
            "labels": ["positive", "negative"],
            "language_model_id": "gpt-4o",
            "concurrency": 2,
        },
    ),
    "extract_async": (
        "_rerun_entity_extraction",
        {
            "column_id": SOURCE_COLUMN_ID,
            "instruction": "Find the person's name",
            "language_model_id": "gpt-4o",
            "concurrency": 2,
        },
    ),
    "extract_json_async": (
        "_rerun_json_extraction",
        {"column_id": SOURCE_COLUMN_ID, "json_key": "answer", "concurrency": 2},
    ),
    "execute_python_code_async": (
        "_rerun_python_code",
        {"code": "def main(**kwargs):\n    return 1\n", "concurrency": 2},
    ),
    "add_api_column_async": ("_rerun_api_call", {**API_CONFIG, "concurrency": 2}),
}


def _rerun_dispatch(name):
    """The args dynamic_columns' rerun call site sends for ``name``."""
    method, metadata = RERUNS[name]
    column = SimpleNamespace(id=TARGET_COLUMN_ID)
    recorder = MagicMock(name=f"{name}.delay")
    view = dynamic_columns.RerunOperationView()
    view.row_ids = [ROW_ID]
    with (
        patch.object(getattr(dynamic_columns, name), "delay", recorder),
        isolated_transactions(),
    ):
        with transaction.atomic():
            response = getattr(view, method)(column, metadata, DATASET_ID)
            if name != "execute_python_code_async":
                recorder.assert_not_called()
    assert response.status_code == 200, response.data
    recorder.assert_called_once()
    args, kwargs = recorder.call_args
    # The legacy positional prefix is unchanged; selected rows travel separately.
    assert kwargs == (
        {} if name == "execute_python_code_async" else {"row_ids": [ROW_ID]}
    )
    assert args[-1] is True
    return args, kwargs


@pytest.mark.parametrize("name", ACTIVITIES)
def test_rerun_call_site_sends_one_more_arg_than_a_create(name):
    # Pins the shape the rerun tests below hand to the worker's function.
    args, kwargs = _rerun_dispatch(name)
    signature = inspect.signature(getattr(dynamic_columns, name)._original_func)
    assert len(args) + len(kwargs) == len(signature.parameters)
    assert signature.bind(*args, **kwargs).arguments["is_rerun"] is True


# --- in-memory ORM -----------------------------------------------------------


# Shared query support includes row scope and multi-field fingerprint reads.
_QuerySet = MemoryQuerySet

@contextmanager
def _activity_runtime(
    *,
    source_value="",
    existing_cell=True,
    helper_reason=None,
    helper_error=None,
    source_state="active",
    additional_source_row=False,
):
    """One dataset with one row, one source cell and (optionally) the
    destination cell a rerun updates, for both module copies.

    The cell store is stateful: a cell an activity creates is in the next
    lookup of its column. ``helper_reason`` makes the classification,
    extraction and Python-code row helpers report that failure instead of a
    result; ``helper_error`` makes them raise it.
    """
    dataset = SimpleNamespace(
        id=DATASET_ID,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        organization=SimpleNamespace(id=ORG_ID),
        workspace=None,
    )
    row = SimpleNamespace(
        id=ROW_ID, dataset=dataset, dataset_id=DATASET_ID, deleted=False, order=0
    )
    rows_by_id = {ROW_ID: row}
    written = SimpleNamespace(saved=[], bulk_updated=[], bulk_created=[])

    class FakeCell:
        # Cell.status defaults to PASS when the activity passes none.
        status = CellStatus.PASS.value
        value_infos = None
        value = None
        deleted = False

        def __init__(self, **fields):
            self.__dict__.update(fields)
            # As on a model instance: row and row_id describe the same row.
            if "row" in fields:
                self.row_id = fields["row"].id
            elif "row_id" in fields:
                self.row = rows_by_id[str(fields["row_id"])]
            if "column" in fields:
                self.column_id = fields["column"].id
            self.dataset_id = fields.get("dataset_id", DATASET_ID)
            self.id = f"{self.column_id}:{self.row_id}"

        def save(self, *args, **kwargs):
            written.saved.append(self)
            # Saving a new cell inserts it.
            column_cells = cells_by_column[str(getattr(self, "column_id", ""))]
            if self not in column_cells:
                column_cells.append(self)

    source = FakeCell(row=row, column_id=SOURCE_COLUMN_ID, value=source_value)
    # RerunOperationView clears every cell of the column before dispatch.
    target = FakeCell(
        row=row,
        column_id=TARGET_COLUMN_ID,
        value=None,
        value_infos=json.dumps({}),
        status=CellStatus.RUNNING.value,
    )
    source.deleted = source_state == "deleted"
    cells_by_column = {
        SOURCE_COLUMN_ID: [] if source_state == "missing" else [source],
        TARGET_COLUMN_ID: [target] if existing_cell else [],
    }
    if additional_source_row:
        other_row = SimpleNamespace(
            id="00000000-0000-0000-0000-0000000001e2",
            dataset=dataset, dataset_id=DATASET_ID, deleted=False, order=1,
        )
        rows_by_id[other_row.id] = other_row
        cells_by_column[SOURCE_COLUMN_ID].append(
            FakeCell(row=other_row, column_id=SOURCE_COLUMN_ID, value=source_value)
        )

    class FakeCellManager:
        def filter(self, **lookup):
            # POST now scopes by column object, dataset and selected row IDs.
            return _QuerySet(
                cell for column_cells in cells_by_column.values() for cell in column_cells
            ).filter(**lookup)

        def bulk_update(self, cells, fields, **kwargs):
            written.bulk_updated.extend(cells)

        def bulk_create(self, cells, **kwargs):
            written.bulk_created.extend(cells)
            for cell in cells:
                cells_by_column[str(cell.column_id)].append(cell)

    FakeCell.objects = FakeCellManager()
    column_model = MagicMock(name="Column")
    row_model = MagicMock(name="Row")
    row_model.objects.filter.side_effect = lambda **lookup: _QuerySet(
        rows_by_id.values()
    ).filter(**lookup)
    dataset_model = MagicMock(name="Dataset")
    dataset_model.no_workspace_objects.get.return_value = dataset
    dataset_model.objects.get.return_value = dataset

    tracked_column = SimpleNamespace(
        id=TARGET_COLUMN_ID, dataset=dataset, dataset_id=DATASET_ID,
        source="classify", metadata={"column_id": SOURCE_COLUMN_ID},
        name="Result", deleted=False, status=StatusType.COMPLETED.value,
        save=MagicMock(),
    )
    input_column = SimpleNamespace(
        id=SOURCE_COLUMN_ID, dataset_id=DATASET_ID, name="Input", deleted=False
    )
    helpers = SimpleNamespace(calls=[])

    def classify(view, cell, labels, model):
        helpers.calls.append(("classify", cell.value, labels, model))
        if helper_error:
            raise helper_error
        if helper_reason:
            return None, {"reason": helper_reason}
        return "positive", {}

    def extract(view, cell, instruction, model):
        helpers.calls.append(("extract", cell.value, instruction, model))
        if helper_error:
            raise helper_error
        if helper_reason:
            return None, {"reason": helper_reason}
        return "Ada Lovelace", {}

    def execute_python_code(view, row, code):
        helpers.calls.append(("python", row, code))
        if helper_error:
            raise helper_error
        if helper_reason:
            return None, {"reason": helper_reason}
        return "3", {}

    def api_call(view, cell, config, *, dataset=None):
        helpers.calls.append(("api_call", cell.row, config, dataset))
        return "pong", {"response_status": 200}

    with ExitStack() as stack:
        for module in (dynamic_columns, develop_dataset):
            for attr, value in (
                ("Cell", FakeCell),
                ("Column", column_model),
                ("Row", row_model),
                ("Dataset", dataset_model),
                ("wrap_for_thread", lambda func: func),
            ):
                stack.enter_context(patch.object(module, attr, value))
            stack.enter_context(
                patch.object(module.ClassifyColumnView, "_classify_cell", classify)
            )
            stack.enter_context(
                patch.object(module.ExtractEntitiesView, "_extract_entities", extract)
            )
            stack.enter_context(
                patch.object(
                    module.ExecutePythonCodeView,
                    "_execute_python_code",
                    execute_python_code,
                )
            )
        snapshots = stack.enter_context(generation_orm(
            column=tracked_column,
            source_columns=[input_column],
            rows=lambda: rows_by_id.values(),
            cells=lambda: [cell for values in cells_by_column.values() for cell in values],
            cell_factory=FakeCell,
            on_column_update=column_model.objects.filter.return_value.update,
        ))
        # The shared helper that gives every rerun row a cell.
        stack.enter_context(patch.object(rerun_cells, "Cell", FakeCell))
        # develop_dataset's AddApiColumnView._make_api_call delegates here.
        stack.enter_context(
            patch.object(dynamic_columns.AddApiColumnView, "_make_api_call", api_call)
        )
        yield SimpleNamespace(
            written=written,
            source=source,
            source_cells=cells_by_column[SOURCE_COLUMN_ID],
            target=target,
            target_cells=lambda: list(cells_by_column[TARGET_COLUMN_ID]),
            row=row,
            dataset=dataset,
            column_model=column_model,
            row_model=row_model,
            helpers=helpers,
            tracked_column=tracked_column,
            snapshots=snapshots,
            rows=lambda: list(rows_by_id.values()),
        )


def _assert_column_completed(runtime):
    runtime.column_model.objects.filter.return_value.update.assert_called_with(
        status=StatusType.COMPLETED.value
    )


def _value_infos(cell):
    infos = cell.value_infos
    return json.loads(infos) if isinstance(infos, str) else infos


def _only_target_cell(runtime):
    cells = runtime.target_cells()
    assert len(cells) == 1, [vars(cell) for cell in cells]
    return cells[0]


# --- reruns through the worker's function ------------------------------------


def test_classification_rerun_updates_the_existing_cell(worker_registry):
    args, kwargs = _rerun_dispatch("classify_column_async")
    with _activity_runtime(source_value="I loved it") as runtime:
        _winner(worker_registry, "classify_column_async")(*args, **kwargs)

    assert runtime.helpers.calls == [
        ("classify", "I loved it", ["positive", "negative"], "gpt-4o")
    ]
    assert runtime.written.bulk_updated == [runtime.target]
    assert runtime.written.bulk_created == []
    assert (runtime.target.value, runtime.target.status) == (
        "positive",
        CellStatus.PASS.value,
    )
    _assert_column_completed(runtime)


def test_entity_extraction_rerun_updates_the_existing_cell(worker_registry):
    args, kwargs = _rerun_dispatch("extract_async")
    with _activity_runtime(source_value="Ada wrote the notes") as runtime:
        _winner(worker_registry, "extract_async")(*args, **kwargs)

    assert runtime.helpers.calls == [
        ("extract", "Ada wrote the notes", "Find the person's name", "gpt-4o")
    ]
    assert runtime.written.bulk_updated == [runtime.target]
    assert runtime.written.bulk_created == []
    assert (runtime.target.value, runtime.target.status) == (
        "Ada Lovelace",
        CellStatus.PASS.value,
    )
    _assert_column_completed(runtime)


def test_json_extraction_rerun_updates_the_existing_cell(worker_registry):
    args, kwargs = _rerun_dispatch("extract_json_async")
    # A value both modules' parsers read the same way.
    with _activity_runtime(source_value="{'answer': 42}") as runtime:
        _winner(worker_registry, "extract_json_async")(*args, **kwargs)

    assert runtime.target_cells() == [runtime.target]
    assert runtime.written.bulk_created == []
    assert (runtime.target.value, runtime.target.status) == (
        "42",
        CellStatus.PASS.value,
    )
    _assert_column_completed(runtime)


def test_api_column_rerun_updates_the_existing_cell(worker_registry):
    args, kwargs = _rerun_dispatch("add_api_column_async")
    with _activity_runtime() as runtime:
        _winner(worker_registry, "add_api_column_async")(*args, **kwargs)

    assert runtime.helpers.calls == [
        ("api_call", runtime.row, args[0], runtime.dataset)
    ]
    assert runtime.written.bulk_updated == [runtime.target]
    assert runtime.written.bulk_created == []
    assert (runtime.target.value, runtime.target.status) == (
        "pong",
        CellStatus.PASS.value,
    )
    assert json.loads(runtime.target.value_infos) == {"response_status": 200}
    _assert_column_completed(runtime)


# A rerun row may have no destination cell: an earlier run that raised for the
# row wrote none, or the row's source cell came later. RerunOperationView only
# clears the cells that exist. Such a row must end the way a row with a cell
# does: its result as a PASS cell, or None as an ERROR cell with the reason.
# Never a PASS cell that drops the result or hides the reason, and never no
# cell at all. A row whose processing raises is an ERROR cell carrying the
# controlled public reason (never raw exception details), never a cell left RUNNING
# after RerunOperationView's clear. Each rule holds in both installs.

DESTINATION = pytest.mark.parametrize(
    "existing_cell", [True, False], ids=["existing-cell", "missing-cell"]
)

LLM_RERUNS = {
    # activity name: (source cell value, the helper's result)
    "classify_column_async": ("I loved it", "positive"),
    "extract_async": ("Ada wrote the notes", "Ada Lovelace"),
}


@DESTINATION
@pytest.mark.parametrize("name", LLM_RERUNS)
def test_llm_rerun_stores_the_result_for_every_row(install_winner, name, existing_cell):
    source_value, result = LLM_RERUNS[name]
    args, kwargs = _rerun_dispatch(name)
    with _activity_runtime(
        source_value=source_value, existing_cell=existing_cell
    ) as runtime:
        install_winner(name)(*args, **kwargs)

    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell)) == (
        result,
        CellStatus.PASS.value,
        {},
    )
    assert cell.row is runtime.row
    _assert_column_completed(runtime)


@DESTINATION
@pytest.mark.parametrize("name", LLM_RERUNS)
def test_llm_rerun_failure_is_an_error_cell_for_every_row(
    install_winner, name, existing_cell
):
    source_value, _ = LLM_RERUNS[name]
    args, kwargs = _rerun_dispatch(name)
    with _activity_runtime(
        source_value=source_value,
        existing_cell=existing_cell,
        helper_reason="model unavailable",
    ) as runtime:
        install_winner(name)(*args, **kwargs)

    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell)) == (
        None,
        CellStatus.ERROR.value,
        {"reason": "model unavailable"},
    )
    _assert_column_completed(runtime)


@DESTINATION
@pytest.mark.parametrize("name", LLM_RERUNS)
def test_llm_rerun_row_exception_is_an_error_cell_for_every_row(
    install_winner, name, existing_cell
):
    # The row helper raises instead of returning a reason. The row must not
    # stay RUNNING with value None after RerunOperationView's clear.
    source_value, _ = LLM_RERUNS[name]
    args, kwargs = _rerun_dispatch(name)
    with _activity_runtime(
        source_value=source_value,
        existing_cell=existing_cell,
        helper_error=RuntimeError(SENSITIVE_ERROR),
    ) as runtime:
        install_winner(name)(*args, **kwargs)

    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell)) == (
        None,
        CellStatus.ERROR.value,
        {"reason": PUBLIC_ROW_ERROR},
    )
    _assert_column_completed(runtime)


@pytest.mark.parametrize("name", [*LLM_RERUNS, "extract_json_async"])
def test_a_retried_rerun_does_not_create_a_second_cell(install_winner, name):
    # Temporal retries an activity that times out or loses its worker; the
    # retry must refill the row's one cell, not add another.
    args, kwargs = _rerun_dispatch(name)
    source_value = LLM_RERUNS.get(name, ("{'answer': 42}",))[0]
    with _activity_runtime(source_value=source_value, existing_cell=False) as runtime:
        install_winner(name)(*args, **kwargs)
        install_winner(name)(*args, **kwargs)

    cell = _only_target_cell(runtime)
    assert cell.status == CellStatus.PASS.value
    assert cell.value == LLM_RERUNS.get(name, (None, "42"))[1]


# JSON sources and the value a new JSON column has always stored for key
# "answer" on the worker. Standard JSON parses first (true/false/null anywhere
# in the document, f4bf60cb88); a Python literal still parses; a missing key or
# a null value is a blank PASS cell.
JSON_SOURCES = {
    "standard-json-with-a-boolean": ('{"answer": 42, "ok": true}', "42"),
    "standard-json-with-a-null": ('{"answer": "yes", "meta": null}', "yes"),
    "python-literal": ("{'answer': 42}", "42"),
    "missing-key": ('{"other": 1}', None),
    "null-value": ('{"answer": null}', None),
}


def _new_json_column(worker_registry, source_value):
    with _activity_runtime(source_value=source_value, existing_cell=False) as runtime:
        _winner(worker_registry, "extract_json_async")(
            SOURCE_COLUMN_ID, "answer", 2, DATASET_ID, TARGET_COLUMN_ID
        )
    _assert_column_completed(runtime)
    return _only_target_cell(runtime)


def _rerun_json_column(worker_registry, source_value, existing_cell):
    args, kwargs = _rerun_dispatch("extract_json_async")
    with _activity_runtime(
        source_value=source_value, existing_cell=existing_cell
    ) as runtime:
        _winner(worker_registry, "extract_json_async")(*args, **kwargs)
    _assert_column_completed(runtime)
    return _only_target_cell(runtime)


def _outcome(cell):
    return cell.value, cell.status, (_value_infos(cell) or {}).get("reason")


@pytest.mark.parametrize(
    "source_value,value", JSON_SOURCES.values(), ids=list(JSON_SOURCES)
)
def test_new_json_column_stores_what_it_always_has(
    worker_registry, source_value, value
):
    cell = _new_json_column(worker_registry, source_value)
    assert (cell.value, cell.status) == (value, CellStatus.PASS.value)


@DESTINATION
@pytest.mark.parametrize(
    "source_value,value", JSON_SOURCES.values(), ids=list(JSON_SOURCES)
)
def test_json_rerun_stores_the_value_for_every_row(
    worker_registry, source_value, value, existing_cell
):
    cell = _rerun_json_column(worker_registry, source_value, existing_cell)
    assert (cell.value, cell.status, _value_infos(cell) or {}) == (
        value,
        CellStatus.PASS.value,
        {},
    )


@DESTINATION
@pytest.mark.parametrize(
    "source_value",
    [
        *(source for source, _ in JSON_SOURCES.values()),
        "not json",
        '{"answer": {"nested": [1, 2]}, "ok": false}',
    ],
)
def test_json_rerun_stores_the_cell_a_new_column_would(
    worker_registry, source_value, existing_cell
):
    # Whatever the worker's parser makes of a source (including one it cannot
    # read), a rerun stores the same outcome a new column does.
    new = _new_json_column(worker_registry, source_value)
    rerun = _rerun_json_column(worker_registry, source_value, existing_cell)
    assert _outcome(rerun) == _outcome(new)


@DESTINATION
def test_json_rerun_stores_a_value_both_parsers_read_for_every_row(
    install_winner, existing_cell
):
    # The two installs parse JSON differently (open release-lead decision); a
    # Python-literal source reads the same in both, so the rerun rule shows.
    args, kwargs = _rerun_dispatch("extract_json_async")
    with _activity_runtime(
        source_value="{'answer': 42}", existing_cell=existing_cell
    ) as runtime:
        install_winner("extract_json_async")(*args, **kwargs)

    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell) or {}) == (
        "42",
        CellStatus.PASS.value,
        {},
    )
    _assert_column_completed(runtime)


@DESTINATION
def test_json_rerun_failure_is_an_error_cell_for_every_row(
    install_winner, existing_cell
):
    def unreadable(view, cell, json_key):
        raise ValueError(SENSITIVE_ERROR)

    args, kwargs = _rerun_dispatch("extract_json_async")
    with ExitStack() as stack:
        for module in (dynamic_columns, develop_dataset):
            stack.enter_context(
                patch.object(module.ExtractJsonColumnView, "_process_cell", unreadable)
            )
        runtime = stack.enter_context(
            _activity_runtime(
                source_value="{'answer': 42}", existing_cell=existing_cell
            )
        )
        install_winner("extract_json_async")(*args, **kwargs)

    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell)) == (
        None,
        CellStatus.ERROR.value,
        {"reason": PUBLIC_ROW_ERROR},
    )
    _assert_column_completed(runtime)


def test_worker_json_parser_logs_an_unreadable_source_instead_of_raising():
    # develop_dataset's parser returns None for a source it cannot read, and
    # logs why. Its log call passed the error positionally to a structlog
    # logger, which raised TypeError from the except branch: the row became an
    # ERROR cell whose reason was 'not all arguments converted during string
    # formatting' instead of the blank cell the branch returns.
    view = develop_dataset.ExtractJsonColumnView()
    for source_value in ("not json", "[1, 2]"):
        assert view._process_cell(SimpleNamespace(value=source_value), "answer") is None


def test_worker_new_json_column_stores_an_unreadable_source_as_a_blank_cell(
    worker_registry,
):
    cell = _new_json_column(worker_registry, "not json")
    assert _outcome(cell) == (None, CellStatus.PASS.value, None)


# Custom code is refused before any read (Capability contract), so the
# Python-code row path is reachable only if that refusal is lifted. These tests
# patch the refusal out to hold that path to the same rerun rule; the live
# behaviour is pinned by test_python_code_rerun_ends_as_the_capability_refusal.
PYTHON_OUTCOMES = {
    # name: (_activity_runtime kwargs, expected (value, status, value_infos))
    "result": ({}, ("3", CellStatus.PASS.value, {})),
    "reason": (
        {"helper_reason": "code failed"},
        (None, CellStatus.ERROR.value, {"reason": "code failed"}),
    ),
    "raises": (
        {"helper_error": RuntimeError(SENSITIVE_ERROR)},
        (None, CellStatus.ERROR.value, {"reason": PUBLIC_ROW_ERROR}),
    ),
}


@DESTINATION
@pytest.mark.parametrize("outcome", PYTHON_OUTCOMES)
def test_python_code_rerun_row_path_stores_every_row(
    install_winner, outcome, existing_cell
):
    runtime_kwargs, expected = PYTHON_OUTCOMES[outcome]
    args, kwargs = _rerun_dispatch("execute_python_code_async")
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(dynamic_columns, "refuse_disabled_code_task", lambda: None)
        )
        runtime = stack.enter_context(
            _activity_runtime(existing_cell=existing_cell, **runtime_kwargs)
        )
        install_winner("execute_python_code_async")(*args, **kwargs)

    assert runtime.helpers.calls == [("python", runtime.row, args[0])]
    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell)) == expected
    assert cell.row is runtime.row
    _assert_column_completed(runtime)


def test_python_code_rerun_ends_as_the_capability_refusal(install_winner):
    # The live rerun view refuses execute_code before dispatch; a queued or
    # retried rerun must still end as the non-retryable refusal, before any
    # read or write, not as a (retryable) TypeError.
    from temporalio.exceptions import ApplicationError

    from model_hub.views.utils.disabled_code import code_execution_disabled_message

    args, kwargs = _rerun_dispatch("execute_python_code_async")
    with _activity_runtime() as runtime:
        with pytest.raises(ApplicationError) as excinfo:
            install_winner("execute_python_code_async")(*args, **kwargs)

    assert excinfo.value.type == "CodeExecutionDisabled"
    assert excinfo.value.non_retryable is True
    assert excinfo.value.message == code_execution_disabled_message()
    assert runtime.row_model.mock_calls == []
    assert runtime.column_model.mock_calls == []
    assert vars(runtime.written) == {
        "saved": [],
        "bulk_updated": [],
        "bulk_created": [],
    }


# --- sparse source overrides and public exception reasons --------------------

SOURCE_OPERATIONS = {
    "classify_column_async": "classify",
    "extract_async": "extract_entities",
    "extract_json_async": "extract_json",
}


def _post_source_override(runtime, name):
    """Run the validated, locked POST and dispatch only after its commit."""
    from rest_framework.test import APIRequestFactory

    _, metadata = RERUNS[name]
    previous_source = "00000000-0000-0000-0000-0000000001c2"
    column = runtime.tracked_column
    column.source = SOURCE_OPERATIONS[name]
    column.metadata = {**metadata, "column_id": previous_source}
    runtime.target.value = "previous result"
    runtime.target.status = CellStatus.PASS.value
    scoped_columns = MagicMock()
    scoped_columns.select_for_update.return_value = scoped_columns
    scoped_columns.filter.return_value.first.return_value = column
    runtime.column_model.objects.filter.return_value.exists.return_value = True
    view = dynamic_columns.RerunOperationView()
    request = view.initialize_request(
        APIRequestFactory().post(
            "/rerun/",
            {
                "operation_type": SOURCE_OPERATIONS[name],
                "config": {"column_id": SOURCE_COLUMN_ID},
            },
            format="json",
        )
    )
    with (
        patch.object(
            dynamic_columns, "_request_column_queryset", return_value=scoped_columns
        ),
        patch.object(getattr(dynamic_columns, name), "delay") as dispatch,
        isolated_transactions(),
    ):
        with transaction.atomic():
            response = view.post(request, TARGET_COLUMN_ID)
            dispatch.assert_not_called()
    assert response.status_code == 200, response.data
    scoped_columns.select_for_update.assert_called_once_with(of=("self",))
    assert str(column.metadata["column_id"]) == SOURCE_COLUMN_ID
    # The reference check really validated the new source, without requiring
    # this row to have a cell there.
    runtime.column_model.objects.filter.assert_any_call(
        id=SOURCE_COLUMN_ID, dataset_id=DATASET_ID, deleted=False
    )
    runtime.column_model.objects.filter.return_value.exists.assert_called_once()
    # Selective reruns preserve the previous output until the worker replaces it.
    assert (runtime.target.value, runtime.target.status) == (
        "previous result",
        CellStatus.RUNNING.value,
    )
    assert _value_infos(runtime.target) == {}
    dispatch.assert_called_once()
    args, kwargs = dispatch.call_args
    assert kwargs == {"row_ids": [str(row.id) for row in runtime.rows()]}
    assert args[-1] is True
    return args, kwargs


@pytest.mark.parametrize("name", SOURCE_OPERATIONS)
@pytest.mark.parametrize("source_state", ["missing", "deleted"])
@pytest.mark.parametrize(
    "additional_source_row", [False, True], ids=["empty-source", "sparse-source"]
)
def test_source_override_finalizes_every_wiped_destination(
    install_winner, name, source_state, additional_source_row
):
    source_value, result = LLM_RERUNS.get(name, ("{'answer': 42}", "42"))
    with _activity_runtime(
        source_value=source_value,
        source_state=source_state,
        additional_source_row=additional_source_row,
    ) as runtime:
        args, kwargs = _post_source_override(runtime, name)
        install_winner(name)(*args, **kwargs)

    assert (
        runtime.target.value,
        runtime.target.status,
        _value_infos(runtime.target),
    ) == (
        None,
        CellStatus.ERROR.value,
        {"reason": MISSING_SOURCE_ERROR},
    )
    cells = runtime.target_cells()
    assert len(cells) == 1 + int(additional_source_row)
    if additional_source_row:
        [processed] = [cell for cell in cells if cell.row_id != ROW_ID]
        assert (processed.value, processed.status, _value_infos(processed)) == (
            result,
            CellStatus.PASS.value,
            {},
        )
    else:
        assert runtime.helpers.calls == []
    assert all(cell.status != CellStatus.RUNNING.value for cell in cells)
    _assert_column_completed(runtime)


@pytest.mark.parametrize("name", SOURCE_OPERATIONS)
def test_missing_source_rerun_can_recover_on_retry(install_winner, name):
    source_value, result = LLM_RERUNS.get(name, ("{'answer': 42}", "42"))
    with _activity_runtime(
        source_value=source_value, source_state="missing"
    ) as runtime:
        args, kwargs = _post_source_override(runtime, name)
        install_winner(name)(*args, **kwargs)
        assert runtime.target.status == CellStatus.ERROR.value
        runtime.source_cells.append(runtime.source)
        install_winner(name)(*args, **kwargs)
    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell)) == (
        result,
        CellStatus.PASS.value,
        {},
    )
    _assert_column_completed(runtime)


@DESTINATION
@pytest.mark.parametrize(
    "name", [*LLM_RERUNS, "extract_json_async", "execute_python_code_async"]
)
def test_rerun_exception_details_stay_in_logs_not_cells(
    install_winner, name, existing_cell
):
    args, kwargs = _rerun_dispatch(name)
    with ExitStack() as stack:
        for module in (dynamic_columns, develop_dataset):
            stack.enter_context(
                patch.object(
                    module.ExtractJsonColumnView,
                    "_process_cell",
                    side_effect=RuntimeError(SENSITIVE_ERROR),
                )
            )
        stack.enter_context(
            patch.object(dynamic_columns, "refuse_disabled_code_task", lambda: None)
        )
        logs = [
            stack.enter_context(patch.object(module, "logger"))
            for module in (dynamic_columns, develop_dataset)
        ]
        runtime = stack.enter_context(
            _activity_runtime(
                source_value="input",
                existing_cell=existing_cell,
                helper_error=RuntimeError(SENSITIVE_ERROR),
            )
        )
        install_winner(name)(*args, **kwargs)
    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell)) == (
        None,
        CellStatus.ERROR.value,
        {"reason": PUBLIC_ROW_ERROR},
    )
    assert SENSITIVE_ERROR not in json.dumps(_value_infos(cell))
    assert any(SENSITIVE_ERROR in str(logger.mock_calls) for logger in logs)
    _assert_column_completed(runtime)


@pytest.mark.parametrize("name", ["extract_json_async", "execute_python_code_async"])
def test_new_row_exceptions_use_the_same_public_reason(install_winner, name):
    args, kwargs = _rerun_dispatch(name)
    args = args[:-1]
    with ExitStack() as stack:
        for module in (dynamic_columns, develop_dataset):
            stack.enter_context(
                patch.object(
                    module.ExtractJsonColumnView,
                    "_process_cell",
                    side_effect=RuntimeError(SENSITIVE_ERROR),
                )
            )
        stack.enter_context(
            patch.object(dynamic_columns, "refuse_disabled_code_task", lambda: None)
        )
        runtime = stack.enter_context(
            _activity_runtime(
                existing_cell=False, helper_error=RuntimeError(SENSITIVE_ERROR)
            )
        )
        install_winner(name)(*args, **kwargs)
    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell)) == (
        None,
        CellStatus.ERROR.value,
        {"reason": PUBLIC_ROW_ERROR},
    )
    _assert_column_completed(runtime)


# --- conditional logging under the standalone structlog configuration --------


@pytest.mark.parametrize("path", ["branch", "condition", "row"])
@pytest.mark.parametrize("mode", ["rerun-existing", "rerun-missing", "new"])
def test_conditional_row_errors_finish_under_standalone_logging(
    install_winner, path, mode
):
    from tfc.logging.config import configure_structlog

    configure_structlog()
    config = [
        {
            "branch_type": "if" if path == "condition" else "else",
            "condition": "true",
            "branch_node_config": {"type": "static_value", "config": {"value": "ok"}},
        }
    ]
    method = {
        "branch": "_process_branch",
        "condition": "_evaluate_condition",
        "row": "_process_row",
    }[path]
    with (
        _activity_runtime(existing_cell=mode == "rerun-existing") as runtime,
        patch.object(
            dynamic_columns.ConditionalColumnView,
            method,
            side_effect=RuntimeError("branch failed"),
        ),
    ):
        install_winner("conditional_column_async")(
            config, DATASET_ID, 2, TARGET_COLUMN_ID, mode != "new"
        )
    cell = _only_target_cell(runtime)
    assert (cell.value, cell.status, _value_infos(cell)) == (
        None,
        CellStatus.ERROR.value,
        {"reason": "branch failed"},
    )
    _assert_column_completed(runtime)


def test_conditional_provider_exception_keeps_the_false_condition_contract():
    # _evaluate_condition catches provider errors and returns False. Fixing its
    # log call must not turn that existing fallback into a new exception policy.
    from tfc.logging.config import configure_structlog

    configure_structlog()
    with (
        _activity_runtime() as runtime,
        patch.object(
            dynamic_columns, "RunPrompt", side_effect=RuntimeError("provider failed")
        ),
    ):
        assert (
            dynamic_columns.ConditionalColumnView()._evaluate_condition(
                "true", runtime.row, ORG_ID
            )
            is False
        )


# --- a new column through the worker's function ------------------------------
#
# The args the create views dispatch (dynamic_columns.py ClassifyColumnView,
# ExtractEntitiesView, ExtractJsonColumnView and AddApiColumnView .post).


def test_new_classification_column_fills_its_cell(worker_registry):
    with _activity_runtime(source_value="I loved it", existing_cell=False) as runtime:
        _winner(worker_registry, "classify_column_async")(
            SOURCE_COLUMN_ID,
            ["positive", "negative"],
            "gpt-4o",
            2,
            DATASET_ID,
            TARGET_COLUMN_ID,
        )

    [cell] = runtime.written.bulk_created
    assert (cell.value, cell.status) == ("positive", CellStatus.PASS.value)
    _assert_column_completed(runtime)


def test_new_entity_extraction_column_fills_its_cell(worker_registry):
    with _activity_runtime(
        source_value="Ada wrote the notes", existing_cell=False
    ) as runtime:
        _winner(worker_registry, "extract_async")(
            SOURCE_COLUMN_ID,
            "Find the person's name",
            "gpt-4o",
            2,
            DATASET_ID,
            TARGET_COLUMN_ID,
        )

    [cell] = runtime.written.bulk_created
    assert (cell.value, cell.status) == ("Ada Lovelace", CellStatus.PASS.value)
    _assert_column_completed(runtime)


def test_new_json_extraction_column_fills_its_cell(worker_registry):
    with _activity_runtime(
        source_value="{'answer': 42}", existing_cell=False
    ) as runtime:
        _winner(worker_registry, "extract_json_async")(
            SOURCE_COLUMN_ID, "answer", 2, DATASET_ID, TARGET_COLUMN_ID
        )

    [cell] = runtime.written.bulk_created
    assert (cell.value, cell.status) == ("42", CellStatus.PASS.value)
    _assert_column_completed(runtime)


def test_new_api_column_fills_its_cell(worker_registry):
    # develop_dataset's copy passed the organization id positionally to a
    # _make_api_call whose dataset argument is keyword-only, so every row
    # raised a TypeError that the activity swallowed: no cell was written
    # and the column still finished as COMPLETED.
    with _activity_runtime(existing_cell=False) as runtime:
        _winner(worker_registry, "add_api_column_async")(
            dict(API_CONFIG), DATASET_ID, 2, TARGET_COLUMN_ID
        )

    assert runtime.helpers.calls == [
        ("api_call", runtime.row, API_CONFIG, runtime.dataset)
    ]
    [cell] = runtime.written.bulk_created
    assert (cell.value, cell.status) == ("pong", CellStatus.PASS.value)
    _assert_column_completed(runtime)


# --- controls for the DB-free boundaries, keeping real selection and hashing ---


def test_commit_fixture_defers_nested_callbacks_and_discards_rollback():
    calls = []
    with isolated_transactions():
        transaction.on_commit(lambda: calls.append("autocommit"))
        assert calls == ["autocommit"]
        with transaction.atomic():
            transaction.on_commit(lambda: calls.append("outer"))
            with transaction.atomic():
                transaction.on_commit(lambda: calls.append("inner"))
            with pytest.raises(RuntimeError, match="rollback"):
                with transaction.atomic():
                    transaction.on_commit(lambda: calls.append("discarded"))
                    raise RuntimeError("rollback")
            assert calls == ["autocommit"]
        assert calls == ["autocommit", "outer", "inner"]
        with pytest.raises(RuntimeError, match="outer rollback"):
            with transaction.atomic():
                with transaction.atomic():
                    transaction.on_commit(lambda: calls.append("also discarded"))
                raise RuntimeError("outer rollback")
        assert calls == ["autocommit", "outer", "inner"]


def test_generation_fixture_hashes_live_inputs_and_upserts_completed_snapshots():
    with _activity_runtime(source_value="before") as runtime:
        before = selective_rerun.input_fingerprints(runtime.tracked_column)
        assert set(before) == {ROW_ID}
        assert before[ROW_ID].startswith("sha256:")
        assert len(before[ROW_ID]) == 71
        assert selective_rerun.input_fingerprints(runtime.tracked_column, []) == {}
        assert selective_rerun.selected_rows(runtime.tracked_column, "all") == [ROW_ID]
        selective_rerun.save_fingerprints(runtime.tracked_column, before)
        assert runtime.snapshots == {}  # RUNNING outputs never acknowledge inputs.
        runtime.target.status = CellStatus.PASS.value
        selective_rerun.save_fingerprints(runtime.tracked_column, before)
        assert runtime.snapshots[(TARGET_COLUMN_ID, ROW_ID)].fingerprint == before[ROW_ID]
        runtime.source.value = "after"
        after = selective_rerun.input_fingerprints(runtime.tracked_column)
        assert after[ROW_ID] != before[ROW_ID]
        selective_rerun.save_fingerprints(runtime.tracked_column, after)
        assert len(runtime.snapshots) == 1
        assert runtime.snapshots[(TARGET_COLUMN_ID, ROW_ID)].fingerprint == after[ROW_ID]
