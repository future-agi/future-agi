"""Every path that disables or deletes a key tells fi-collector (READINESS L03).

fi-collector caches a verified key for its warm TTL (1 h); ``fi:auth:revoke``
evicts it at once. The payload names the key by ``sha256(api_key)``, never by its
secret (the secret is stored hashed). Real Postgres (tfc.settings.test), two
tenants. Paths: the Keys page (disable, delete), the AI tool ``revoke_api_key``
and the Django admin (disable-only change form, refused single/bulk deletion).
"""

import hashlib
import uuid
from unittest.mock import patch

import pytest
import redis
from django.test import Client

from accounts.models import Organization, OrgApiKey, User
from accounts.models.workspace import Workspace
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

# The publisher module's own reference to redis: patching redis.Redis itself would
# reach every other Redis client the request uses.
PUBLISHER = "accounts.api_key_revocation.redis"
CHANNEL = "fi:auth:revoke"


def _digest_payload(key):
    return "apikey:" + hashlib.sha256(key.api_key.encode()).hexdigest()


def _issue_user_key(owner, name="ci"):
    key = OrgApiKey.no_workspace_objects.create(
        name=name, organization=owner.organization, type="user", user=owner
    )
    return key, key.secret_key  # the issuing instance holds the plaintext


def _published(redis_module):
    client = redis_module.Redis.from_url.return_value
    return [call.args for call in client.publish.call_args_list]


def _enabled(key):
    return OrgApiKey._base_manager.get(id=key.id).enabled


@pytest.fixture
def tenant_b(db):
    org = Organization.objects.create(name="Revocation tenant B")
    owner = User.objects.create_user(
        email=f"revoke-b-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Tenant B Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    Workspace.objects.create(
        name="B", organization=org, is_default=True, is_active=True, created_by=owner
    )
    clear_workspace_context()
    return owner


@pytest.mark.django_db
class TestKeysPage:
    def test_disable_publishes_by_key_never_by_secret(self, auth_client, user):
        key, secret = _issue_user_key(user)
        with patch(PUBLISHER) as redis_module:
            auth_client.post(
                "/accounts/key/disable_key/", {"key_id": str(key.id)}, format="json"
            )
        published = _published(redis_module)
        assert published == [(CHANNEL, _digest_payload(key))]
        assert all(secret not in payload for _channel, payload in published)

    def test_system_key_revocation_also_sends_the_legacy_payload(self, user):
        from accounts.api_key_revocation import publish_key_revocation

        key = OrgApiKey.no_workspace_objects.create(
            organization=user.organization, type="system"
        )
        loaded = OrgApiKey.no_workspace_objects.get(id=key.id)
        with patch(PUBLISHER) as redis_module:
            publish_key_revocation(loaded)
        payloads = [payload for _channel, payload in _published(redis_module)]
        legacy = hashlib.sha256(f"{key.api_key}:{key.secret_key}".encode()).hexdigest()
        assert payloads == [_digest_payload(key), legacy]

    def test_a_redis_outage_never_fails_the_revocation(self, auth_client, user):
        key, _secret = _issue_user_key(user)
        with patch(PUBLISHER) as redis_module:
            client = redis_module.Redis.from_url.return_value
            client.publish.side_effect = redis.exceptions.ConnectionError("down")
            response = auth_client.post(
                "/accounts/key/disable_key/", {"key_id": str(key.id)}, format="json"
            )
        assert response.status_code == 200
        assert not _enabled(key)


