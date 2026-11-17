"""Real schema forward/back rehearsal on pytest's seeded disposable database."""

from collections import Counter
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.commands import makemigrations
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from simulate.models import HarnessEnvironmentVersion, HarnessSuiteVersion
from simulate.models import HarnessVersioningBackfillOutcome as Outcome
from simulate.models import HarnessVersioningBackfillProgress as Progress
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.harness_versioning.upgrade_runner import run_managed
from simulate.tests.test_harness_versioning_backfill import (
    _legacy,
    _RealisticStorage,
)

pytestmark = pytest.mark.django_db(transaction=True)

OLD = ("simulate", "0110_seed_system_personas")
NEW = ("simulate", "0111_harness_versioning_backfill_state")


def test_migration_graph_has_one_leaf_per_app_and_models_match():
    loader = MigrationExecutor(connection).loader
    leaves = Counter(app for app, _ in loader.graph.leaf_nodes())
    assert leaves
    assert all(count == 1 for count in leaves.values()), dict(leaves)
    assert not loader.detect_conflicts()
    # makemigrations also checks the direct alias's migration history.
    # Only this command's history reads use the already-isolated test connection;
    # never unblock all aliases or change the suite's database permissions.
    with patch.object(
        makemigrations,
        "connections",
        {"default": connection, "default_direct": connection},
    ):
        call_command("makemigrations", check=True, dry_run=True, verbosity=1)


def test_forward_and_rollback_preserve_seeded_versions(
    user, workspace, monkeypatch, settings
):
    from tfc.utils import storage_client

    settings.HARNESS_VERSIONING_ENABLED = True
    storage = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", storage)
    environment = _legacy(user, workspace, "upgrade-schema-seed", storage)
    assert backfill_environment(environment.id).outcome == "versioned"
    environment.refresh_from_db()
    pins = (environment.environment_version_id, environment.suite_version_id)
    assert all(pins)
    tables = {Progress._meta.db_table, Outcome._meta.db_table}

    def move(target):
        MigrationExecutor(connection).migrate([target])

    def preserved():
        environment.refresh_from_db()
        assert (
            environment.environment_version_id,
            environment.suite_version_id,
        ) == pins
        assert (
            HarnessEnvironmentVersion.no_workspace_objects.filter(
                job=environment
            ).count()
            == 1
        )
        assert (
            HarnessSuiteVersion.no_workspace_objects.filter(job=environment).count()
            == 1
        )

    try:
        move(OLD)
        assert not tables.intersection(connection.introspection.table_names())
        preserved()
        move(NEW)
        assert tables.issubset(connection.introspection.table_names())
        preserved()
        report = run_managed([environment.id], automatic=True)
        assert report.state == "complete"
        assert (
            Progress.objects.get(organization_id=environment.organization_id).processed
            == 1
        )
        assert Outcome.objects.get(environment=environment).outcome_code == "current"
        preserved()
        move(OLD)
        assert not tables.intersection(connection.introspection.table_names())
        preserved()
    finally:
        # Restore the schema even if an assertion fails, before pytest flushes it.
        move(NEW)
