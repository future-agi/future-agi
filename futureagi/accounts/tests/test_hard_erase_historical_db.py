"""PostgreSQL rollback/reapply regressions with the real installed erase guard.

Every rollback target comes from the loaded migration graph, never from a fixed
migration name: walk back from an app's leaf while each migration on the way can
really be reversed. A new migration, or one made irreversible on purpose, moves
the target instead of leaving the proof with nothing to reverse.
"""

import builtins
import functools
import inspect
import sys
import types
from pathlib import Path
from typing import NamedTuple

import pytest
from django.apps import apps as installed_apps
from django.db import connections
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.operations import RunPython, RunSQL, SeparateDatabaseAndState
from django.db.migrations.operations.base import Operation
from django.db.migrations.recorder import MigrationRecorder
from django.db.models.deletion import Collector

from accounts.services.hard_erase import guarded_delete
from accounts.services.hard_erase_migrations import migration_sql_allowed
from accounts.services.hard_erase_sql import _SQL_AUTH, guard_execute

# Match manage.py migrate: the executor scopes the real wrapper, not Django's
# convenience ConnectionProxy (which cannot match cursor connection identity).
connection = connections["default"]
pytestmark = pytest.mark.django_db(transaction=True, databases=["default"])
GATEWAY_APP = "prism"
# The gateway history this roundtrip was first written against must be loaded.
GATEWAY_HISTORY = ("prism", "0074_api_key_provisioning_state")
# These refuse by raising IrreversibleError from their reverse code, which
# Django still reports as reversible: the rule below has to find them itself.
WEBHOOK_STEPS_THAT_REFUSE = (
    ("prism", "0075_webhook_credential_columns"),
    ("prism", "0076_encrypt_webhook_credentials"),
    ("prism", "0077_remove_webhook_plaintext"),
)
REFUSALS = (IrreversibleError, NotImplementedError)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migration_venue(transactional_db):
    assert connection.vendor == "postgresql", "PostgreSQL migration venue required"
    assert Collector.delete is guarded_delete
    assert guard_execute in connection.execute_wrappers
    assert getattr(MigrationExecutor.unapply_migration, "_hard_erase_migration", False)
    assert _SQL_AUTH.get() is None
    assert not migration_sql_allowed(connection)
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    assert GATEWAY_HISTORY in executor.loader.graph.nodes
    try:
        MigrationExecutor(connection).migrate(leaves)
        yield leaves
    finally:
        MigrationExecutor(connection).migrate(leaves)
        assert _SQL_AUTH.get() is None
        assert not migration_sql_allowed(connection)


def _code_names(code):
    names = set(code.co_names)
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            names |= _code_names(constant)
    return names


def _reverse_code_refusal(reverse_code):
    function = reverse_code
    while isinstance(function, functools.partial) or inspect.ismethod(function):
        function = getattr(function, "func", None) or function.__func__
    function = inspect.unwrap(function)
    code = getattr(function, "__code__", None)
    if code is None:
        return f"its reverse callable {reverse_code!r} cannot be read"
    namespace = {**vars(builtins), **getattr(function, "__globals__", {})}
    refusal_names = {refusal.__name__ for refusal in REFUSALS}
    for name in sorted(_code_names(code)):
        resolved = namespace.get(name)
        if name in refusal_names or any(resolved is refusal for refusal in REFUSALS):
            return f"its reverse code {function.__qualname__} raises {name}"
    return None


def operation_refusals(operation):
    """Why this operation cannot really be reversed; empty when it can."""
    reasons = []
    if not operation.reversible:
        reasons.append(f"{type(operation).__name__} has no reverse")
    if isinstance(operation, RunPython) and operation.reverse_code is not None:
        reason = _reverse_code_refusal(operation.reverse_code)
        if reason:
            reasons.append(f"{type(operation).__name__}: {reason}")
    for attribute in ("operations", "database_operations"):
        nested = getattr(operation, attribute, None)
        if isinstance(nested, (list, tuple)):
            for child in nested:
                if isinstance(child, Operation):
                    reasons.extend(operation_refusals(child))
    return reasons


def migration_refusals(migration):
    return [
        reason
        for operation in migration.operations
        for reason in operation_refusals(operation)
    ]


class Rollback(NamedTuple):
    app_label: str
    target: tuple
    reversed_nodes: tuple
    stop: tuple | None
    stop_refusing: tuple

    def label(self):
        names = ", ".join(f"{app}.{name}" for app, name in self.reversed_nodes)
        return (
            f"{self.target[0]}.{self.target[1]} in app {self.app_label}, "
            f"reversing {len(self.reversed_nodes)}: {names}"
        )


def _walk_back(executor, app_label):
    """Candidates from the leaf backwards while each real plan can be reversed."""
    graph = executor.loader.graph
    leaves = graph.leaf_nodes(app_label)
    if len(leaves) != 1:
        return [], None, f"{len(leaves)} leaves"
    chain = [node for node in graph.forwards_plan(leaves[0]) if node[0] == app_label]
    qualified = []
    for candidate in reversed(chain[:-1]):
        steps = [
            ((migration.app_label, migration.name), backwards)
            for migration, backwards in executor.migration_plan([candidate])
        ]
        nodes = tuple(node for node, _ in steps)
        refusing = tuple(
            node for node in nodes if migration_refusals(graph.nodes[node])
        )
        if not steps or not all(backwards for _, backwards in steps) or refusing:
            return qualified, (candidate, refusing), None
        qualified.append((candidate, nodes))
    return qualified, None, None


