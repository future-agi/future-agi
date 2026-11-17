"""C14/C25: registration order must not change selective-rerun contracts.

The legacy workers missed row_ids after the canonical workers gained it. Merely
adding that argument also exposed a whole-column missing-source finalizer that
changed unselected outputs. Exercise the real registry decorator and tracked
worker bodies in both orders, with in-memory ORM objects and offline row helpers.
These fixtures are independent of the separately owned rerun/authority suites.
"""

import inspect
import json
import socket
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import CellStatus, StatusType
from model_hub.services import selective_rerun
from model_hub.views import develop_dataset, dynamic_columns
from model_hub.views.utils import rerun_cells
from tfc.temporal.drop_in import decorator as activity_registry

MODULES = (develop_dataset, dynamic_columns)
SOURCE_ACTIVITIES = ("classify_column_async", "extract_async", "extract_json_async")
SELECTIVE_ACTIVITIES = SOURCE_ACTIVITIES + (
    "add_api_column_async", "conditional_column_async", "add_vector_db_column_async"
)
SHARED_ACTIVITIES = SELECTIVE_ACTIVITIES + ("execute_python_code_async",)
AUTHORIZED_ACTIVITIES = ("conditional_column_async", "add_vector_db_column_async")
DATASET_ID, SOURCE_ID, TARGET_ID = "dataset", "source", "target"
LIVE_ROWS = ("row-selected", "row-untouched")
AUTHORITY = {"actor_id": "actor", "organization_id": "org", "workspace_id": "workspace"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access is forbidden in activity parity tests")

    for target, name in (
        (socket.socket, "connect"), (socket.socket, "connect_ex"),
        (socket, "create_connection"), (socket, "getaddrinfo"),
    ):
        monkeypatch.setattr(target, name, refuse)


@contextmanager
def registered(order):
    # Reapply the actual registration decorator, not a last-wins test double.
    # Isolate the dictionaries so no other test inherits these registrations.
    with (
        patch.object(activity_registry, "_ACTIVITY_REGISTRY", {}),
        patch.object(activity_registry, "_ACTIVITY_WRAPPERS", {}),
    ):
        for module in order:
            for name in SHARED_ACTIVITIES:
                worker = getattr(module, name)
                activity_registry.temporal_activity(
                    name=worker._activity_name, queue=worker._metadata["queue"]
                )(worker._original_func)
        yield activity_registry._ACTIVITY_REGISTRY


@pytest.mark.parametrize("name", SHARED_ACTIVITIES)
def test_both_registration_orders_expose_the_same_signature(name):
    signatures = []
    for order in (MODULES, MODULES[::-1]):
        with registered(order) as registry:
            winner = registry[name]["func"]
            assert winner is getattr(order[-1], name)._original_func
            signatures.append(inspect.signature(winner))
    assert signatures[0] == signatures[1]
    if name in SELECTIVE_ACTIVITIES:
        row_ids = signatures[0].parameters["row_ids"]
        assert row_ids.default is None
        assert row_ids.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    if name in AUTHORIZED_ACTIVITIES:
        authority = signatures[0].parameters["authority"]
        assert authority.kind is inspect.Parameter.KEYWORD_ONLY
        assert authority.default is None


class QuerySet(list):
    @staticmethod
    def matches(item, lookup):
        for key, expected in lookup.items():
            parts = key.split("__")
            membership = parts[-1] == "in"
            actual = item
            for part in parts[:-1] if membership else parts:
                actual = getattr(actual, part)
            if membership:
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    def filter(self, **lookup):
        return QuerySet(item for item in self if self.matches(item, lookup))

    def exclude(self, **lookup):
        return QuerySet(item for item in self if not self.matches(item, lookup))

    def get(self, **lookup):
        found = self.filter(**lookup)
        assert len(found) == 1, lookup
        return found[0]

    def select_related(self, *fields):
        return self

    def order_by(self, *fields):
        return self

    def values_list(self, field, *, flat):
        assert flat
        return [getattr(item, field) for item in self]

    def update(self, **fields):
        for item in self:
            item.__dict__.update(fields)
        return len(self)

    def bulk_create(self, items, **kwargs):
        self.extend(items)

    def bulk_update(self, items, fields, **kwargs):
        assert all(item in self for item in items)


def cell_state(cells):
    return {
        cell.row_id: (cell.value, cell.status, cell.value_infos)
        for cell in cells if cell.column_id == TARGET_ID
    }


@contextmanager
def runtime(row_ids, *, missing_target=False, missing_source=False):
    dataset = SimpleNamespace(id=DATASET_ID)
    rows = QuerySet(
        SimpleNamespace(id=key, pk=key, dataset_id=DATASET_ID, deleted=False)
        for key in LIVE_ROWS
    )
    rows.extend([
        SimpleNamespace(id="deleted-row", pk="deleted-row", dataset_id=DATASET_ID,
                        deleted=True),
        SimpleNamespace(id="foreign-row", pk="foreign-row", dataset_id="foreign",
                        deleted=False),
    ])
    selected = [key for key in LIVE_ROWS if row_ids is None or key in row_ids]
    columns = QuerySet(
        SimpleNamespace(id=key, dataset_id=DATASET_ID, deleted=False,
                        status=StatusType.RUNNING.value)
        for key in (SOURCE_ID, TARGET_ID)
    )
    target = columns.get(id=TARGET_ID)
    cells, calls = QuerySet(), []

    class Cell:
        objects = cells

        def __init__(self, **fields):
            self.value, self.value_infos = None, json.dumps({})
            self.status, self.deleted = CellStatus.PASS.value, False
            self.__dict__.update(fields)
            self.row = fields.get("row") or rows.get(id=fields["row_id"])
            self.row_id = self.row.id
            self.column = fields.get("column") or columns.get(id=fields["column_id"])
            self.column_id = self.column.id
            self.dataset_id = fields.get("dataset_id", DATASET_ID)

        def save(self, *args, **kwargs):
            if self not in cells:
                cells.append(self)

    for row in rows:
        if not (missing_source and row.id == LIVE_ROWS[0]):
            cells.append(Cell(row=row, column_id=SOURCE_ID,
                              value=repr({"answer": f"result-{row.id}"})))
        if row.id in LIVE_ROWS and not (missing_target and row.id == LIVE_ROWS[0]):
            cells.append(Cell(
                row=row, column_id=TARGET_ID, value=f"old-{row.id}",
                value_infos=json.dumps({"old": row.id}),
                status=CellStatus.RUNNING.value if row.id in selected else CellStatus.PASS.value,
            ))
    before = cell_state(cells)
    fingerprints = {key: f"sha256:{key}" for key in selected}
    fingerprint_read = MagicMock(return_value=fingerprints)
    fingerprint_save = MagicMock()
    authority_checks = []

    def authorize(authority, *args, **kwargs):
        assert authority is AUTHORITY
        authority_checks.append((args, kwargs))
        return dataset

    def validate(config, authority):
        assert authority is AUTHORITY
        authority_checks.append(config)

    def result(view, item, *args, **kwargs):
        if "authority" in kwargs:
            assert kwargs["authority"] is AUTHORITY
        row = getattr(item, "row", item)
        calls.append(row.id)
        return f"result-{row.id}", {}

    def json_result(original):
        def process(view, cell, key):
            calls.append(cell.row_id)
            return original(view, cell, key)
        return process

    with ExitStack() as stack:
        for module in (*MODULES, selective_rerun, rerun_cells):
            stack.enter_context(patch.object(module, "Cell", Cell))
        for module in (*MODULES, selective_rerun):
            stack.enter_context(patch.object(module, "Row", SimpleNamespace(objects=rows)))
            stack.enter_context(patch.object(module, "Column", SimpleNamespace(objects=columns)))
        for module in MODULES:
            stack.enter_context(patch.object(module, "Dataset", SimpleNamespace(
                no_workspace_objects=SimpleNamespace(get=lambda **kw: dataset))))
            stack.enter_context(patch.object(module, "wrap_for_thread", lambda fn: fn))
            stack.enter_context(patch.object(module, "close_old_connections", lambda: None))
            for cls, method in (
                (module.ClassifyColumnView, "_classify_cell"),
                (module.ExtractEntitiesView, "_extract_entities"),
                (module.AddApiColumnView, "_make_api_call"),
                (module.ConditionalColumnView, "_process_row"),
                (module.AddVectorDBColumnView, "_process_row"),
            ):
                stack.enter_context(patch.object(cls, method, result))
            cls = module.ExtractJsonColumnView
            stack.enter_context(patch.object(cls, "_process_cell", json_result(cls._process_cell)))
        for name in ("authorized_dataset", "live_authority"):
            stack.enter_context(patch.object(dynamic_columns, name, authorize))
        for name in ("validate_conditional_config", "validate_vector_config"):
            stack.enter_context(patch.object(dynamic_columns, name, validate))
        stack.enter_context(patch.object(selective_rerun, "input_fingerprints", fingerprint_read))
        stack.enter_context(patch.object(selective_rerun, "save_fingerprints", fingerprint_save))
        yield SimpleNamespace(
            cells=cells, target=target, before=before, calls=calls, selected=selected,
            fingerprints=fingerprints, fingerprint_read=fingerprint_read,
            fingerprint_save=fingerprint_save, authority_checks=authority_checks,
        )


def arguments(name):
    if name == "classify_column_async":
        return (SOURCE_ID, ["positive"], "model", 1, DATASET_ID, TARGET_ID, True)
    if name == "extract_async":
        return (SOURCE_ID, "extract", "model", 1, DATASET_ID, TARGET_ID, True)
    if name == "extract_json_async":
        return (SOURCE_ID, "answer", 1, DATASET_ID, TARGET_ID, True)
    if name == "add_vector_db_column_async":
        return ({"column_id": SOURCE_ID}, DATASET_ID, 1, SOURCE_ID,
                AUTHORITY["organization_id"], TARGET_ID, AUTHORITY["workspace_id"], True)
    return ({}, DATASET_ID, 1, TARGET_ID, True)


def assert_order_independent(name, selection, *, missing_target=False, missing_source=False):
    row_ids = {
        "omitted": None, "all": None, "one": [LIVE_ROWS[0]], "empty": [],
        "mixed": [LIVE_ROWS[0], "deleted-row", "foreign-row", "unknown-row"],
    }[selection]
    outcomes = []
    for order in (MODULES, MODULES[::-1]):
        with (
            registered(order) as registry,
            runtime(row_ids, missing_target=missing_target, missing_source=missing_source) as state,
        ):
            kwargs = {} if selection == "omitted" else {"row_ids": row_ids}
            if name in AUTHORIZED_ACTIVITIES:
                kwargs["authority"] = AUTHORITY
            winner = registry[name]["func"]
            assert winner is getattr(order[-1], name)._original_func
            inspect.signature(winner).bind(*arguments(name), **kwargs)
            winner(*arguments(name), **kwargs)
            after = cell_state(state.cells)
            expected_rows = set(state.before) | set(state.selected)
            assert set(after) == expected_rows
            targets = [cell for cell in state.cells if cell.column_id == TARGET_ID]
            assert len(targets) == len(expected_rows), "rerun duplicated a destination"
            for key in expected_rows:
                if key not in state.selected:
                    assert after[key] == state.before[key], "unselected output changed"
                elif missing_source and key == LIVE_ROWS[0]:
                    assert after[key][0:2] == (None, CellStatus.ERROR.value)
                    assert json.loads(after[key][2])["reason"] == (
                        "Source cell is missing for this row."
                    )
                else:
                    assert after[key][0:2] == (f"result-{key}", CellStatus.PASS.value)
            expected_calls = [key for key in state.selected
                              if not (missing_source and key == LIVE_ROWS[0])]
            assert sorted(state.calls) == sorted(expected_calls)
            state.fingerprint_read.assert_called_once_with(state.target, row_ids)
            state.fingerprint_save.assert_called_once_with(state.target, state.fingerprints)
            assert state.target.status == StatusType.COMPLETED.value
            if name in AUTHORIZED_ACTIVITIES:
                assert state.authority_checks, "authority checks were bypassed"
            outcomes.append(after)
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("name", SELECTIVE_ACTIVITIES)
@pytest.mark.parametrize("selection", ["omitted", "all", "one", "empty", "mixed"])
@pytest.mark.parametrize("missing_target", [False, True], ids=["existing", "missing"])
def test_both_registration_orders_preserve_selective_reruns(name, selection, missing_target):
    assert_order_independent(name, selection, missing_target=missing_target)


@pytest.mark.parametrize("name", SOURCE_ACTIVITIES)
@pytest.mark.parametrize("missing_target", [False, True], ids=["existing", "missing"])
def test_selected_missing_sources_finalize_without_touching_other_rows(name, missing_target):
    assert_order_independent(name, "one", missing_target=missing_target, missing_source=True)


@pytest.mark.parametrize("name", SOURCE_ACTIVITIES)
@pytest.mark.parametrize("selection", ["omitted", "all", "empty", "mixed"])
@pytest.mark.parametrize("missing_target", [False, True], ids=["existing", "missing"])
def test_missing_source_finalization_preserves_selection_boundaries(
    name, selection, missing_target
):
    assert_order_independent(
        name, selection, missing_target=missing_target, missing_source=True
    )
