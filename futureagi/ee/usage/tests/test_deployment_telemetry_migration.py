import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


def _table_names() -> set[str]:
    return set(connection.introspection.table_names())


@pytest.mark.django_db(transaction=True)
def test_deployment_telemetry_migration_rolls_back_cleanly():
    executor = MigrationExecutor(connection)
    previous_target = [("usage", "0025_organizationsubscription_plan_changed_at")]
    current_target = [("usage", "0026_deployment_telemetry")]
    instance_table = "usage_deploymenttelemetryinstance"
    heartbeat_table = "usage_deploymenttelemetryheartbeat"

    try:
        executor.migrate(previous_target)
        assert instance_table not in _table_names()
        assert heartbeat_table not in _table_names()

        executor.loader.build_graph()
        executor.migrate(current_target)
        assert instance_table in _table_names()
        assert heartbeat_table in _table_names()

        executor.loader.build_graph()
        executor.migrate(previous_target)
        assert instance_table not in _table_names()
        assert heartbeat_table not in _table_names()
    finally:
        executor.loader.build_graph()
        executor.migrate(current_target)
