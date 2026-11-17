"""No internal export or onboarding path may silently issue a customer key."""

from unittest.mock import patch

import pytest

from accounts.models import OrgApiKey
from accounts.tests.test_personal_keys import actor  # noqa: F401


@pytest.mark.django_db
def test_platform_emission_and_sandbox_export_are_explicitly_off(actor):
    from tracer.services import collector_ingest

    user, org, _ = actor
    before = OrgApiKey._base_manager.count()
    with patch.object(collector_ingest, "logger") as log:
        assert (
            collector_ingest.resolve_system_emit_credentials(str(org.pk), None) is None
        )
        assert log.info.call_count == 1
        assert log.info.call_args.kwargs["state"] == "OFF"
    with patch.object(collector_ingest, "logger") as log:
        credentials = collector_ingest.get_or_create_export_credentials(
            organization_id=str(org.pk), workspace_id=None, agent_definition_id=None
        )
        assert not credentials.enabled
        assert credentials.api_key == credentials.secret_key == ""
        assert log.info.call_count == 1
        assert log.info.call_args.kwargs["state"] == "OFF"
    assert collector_ingest.PLATFORM_EMISSION_STATE == "OFF"
    assert collector_ingest.SANDBOX_EXPORT_STATE == "OFF"
    assert OrgApiKey._base_manager.count() == before


@pytest.mark.django_db
def test_gateway_customer_issuer_refuses_key_authenticated_person(actor):
    from rest_framework.test import APIRequestFactory, force_authenticate
    from agentcc.views.api_key import AgentccAPIKeyViewSet
    from accounts.tests.personal_keys import personal_key_for

    user, org, _ = actor
    key = personal_key_for(user, org)
    req = APIRequestFactory().post(
        "/agentcc/api-keys/", {"name": "never-created"}, format="json"
    )
    req.organization = org
    req.org_api_key = key
    force_authenticate(req, user=user)
    with patch("agentcc.views.api_key.auth_bridge.provision_key") as provision:
        response = AgentccAPIKeyViewSet.as_view({"post": "create"})(req)
    assert response.status_code == 403
    provision.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["Owner", "Admin"])
def test_org_administrator_revokes_member_key(actor, role):
    from accounts.models import User
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.tests.personal_keys import personal_key_for, session_client_for

    user, org, _ = actor
    key = personal_key_for(user, org)
    admin = User.objects.create_user(
        email=f"{role.lower()}@fixture.invalid",
        organization=org,
        organization_role=role,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=admin, organization=org, role=role
    )
    client = session_client_for(admin, org)
    with patch("accounts.views.keys._publish_key_revocation") as publish:
        response = client.delete(
            "/accounts/key/delete_secret_key/", {"key_id": str(key.pk)}, format="json"
        )
    assert response.status_code == 200
    key.refresh_from_db()
    assert key.deleted and not key.enabled
    publish.assert_called_once()


@pytest.mark.django_db
def test_member_cannot_revoke_someone_elses_key_in_same_org(actor):
    from accounts.models import User
    from accounts.tests.personal_keys import personal_key_for, session_client_for

    user, org, _ = actor
    other = User.objects.create_user(
        email="other-key-owner@fixture.invalid",
        organization=org,
        organization_role="Member",
    )
    key = personal_key_for(other, org)
    client = session_client_for(user, org)
    with patch("accounts.views.keys._publish_key_revocation") as publish:
        response = client.delete(
            "/accounts/key/delete_secret_key/", {"key_id": str(key.pk)}, format="json"
        )
    assert response.status_code == 400
    key.refresh_from_db()
    assert key.enabled and not key.deleted
    assert key.deleted_at is None
    publish.assert_not_called()


@pytest.mark.django_db
def test_django_admin_can_disable_but_cannot_issue_or_reenable(actor):
    from types import SimpleNamespace
    from django.contrib.admin.sites import AdminSite
    from django.core.exceptions import PermissionDenied
    from accounts.admin import OrgApiKeyAdmin
    from accounts.tests.personal_keys import personal_key_for

    user, org, _ = actor
    key = personal_key_for(user, org)
    admin = OrgApiKeyAdmin(OrgApiKey, AdminSite())
    assert not admin.has_add_permission(None)
    assert not admin.has_delete_permission(None, key)
    assert "name" in admin.get_readonly_fields(None, key)
    assert "type" in admin.get_readonly_fields(None, key)
    assert admin.masked_api_key(key) != key.api_key
    key.enabled = False
    with patch("accounts.admin.publish_key_revocation") as publish:
        admin.save_model(None, key, SimpleNamespace(changed_data=["enabled"]), True)
    publish.assert_called_once()
    assert "enabled" in admin.get_readonly_fields(None, key)
    key.enabled = True
    with pytest.raises(PermissionDenied):
        admin.save_model(None, key, SimpleNamespace(changed_data=["enabled"]), True)
    key.refresh_from_db()
    assert not key.enabled
