"""0029 adds agentcc_org_config.managed_models and 0045 adds identity and tools,
each a NOT NULL jsonb column. Django adds a column with a temporary default and
drops it, so until 0046 gave them a database default an INSERT from a pod still
on older code (a rolling upgrade from 1.58.0, whose model has none of the three)
failed NOT NULL, during every migration in between (0031's index builds
included). Each AddField now carries db_default={}, so the column keeps '{}'
from the moment it exists; 0046 changes nothing after them.

Real shape, real PostgreSQL DDL: a scratch copy of agentcc_org_config at the
schema before each migration (the historical model's own fields, its
constraints and indexes left out so their names stay unique), the migration's
own operations run on it from that state, then the row the older model writes,
through that model. The scratch DDL runs inside the migration boundary (an
unrecorded migration through the real executor, accounts' FixtureExecutor), the
only place the hard-erase guard admits schema SQL. Test values only.
"""

import pytest
from django.db import IntegrityError, connections, migrations, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState

from accounts.models import Organization
from accounts.tests.hard_erase_fixture import FixtureExecutor

pytestmark = pytest.mark.django_db(transaction=True)

APP = "prism"
MODEL = "agentccorgconfig"
SCRATCH = "agentcc_org_config_dbdefault_scratch"
# The last migration in 1.58.0 that touches the org config table.
RELEASED_LAST = "0028_drop_rejected_org_config_keys"
CASES = {
    # migration: (the state an older pod's model has, the columns it adds)
    "0029_org_config_managed_models": (
        "0028_drop_rejected_org_config_keys",
        ["managed_models"],
    ),
    "0045_org_config_tools_identity": ("0044_memory_forget", ["identity", "tools"]),
}


def _conn():
    return connections["default"]


def _scratch_state(state):
    """``state`` with the org config model on the scratch table, no
    Meta constraints or indexes (their names belong to the real table)."""
    state = state.clone()
    model = state.models[(APP, MODEL)]
    model.options = {
        **model.options,
        "db_table": SCRATCH,
        "constraints": [],
        "indexes": [],
    }
    state.reload_model(APP, MODEL, delay=False)
    return state


def _in_boundary(work):
    """Run ``work(schema_editor)`` as the body of an unrecorded migration."""
    migration = migrations.Migration("org_config_db_default_scratch", APP)
    migration.operations = [
        migrations.RunPython(
            lambda apps, editor: work(editor), migrations.RunPython.noop
        )
    ]
    FixtureExecutor(_conn()).apply_migration(ProjectState(), migration)


def _drop_scratch():
    _in_boundary(
        lambda editor: editor.execute(f'DROP TABLE IF EXISTS "{SCRATCH}" CASCADE')
    )


def _column_defaults(names):
    with _conn().cursor() as cursor:
        cursor.execute(
            "SELECT column_name, column_default, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s AND column_name = ANY(%s)",
            [SCRATCH, names],
        )
        return {name: (default, nullable) for name, default, nullable in cursor}


def _apply(migration_names, before):
    """The scratch table at ``before``'s schema, then each named migration's
    own operations on it, in order. Returns (state before, state after)."""
    loader = MigrationLoader(_conn())
    edited = [loader.get_migration(APP, name) for name in migration_names]
    old = _scratch_state(loader.project_state((APP, before), at_end=True))
    states = [old]

    def work(editor):
        editor.execute(f'DROP TABLE IF EXISTS "{SCRATCH}" CASCADE')
        editor.create_model(old.apps.get_model(APP, MODEL))
        for migration in edited:
            for operation in migration.operations:
                new = states[-1].clone()
                operation.state_forwards(APP, new)
                operation.database_forwards(APP, editor, states[-1], new)
                states.append(new)

    _in_boundary(work)
    return old, states[-1]


def _older_pod_insert(old_state, organization):
    """The INSERT a pod still on the older model sends: every field it has,
    none of the columns the migration added."""
    Old = old_state.apps.get_model(APP, MODEL)
    with transaction.atomic(using="default"):
        return Old._base_manager.db_manager("default").create(
            organization_id=organization.pk, version=1, is_active=True
        )


