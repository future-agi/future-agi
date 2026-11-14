"""The export key a simulated run's own process sends its spans with."""

import uuid
from unittest.mock import patch

import pytest
from django.db import connection
from django.db.models.query import QuerySet

from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace
from simulate.models.agent_definition import AgentDefinition
from tfc.middleware.workspace_context import workspace_context
from tracer.services.collector_ingest import (
    ExportCredentials,
    get_or_create_export_credentials,
)

# The collector's key lookup (fi-collector/pkg/auth/pgresolver.go, ValidateKey)
# with psycopg placeholders, extended with the join that routes a key to its
# agent. No collector runs this shape yet; it pins what the columns must answer.
# It is a hand copy that documents the current Go query, not a guard: if the
# collector's query changes, this copy will not, and these tests stay green.
COLLECTOR_VALIDATE_KEY_SQL = """
    SELECT k.organization_id, k.workspace_id, k.user_id, k.type,
           k.simulation_agent_id, a.organization_id, a.deleted
    FROM accounts_orgapikey k
    LEFT JOIN simulate_agent_definition a ON a.id = k.simulation_agent_id
    WHERE k.api_key = %s AND k.secret_key = %s
      AND k.enabled = true AND k.deleted = false
    LIMIT 1
"""


def _second_workspace(organization, user):
    return Workspace.objects.create(
        name="Second Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


def _agent_definition(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Export Key Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Agent that owns a sandbox key",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


def _ingest_rows(organization, workspace=None, agent_id=None):
    rows = OrgApiKey.no_workspace_objects.filter(
        organization=organization, type="ingest"
    )
    if workspace is not None:
        rows = rows.filter(workspace=workspace)
    if agent_id is not None:
        rows = rows.filter(simulation_agent_id=agent_id)
    return rows


def _seed_ingest_key(organization, workspace, agent_id, **extra):
    return OrgApiKey.no_workspace_objects.create(
        name=f"simulation_export_key {agent_id}",
        organization=organization,
        workspace=workspace,
        type="ingest",
        user=None,
        simulation_agent_id=agent_id,
        **extra,
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_get_or_create_export_credentials_creates_once_per_agent(
    user, organization, workspace
):
    system = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, type="system"
    )
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()

    first = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        agent_definition_id=str(agent_a),
    )

    assert isinstance(first, ExportCredentials)
    assert first.enabled is True
    assert first.api_key != system.api_key
    row = _ingest_rows(organization, workspace, agent_a).get()
    assert row.name == f"simulation_export_key {agent_a}"
    assert row.type == "ingest"
    assert row.user_id is None
    assert row.workspace_id == workspace.id
    assert (first.api_key, first.secret_key) == (row.api_key, row.secret_key)

    again = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        agent_definition_id=str(agent_a),
    )
    assert again == first
    assert _ingest_rows(organization, workspace).count() == 1

    # A second agent of the same workspace gets its own key.
    second = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        agent_definition_id=str(agent_b),
    )
    assert second is not None
    assert second.api_key != first.api_key
    second_row = _ingest_rows(organization, workspace, agent_b).get()
    assert second_row.name == f"simulation_export_key {agent_b}"
    assert second_row.user_id is None
    assert _ingest_rows(organization, workspace).count() == 2

    # The same agent id in another workspace is a separate key.
    other_workspace = _second_workspace(organization, user)
    other = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(other_workspace.id),
        agent_definition_id=str(agent_a),
    )
    assert other is not None
    assert other.api_key not in {first.api_key, second.api_key}
    assert _ingest_rows(organization, other_workspace).get().api_key == other.api_key
    assert _ingest_rows(organization).count() == 3


@pytest.mark.integration
@pytest.mark.django_db
def test_get_or_create_export_credentials_normalises_the_agent_id(
    organization, workspace
):
    agent_id = uuid.uuid4()

    first = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        agent_definition_id=agent_id.hex.upper(),
    )
    again = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        agent_definition_id=agent_id,
    )

    assert first == again
    row = _ingest_rows(organization, workspace).get()
    assert row.simulation_agent_id == agent_id
    assert row.name == f"simulation_export_key {agent_id}"


