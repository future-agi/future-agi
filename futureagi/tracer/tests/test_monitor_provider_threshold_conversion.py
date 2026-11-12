"""Saved service-provider error-rate monitors keep alerting exactly as before.

The metric now measures the share of providers that had an error in the
window; it used to measure the share that had none, so the new value is 1 - the
old one. 0134 converts every static monitor on it saved under the old meaning:
the operator flips, each threshold v becomes 1 - v and the row is marked
"current". Running it again changes nothing, and reversing it restores the
saved operator and numbers exactly. Rows it cannot convert exactly keep the
earlier rule, which reads them on the share with no errors.
"""

import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.db import DEFAULT_DB_ALIAS, connection, connections
from django.db.migrations.executor import MigrationExecutor

from tracer.models.monitor import UserAlertMonitor
from tracer.utils.monitor import _compare

BEFORE_RULE = ("tracer", "0132_seed_alert_issue_occurrences_high_water")
ADD_RULE = ("tracer", "0133_useralertmonitor_threshold_rule")
CONVERT = ("tracer", "0134_convert_provider_error_rate_thresholds")
PROVIDER = "service_provider_error_rates"
GT, LT = "greater_than", "less_than"
FIELDS = (
    "metric_type",
    "threshold_type",
    "threshold_operator",
    "critical_threshold_value",
    "warning_threshold_value",
    "threshold_rule",
    "deleted",
    "updated_at",
)

# name: (saved row, (operator, critical, warning) after conversion, or None
# when the row is left exactly as saved).
CASES = {
    # "Too few providers error-free": 95% / 98%.
    "share-error-free-below": (
        {"threshold_operator": LT, "critical": 0.95, "warning": 0.98},
        (GT, 0.05, 0.02),
    ),
    "share-error-free-above": (
        {"threshold_operator": GT, "critical": 0.2, "warning": 0.1},
        (LT, 0.8, 0.9),
    ),
    # Boundaries: "below 0" and "above 1" never fire on either meaning.
    "boundary-below-0-warn-below-1": (
        {"threshold_operator": LT, "critical": 0.0, "warning": 1.0},
        (GT, 1.0, 0.0),
    ),
    "boundary-above-1-warn-above-0": (
        {"threshold_operator": GT, "critical": 1.0, "warning": 0.0},
        (LT, 0.0, 1.0),
    ),
    "no-warning": (
        {"threshold_operator": GT, "critical": 0.5, "warning": None},
        (LT, 0.5, None),
    ),
    # A deleted monitor is converted too, so restoring it restores its alerts.
    "deleted": (
        {"threshold_operator": LT, "critical": 0.9, "warning": None, "deleted": True},
        (GT, 0.1, None),
    ),
    # A share is at most 1: "above 95" never fires on either meaning. Its
    # numbers have no exact non-negative conversion, so it keeps the earlier
    # rule and is read on the share with no errors.
    "above-one": (
        {"threshold_operator": GT, "critical": 95.0, "warning": None},
        None,
    ),
    # A threshold no one types: 1/3 written out to 16 digits. Its 1 - v
    # converts back, but with 1 of 3 providers error-free the old row stays
    # quiet (1/3 is not above it) while "below 0.6666666666666667" would fire
    # (2/3 rounds below it). It keeps its numbers and the earlier rule.
    "third-written-out": (
        {"threshold_operator": GT, "critical": 0.3333333333333333, "warning": None},
        None,
    ),
    # A percentage-change monitor keeps its numbers and its stored rule.
    "percentage-change": (
        {
            "threshold_type": "percentage_change",
            "threshold_operator": GT,
            "critical": 200.0,
            "warning": 100.0,
        },
        None,
    ),
    # Another metric is not touched.
    "other-metric": (
        {
            "metric_type": "llm_api_failure_rates",
            "threshold_operator": LT,
            "critical": 0.95,
            "warning": 0.98,
        },
        None,
    ),
}


