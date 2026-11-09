"""Platform export stays OFF without issuing or exposing stored credentials.

Historical collector query checks below seed rows directly; they do not use
an automatic issuer or claim that the platform export path is enabled.
"""

import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import connection
from django.db.models.query import QuerySet

from accounts.api_secrets import verify_secret
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
@pytest.mark.django_db
@pytest.mark.parametrize("spans", [[], [{"name": "synthetic-span"}]])
def test_platform_span_emission_is_off_without_export_or_key_creation(
    organization, workspace, spans
):
    from tracer.services.collector_ingest import emit_spans_to_collector

    before = OrgApiKey._base_manager.count()
    with (
        patch("simulate.services.sim_collector_emit.export_sim_spans") as export,
        patch("tracer.services.collector_ingest.logger") as log,
    ):
        count = emit_spans_to_collector(
            spans,
            project_name="synthetic", project_type="observe",
            organization_id=str(organization.pk), workspace_id=str(workspace.pk),
        )
    assert count == 0
    export.assert_not_called()
    assert OrgApiKey._base_manager.count() == before
    if spans:
        log.info.assert_called_once_with(
            "platform_span_export_off", state="OFF", reason="run_token_unavailable"
        )
    else:
        log.info.assert_not_called()


def _collector_query(name):
    """A key query exactly as fi-collector runs it (pkg/auth/pgresolver.go), with
    psycopg placeholders. Read from the Go source, so this copy cannot drift."""
    go = (
        Path(__file__).resolve().parents[3] / "fi-collector/pkg/auth/pgresolver.go"
    ).read_text()
    found = re.search(rf"const {name} = `(.*?)`", go, re.S)
    assert found, f"{name} not found in pgresolver.go"
    return found.group(1).replace("$1", "%s")


# READINESS L03 (integration round 7): the collector looks a key up by api_key
# only and checks the secret in Go against its keyed hash, as verify_secret does.
COLLECTOR_VALIDATE_KEY_SQL = _collector_query("validateKeyWithAgentQuery")


def _collector_rows(api_key, presented_secret):
    """The rows the collector accepts for this pair: the lookup, then the check."""
    with connection.cursor() as cursor:
        cursor.execute(COLLECTOR_VALIDATE_KEY_SQL, [api_key])
        rows = cursor.fetchall()
    return [
        (*row[:4], *row[6:])
        for row in rows
        if verify_secret(row[5], row[4], presented_secret).ok
    ]


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
def test_get_or_create_export_credentials_creates_no_key_for_any_agent(
    user, organization, workspace
):
    system = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, type="system"
    )
    before = list(OrgApiKey._base_manager.values())
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    other_workspace = _second_workspace(organization, user)

    # Same launch, repeat launch, second agent, and another workspace all stay
    # OFF. The historical system pair must not be exported as a fallback.
    for agent_id, target_workspace in (
        (agent_a, workspace), (agent_a, workspace),
        (agent_b, workspace), (agent_a, other_workspace),
    ):
        with patch("tracer.services.collector_ingest.logger") as log:
            result = get_or_create_export_credentials(
                organization_id=str(organization.id),
                workspace_id=str(target_workspace.id),
                agent_definition_id=str(agent_id),
            )
        assert isinstance(result, ExportCredentials)
        assert result.enabled is False
        assert result.api_key == ""
        assert result.secret_key == ""
        assert system.api_key not in repr(result)
        assert system.secret_key not in repr(result)
        assert not _ingest_rows(organization, target_workspace, agent_id).exists()
        assert list(OrgApiKey._base_manager.values()) == before
        log.info.assert_called_once_with(
            "sandbox_span_export_off", state="OFF", reason="run_token_unavailable"
        )
    assert not _ingest_rows(organization).exists()


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

    assert first == again == ExportCredentials("", "", False)
    assert first.api_key == ""
    assert first.secret_key == ""
    assert not _ingest_rows(organization, workspace).exists()


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

    # OFF never enters the lookup/create race, nor retrieves the winner's pair.
    assert first_missed == []
    assert missed == []
    create.assert_not_called()
    assert result == ExportCredentials("", "", False)
    assert winner.api_key not in repr(result)
    assert winner.secret_key not in repr(result)
    assert _ingest_rows(organization, workspace, agent_a).get().id == winner.id
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

    assert result == ExportCredentials("", "", False)
    assert disabled.api_key not in repr(result)
    assert disabled.secret_key not in repr(result)
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

    assert result == ExportCredentials("", "", False)
    assert deleted.api_key not in repr(result)
    rows = OrgApiKey._base_manager.filter(
        organization=organization, type="ingest", simulation_agent_id=agent_id
    )
    assert rows.count() == 1
    deleted.refresh_from_db()
    assert (deleted.deleted, deleted.enabled) == (True, was_enabled)

    # A neighbouring agent cannot mint a replacement either.
    neighbour = get_or_create_export_credentials(
        **ids, agent_definition_id=neighbour_id
    )
    assert neighbour is not None
    assert neighbour.enabled is False
    assert neighbour.api_key == ""
    assert neighbour.secret_key == ""
    assert not _ingest_rows(organization, workspace, neighbour_id).exists()

    # Restoring a historical row must not restart the retired export path.
    deleted.deleted = False
    deleted.save(update_fields=["deleted"])
    restored = get_or_create_export_credentials(**ids, agent_definition_id=agent_id)
    assert restored == ExportCredentials("", "", False)
    assert rows.count() == 1
    deleted.refresh_from_db()
    assert deleted.enabled == was_enabled

    # Even a manually seeded live row alongside the deleted row stays private.
    deleted.deleted = True
    deleted.save(update_fields=["deleted"])
    fresh = _seed_ingest_key(organization, workspace, agent_id)
    current = get_or_create_export_credentials(**ids, agent_definition_id=agent_id)
    assert current == ExportCredentials("", "", False)
    assert fresh.api_key not in repr(current)
    assert fresh.secret_key not in repr(current)
    assert rows.count() == 2


