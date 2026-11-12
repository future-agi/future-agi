"""Personal rotation authorization and attribution through real session auth."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve

from accounts.models.audit_log import AuditLog
from accounts.tests.personal_keys import session_client_for
from agentcc.models import AgentccAPIKey
from agentcc.tests.test_agentcc_permissions import _user_with_level
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

KEYS = "/agentcc/api-keys/"
REFUSAL = "Key rotation is not permitted"
ACTIONS = ("gateway_key.created", "gateway_key.rotated", "gateway_key.revoked")
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def isolated_gateway():
    view = resolve(KEYS).func.cls
    bridge = view.create.__globals__["auth_bridge"]
    gateway = MagicMock()
    gateway.create_key.side_effect = AssertionError("Legacy remote issuance forbidden")
    gateway.import_committed_key.return_value = True
    gateway.revoke_key_confirmed.return_value = False
    with (
        patch.object(bridge, "get_gateway_client", return_value=gateway),
        patch.dict(view.create.__globals__, {"ensure_first_org_config": MagicMock()}),
    ):
        yield gateway
    clear_workspace_context()


def key_for(user, organization, **kwargs):
    return AgentccAPIKey.unscoped_objects.create(
        user=user,
        organization=organization,
        gateway_key_id=f"rotation-old-{uuid.uuid4().hex}",
        name="rotation fixture",
        owner=user.email,
        **kwargs,
    )


def events(organization):
    return AuditLog.no_workspace_objects.filter(
        organization=organization, action__in=ACTIONS
    )


@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN])
def test_other_person_and_missing_id_have_identical_value_free_refusal(
    organization, workspace, isolated_gateway, level
):
    actor = _user_with_level(organization, level)
    member = _user_with_level(organization, Level.MEMBER)
    old = key_for(member, organization)
    client = session_client_for(actor, organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    bodies = []
    for marker in [str(old.pk), str(uuid.uuid4())]:
        response = client.post(
            KEYS, {"name": "replacement", "rotates_key_id": marker}, format="json"
        )
        assert response.status_code == 403
        bodies.append(response.data)
        assert REFUSAL in str(response.data)
        for value in (marker, str(member.pk), member.email, actor.email):
            assert value not in str(response.data)
        assert AgentccAPIKey.unscoped_objects.count() == before
        old.refresh_from_db()
        assert old.status == AgentccAPIKey.ACTIVE
        assert not old.deleted
    assert bodies[0] == bodies[1]
    isolated_gateway.create_key.assert_not_called()
    isolated_gateway.import_committed_key.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_not_called()
    assert not events(organization).exists()


@pytest.mark.parametrize("state", ["foreign-org", "deleted", "revoked"])
def test_unavailable_rotation_source_is_refused_before_issuance(
    organization, workspace, isolated_gateway, state
):
    from accounts.models import Organization

    actor = _user_with_level(organization, Level.ADMIN)
    old_org = organization
    if state == "foreign-org":
        old_org = Organization.objects.create(name="Rotation other organization")
    old = key_for(
        actor,
        old_org,
        deleted=state == "deleted",
        status=AgentccAPIKey.REVOKED if state == "revoked" else AgentccAPIKey.ACTIVE,
    )
    client = session_client_for(actor, organization, workspace)
    response = client.post(
        KEYS, {"name": "replacement", "rotates_key_id": str(old.pk)}, format="json"
    )
    assert response.status_code == 403
    assert REFUSAL in str(response.data)
    isolated_gateway.create_key.assert_not_called()
    isolated_gateway.import_committed_key.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_not_called()


@pytest.mark.parametrize("marker", [None, "", "not-a-uuid", [], {}])
def test_invalid_marker_never_falls_back_to_plain_create(
    organization, workspace, isolated_gateway, marker
):
    actor = _user_with_level(organization, Level.ADMIN)
    client = session_client_for(actor, organization, workspace)
    response = client.post(
        KEYS, {"name": "replacement", "rotates_key_id": marker}, format="json"
    )
    assert response.status_code == 400
    isolated_gateway.create_key.assert_not_called()
    isolated_gateway.import_committed_key.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_not_called()


def test_own_rotation_records_pair_and_actor_without_revoking(
    organization, workspace, isolated_gateway
):
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    client = session_client_for(actor, organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    response = client.post(
        KEYS, {"name": "replacement", "rotates_key_id": str(old.pk)}, format="json"
    )
    assert response.status_code == 201
    new = AgentccAPIKey.unscoped_objects.get(pk=response.data["result"]["id"])
    assert new.user_id == actor.pk
    assert new.owner == actor.email
    assert AgentccAPIKey.unscoped_objects.count() == before + 1
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    isolated_gateway.revoke_key_confirmed.assert_not_called()
    created = events(organization).get(action="gateway_key.created")
    rotated = events(organization).get(action="gateway_key.rotated")
    assert created.actor_id == rotated.actor_id == actor.pk
    assert created.target_id == rotated.target_id == new.pk
    assert rotated.changes == {"old_key_id": str(old.pk), "new_key_id": str(new.pk)}
    assert rotated.metadata == {"phase": "replacement_created"}
    assert str(response.data["result"]["key"]) not in str(rotated.changes)


@pytest.mark.parametrize("rotate", [False, True])
def test_client_owner_and_user_never_override_actor(
    organization, workspace, isolated_gateway, rotate
):
    actor = _user_with_level(organization, Level.ADMIN)
    member = _user_with_level(organization, Level.MEMBER)
    payload = {"name": "replacement", "owner": member.email, "user": str(member.pk)}
    if rotate:
        payload["rotates_key_id"] = str(key_for(actor, organization).pk)
    client = session_client_for(actor, organization, workspace)
    response = client.post(KEYS, payload, format="json")
    assert response.status_code == 201
    new = AgentccAPIKey.unscoped_objects.get(pk=response.data["result"]["id"])
    assert new.user_id == actor.pk
    assert new.owner == actor.email
    sent = isolated_gateway.import_committed_key.call_args.args[0]
    assert sent["owner"] == actor.email
    assert sent["metadata"]["user_id"] == str(actor.pk)
    assert events(organization).get(action="gateway_key.created").actor_id == actor.pk


def test_admin_revoke_of_member_key_records_actor_not_owner(
    organization, workspace, isolated_gateway
):
    actor = _user_with_level(organization, Level.ADMIN)
    member = _user_with_level(organization, Level.MEMBER)
    old = key_for(member, organization)
    client = session_client_for(actor, organization, workspace)
    response = client.post(f"{KEYS}{old.pk}/revoke/", format="json")
    assert response.status_code == 200
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.REVOKED
    event = events(organization).get(action="gateway_key.revoked")
    assert event.actor_id == actor.pk
    assert event.actor_id != member.pk
    assert event.target_id == old.pk
    isolated_gateway.create_key.assert_not_called()
    isolated_gateway.import_committed_key.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_source_row_is_locked_in_create_transaction(
    organization, workspace, isolated_gateway
):
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock evidence requires the PostgreSQL fixture")
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    client = session_client_for(actor, organization, workspace)
    response_factory = lambda *args: True
    with CaptureQueriesContext(connection) as queries:
        def observe(*args):
            assert not connection.in_atomic_block
            assert connection.get_autocommit()
            assert any(
                "agentcc_api_key" in query["sql"] and "FOR UPDATE" in query["sql"]
                for query in queries.captured_queries
            )
            return response_factory(*args)

        isolated_gateway.import_committed_key.side_effect = observe
        response = client.post(
            KEYS,
            {"name": "replacement", "rotates_key_id": str(old.pk)},
            format="json",
        )
    assert response.status_code == 201
    assert not connection.in_atomic_block
