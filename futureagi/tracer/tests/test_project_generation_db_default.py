"""The collector's raw project insert survives the analysis generation fence."""

import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations import AlterField
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from tracer.models.project import Project

PREVIOUS = ("tracer", "0128_instantevalrow_source_session_ids")
CURRENT = ("tracer", "0129_project_analysis_source_generation_db_default")

# fi-collector/pkg/auth/pgresolver.go:GetOrCreateProject, with only the driver
# placeholders changed ($1..$5 -> %s). In particular, an older/raw collector
# does not know analysis_source_generation. Django's Python default cannot
# satisfy this insert, nor the attempted insert on the ON CONFLICT path.
COLLECTOR_INSERT = """
INSERT INTO tracer_project (
    id, name, organization_id, workspace_id, trace_type,
    model_type, source, tags, deleted, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, 'GenerativeLLM', 'prototype', '[]', false, now(), now())
ON CONFLICT (name, trace_type, organization_id, workspace_id) WHERE NOT deleted
DO UPDATE SET updated_at = EXCLUDED.updated_at
RETURNING id
"""


def _collector_project(organization, workspace, name):
    with connection.cursor() as cursor:
        cursor.execute(
            COLLECTOR_INSERT,
            [uuid.uuid4(), name, organization.id, workspace.id, "observe"],
        )
        return cursor.fetchone()[0]


def _database_default():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'tracer_project' "
            "AND column_name = 'analysis_source_generation'"
        )
        return cursor.fetchone()


@pytest.mark.django_db
def test_generation_model_and_forward_migration_keep_a_database_default():
    field = Project._meta.get_field("analysis_source_generation")
    assert (field.default, field.db_default, field.editable) == (0, 0, False)
    default, nullable = _database_default()
    assert default is not None
    assert nullable == "NO"

    loader = MigrationLoader(connection)
    migration = loader.graph.nodes[CURRENT]
    assert migration.dependencies == [PREVIOUS]
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    assert isinstance(operation, AlterField)
    assert (operation.model_name, operation.name) == (
        "project",
        "analysis_source_generation",
    )
    assert (operation.field.default, operation.field.db_default) == (0, 0)
    state_field = (
        loader.project_state([CURRENT])
        .apps.get_model("tracer", "Project")
        ._meta.get_field("analysis_source_generation")
    )
    assert state_field.db_default == field.db_default


@pytest.mark.django_db
def test_raw_collector_insert_defaults_generation_and_retry_preserves_fence(
    organization, workspace
):
    project_id = _collector_project(organization, workspace, "collector-default")
    project = Project.with_simulation.using("default").get(id=project_id)
    assert project.analysis_source_generation == 0

    Project.with_simulation.using("default").filter(id=project_id).update(
        analysis_source_generation=23
    )
    repeated = _collector_project(organization, workspace, "collector-default")
    assert repeated == project_id
    project.refresh_from_db(using="default")
    assert project.analysis_source_generation == 23
    assert Project.with_simulation.using("default").filter(id=project_id).count() == 1


@pytest.fixture
def historical_database():
    from django.db import connections
    from psycopg import sql

    shared = connections["default"]
    historical = shared.copy()
    name = f"test_generation_{uuid.uuid4().hex}"
    historical.settings_dict["NAME"] = name
    with shared.cursor() as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            connections["default"] = historical
            yield historical
        finally:
            connections["default"] = shared
            historical.close()
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
    executor = MigrationExecutor(shared)
    assert executor.migration_plan(executor.loader.graph.leaf_nodes()) == []


@pytest.mark.django_db(transaction=True)
def test_forward_default_migration_preserves_existing_generations(historical_database):
    executor = MigrationExecutor(historical_database)
    apps = executor.migrate([PREVIOUS]).apps
    organization = apps.get_model("accounts", "Organization").objects.create(
        name="Generation"
    )
    user = apps.get_model("accounts", "User").objects.create(
        email="collector@example.invalid", organization=organization
    )
    workspace = apps.get_model("accounts", "Workspace").objects.create(
        name="default", organization=organization, created_by=user
    )
    projects = apps.get_model("tracer", "Project")._base_manager.using("default")
    project_id = projects.create(
        organization=organization,
        workspace=workspace,
        name="collector",
        trace_type="observe",
        model_type="GenerativeLLM",
    ).id
    assert _database_default() == (None, "NO")
    projects.filter(id=project_id).update(analysis_source_generation=17)
    untouched = projects.create(
        organization=organization,
        workspace=workspace,
        name="existing-zero-generation",
        trace_type="observe",
        model_type="GenerativeLLM",
    )
    before = dict(
        projects.filter(id__in=[project_id, untouched.id]).values_list(
            "id", "analysis_source_generation"
        )
    )
    assert before == {project_id: 17, untouched.id: 0}
    # Pin the actual old-schema failure rather than merely inspecting the
    # field: the database rejects the exact collector insert with 23502.
    with pytest.raises(IntegrityError) as failure, transaction.atomic():
        _collector_project(organization, workspace, "collector-upgrade")
    cause = failure.value.__cause__
    assert getattr(cause, "sqlstate", getattr(cause, "pgcode", None)) == "23502"

    executor.loader.build_graph()
    executor.migrate([CURRENT])
    default, nullable = _database_default()
    assert default is not None and nullable == "NO"
    after = dict(
        projects.filter(id__in=before).values_list("id", "analysis_source_generation")
    )
    assert after == before
    new_id = _collector_project(organization, workspace, "collector-upgrade")
    assert projects.get(id=new_id).analysis_source_generation == 0
