"""Monitors saved before the threshold update keep the rule they were saved with.

0133 adds ``UserAlertMonitor.threshold_rule`` with a database default of
"earlier" and a Python default of "current". Every row that exists when it
runs, and every row an older server inserts without the column while the
update rolls out, reads "earlier"; a row the ORM saves afterwards reads
"current".
"""

import pytest
from django.db import DEFAULT_DB_ALIAS, connection, connections
from django.db.migrations import AddField
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from tracer.models.monitor import UserAlertMonitor

PREVIOUS = ("tracer", "0132_seed_alert_issue_occurrences_high_water")
CURRENT = ("tracer", "0133_useralertmonitor_threshold_rule")


def _database_default():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'tracer_useralertmonitor' "
            "AND column_name = 'threshold_rule'"
        )
        return cursor.fetchone()


def _production_rows(model, organization, workspace, project) -> list:
    """Monitors as the production build saved them: percentage and provider rows."""
    scope = {
        "organization_id": organization.id,
        "workspace_id": workspace.id,
        "project_id": project.id,
    }
    rows = [
        # The form's numbers with percentage chosen: 400% / 300%, hourly, 7 days.
        {
            "name": "Errors up",
            "metric_type": "count_of_errors",
            "threshold_type": "percentage_change",
            "threshold_operator": "greater_than",
            "critical_threshold_value": 400,
            "warning_threshold_value": 300,
            "alert_frequency": 60,
            "auto_threshold_time_window": 60 * 24 * 7,
        },
        # Omega's "Errors spike": 200% / 100%, every 15 minutes, 7 days.
        {
            "name": "Errors spike (production)",
            "metric_type": "count_of_errors",
            "threshold_type": "percentage_change",
            "threshold_operator": "greater_than",
            "critical_threshold_value": 200,
            "warning_threshold_value": 100,
            "alert_frequency": 15,
            "auto_threshold_time_window": 60 * 24 * 7,
        },
        # Too few providers healthy.
        {
            "name": "Providers healthy",
            "metric_type": "service_provider_error_rates",
            "threshold_type": "static",
            "threshold_operator": "less_than",
            "critical_threshold_value": 0.95,
            "warning_threshold_value": 0.98,
            "alert_frequency": 60,
        },
    ]
    return [model.objects.create(**scope, **row).pk for row in rows]


def _rules(pks) -> dict:
    return dict(
        UserAlertMonitor._base_manager.filter(pk__in=pks).values_list(
            "pk", "threshold_rule"
        )
    )


@pytest.mark.django_db
def test_threshold_rule_model_and_migration_keep_a_database_default():
    field = UserAlertMonitor._meta.get_field("threshold_rule")
    assert (field.default, field.db_default, field.max_length) == (
        "current",
        "earlier",
        16,
    )
    assert [value for value, _ in field.choices] == ["earlier", "current"]
    default, nullable = _database_default()
    assert "earlier" in default
    assert nullable == "NO"

    loader = MigrationLoader(connection)
    migration = loader.graph.nodes[CURRENT]
    assert migration.dependencies == [PREVIOUS]
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    assert isinstance(operation, AddField)
    assert (operation.model_name, operation.name) == (
        "useralertmonitor",
        "threshold_rule",
    )
    assert (operation.field.default, operation.field.db_default) == (
        "current",
        "earlier",
    )
    state_field = (
        loader.project_state([CURRENT])
        .apps.get_model("tracer", "UserAlertMonitor")
        ._meta.get_field("threshold_rule")
    )
    assert state_field.db_default == field.db_default


@pytest.mark.django_db(transaction=True)
def test_monitors_saved_before_the_update_read_earlier_and_new_ones_current(
    organization, workspace, observe_project
):
    # The executor gets the connection object itself, not django.db.connection (a proxy): the hard-erase
    # guard admits migration-time writes only when the migration's connection is connections[alias].
    executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
    original_targets = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([PREVIOUS])
        assert _database_default() is None
        production = executor.loader.project_state([PREVIOUS]).apps.get_model(
            "tracer", "UserAlertMonitor"
        )
        saved = _production_rows(production, organization, workspace, observe_project)

        executor.loader.build_graph()
        executor.migrate([CURRENT])
        default, nullable = _database_default()
        assert "earlier" in default and nullable == "NO"
        assert _rules(saved) == {pk: "earlier" for pk in saved}

        # An older server still rolling out inserts without the column.
        late = production.objects.create(
            organization_id=organization.id,
            workspace_id=workspace.id,
            project_id=observe_project.id,
            name="Saved by an older server",
            metric_type="count_of_errors",
            threshold_type="percentage_change",
            threshold_operator="greater_than",
            critical_threshold_value=50,
            alert_frequency=60,
        )
        assert _rules([late.pk]) == {late.pk: "earlier"}

        # The ORM saves the current rule.
        new = UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name="Saved after the update",
            metric_type="count_of_errors",
            threshold_type="percentage_change",
            threshold_operator="greater_than",
            critical_threshold_value=50,
            alert_frequency=60,
        )
        assert _rules([new.pk]) == {new.pk: "current"}
    finally:
        executor.loader.build_graph()
        executor.migrate(original_targets)