@pytest.mark.django_db
class TestAiTool:
    def _revoke(self, key, owner):
        from ai_tools.base import ToolContext
        from ai_tools.registry import registry

        workspace = Workspace.objects.get(
            organization=owner.organization, is_default=True
        )
        context = ToolContext(
            user=owner, organization=owner.organization, workspace=workspace
        )
        return registry.get("revoke_api_key").run({"key_id": str(key.id)}, context)

    def test_revoke_publishes_once_by_key_and_spares_the_other_tenant(
        self, user, tenant_b
    ):
        key, secret = _issue_user_key(user)
        other, _other_secret = _issue_user_key(tenant_b, "b")
        with patch(PUBLISHER) as redis_module:
            result = self._revoke(key, user)
            assert not result.is_error
            # Tenant B's key is not found from tenant A's context.
            assert self._revoke(other, user).is_error
            # A second revoke is refused and publishes nothing more.
            assert self._revoke(key, user).is_error
        published = _published(redis_module)
        assert published == [(CHANNEL, _digest_payload(key))]
        assert all(secret not in payload for _channel, payload in published)
        assert not _enabled(key) and _enabled(other)


@pytest.fixture
def admin_client(db, user):
    superuser = User.objects.create_superuser(
        email=f"root-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Root",
        organization=user.organization,
    )
    client = Client()
    client.force_login(superuser)
    return client


def _change_form(key, *, enabled):
    data = {
        "name": key.name,
        "organization": str(key.organization_id),
        "type": key.type,
        "user": str(key.user_id) if key.user_id else "",
        "workspace": str(key.workspace_id) if key.workspace_id else "",
        "_save": "Save",
    }
    if enabled:
        data["enabled"] = "on"
    return data


@pytest.mark.django_db
class TestDjangoAdmin:
    def test_disabling_publishes_and_other_edits_do_not(self, admin_client, user):
        key, _secret = _issue_user_key(user)
        url = f"/admin/accounts/orgapikey/{key.id}/change/"
        with patch(PUBLISHER) as redis_module:
            renamed = {**_change_form(key, enabled=True), "name": "renamed"}
            assert admin_client.post(url, renamed).status_code == 302
            assert _published(redis_module) == []
            assert (
                admin_client.post(url, _change_form(key, enabled=False)).status_code
                == 302
            )
        assert not _enabled(key)
        assert _published(redis_module) == [(CHANNEL, _digest_payload(key))]

    def test_delete_view_refused_and_disable_publishes(self, admin_client, user):
        key, secret = _issue_user_key(user)
        with patch(PUBLISHER) as redis_module:
            response = admin_client.post(
                f"/admin/accounts/orgapikey/{key.id}/delete/", {"post": "yes"}
            )
            assert response.status_code == 403
            assert _published(redis_module) == []
            assert _enabled(key)
            assert (
                admin_client.post(
                    f"/admin/accounts/orgapikey/{key.id}/change/",
                    _change_form(key, enabled=False),
                ).status_code
                == 302
            )
        assert not _enabled(key)
        assert _published(redis_module) == [(CHANNEL, _digest_payload(key))]
        assert all(secret not in payload for _, payload in _published(redis_module))

    def test_bulk_delete_refused_and_each_disable_publishes(
        self, admin_client, user, tenant_b
    ):
        issued = [_issue_user_key(user, "one"), _issue_user_key(tenant_b, "two")]
        keys = [key for key, _ in issued]
        with patch(PUBLISHER) as redis_module:
            response = admin_client.post(
                "/admin/accounts/orgapikey/",
                {
                    "action": "delete_selected",
                    "_selected_action": [str(key.id) for key in keys],
                    "post": "yes",
                },
            )
            # With no permitted actions Django renders the changelist; no delete
            # confirmation or action is dispatched, even for a superuser.
            assert response.status_code == 200
            action_form = response.context["action_form"]
            assert action_form is None or "delete_selected" not in dict(
                action_form.fields["action"].choices
            )
            assert _published(redis_module) == []
            assert all(_enabled(key) for key in keys)
            for key in keys:
                assert (
                    admin_client.post(
                        f"/admin/accounts/orgapikey/{key.id}/change/",
                        _change_form(key, enabled=False),
                    ).status_code
                    == 302
                )
        assert all(not _enabled(key) for key in keys)
        assert sorted(_published(redis_module)) == sorted(
            (CHANNEL, _digest_payload(key)) for key in keys
        )
        assert all(
            secret not in payload
            for _, secret in issued
            for _, payload in _published(redis_module)
        )