@pytest.mark.integration
@pytest.mark.django_db
def test_get_or_create_export_credentials_refuses_empty_ids(
    user, organization, workspace
):
    org_id, ws_id, agent_id = str(organization.id), str(workspace.id), str(uuid.uuid4())

    # Invalid and missing IDs still cannot issue or retrieve a credential.
    # The disabled path reports OFF without parsing identifiers or touching DB.
    for org_value, ws_value, agent_value in (
        ("", ws_id, agent_id), (org_id, "", agent_id), (org_id, ws_id, ""),
        (None, ws_id, agent_id), (org_id, None, agent_id), (org_id, ws_id, None),
        (org_id, ws_id, "not-a-uuid"),
    ):
        with patch("tracer.services.collector_ingest.logger") as log:
            result = get_or_create_export_credentials(
                organization_id=org_value,
                workspace_id=ws_value,
                agent_definition_id=agent_value,
            )
        assert result == ExportCredentials("", "", False)
        assert not _ingest_rows(organization).exists()
        log.info.assert_called_once_with(
            "sandbox_span_export_off", state="OFF", reason="run_token_unavailable"
        )


@pytest.mark.integration
@pytest.mark.django_db
def test_collector_validate_key_query_accepts_the_ingest_row(
    user, organization, workspace
):
    # Historical storage/query coverage; no platform issuer is used. The
    # collector admission-policy replacement is a separate integration lane.
    agent = _agent_definition(organization, workspace)
    credentials = _seed_ingest_key(organization, workspace, agent.id)

    rows = _collector_rows(credentials.api_key, credentials.secret_key)
    assert _collector_rows(credentials.api_key, "not-the-secret") == []

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
    credentials = _seed_ingest_key(organization, workspace, agent.id)

    AgentDefinition.all_objects.filter(id=agent.id).delete()
    assert not AgentDefinition.all_objects.filter(id=agent.id).exists()
    survivor = _ingest_rows(organization, workspace, agent.id).get()
    assert (survivor.api_key, survivor.reveal_secret()) == (
        credentials.api_key,
        credentials.secret_key,
    )
    assert survivor.simulation_agent_id == agent.id
    assert survivor.deleted is False

    rows = _collector_rows(credentials.api_key, credentials.secret_key)

    assert len(rows) == 1
    _, _, _, key_type, agent_id, agent_organization_id, agent_deleted = rows[0]
    assert key_type == "ingest"
    assert str(agent_id) == str(agent.id)
    assert agent_organization_id is None
    assert agent_deleted is None


@pytest.mark.integration
@pytest.mark.django_db
def test_get_or_create_export_credentials_fails_closed_on_an_unrecoverable_secret(
    user, organization, workspace
):
    # Round 7 (L03 x dev's ingest keys): a key whose encrypted copy is gone (a
    # hash-only row) is never handed out and never given a new secret.
    agent_id = uuid.uuid4()
    row = _seed_ingest_key(organization, workspace, agent_id)
    assert row.enabled is True and row.secret_key
    OrgApiKey._base_manager.filter(pk=row.pk).update(secret_key_ciphertext="")
    before = OrgApiKey._base_manager.filter(pk=row.pk).values().get()

    result = get_or_create_export_credentials(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id),
        agent_definition_id=str(agent_id),
    )

    assert result == ExportCredentials("", "", False)
    assert row.api_key not in repr(result)
    assert row.secret_key not in repr(result)
    assert OrgApiKey._base_manager.filter(pk=row.pk).values().get() == before
    assert _ingest_rows(organization, workspace, agent_id).count() == 1
