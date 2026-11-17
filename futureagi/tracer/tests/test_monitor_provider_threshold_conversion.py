"""Saved service-provider error-rate monitors keep alerting exactly as before.

The metric now measures the share of providers that had an error in the
window; it used to measure the share that had none, so the new value is 1 - the
old one. 0134 converts every static monitor on it saved under the old meaning:
the operator flips, each threshold v becomes 1 - v and the row is marked
"current". Running it again changes nothing, and reversing it restores the
saved operator and numbers exactly. Rows it cannot convert exactly, an infinite
or not-a-number threshold among them, keep the earlier rule, which reads them on
the share with no errors, and it prints how many it left.
"""

import importlib
import math
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

INF, NAN = math.inf, math.nan

# PostgreSQL float columns hold infinity and not-a-number. name: (saved row,
# what each check with data raises on the share of providers with no errors:
# the same alert type every time, or None when it never fires).
NON_FINITE = {
    "above-infinity": (
        {"threshold_operator": GT, "critical": INF, "warning": None},
        None,
    ),
    "below-infinity": (
        {"threshold_operator": LT, "critical": INF, "warning": None},
        "critical",
    ),
    "above-minus-infinity": (
        {"threshold_operator": GT, "critical": -INF, "warning": None},
        "critical",
    ),
    "below-minus-infinity": (
        {"threshold_operator": LT, "critical": -INF, "warning": None},
        None,
    ),
    "above-not-a-number": (
        {"threshold_operator": GT, "critical": NAN, "warning": None},
        None,
    ),
    "below-not-a-number": (
        {"threshold_operator": LT, "critical": NAN, "warning": None},
        None,
    ),
    # A share is 0 to 1, so these critical thresholds never fire and the
    # warning alone decides.
    "warning-below-infinity": (
        {"threshold_operator": LT, "critical": 0.0, "warning": INF},
        "warning",
    ),
    "warning-above-minus-infinity": (
        {"threshold_operator": GT, "critical": 1.0, "warning": -INF},
        "warning",
    ),
    "warning-not-a-number": (
        {"threshold_operator": LT, "critical": 0.0, "warning": NAN},
        None,
    ),
}


def _migration_module():
    return importlib.import_module(
        "tracer.migrations.0134_convert_provider_error_rate_thresholds"
    )


def _save_production_rows(model, organization, workspace, project) -> dict:
    """Insert each case through a model without threshold_rule, as production did."""
    rows = {name: saved for name, (saved, _) in CASES.items()}
    return _save_rows(model, (organization, workspace, project), rows)


def _save_rows(model, owners, rows: dict, **fields) -> dict:
    """Insert each saved row through ``model``; return its pk by name."""
    organization, workspace, project = owners
    pks = {}
    for name, saved in rows.items():
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
            **fields,
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


def _same_floats(snapshot_row) -> tuple:
    """The row with each float as its repr, so a saved NaN equals itself."""
    return tuple(repr(v) if isinstance(v, float) else v for v in snapshot_row)


def _outcome(call, *args) -> tuple:
    """(what ``call(*args)`` returned, None), or (None, what it raised)."""
    try:
        return call(*args), None
    except Exception as error:
        return None, error


def _printed(output: str, words: str) -> list:
    return [line for line in output.splitlines() if words in line]


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


@pytest.mark.django_db(transaction=True)
def test_non_finite_thresholds_are_left_as_saved_and_counted(
    organization, workspace, observe_project, capsys
):
    executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
    original_targets = executor.loader.graph.leaf_nodes()
    # Every static row it leaves as saved is counted: non-finite, outside 0..1
    # and not exact. A percentage-change row is neither converted nor counted.
    left_as_saved = {name: saved for name, (saved, _) in NON_FINITE.items()}
    for name in ("above-one", "third-written-out"):
        left_as_saved[name] = CASES[name][0]
    not_counted = {"percentage-change": CASES["percentage-change"][0]}
    converted = "share-error-free-below"
    rows = {**left_as_saved, **not_counted, converted: CASES[converted][0]}
    pks = {}
    try:
        executor.migrate([ADD_RULE])
        production = executor.loader.project_state([BEFORE_RULE]).apps.get_model(
            "tracer", "UserAlertMonitor"
        )
        owners = (organization, workspace, observe_project)
        pks = _save_rows(production, owners, rows)
        saved = _snapshot(pks)
        assert {row[5] for row in saved.values()} == {"earlier"}

        executor.loader.build_graph()
        capsys.readouterr()
        _, error = _outcome(executor.migrate, [CONVERT])
        output = capsys.readouterr().out

        assert error is None
        after = _snapshot(pks)
        for name in (*left_as_saved, *not_counted):
            assert _same_floats(after[name]) == _same_floats(saved[name]), name
        assert after[converted][2:6] == (*CASES[converted][1], "current")
        assert _printed(output, "left unconverted") == [
            "[convert_provider_thresholds] service-provider error-rate monitors "
            f"left unconverted: {len(left_as_saved)} (they alert as before)"
        ]

        # Read on the share with no errors, as before: each row raises the same
        # alert on every check with data, or never fires.
        for name, (_, raised) in NON_FINITE.items():
            alerts = {
                _static_alert(*_thresholds(after[name]), a / n)
                for n in range(1, 21)
                for a in range(n + 1)
            }
            assert alerts == {raised}, name
    finally:
        # The restore below may run the conversion again; this test's rows are
        # marked current first so that it passes them by.
        UserAlertMonitor._base_manager.filter(pk__in=list(pks.values())).update(
            threshold_rule="current"
        )
        executor.loader.build_graph()
        executor.migrate(original_targets)


@pytest.mark.django_db(transaction=True)
def test_reverse_leaves_non_finite_current_thresholds_as_they_are(
    organization, workspace, observe_project, capsys
):
    executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
    original_targets = executor.loader.graph.leaf_nodes()
    left = {name: saved for name, (saved, _) in NON_FINITE.items()}
    # Saved after the update, in the current meaning: more than 5% / 2% of
    # providers had errors.
    restored = "saved-after-the-update"
    rows = {
        **left,
        restored: {"threshold_operator": GT, "critical": 0.05, "warning": 0.02},
    }
    pks = {}
    try:
        executor.migrate([CONVERT])
        current = executor.loader.project_state([CONVERT]).apps.get_model(
            "tracer", "UserAlertMonitor"
        )
        owners = (organization, workspace, observe_project)
        pks = _save_rows(current, owners, rows, threshold_rule="current")
        saved = _snapshot(pks)
        assert {row[5] for row in saved.values()} == {"current"}

        executor.loader.build_graph()
        capsys.readouterr()
        _, error = _outcome(executor.migrate, [ADD_RULE])
        output = capsys.readouterr().out

        assert error is None
        after = _snapshot(pks)
        for name in left:
            assert _same_floats(after[name]) == _same_floats(saved[name]), name
        assert after[restored][2:6] == (LT, 0.95, 0.98, "earlier")
        assert _printed(output, "left in the current meaning") == [
            "[restore_provider_thresholds] service-provider error-rate monitors "
            f"left in the current meaning: {len(left)}"
        ]
    finally:
        executor.loader.build_graph()
        executor.migrate(original_targets)


@pytest.mark.parametrize(
    "value", [INF, -INF, NAN], ids=["infinity", "minus-infinity", "not-a-number"]
)
def test_non_finite_threshold_has_no_exact_conversion(value):
    converts_exactly = _migration_module()._converts_exactly
    assert _outcome(converts_exactly, value) == (False, None)


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