def _migration_module():
    return importlib.import_module(
        "tracer.migrations.0134_convert_provider_error_rate_thresholds"
    )


def _save_production_rows(model, organization, workspace, project) -> dict:
    """Insert each case through a model without threshold_rule, as production did."""
    pks = {}
    for name, (saved, _) in CASES.items():
        pks[name] = model.objects.create(
            organization_id=organization.id,
            workspace_id=workspace.id,
            project_id=project.id,
            name=name,
            metric_type=saved.get("metric_type", PROVIDER),
            threshold_type=saved.get("threshold_type", "static"),
            threshold_operator=saved["threshold_operator"],
            critical_threshold_value=saved["critical"],
            warning_threshold_value=saved["warning"],
            alert_frequency=60,
            deleted=saved.get("deleted", False),
        ).pk
    return pks


def _snapshot(pks: dict) -> dict:
    rows = UserAlertMonitor._base_manager.in_bulk(list(pks.values()))
    return {
        name: tuple(getattr(rows[pk], field) for field in FIELDS)
        for name, pk in pks.items()
    }


def _thresholds(snapshot_row) -> tuple:
    return snapshot_row[2:5]


def _static_alert(operator, critical, warning, value):
    """The static check's decision (tracer.utils.monitor._check_static_threshold)."""
    if critical is not None and _compare(value, operator, critical):
        return "critical"
    if warning is not None and _compare(value, operator, warning):
        return "warning"
    return None


@pytest.mark.django_db(transaction=True)
def test_provider_thresholds_convert_once_and_reverse_exactly(
    organization, workspace, observe_project
):
    # The executor gets the connection object itself, not django.db.connection (a proxy): the hard-erase
    # guard admits migration-time writes only when the migration's connection is connections[alias].
    executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
    original_targets = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([ADD_RULE])
        production = executor.loader.project_state([BEFORE_RULE]).apps.get_model(
            "tracer", "UserAlertMonitor"
        )
        pks = _save_production_rows(
            production, organization, workspace, observe_project
        )
        saved = _snapshot(pks)
        assert {row[5] for row in saved.values()} == {"earlier"}

        executor.loader.build_graph()
        executor.migrate([CONVERT])
        converted = _snapshot(pks)

        for name, (_, expected) in CASES.items():
            if expected is None:
                assert converted[name] == saved[name], name
                continue
            assert converted[name][:2] == saved[name][:2], name
            assert _thresholds(converted[name]) == expected, name
            assert converted[name][5] == "current", name
            assert converted[name][6] == saved[name][6], name

            # It alerts exactly as before: the saved numbers on the share with
            # no errors (a of n providers), the converted ones on the share
            # with errors, as ClickHouse divides them.
            for n in range(1, 21):
                for a in range(n + 1):
                    assert _static_alert(*_thresholds(saved[name]), a / n) == (
                        _static_alert(*expected, (n - a) / n)
                    ), (name, a, n)

        # A second run finds nothing left in the old meaning.
        _migration_module().convert_provider_thresholds(
            django_apps, SimpleNamespace(connection=connection)
        )
        assert _snapshot(pks) == converted

        # Reversing restores the saved operator and numbers exactly.
        executor.loader.build_graph()
        executor.migrate([ADD_RULE])
        assert _snapshot(pks) == saved
    finally:
        executor.loader.build_graph()
        executor.migrate(original_targets)


def test_conversion_migration_declares_its_update_safety():
    module = _migration_module()
    migration = module.Migration
    assert migration.dependencies == [ADD_RULE]
    [operation] = migration.operations
    assert operation.code is module.convert_provider_thresholds
    assert operation.reverse_code is module.restore_provider_thresholds
    declaration = module.UPDATE_SAFETY
    assert declaration["risk_classes"] == ["data_migration"]
    assert declaration["tables"] == ["postgres:tracer_useralertmonitor"]
    assert declaration["large_volume_rehearsal"] is True