@pytest.mark.integration
@pytest.mark.django_db
def test_get_or_create_export_credentials_survives_a_create_race(
    user, organization, workspace
):
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    winner = _seed_ingest_key(organization, workspace, agent_a)
    # A neighbour agent in the same workspace: a lookup that drops the agent
    # finds two rows instead of the winner.
    neighbour = _seed_ingest_key(organization, workspace, agent_b)

    real_first = QuerySet.first
    real_get = QuerySet.get
    real_create = QuerySet.create
    missed = []
    first_missed = []

    def first_lookup_misses(queryset, *args, **kwargs):
        # The lookup this launch made before the other launch committed its row.
        if not first_missed:
            first_missed.append(True)
            return None
        return real_first(queryset, *args, **kwargs)

    def first_read_misses(queryset, *args, **kwargs):
        # The read this launch made before the other launch committed its row.
        if not missed:
            missed.append(True)
            raise OrgApiKey.DoesNotExist
        return real_get(queryset, *args, **kwargs)

    with (
        patch(
            "django.db.models.query.QuerySet.first",
            autospec=True,
            side_effect=first_lookup_misses,
        ),
        patch(
            "django.db.models.query.QuerySet.get",
            autospec=True,
            side_effect=first_read_misses,
        ),
        patch(
            "django.db.models.query.QuerySet.create",
            autospec=True,
            side_effect=real_create,
        ) as create,
    ):
        result = get_or_create_export_credentials(
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            agent_definition_id=str(agent_a),
        )

    assert first_missed == [True]
    assert missed == [True]
    create.assert_called_once()
    assert result == ExportCredentials(winner.api_key, winner.secret_key, True)
    assert _ingest_rows(organization, workspace, agent_a).count() == 1
    assert _ingest_rows(organization, workspace, agent_b).get().id == neighbour.id
    assert _ingest_rows(organization, workspace).count() == 2


@pytest.mark.integration
@pytest.mark.django_db
def test_get_or_create_export_credentials_returns_a_disabled_key_as_disabled(
    user, organization, workspace
):
    agent_id = uuid.uuid4()
    disabled = _seed_ingest_key(organization, workspace, agent_id, enabled=False)

    result = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        agent_definition_id=str(agent_id),
    )

    assert result == ExportCredentials(disabled.api_key, disabled.secret_key, False)
    assert _ingest_rows(organization).count() == 1
    disabled.refresh_from_db()
    assert disabled.enabled is False


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("was_enabled", [True, False], ids=["enabled", "disabled"])
def test_get_or_create_export_credentials_keeps_a_deleted_key_stopped(
    user, organization, workspace, was_enabled
):
    agent_id, neighbour_id = uuid.uuid4(), uuid.uuid4()
    deleted = _seed_ingest_key(
        organization, workspace, agent_id, enabled=was_enabled, deleted=True
    )
    ids = {
        "organization_id": str(organization.id),
        "workspace_id": str(workspace.id),
    }

    # Deleting the key stopped the export: no new key is minted, even while the
    # request's workspace context names another workspace.
    with workspace_context(_second_workspace(organization, user), organization):
        result = get_or_create_export_credentials(**ids, agent_definition_id=agent_id)

    assert result == ExportCredentials(deleted.api_key, deleted.secret_key, False)
    rows = OrgApiKey._base_manager.filter(
        organization=organization, type="ingest", simulation_agent_id=agent_id
    )
    assert rows.count() == 1
    deleted.refresh_from_db()
    assert (deleted.deleted, deleted.enabled) == (True, was_enabled)

    # Another agent of the same workspace still gets its own key.
    neighbour = get_or_create_export_credentials(
        **ids, agent_definition_id=neighbour_id
    )
    assert neighbour is not None
    assert neighbour.enabled is True
    assert neighbour.api_key != deleted.api_key

    # Restoring the key restarts the export with the same pair.
    deleted.deleted = False
    deleted.save(update_fields=["deleted"])
    restored = get_or_create_export_credentials(**ids, agent_definition_id=agent_id)
    assert restored == ExportCredentials(
        deleted.api_key, deleted.secret_key, was_enabled
    )
    assert rows.count() == 1

    # A live key beside a deleted one wins: delete the restored key again and
    # give the agent a fresh one by hand.
    deleted.deleted = True
    deleted.save(update_fields=["deleted"])
    fresh = _seed_ingest_key(organization, workspace, agent_id)
    current = get_or_create_export_credentials(**ids, agent_definition_id=agent_id)
    assert current == ExportCredentials(fresh.api_key, fresh.secret_key, True)
    assert rows.count() == 2


