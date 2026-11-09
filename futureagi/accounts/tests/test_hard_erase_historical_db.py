"""PostgreSQL rollback/reapply regressions with the real installed erase guard."""

import pytest
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.models.deletion import Collector

from accounts.services.hard_erase import guarded_delete
from accounts.services.hard_erase_migrations import migration_sql_allowed
from accounts.services.hard_erase_sql import _SQL_AUTH, guard_execute

# Match manage.py migrate: the executor scopes the real wrapper, not Django's
# convenience ConnectionProxy (which cannot match cursor connection identity).
connection = connections["default"]
pytestmark = pytest.mark.django_db(transaction=True, databases=["default"])
TARGET = ("prism", "0074_api_key_provisioning_state")
PREVIOUS = ("prism", "0073_merge_708j_round3")


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
    prism_leaves = executor.loader.graph.leaf_nodes("prism")
    assert TARGET in executor.loader.graph.nodes
    try:
        MigrationExecutor(connection).migrate(leaves)
        yield leaves, prism_leaves
    finally:
        MigrationExecutor(connection).migrate(leaves)
        assert _SQL_AUTH.get() is None
        assert not migration_sql_allowed(connection)


def assert_rollback_roundtrip(leaves, target):
    before = set(MigrationRecorder(connection).applied_migrations())
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan([target])
    reversed_nodes = {
        (migration.app_label, migration.name)
        for migration, backwards in plan
        if backwards
    }
    assert reversed_nodes, "A no-op migrate does not qualify recorder deletion"
    executor.migrate([target])
    after = set(MigrationRecorder(connection).applied_migrations())
    assert target in after
    assert reversed_nodes.isdisjoint(after)
    assert _SQL_AUTH.get() is None
    assert not migration_sql_allowed(connection)
    MigrationExecutor(connection).migrate(leaves)
    assert before == set(MigrationRecorder(connection).applied_migrations())
    assert _SQL_AUTH.get() is None
    assert not migration_sql_allowed(connection)


def test_prism_leaf_to_0074_and_forward(migration_venue):
    leaves, prism_leaves = migration_venue
    if prism_leaves == [TARGET]:
        pytest.skip("Assigned tree ends at 0074; 0075_dispatch_claim is not present")
    assert_rollback_roundtrip(leaves, TARGET)


def test_prism_0074_recorder_delete_and_forward(migration_venue):
    # The assigned c2.5 base has no 0075. Reversing its existing 0074 exercises
    # the identical recorder -> Collector -> row_keys failure without inventing
    # a production migration or accepting an empty reverse plan as evidence.
    leaves, _ = migration_venue
    assert_rollback_roundtrip(leaves, PREVIOUS)