@pytest.mark.parametrize("migration_name", sorted(CASES))
def test_an_older_pods_settings_row_is_written_right_after_the_migration(
    migration_name,
):
    before, added = CASES[migration_name]
    organization = Organization.objects.create(name=f"db default {migration_name}")
    try:
        old, new = _apply([migration_name], before)
        # The columns exist, NOT NULL, each with the database default '{}'.
        defaults = _column_defaults(added)
        assert sorted(defaults) == sorted(added)
        for name, (default, nullable) in defaults.items():
            assert nullable == "NO", name
            assert default is not None and default.startswith("'{}'"), (name, default)
        row = _older_pod_insert(old, organization)
        New = new.apps.get_model(APP, MODEL)
        stored = New._base_manager.db_manager("default").get(pk=row.pk)
        for name in added:
            assert getattr(stored, name) == {}, name
    finally:
        _drop_scratch()


def test_a_1_58_0_pods_row_is_written_after_0029_and_0045():
    """1.58.0's org config model is the state at 0028: of the migrations after
    it up to 0046, only 0029, 0045 and 0046 touch the table (pinned here). Run
    0029 and 0045 on that schema; the row a 1.58.0 pod writes then stores {}
    in all three new columns."""
    loader = MigrationLoader(_conn())
    released = set(loader.graph.forwards_plan((APP, RELEASED_LAST)))
    plan = loader.graph.forwards_plan((APP, "0046_org_config_db_defaults"))
    touching = sorted(
        name
        for app, name in plan
        if app == APP
        and (app, name) not in released
        and any(
            getattr(op, "model_name_lower", None) == MODEL
            for op in loader.get_migration(app, name).operations
        )
    )
    assert touching == [
        "0029_org_config_managed_models",
        "0045_org_config_tools_identity",
        "0046_org_config_db_defaults",
    ]
    organization = Organization.objects.create(name="db default 1.58.0 pod")
    try:
        old, new = _apply(
            ["0029_org_config_managed_models", "0045_org_config_tools_identity"],
            RELEASED_LAST,
        )
        row = _older_pod_insert(old, organization)
        New = new.apps.get_model(APP, MODEL)
        stored = New._base_manager.db_manager("default").get(pk=row.pk)
        assert (stored.managed_models, stored.identity, stored.tools) == ({}, {}, {})
    finally:
        _drop_scratch()


@pytest.mark.parametrize("migration_name", sorted(CASES))
def test_0046_changes_nothing_after_the_migration(migration_name):
    """0046 stays in the chain: on a table the migration already gave its
    defaults, its AlterField operations emit no SQL."""
    before, added = CASES[migration_name]
    try:
        _, state = _apply([migration_name], before)
        loader = MigrationLoader(_conn())
        m0046 = loader.get_migration(APP, "0046_org_config_db_defaults")
        with _conn().schema_editor(collect_sql=True) as editor:
            for operation in m0046.operations:
                if operation.name not in added:
                    continue
                new = state.clone()
                operation.state_forwards(APP, new)
                operation.database_forwards(APP, editor, state, new)
                state = new
            assert editor.collected_sql == []
    finally:
        _drop_scratch()


def test_a_database_that_applied_them_has_nothing_to_migrate():
    """A database that already applied 0029-0047 (this one) plans no work for
    the edited migrations, and its real columns keep 0046's defaults."""
    executor = MigrationExecutor(_conn())
    applied = executor.loader.applied_migrations
    for name in (
        "0029_org_config_managed_models",
        "0045_org_config_tools_identity",
        "0046_org_config_db_defaults",
        "0047_encrypt_org_config_secrets",
    ):
        assert (APP, name) in applied, name
    assert executor.migration_plan(executor.loader.graph.leaf_nodes()) == []
    with _conn().cursor() as cursor:
        cursor.execute(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'agentcc_org_config' "
            "AND column_name IN ('managed_models', 'identity', 'tools')"
        )
        defaults = dict(cursor.fetchall())
    assert sorted(defaults) == ["identity", "managed_models", "tools"]
    assert all(d is not None and d.startswith("'{}'") for d in defaults.values())


def test_the_old_shape_really_failed():
    """The red this guards: without a database default, an older pod's row
    fails NOT NULL. Shown on the scratch table by dropping the default the
    migration now gives it."""
    before, added = CASES["0029_org_config_managed_models"]
    organization = Organization.objects.create(name="db default old shape")
    try:
        old, _ = _apply(["0029_org_config_managed_models"], before)
        _in_boundary(
            lambda editor: editor.execute(
                f'ALTER TABLE "{SCRATCH}" ALTER COLUMN managed_models DROP DEFAULT'
            )
        )
        with pytest.raises(IntegrityError):
            _older_pod_insert(old, organization)
    finally:
        _drop_scratch()