@pytest.mark.integration
@pytest.mark.django_db
def test_get_or_create_export_credentials_refuses_empty_ids(
    user, organization, workspace
):
    org_id, ws_id, agent_id = str(organization.id), str(workspace.id), str(uuid.uuid4())

    assert (
        get_or_create_export_credentials(
            organization_id="", workspace_id=ws_id, agent_definition_id=agent_id
        )
        is None
    )
    assert (
        get_or_create_export_credentials(
            organization_id=org_id, workspace_id="", agent_definition_id=agent_id
        )
        is None
    )
    assert (
        get_or_create_export_credentials(
            organization_id=org_id, workspace_id=ws_id, agent_definition_id=""
        )
        is None
    )
    assert (
        get_or_create_export_credentials(
            organization_id=None, workspace_id=ws_id, agent_definition_id=agent_id
        )
        is None
    )
    assert (
        get_or_create_export_credentials(
            organization_id=org_id, workspace_id=None, agent_definition_id=agent_id
        )
        is None
    )
    assert (
        get_or_create_export_credentials(
            organization_id=org_id, workspace_id=ws_id, agent_definition_id=None
        )
        is None
    )
    assert not _ingest_rows(organization).exists()

    with pytest.raises(ValueError):
        get_or_create_export_credentials(
            organization_id=org_id, workspace_id=ws_id, agent_definition_id="not-a-uuid"
        )
    assert not _ingest_rows(organization).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_collector_validate_key_query_accepts_the_ingest_row(
    user, organization, workspace
):
    agent = _agent_definition(organization, workspace)
    credentials = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        agent_definition_id=str(agent.id),
    )

    with connection.cursor() as cursor:
        cursor.execute(
            COLLECTOR_VALIDATE_KEY_SQL,
            [credentials.api_key, credentials.secret_key],
        )
        rows = cursor.fetchall()

    assert len(rows) == 1
    (
        organization_id,
        workspace_id,
        user_id,
        key_type,
        agent_id,
        agent_organization_id,
        agent_deleted,
    ) = rows[0]
    assert str(organization_id) == str(organization.id)
    assert str(workspace_id) == str(workspace.id)
    assert user_id is None
    assert key_type == "ingest"
    assert str(agent_id) == str(agent.id)
    assert str(agent_organization_id) == str(organization.id)
    assert agent_deleted is False


@pytest.mark.integration
@pytest.mark.django_db
def test_collector_validate_key_query_keeps_the_row_of_a_hard_deleted_agent(
    user, organization, workspace
):
    # The column is a plain UUID, not a relation: deleting the agent for good
    # must leave the key in place, still naming the agent.
    assert OrgApiKey._meta.get_field("simulation_agent_id").is_relation is False
    agent = _agent_definition(organization, workspace)
    credentials = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        agent_definition_id=str(agent.id),
    )

    AgentDefinition.all_objects.filter(id=agent.id).delete()
    assert not AgentDefinition.all_objects.filter(id=agent.id).exists()
    survivor = _ingest_rows(organization, workspace, agent.id).get()
    assert (survivor.api_key, survivor.secret_key) == (
        credentials.api_key,
        credentials.secret_key,
    )
    assert survivor.simulation_agent_id == agent.id
    assert survivor.deleted is False

    with connection.cursor() as cursor:
        cursor.execute(
            COLLECTOR_VALIDATE_KEY_SQL,
            [credentials.api_key, credentials.secret_key],
        )
        rows = cursor.fetchall()

    assert len(rows) == 1
    _, _, _, key_type, agent_id, agent_organization_id, agent_deleted = rows[0]
    assert key_type == "ingest"
    assert str(agent_id) == str(agent.id)
    assert agent_organization_id is None
    assert agent_deleted is None