def first_party_apps():
    interpreter = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
    labels = []
    for config in installed_apps.get_app_configs():
        path = Path(config.path).resolve()
        if (
            path.is_relative_to(PROJECT_ROOT)
            and "site-packages" not in path.parts
            and not any(path.is_relative_to(root) for root in interpreter)
        ):
            labels.append(config.label)
    return labels


def select_rollback(executor, deepest=True):
    """Gateway first, then other first-party apps in sorted order."""
    labels = set(first_party_apps())
    order = [GATEWAY_APP] if GATEWAY_APP in labels else []
    order += sorted(labels - {GATEWAY_APP})
    checked = []
    for app_label in order:
        if app_label not in executor.loader.migrated_apps:
            continue
        qualified, stop, problem = _walk_back(executor, app_label)
        if qualified:
            target, nodes = qualified[-1] if deepest else qualified[0]
            return Rollback(app_label, target, nodes, *(stop or (None, ())))
        if problem:
            checked.append(f"{app_label}: {problem}")
        elif stop:
            refused = ", ".join(f"{app}.{name}" for app, name in stop[1])
            checked.append(f"{app_label}: leaf cannot be reversed ({refused})")
    pytest.fail(
        "No first-party app has a leaf migration that can be reversed; checked "
        + ("; ".join(checked) or "no first-party app with migrations")
    )


def assert_rollback_roundtrip(leaves, target, picked="", expected=None):
    before = set(MigrationRecorder(connection).applied_migrations())
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan([target])
    reversed_nodes = {
        (migration.app_label, migration.name)
        for migration, backwards in plan
        if backwards
    }
    no_op = f"A no-op migrate does not qualify recorder deletion ({picked})"
    assert reversed_nodes, no_op
    if expected is not None:
        assert reversed_nodes == set(expected), picked
    executor.migrate([target])
    after = set(MigrationRecorder(connection).applied_migrations())
    assert target in after, picked
    assert reversed_nodes.isdisjoint(after), picked
    assert _SQL_AUTH.get() is None
    assert not migration_sql_allowed(connection)
    MigrationExecutor(connection).migrate(leaves)
    assert before == set(MigrationRecorder(connection).applied_migrations()), picked
    assert _SQL_AUTH.get() is None
    assert not migration_sql_allowed(connection)


def test_deepest_reversible_rollback_and_forward(migration_venue):
    leaves = migration_venue
    rollback = select_rollback(MigrationExecutor(connection), deepest=True)
    print(f"rollback target (deepest): {rollback.label()}")
    assert_rollback_roundtrip(
        leaves, rollback.target, rollback.label(), rollback.reversed_nodes
    )


def test_one_step_rollback_removes_recorder_rows_and_forward(migration_venue):
    # The smallest real plan the same walk allows: the recorder rows of the
    # reversed nodes go through the guarded Collector and come back on reapply.
    leaves = migration_venue
    rollback = select_rollback(MigrationExecutor(connection), deepest=False)
    print(f"rollback target (one step): {rollback.label()}")
    assert_rollback_roundtrip(
        leaves, rollback.target, rollback.label(), rollback.reversed_nodes
    )


def _forwards(apps, schema_editor):
    return None


def _refusing_reverse(apps, schema_editor):
    raise IrreversibleError("synthetic refusal")


def test_rollback_selection_is_honest(migration_venue):
    executor = MigrationExecutor(connection)
    graph = executor.loader.graph
    refusing = {
        node for node, migration in graph.nodes.items() if migration_refusals(migration)
    }
    for node in WEBHOOK_STEPS_THAT_REFUSE:
        if node in graph.nodes:
            assert node in refusing, f"{node} refuses to reverse but was not detected"
    for operation, refuses in (
        (RunPython(_forwards), True),
        (RunPython(_forwards, _refusing_reverse), True),
        (RunPython(_forwards, functools.partial(_refusing_reverse)), True),
        (RunPython(_forwards, RunPython.noop), False),
        (RunSQL(RunSQL.noop), True),
        (RunSQL(RunSQL.noop, RunSQL.noop), False),
        (SeparateDatabaseAndState(database_operations=[RunPython(_forwards)]), True),
        (
            SeparateDatabaseAndState(
                database_operations=[RunPython(_forwards, _refusing_reverse)]
            ),
            True,
        ),
        (
            SeparateDatabaseAndState(
                database_operations=[RunPython(_forwards, RunPython.noop)]
            ),
            False,
        ),
    ):
        assert bool(operation_refusals(operation)) is refuses, operation
    for deepest in (True, False):
        rollback = select_rollback(MigrationExecutor(connection), deepest=deepest)
        picked = rollback.label()
        print(f"rollback target ({'deepest' if deepest else 'one step'}): {picked}")
        plan = MigrationExecutor(connection).migration_plan([rollback.target])
        assert plan, picked
        assert all(backwards for _, backwards in plan), picked
        planned = tuple((migration.app_label, migration.name) for migration, _ in plan)
        assert planned == rollback.reversed_nodes, picked
        assert rollback.target not in planned, picked
        crossed = refusing.intersection(planned)
        assert not crossed, f"{picked} crosses migrations that refuse: {crossed}"
    walk = select_rollback(MigrationExecutor(connection), deepest=True)
    if walk.stop is None:
        # The walk only ends without a refusal at the app's first migration.
        assert walk.target in graph.root_nodes(walk.app_label), walk.label()
    else:
        # Going one node further back would reverse a migration that refuses.
        assert walk.stop_refusing, f"{walk.label()} stopped at {walk.stop}"
        assert set(walk.stop_refusing) <= refusing, walk.label()
