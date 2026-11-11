from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.db import DatabaseError, InterfaceError, connection
from django.db.migrations import Migration
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

from tfc import schema_readiness as schema
from tfc.schema_readiness import SchemaReadinessChecker, SchemaState


@pytest.fixture
def migration_history(monkeypatch):
    first = Migration("0001_initial", "guard_test")
    second = Migration("0002_next", "guard_test")
    second.dependencies = [(first.app_label, first.name)]
    disk = {(m.app_label, m.name): m for m in (first, second)}

    def load_disk(loader):
        loader.disk_migrations = disk.copy()
        loader.migrated_apps = {"guard_test"}
        loader.unmigrated_apps = set()

    monkeypatch.setattr(MigrationLoader, "load_disk", load_disk)
    table = Mock(return_value=True)
    applied = Mock(return_value={})
    monkeypatch.setattr(MigrationRecorder, "has_table", table)
    monkeypatch.setattr(MigrationRecorder, "applied_migrations", applied)
    return disk, table, applied


def test_missing_recorder_is_pending(migration_history):
    _, table, applied = migration_history
    table.return_value = False

    assert SchemaReadinessChecker().check() is SchemaState.PENDING
    applied.assert_not_called()


@pytest.mark.parametrize(
    "names, expected",
    [
        ([], SchemaState.PENDING),
        (["0001_initial"], SchemaState.PENDING),
        (["0001_initial", "0002_next"], SchemaState.READY),
        (["0001_initial", "0002_next", "0003_new_image"], SchemaState.READY),
    ],
    ids=["empty", "unapplied", "all-applied", "database-ahead-of-image"],
)
def test_image_relative_readiness(migration_history, names, expected):
    _, _, applied = migration_history
    applied.return_value = {("guard_test", name): object() for name in names}

    assert SchemaReadinessChecker().check() is expected


@pytest.mark.parametrize("applied_count", [0, 1, 2])
def test_squashed_migrations_use_djangos_replacement_graph(
    migration_history, applied_count
):
    disk, _, applied = migration_history
    originals = list(disk)
    squashed = Migration("0001_squashed_0002", "guard_test")
    squashed.replaces = originals
    disk[(squashed.app_label, squashed.name)] = squashed
    applied.return_value = dict.fromkeys(originals[:applied_count])

    expected = SchemaState.READY if applied_count == 2 else SchemaState.PENDING
    assert SchemaReadinessChecker().check() is expected


@pytest.mark.parametrize("error", [DatabaseError, InterfaceError])
def test_database_errors_are_unavailable_and_logged(
    migration_history, monkeypatch, error
):
    _, table, _ = migration_history
    table.side_effect = error("database unavailable")
    logger = Mock()
    monkeypatch.setattr(schema, "logger", logger)

    assert SchemaReadinessChecker().check() is SchemaState.UNAVAILABLE
    logger.warning.assert_called_once_with(
        "startup_schema_database_unavailable", exc_info=True
    )


def test_unexpected_checker_errors_are_not_hidden(migration_history):
    _, table, _ = migration_history
    table.side_effect = ValueError("checker defect")

    with pytest.raises(ValueError, match="checker defect"):
        SchemaReadinessChecker().check()


def test_ready_is_cached_even_if_database_later_fails(migration_history, monkeypatch):
    disk, table, applied = migration_history
    applied.return_value = dict.fromkeys(disk)
    clock = Mock(return_value=0)
    monkeypatch.setattr(schema, "monotonic", clock)
    checker = SchemaReadinessChecker()
    assert checker.check() is SchemaState.READY

    table.reset_mock(side_effect=True)
    table.side_effect = DatabaseError("database went down")
    clock.return_value = 1000
    assert checker.check() is SchemaState.READY
    table.assert_not_called()


def test_pending_is_logged_once_per_recheck(migration_history, monkeypatch):
    logger = Mock()
    clock = Mock(return_value=0)
    monkeypatch.setattr(schema, "logger", logger)
    monkeypatch.setattr(schema, "monotonic", clock)
    checker = SchemaReadinessChecker()

    assert checker.check() is SchemaState.PENDING
    assert checker.check() is SchemaState.PENDING
    logger.info.assert_called_once_with("startup_schema_pending", retry_after_seconds=5)
    clock.return_value = schema.RECHECK_SECONDS
    assert checker.check() is SchemaState.PENDING
    assert logger.info.call_count == 2


@pytest.mark.parametrize("initial", [SchemaState.PENDING, SchemaState.UNAVAILABLE])
def test_rechecks_are_throttled_and_recover(migration_history, monkeypatch, initial):
    disk, table, applied = migration_history
    if initial is SchemaState.UNAVAILABLE:
        table.side_effect = DatabaseError("database unavailable")
    clock = Mock(return_value=0)
    monkeypatch.setattr(schema, "monotonic", clock)
    checker = SchemaReadinessChecker()
    assert checker.check() is initial

    table.reset_mock(side_effect=True)
    applied.return_value = dict.fromkeys(disk)
    clock.return_value = schema.RECHECK_SECONDS - 0.1
    assert checker.check() is initial
    table.assert_not_called()
    clock.return_value = schema.RECHECK_SECONDS
    assert checker.check() is SchemaState.READY


def test_concurrent_requests_share_one_check(migration_history):
    _, _, applied = migration_history
    checker = SchemaReadinessChecker()
    barrier = Barrier(8)

    def check(_):
        barrier.wait(timeout=5)
        return checker.check()

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(check, range(8))) == [SchemaState.PENDING] * 8
    applied.assert_called_once()


@pytest.mark.django_db
def test_real_default_database_recorder_recovers_without_writes_from_checker(
    monkeypatch, django_assert_num_queries
):
    # A transaction-local schema leaves the suite's migrated tables untouched.
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA schema_guard_test")
        cursor.execute("SET LOCAL search_path = schema_guard_test")
    clock = Mock(return_value=0)
    monkeypatch.setattr(schema, "monotonic", clock)
    checker = SchemaReadinessChecker()
    assert checker.check() is SchemaState.PENDING
    assert not MigrationRecorder(connection).has_table()

    recorder = MigrationRecorder(connection)
    recorder.ensure_schema()
    clock.return_value += schema.RECHECK_SECONDS
    assert checker.check() is SchemaState.PENDING
    assert recorder.migration_qs.count() == 0

    shipped = MigrationLoader(None, ignore_no_migrations=True).graph.nodes
    recorder.migration_qs.bulk_create(
        recorder.Migration(app=app, name=name) for app, name in shipped
    )
    recorder.record_applied("future_image", "0001_newer")
    clock.return_value += schema.RECHECK_SECONDS
    assert checker.check() is SchemaState.READY
    assert recorder.migration_qs.count() == len(shipped) + 1
    with django_assert_num_queries(0):
        assert checker.check() is SchemaState.READY
