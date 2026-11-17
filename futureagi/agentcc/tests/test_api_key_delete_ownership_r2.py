"""Personal-key removal must not acquire the request's browsing workspace."""

import pytest
from django.core.exceptions import ValidationError

from accounts.models import Organization
from agentcc.models import AgentccAPIKey
from agentcc.models.api_key_ownership import IMMUTABLE_MESSAGE
from agentcc.tests import test_personal_key_self_service as personal_key_tests
from agentcc.tests.test_agentcc_permissions import _user_with_level
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import get_current_workspace, workspace_context

pytestmark = pytest.mark.django_db
gateway = personal_key_tests.gateway


def _ownership(key):
    return (key.organization_id, key.user_id, key.workspace_id)


def _existing_personal_key(organization, user):
    # Seed a valid org-wide key even on the RED tree, so DELETE itself is tested
    # rather than failing during setup's post-insert workspace assignment.
    with workspace_context(None, organization=organization, user=user):
        return AgentccAPIKey.unscoped_objects.create(
            organization=organization,
            user=user,
            workspace=None,
            name="personal-delete-regression",
            gateway_key_id="personal-delete-regression",
        )


def test_member_deletes_personal_key_without_reassigning_workspace(
    organization, workspace, gateway
):
    user = _user_with_level(organization, Level.MEMBER)
    key = _existing_personal_key(organization, user)
    ownership = _ownership(key)
    assert ownership == (organization.pk, user.pk, None)
    client = personal_key_tests.session_client(user, organization, workspace)

    def observe_revoke(gateway_key_id):
        # Observe the real authenticated request, not just a pre-request context.
        assert get_current_workspace() == workspace
        assert gateway_key_id == key.gateway_key_id
        row = AgentccAPIKey.unscoped_objects.get(pk=key.pk)
        assert row.status == AgentccAPIKey.REVOKED
        assert row.deleted is False
        assert _ownership(row) == ownership
        return {}

    gateway.revoke_key.side_effect = observe_revoke
    response = client.delete(f"{personal_key_tests.KEYS}{key.pk}/")

    assert response.status_code == 204, response.content
    gateway.revoke_key.assert_called_once_with(key.gateway_key_id)
    gateway.revoke_key_confirmed.assert_not_called()
    row = AgentccAPIKey.unscoped_objects.get(pk=key.pk)
    assert row.deleted is True
    assert row.deleted_at is not None
    assert row.status == AgentccAPIKey.REVOKED
    assert _ownership(row) == ownership
    assert not AgentccAPIKey.no_workspace_objects.filter(pk=key.pk).exists()


@pytest.mark.parametrize("field", ["workspace", "organization"])
@pytest.mark.parametrize("partial", [False, True], ids=["full-save", "partial-save"])
def test_explicit_ownership_change_still_refused(
    organization, workspace, field, partial
):
    user = _user_with_level(organization, Level.MEMBER)
    key = _existing_personal_key(organization, user)
    ownership = _ownership(key)
    replacement = (
        workspace if field == "workspace" else Organization.objects.create(name="Other")
    )
    setattr(key, field, replacement)

    with workspace_context(workspace, organization=organization, user=user):
        with pytest.raises(ValidationError) as caught:
            key.save(update_fields=[field, "updated_at"] if partial else None)

    assert caught.value.message_dict == {field: [IMMUTABLE_MESSAGE]}
    row = AgentccAPIKey.unscoped_objects.get(pk=key.pk)
    assert _ownership(row) == ownership
    assert row.deleted is False
    assert row.status == AgentccAPIKey.ACTIVE


@pytest.mark.parametrize("scoped", [False, True], ids=["personal", "explicit-workspace"])
def test_creation_preserves_explicit_workspace_choice(
    organization, workspace, scoped
):
    user = _user_with_level(organization, Level.MEMBER)
    chosen_workspace = workspace if scoped else None
    with workspace_context(workspace, organization=organization, user=user):
        assert get_current_workspace() == workspace
        key = AgentccAPIKey.unscoped_objects.create(
            organization=organization,
            user=user,
            workspace=chosen_workspace,
            name="explicit-workspace-choice",
            gateway_key_id="explicit-workspace-choice",
        )
        key.refresh_from_db()
        assert _ownership(key) == (
            organization.pk,
            user.pk,
            chosen_workspace.pk if chosen_workspace else None,
        )
