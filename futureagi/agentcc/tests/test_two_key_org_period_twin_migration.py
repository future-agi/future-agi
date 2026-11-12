"""prism 0089 is the c2.6 twin of the c2.5.x data migration
0081_normalize_two_key_org_period (hosted two-key org periods).

It is a bridge, not a replacement: no ``replaces``. A c2.5.x database that
recorded the c2.5.x name still PLANS 0089, and applying it runs no database
step (SkipDatabaseIfLegacyApplied). A c2.6 database without the c2.5.x name
runs it normally, and with no candidate rows it writes nothing.
"""

import importlib
from unittest.mock import patch

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

from agentcc.models.org_config import AgentccOrgConfig
from tfc.migration_bridge import LEGACY_APPLIED_NAMES, SkipDatabaseIfLegacyApplied

TWIN = ("prism", "0089_normalize_two_key_org_period")
C25X_NAME = ("prism", "0081_normalize_two_key_org_period")


def _module():
    return importlib.import_module(
        "agentcc.migrations.0089_normalize_two_key_org_period"
    )


def _disk_nodes():
    return set(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)


def _plan_with_recorded(recorded):
    """The forward plan for a database whose history holds exactly ``recorded``."""
    with patch.object(
        MigrationRecorder,
        "applied_migrations",
        return_value=dict.fromkeys(recorded),
    ):
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return executor, [(m.app_label, m.name) for m, _ in plan]


def test_twin_is_a_bridge_on_the_c26_leaf_not_a_replacement():
    migration = _module().Migration
    assert getattr(migration, "replaces", []) == []
    assert migration.dependencies == [("prism", "0088_mcp_shared_connect_generation")]
    assert LEGACY_APPLIED_NAMES[TWIN] == C25X_NAME
    (wrapper,) = migration.operations
    assert isinstance(wrapper, SkipDatabaseIfLegacyApplied)
    assert wrapper.legacy == C25X_NAME
    (inner,) = wrapper.operations
    assert inner.code is _module().forwards
    assert TWIN in _disk_nodes()


@pytest.mark.django_db
def test_a_database_that_recorded_the_c25x_name_plans_the_twin_with_no_database_step():
    recorded = (_disk_nodes() - {TWIN}) | {C25X_NAME}
    executor, plan = _plan_with_recorded(recorded)
    assert TWIN not in executor.loader.applied_migrations
    assert plan == [TWIN]
    (wrapper,) = _module().Migration.operations
    with (
        patch.object(SkipDatabaseIfLegacyApplied, "_legacy_applied", return_value=True),
        patch.object(wrapper.operations[0], "database_forwards") as inner,
    ):
        wrapper.database_forwards("prism", None, None, None)
    inner.assert_not_called()


@pytest.mark.django_db
def test_a_c26_database_without_it_plans_and_runs_the_twin():
    recorded = _disk_nodes() - {TWIN}
    executor, plan = _plan_with_recorded(recorded)
    assert TWIN not in executor.loader.applied_migrations
    assert plan == [TWIN]
    (wrapper,) = _module().Migration.operations
    with (
        patch.object(
            SkipDatabaseIfLegacyApplied, "_legacy_applied", return_value=False
        ),
        patch.object(wrapper.operations[0], "database_forwards") as inner,
        patch.object(wrapper.operations[0], "state_forwards"),
    ):
        wrapper.database_forwards("prism", None, _StateStub(), None)
    inner.assert_called_once()


@pytest.mark.django_db
def test_the_twin_is_a_no_op_with_no_candidate_rows(capsys):
    module = _module()
    assert not AgentccOrgConfig.no_workspace_objects.exists()
    with patch.object(module, "is_cloud_env", return_value=True):
        module.forwards(django_apps, None)
    assert not AgentccOrgConfig.no_workspace_objects.exists()
    assert capsys.readouterr().out.strip() == "0 0"


class _StateStub:
    def clone(self):
        return self
